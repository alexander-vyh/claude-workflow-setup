#!/usr/bin/env python3
"""Observe managed Claude Agent dispatch and completion without denying capacity.

Every handler here is non-blocking. PreToolUse returns an allow and never
denies native Agent capacity; the completion handlers return no decision at all,
because the events they observe do not ask for one.

The completion side (escapement-mn2q) is built entirely on the payloads
escapement-g27c captured from a real host (Claude Code 2.1.248), stored at
harness/tests/fixtures/agent_dispatch_hook_payloads.json. Four captured facts
shape it:

- Agent PostToolUse carries the native child id at tool_response.agentId. That
  is the ONLY place the dispatch's tool_use_id and the child's identity appear
  together, so it is the binding event.
- The relative order of PostToolUse and SubagentStop is dispatch-mode
  dependent, so binding must be order-tolerant.
- tool_use_id is absent from the subagent events, so agent_id is the only join
  back to the dispatch.
- A backgrounded PostToolUse is a launch receipt, not a verdict. The child's
  final text comes from SubagentStop.last_assistant_message when backgrounded,
  and from tool_response.content when synchronous.

A dispatch the host rejects fires PostToolUseFailure and no subagent event,
which would otherwise strand the execution registered at PreToolUse.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import uuid

from delegation_completion import post_tool, post_tool_failure, subagent_stop
from execution_expectation import record_expectation, record_incident
from execution_ledger import new_ledger, register_execution
from execution_store import initialize_or_mutate_atomic
from task_session_mode import load_task_context
from thread_identity import InvalidActorIdentity, resolve_thread_dir


UTC = dt.timezone.utc


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
    return resolve_thread_dir(session_id, _harness_root()) / "executions.json"


def pre_tool(payload: dict, run_bd, ledger_path) -> dict:
    """Observe one managed dispatch and always preserve native Agent capacity."""
    del run_bd
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("hook_event_name") != "PreToolUse"
        or payload.get("tool_name") != "Agent"
    ):
        return {"decision": "allow", "reason": "unmanaged_native_agent"}
    tool_input = payload.get("tool_input")
    agent_name = tool_input.get("name") if isinstance(tool_input, dict) else None
    if not isinstance(session_id, str) or not session_id:
        return {"decision": "allow", "reason": "dispatch_evidence_unresolved"}
    path = pathlib.Path(ledger_path)
    task_mode = load_task_context(path.parent / "session_mode.json", session_id)
    if task_mode is None:
        return {"decision": "allow", "reason": "unmanaged_native_agent"}
    tool_use_id = payload.get("tool_use_id")
    if (
        not isinstance(agent_name, str)
        or not agent_name
        or not isinstance(tool_use_id, str)
        or not tool_use_id
    ):
        try:
            record_incident(
                path.parent / "execution_incident.json",
                parent_session_id=session_id,
                tool_use_id=tool_use_id
                if isinstance(tool_use_id, str) and tool_use_id
                else None,
                reason="invalid_agent_dispatch_payload",
                now=_iso_now(),
            )
        except (OSError, ValueError):
            pass
        return {"decision": "allow", "reason": "dispatch_evidence_unresolved"}
    task_id = task_mode.get("task_id") or task_mode.get("parent_id")
    if not isinstance(task_id, str) or not task_id:
        return {"decision": "allow", "reason": "dispatch_evidence_unresolved"}
    now = _iso_now()
    expectation_path = path.parent / "execution_expectation.json"
    incident_path = path.parent / "execution_incident.json"
    try:
        record_expectation(
            expectation_path,
            parent_session_id=session_id,
            task_id=task_id,
            tool_use_id=tool_use_id,
            agent_name=agent_name,
            host="claude",
            now=now,
        )
    except (OSError, TypeError, ValueError, KeyError):
        try:
            record_incident(
                incident_path,
                parent_session_id=session_id,
                tool_use_id=tool_use_id,
                reason="expectation_persistence_failed",
                now=now,
            )
        except (OSError, ValueError):
            pass
        return {"decision": "allow", "reason": "dispatch_evidence_unresolved"}

    execution_id = uuid.uuid4().hex
    watchdog_id = uuid.uuid4().hex
    event = {
        "kind": "dispatch_registered",
        "parent_session_id": session_id,
        "bead_id": task_id,
        "execution_id": execution_id,
        "host": "claude",
        "agent_name": agent_name,
        "dispatch_tool_use_id": tool_use_id,
        "watchdog_id": watchdog_id,
        "attempt": 1,
        "generation": 1,
    }

    def register(current: dict) -> dict:
        matches = [
            item
            for item in current.get("executions", [])
            if item.get("dispatch_tool_use_id") == tool_use_id
        ]
        if len(matches) > 1:
            raise ValueError("dispatch tool identity is ambiguous")
        if matches and any(
            matches[0].get(key) != event[key]
            for key in ("bead_id", "agent_name", "host")
        ):
            raise ValueError("dispatch tool identity conflicts")
        return current if matches else register_execution(current, event, now)

    try:
        updated = initialize_or_mutate_atomic(
            path,
            lambda: new_ledger(session_id),
            register,
        )
    except (OSError, TypeError, ValueError, KeyError):
        return {"decision": "allow", "reason": "dispatch_evidence_unresolved"}

    dispatched = next(
        item
        for item in updated["executions"]
        if item["dispatch_tool_use_id"] == tool_use_id
    )
    return {
        "decision": "allow",
        "reason": "dispatch_registered",
        "execution_id": dispatched["execution_id"],
        "attempt": dispatched["attempt"],
        "generation": dispatched["generation"],
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
                        "hookEventName": payload.get("hook_event_name", "PreToolUse"),
                        "permissionDecision": "allow",
                        "permissionDecisionReason": (
                            f"dispatch_evidence_unresolved: {exc}"
                        ),
                    }
                }
            )
        )
        return 0
    # Completion observation never speaks: these events carry no decision, and
    # a SubagentStop hook that printed a decision would be steering the child.
    # Observation must also never fail the host, so the appliers swallow their
    # own errors and the unresolved execution falls through to the deadline
    # path rather than taking the session down.
    completion = {
        "PostToolUse": post_tool,
        "PostToolUseFailure": post_tool_failure,
        "SubagentStop": subagent_stop,
    }.get(payload.get("hook_event_name"))
    if completion is not None:
        completion(payload, ledger_path)
        return 0
    result = pre_tool(
        payload,
        None,
        ledger_path,
    )
    reason = result["reason"]
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
    try:
        prepared = _prepare(args)
    except InvalidActorIdentity as exc:
        print(f"invalid actor identity: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(prepared, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
