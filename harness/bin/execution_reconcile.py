#!/usr/bin/env python3
"""Reconciliation for durable delegated execution attempts.

With no arguments this is the SessionStart hook.  With `list` or `cancel` it is
the supported operator front door for an execution the host never reported on,
so recovering a wedged session never means hand-editing `executions.json`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

from execution_cancellation import cancel_unreported, overdue_reason
from execution_ledger import apply_event, reconcile_deadlines
from execution_parent import classify_canonical_parent
from execution_store import load_trusted, mutate_atomic
import gate_signal
from thread_identity import InvalidActorIdentity, resolve_thread_dir


def _harness_root() -> pathlib.Path:
    configured = os.environ.get("HARNESS_ROOT") or os.environ.get(
        "CONTINUATION_HARNESS_HOME"
    )
    return (
        pathlib.Path(configured)
        if configured
        else pathlib.Path.home() / ".claude" / "harness"
    )


def _ledger_path(session_id: str) -> pathlib.Path:
    return resolve_thread_dir(session_id, _harness_root()) / "executions.json"


def _one_exact(records: object, expected_id: str) -> dict | None:
    if not isinstance(records, list) or len(records) != 1:
        return None
    record = records[0]
    if not isinstance(record, dict) or record.get("id") != expected_id:
        return None
    return record


def _append_once(messages: list[str], message: str) -> None:
    if message not in messages:
        messages.append(message)


def _apply_normalized_events(
    payload: dict, ledger: dict, now: dt.datetime, messages: list[str]
) -> None:
    events = payload.get("execution_events", [])
    if not isinstance(events, list):
        _append_once(
            messages,
            "terminal event identity is unresolved; do not default missing generation "
            "to the active attempt.",
        )
        return
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path:
        try:
            from claude_agent_lifecycle import observe_transcript

            events = [*events, *observe_transcript(pathlib.Path(transcript_path), ledger)]
        except (OSError, TypeError, ValueError):
            # Host observation is deliberately fail-open.  Its missing evidence
            # remains visible through the managed ledger/completion boundary.
            pass
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("generation"), int):
            _append_once(
                messages,
                "terminal event identity is unresolved; do not default missing generation "
                "to the active attempt.",
            )
            continue
        try:
            apply_event(ledger, event, now)
        except ValueError:
            _append_once(
                messages,
                "terminal event identity is unresolved; inspect its execution, attempt, "
                "generation, and native child before continuing.",
            )


def _canonical_parent_messages(ledger: dict, run_bd, messages: list[str]) -> None:
    parents: list[str] = []
    seen_children: set[str] = set()
    for execution in ledger.get("executions", []):
        bead_id = execution.get("bead_id") if isinstance(execution, dict) else None
        if not isinstance(bead_id, str) or not bead_id:
            _append_once(
                messages,
                "execution Beads identity is unresolved; inspect executions.json before "
                "continuing.",
            )
            continue
        if bead_id in seen_children:
            continue
        seen_children.add(bead_id)
        child = _one_exact(run_bd(["show", bead_id]), bead_id)
        if child is None:
            _append_once(
                messages,
                f"canonical Beads state for {bead_id} is unresolved; run `bd show "
                f"{bead_id}` before continuing.",
            )
            continue
        relationship, parent_id = classify_canonical_parent(child)
        if relationship == "standalone":
            continue
        if relationship != "parented":
            _append_once(
                messages,
                f"canonical parent relationship for {bead_id} is unresolved; run `bd "
                f"show {bead_id}` and repair its Beads parent relationship before "
                "continuing.",
            )
            continue
        if parent_id not in parents:
            parents.append(parent_id)

    for parent_id in parents:
        parent = _one_exact(run_bd(["show", parent_id]), parent_id)
        if parent is None:
            _append_once(
                messages,
                f"canonical Beads state for parent {parent_id} is unresolved; run `bd "
                f"show {parent_id}` and resolve the parent record before continuing.",
            )
        elif parent.get("status") != "closed":
            _append_once(
                messages,
                f"parent outcome {parent_id} is unresolved; run `bd show {parent_id}` "
                "and verify the outcome before closing.",
            )


def reconcile_session(
    payload: dict,
    run_bd,
    ledger_loader,
    now: dt.datetime,
    ledger_mutator=None,
) -> dict:
    """Return normalized SessionStart continuation context."""
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session_id, str) or not session_id:
        return {
            "status": "continue",
            "additional_context": (
                "delegated execution session identity is unresolved; inspect "
                "executions.json before continuing."
            ),
        }
    ledger = ledger_loader(session_id)
    if (
        not isinstance(ledger, dict)
        or ledger.get("parent_session_id") != session_id
        or not isinstance(ledger.get("executions"), list)
    ):
        return {
            "status": "continue",
            "additional_context": (
                "execution ledger is missing or untrusted; inspect executions.json "
                "before continuing."
            ),
        }

    messages: list[str] = []
    due: list[dict] = []

    def reconcile(current: dict) -> dict:
        nonlocal due
        if current.get("parent_session_id") != session_id:
            raise ValueError("parent session does not match ledger")
        _apply_normalized_events(payload, current, now, messages)
        due = reconcile_deadlines(current, now)
        return current

    try:
        ledger = (
            ledger_mutator(session_id, reconcile)
            if ledger_mutator is not None
            else reconcile(ledger)
        )
    except (OSError, TypeError, ValueError, KeyError):
        return {
            "status": "continue",
            "additional_context": (
                "execution reconciliation could not be durably persisted; inspect "
                "executions.json before continuing."
            ),
        }
    _canonical_parent_messages(ledger, run_bd, messages)
    for execution in due:
        _append_once(
            messages,
            f"execution {execution['execution_id']} attempt {execution['attempt']} "
            f"generation {execution['generation']} crossed its "
            f"{execution['reconcile_due']} deadline; reconcile before continuing or "
            "yielding.",
        )

    if not messages:
        return {"status": "clear", "additional_context": ""}
    return {"status": "continue", "additional_context": " ".join(messages)}


def _default_run_bd(cwd: str):
    def run_bd(args: list[str]):
        try:
            repo_cwd = cwd if cwd and pathlib.Path(cwd).is_dir() else None
            result = subprocess.run(
                ["bd", *args, "--json"],
                cwd=repo_cwd,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return None
            value = json.loads(result.stdout)
            return value if isinstance(value, list) else None
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

    return run_bd


def _resolve_ledger_path(args) -> pathlib.Path:
    if args.ledger_path:
        return pathlib.Path(args.ledger_path)
    return _ledger_path(args.session)


def _parse_now(value: str | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--now must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def _list_command(args) -> int:
    """Print every execution this session owns, with why it is still active."""
    try:
        path = _resolve_ledger_path(args)
        now = _parse_now(args.now)
    except (InvalidActorIdentity, ValueError) as exc:
        print(f"execution_reconcile list: {exc}", file=sys.stderr)
        return 2
    ledger = load_trusted(path, args.session)
    if ledger is None:
        print(
            f"execution_reconcile list: no trusted ledger for session "
            f"{args.session} at {path}",
            file=sys.stderr,
        )
        return 2
    rows = [
        {
            "execution_id": item["execution_id"],
            "bead_id": item["bead_id"],
            "agent_name": item["agent_name"],
            "attempt": item["attempt"],
            "generation": item["generation"],
            "state": item["state"],
            "native_child_id": item["native_child_id"],
            "result_application": item["result_application"]["state"],
            "overdue": overdue_reason(item, now),
        }
        for item in ledger["executions"]
    ]
    print(
        json.dumps(
            {"parent_session_id": ledger["parent_session_id"], "executions": rows},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _cancel_command(args) -> int:
    """Terminalize one overdue execution whose child never reported a result."""
    try:
        path = _resolve_ledger_path(args)
        now = _parse_now(args.now)
    except (InvalidActorIdentity, ValueError) as exc:
        print(f"execution_reconcile cancel: {exc}", file=sys.stderr)
        return 2
    actor = args.actor or f"session:{args.session}"
    recorded: dict[str, dict] = {}

    def cancel(current: dict) -> dict:
        if current.get("parent_session_id") != args.session:
            raise ValueError("parent session does not match ledger")
        updated = cancel_unreported(
            current, args.execution_id, now, reason=args.reason, actor=actor
        )
        item = next(
            entry
            for entry in updated["executions"]
            if entry["execution_id"] == args.execution_id
        )
        recorded["item"] = {
            "execution_id": item["execution_id"],
            "bead_id": item["bead_id"],
            "attempt": item["attempt"],
            "generation": item["generation"],
            "state": item["state"],
            "terminal_reason": item["terminal_reason"],
            "terminal_event_id": item["terminal_event_id"],
            "result_application": item["result_application"]["state"],
        }
        return updated

    try:
        mutate_atomic(path, cancel)
    except (OSError, ValueError) as exc:
        gate_signal.record(
            "waiver-rejected",
            "delegated_execution_cancel_refused",
            args.session,
            execution_id=args.execution_id,
            refusal=str(exc),
        )
        print(f"execution_reconcile cancel: {exc}", file=sys.stderr)
        return 2
    # Rule 2 of gate-design: the escape's reason is the labeled corpus for
    # half-life review, and half-life review reads only .gate-signal.jsonl.
    # The ledger incident is the per-thread audit record; this is the greppable
    # cross-session one.
    gate_signal.record(
        "waiver-accepted",
        "delegated_execution_cancelled_unreported",
        args.session,
        waiver_reason=args.reason.strip(),
        execution_id=args.execution_id,
        bead_id=recorded["item"]["bead_id"],
        actor=actor,
    )
    print(json.dumps({"status": "cancelled", **recorded["item"]}, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="execution_reconcile",
        description=(
            "Inspect and terminalize this session's delegated executions. Run with "
            "no arguments to act as the SessionStart reconciliation hook."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser(
        "list", help="show each execution's state, child binding, and overdue reason"
    )
    listing.add_argument("--session", required=True)
    listing.add_argument("--ledger-path")
    listing.add_argument("--now")
    listing.set_defaults(handler=_list_command)

    cancel = subparsers.add_parser(
        "cancel",
        help=(
            "record that an overdue execution's child died without reporting; this "
            "is a cancellation, never a claim that its work succeeded"
        ),
    )
    cancel.add_argument("--session", required=True)
    cancel.add_argument("--execution-id", required=True)
    cancel.add_argument(
        "--reason",
        required=True,
        help=(
            "why no result will arrive; at least 20 characters and not a "
            "placeholder, recorded durably in the ledger's incidents"
        ),
    )
    cancel.add_argument("--actor", default=None)
    cancel.add_argument("--ledger-path")
    cancel.add_argument("--now")
    cancel.set_defaults(handler=_cancel_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if argv:
        args = _build_parser().parse_args(argv)
        return args.handler(args)
    return _hook_main()


def _hook_main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    session_id = payload.get("session_id", "") if isinstance(payload, dict) else ""
    try:
        ledger_path = _ledger_path(session_id)
    except InvalidActorIdentity as exc:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": f"invalid actor identity: {exc}",
                    }
                }
            )
        )
        return 0
    result = reconcile_session(
        payload,
        _default_run_bd(payload.get("cwd", "")),
        lambda expected: load_trusted(ledger_path, expected),
        dt.datetime.now(dt.timezone.utc),
        lambda _expected, mutation: mutate_atomic(ledger_path, mutation),
    )
    if result["additional_context"]:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": result["additional_context"],
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
