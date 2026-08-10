#!/usr/bin/env python3
"""SessionStart reconciliation for durable delegated execution attempts."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

from execution_ledger import apply_event, reconcile_deadlines
from execution_store import load_trusted, mutate_atomic


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
    return _harness_root() / "threads" / session_id / "executions.json"


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
        parent_id = child.get("parent") or child.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
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


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    result = reconcile_session(
        payload,
        _default_run_bd(payload.get("cwd", "")),
        lambda expected: load_trusted(_ledger_path(expected), expected),
        dt.datetime.now(dt.timezone.utc),
        lambda expected, mutation: mutate_atomic(_ledger_path(expected), mutation),
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
