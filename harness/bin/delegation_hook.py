#!/usr/bin/env python3
"""Prepare and gate host-native delegated execution attempts.

Only Claude Agent PreToolUse is registered.  Agent PostToolUse remains an
explicit unresolved adapter until an installed payload capture proves the
native child identifier's location.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import uuid

from execution_ledger import new_ledger, register_execution
from execution_store import initialize_or_mutate_atomic, load_trusted, mutate_atomic


UTC = dt.timezone.utc


class _PreparedAttemptConsumed(ValueError):
    """The fresh atomic view no longer contains the selected preparation."""


def _iso_now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _parse_now(value: str | None) -> dt.datetime:
    if value is None:
        return _iso_now()
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--now must be timezone-aware")
    return parsed.astimezone(UTC)


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


def _repair_command(session_id: str, agent_name: str) -> str:
    return (
        'python3 -B "${CLAUDE_PLUGIN_ROOT}/harness/bin/delegation_hook.py" prepare '
        f"--bead-id <child-bead-id> --session {session_id} --host claude "
        f"--agent-name {agent_name}"
    )


def _deny(reason: str, session_id: str, agent_name: str) -> dict:
    return {
        "decision": "deny",
        "reason": reason,
        "additional_context": _repair_command(session_id, agent_name),
    }


def find_prepared_execution(tool_input: dict, ledger: dict) -> dict | None:
    """Find one structural preparation; prompt prose is deliberately ignored."""
    if not isinstance(tool_input, dict) or not isinstance(ledger, dict):
        return None
    agent_name = tool_input.get("name")
    if (
        not isinstance(agent_name, str)
        or not agent_name
        or tool_input.get("run_in_background") is not True
    ):
        return None
    matches = [
        item
        for item in ledger.get("executions", [])
        if isinstance(item, dict)
        and item.get("host") == "claude"
        and item.get("agent_name") == agent_name
        and item.get("state") == "queued"
        and item.get("native_child_id") is None
        and item.get("dispatch_tool_use_id") == f"prepared:{item.get('execution_id')}"
    ]
    return matches[0] if len(matches) == 1 else None


def _canonical_bead(records: object, bead_id: str) -> tuple[str, str]:
    if not isinstance(records, list) or len(records) != 1:
        return ("deny", "bead_state_unresolved")
    record = records[0]
    if not isinstance(record, dict) or record.get("id") != bead_id:
        return ("deny", "bead_state_unresolved")
    status = record.get("status")
    if status == "closed":
        return ("deny", "bead_not_dispatchable")
    if status not in {"open", "in_progress"}:
        return ("deny", "bead_not_dispatchable")
    return ("allow", "bead_dispatchable")


def pre_tool(payload: dict, run_bd, ledger_path) -> dict:
    """Validate and durably consume one prepared Claude Agent dispatch."""
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    agent_name = tool_input.get("name") if isinstance(tool_input, dict) else None
    if not isinstance(session_id, str) or not session_id:
        session_id = "<session-id>"
    if not isinstance(agent_name, str) or not agent_name:
        agent_name = "<agent-name>"
    if (
        not isinstance(payload, dict)
        or payload.get("hook_event_name") != "PreToolUse"
        or payload.get("tool_name") != "Agent"
        or not isinstance(tool_input, dict)
    ):
        return _deny("prepared_execution_required", session_id, agent_name)

    path = pathlib.Path(ledger_path)
    ledger = load_trusted(path, session_id)
    prepared = find_prepared_execution(tool_input, ledger or {})
    if prepared is None:
        return _deny("prepared_execution_required", session_id, agent_name)

    bead_id = prepared["bead_id"]
    decision, reason = _canonical_bead(run_bd(["show", bead_id]), bead_id)
    if decision != "allow":
        return _deny(reason, session_id, agent_name)

    tool_use_id = payload.get("tool_use_id")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        return _deny("prepared_execution_required", session_id, agent_name)
    execution_id = prepared["execution_id"]

    def consume(current: dict) -> dict:
        fresh = find_prepared_execution(tool_input, current)
        if fresh is None or fresh.get("execution_id") != execution_id:
            raise _PreparedAttemptConsumed("prepared execution was already consumed")
        fresh["dispatch_tool_use_id"] = tool_use_id
        return current

    try:
        updated = mutate_atomic(path, consume)
    except _PreparedAttemptConsumed:
        return _deny("prepared_execution_required", session_id, agent_name)
    except (OSError, ValueError):
        return _deny("dispatch_persistence_failed", session_id, agent_name)

    dispatched = next(
        item for item in updated["executions"] if item["execution_id"] == execution_id
    )
    return {
        "decision": "allow",
        "reason": "dispatch_registered",
        "execution_id": execution_id,
        "attempt": dispatched["attempt"],
        "generation": dispatched["generation"],
    }


def post_tool(payload: dict, ledger_path) -> dict:
    """Fail closed until an installed Agent result fixture proves child identity."""
    del payload, ledger_path
    return {
        "status": "unresolved",
        "reason": "native_child_identity_unverified",
    }


def _prepare(args: argparse.Namespace) -> dict:
    path = (
        pathlib.Path(args.ledger_path)
        if args.ledger_path
        else _ledger_path(args.session)
    )
    event = {
        "kind": "dispatch_registered",
        "parent_session_id": args.session,
        "bead_id": args.bead_id,
        "execution_id": args.execution_id,
        "host": args.host,
        "agent_name": args.agent_name,
        "dispatch_tool_use_id": f"prepared:{args.execution_id}",
        "watchdog_id": args.watchdog_id,
        "attempt": 1,
        "generation": 1,
    }
    now = _parse_now(args.now)
    path.parent.mkdir(parents=True, exist_ok=True)

    def register(current: dict) -> dict:
        if current.get("parent_session_id") != args.session:
            raise ValueError("parent session does not match ledger")
        return register_execution(current, event, now)

    initialize_or_mutate_atomic(
        path,
        lambda: new_ledger(args.session),
        register,
    )
    return {
        "status": "prepared",
        "bead_id": args.bead_id,
        "execution_id": args.execution_id,
        "attempt": 1,
        "generation": 1,
    }


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


def _hook_main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    session_id = payload.get("session_id", "") if isinstance(payload, dict) else ""
    if payload.get("hook_event_name") == "PostToolUse":
        post_tool(payload, _ledger_path(session_id))
        return 0
    result = pre_tool(
        payload,
        _default_run_bd(payload.get("cwd", "")),
        _ledger_path(session_id),
    )
    reason = result["reason"]
    if result["decision"] == "deny":
        reason = f"{reason}. Prepare it first: {result['additional_context']}"
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": result["decision"],
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--ledger-path")
    prepare.add_argument("--bead-id", required=True)
    prepare.add_argument("--session", required=True)
    prepare.add_argument("--host", required=True, choices=("claude", "codex"))
    prepare.add_argument("--agent-name", required=True)
    prepare.add_argument("--execution-id", default=None)
    prepare.add_argument("--watchdog-id", default=None)
    prepare.add_argument("--now")
    args = parser.parse_args(argv)
    if args.command != "prepare":
        return _hook_main()
    args.execution_id = args.execution_id or uuid.uuid4().hex
    args.watchdog_id = args.watchdog_id or uuid.uuid4().hex
    print(json.dumps(_prepare(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
