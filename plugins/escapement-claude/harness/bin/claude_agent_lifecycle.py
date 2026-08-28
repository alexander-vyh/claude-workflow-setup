#!/usr/bin/env python3
"""Normalize only Claude Agent lifecycle records proven by captured fixtures.

This module deliberately recognizes a very small installed-host vocabulary.
Anything incomplete, stale, or structurally different is unresolved evidence,
not an invitation to recover identities from text or ledger defaults.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _canonical_digest(record: dict) -> str:
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_id(kind: str, record: dict) -> str:
    return f"claude:{kind}:sha256:{_canonical_digest(record)}"


def _result_digest(record: dict) -> str:
    return f"sha256:{_canonical_digest(record)}"


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _session(record: object) -> str | None:
    return _text(record.get("session_id")) if isinstance(record, dict) else None


def _only_execution(
    ledger: dict, session_id: str, dispatch_id: str, name: str
) -> dict | None:
    if ledger.get("parent_session_id") != session_id:
        return None
    matches = [
        execution
        for execution in ledger.get("executions", [])
        if isinstance(execution, dict)
        and execution.get("host") == "claude"
        and execution.get("dispatch_tool_use_id") == dispatch_id
        and execution.get("agent_name") == name
        and execution.get("attempt") == 1
        # Claude's captured lifecycle records carry no generation.  Generation
        # one is the only independently proven mapping; do not copy the active
        # ledger generation into a host observation after recovery takeover.
        and execution.get("generation") == 1
    ]
    return matches[0] if len(matches) == 1 else None


def _event_base(ledger: dict, execution: dict, kind: str, record: dict) -> dict:
    return {
        "kind": kind,
        "parent_session_id": ledger["parent_session_id"],
        "execution_id": execution["execution_id"],
        "attempt": execution["attempt"],
        "generation": execution["generation"],
        "host_event_id": _event_id(kind, record),
    }


def _tool_content(record: object) -> dict | None:
    if not isinstance(record, dict):
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    item = content[0]
    return item if isinstance(item, dict) else None


def _agent_use(record: object) -> tuple[str, str, str] | None:
    content = _tool_content(record)
    session_id = _session(record)
    if content is None or session_id is None:
        return None
    tool_id = _text(content.get("id"))
    input_value = content.get("input")
    name = _text(input_value.get("name")) if isinstance(input_value, dict) else None
    if (
        content.get("type") != "tool_use"
        or content.get("name") != "Agent"
        or not isinstance(input_value, dict)
        or input_value.get("run_in_background") is not True
    ):
        return None
    if tool_id is None or name is None:
        return None
    return session_id, tool_id, name


def _result_tool_id(record: object) -> str | None:
    content = _tool_content(record)
    if content is None or content.get("type") != "tool_result":
        return None
    return _text(content.get("tool_use_id"))


def _historical_interactive_spawn(record: dict, ledger: dict) -> list[dict]:
    """Accept only the retained, exact historical interactive spawn result."""
    session_id = _session(record)
    result = record.get("toolUseResult")
    dispatch_id = _result_tool_id(record)
    if session_id is None or dispatch_id is None or not isinstance(result, dict):
        return []
    child_id = _text(result.get("agent_id"))
    teammate_id = _text(result.get("teammate_id"))
    name = _text(result.get("name"))
    if (
        result.get("status") != "teammate_spawned"
        or child_id is None
        or teammate_id != child_id
        or name is None
        or "native_child_id" in result
    ):
        return []
    execution = _only_execution(ledger, session_id, dispatch_id, name)
    if (
        execution is None
        or execution.get("state") != "queued"
        or execution.get("native_child_id") is not None
    ):
        return []
    bound = _event_base(ledger, execution, "child_bound", record)
    bound["native_child_id"] = child_id
    started = _event_base(ledger, execution, "child_started", record)
    started["native_child_id"] = child_id
    return [bound, started]


def observe_post_tool(payload: dict, ledger: dict) -> dict:
    """Observe the public Claude 2.1.248 Agent PostToolUse envelope."""
    if not isinstance(payload, dict) or not isinstance(ledger, dict):
        return {
            "status": "unresolved",
            "reason": "native_child_identity_unverified",
            "events": [],
        }
    session_id = _session(payload)
    dispatch_id = _text(payload.get("tool_use_id"))
    tool_input = payload.get("tool_input")
    tool_response = payload.get("tool_response")
    name = _text(tool_input.get("name")) if isinstance(tool_input, dict) else None
    child_id = (
        _text(tool_response.get("agentId")) if isinstance(tool_response, dict) else None
    )
    if (
        payload.get("hook_event_name") != "PostToolUse"
        or payload.get("tool_name") != "Agent"
        or session_id is None
        or dispatch_id is None
        or not isinstance(tool_input, dict)
        or set(payload) != {
            "hook_event_name", "session_id", "tool_name", "tool_use_id",
            "tool_input", "tool_response",
        }
        or set(tool_input) != {"name", "run_in_background", "subagent_type"}
        or name is None
        or tool_input.get("run_in_background") is not True
        or _text(tool_input.get("subagent_type")) is None
        or not isinstance(tool_response, dict)
        or set(tool_response) != {"status", "isAsync", "agentId"}
        or tool_response.get("status") != "async_launched"
        or tool_response.get("isAsync") is not True
        or child_id is None
    ):
        return {
            "status": "unresolved",
            "reason": "native_child_identity_unverified",
            "events": [],
        }
    execution = _only_execution(ledger, session_id, dispatch_id, name)
    if (
        execution is None
        or execution.get("state") != "queued"
        or execution.get("native_child_id") is not None
    ):
        return {
            "status": "unresolved",
            "reason": "native_child_identity_unverified",
            "events": [],
        }
    bound = _event_base(ledger, execution, "child_bound", payload)
    bound["native_child_id"] = child_id
    started = _event_base(ledger, execution, "child_started", payload)
    started["native_child_id"] = child_id
    return {"status": "observed", "events": [bound, started]}


def _ordered_background_witness(
    records: list[dict], execution: dict, session_id: str
) -> tuple[int, int, int, dict, dict, dict] | None:
    dispatches = []
    starts = []
    launches = []
    for index, record in enumerate(records):
        agent = _agent_use(record)
        if agent == (
            session_id,
            execution.get("dispatch_tool_use_id"),
            execution.get("agent_name"),
        ):
            dispatches.append((index, record))
        if (
            _session(record) == session_id
            and record.get("type") == "system"
            and record.get("subtype") == "task_started"
            and record.get("tool_use_id") == execution.get("dispatch_tool_use_id")
            and _text(record.get("task_id")) is not None
            and record.get("is_backgrounded") is True
            and record.get("task_type") == "local_agent"
        ):
            starts.append((index, record))
        if (
            _session(record) == session_id
            and _result_tool_id(record) == execution.get("dispatch_tool_use_id")
            and isinstance(record.get("tool_use_result"), dict)
            and record["tool_use_result"].get("status") == "async_launched"
            and record["tool_use_result"].get("isAsync") is True
            and _text(record["tool_use_result"].get("agentId")) is not None
        ):
            launches.append((index, record))
    if len(dispatches) != 1 or len(starts) != 1 or len(launches) != 1:
        return None
    dispatch_index, dispatch = dispatches[0]
    start_index, started = starts[0]
    launch_index, launched = launches[0]
    if not dispatch_index < start_index < launch_index:
        return None
    if started["task_id"] != launched["tool_use_result"]["agentId"]:
        return None
    return dispatch_index, start_index, launch_index, dispatch, started, launched


def _background_spawn(records: list[dict], ledger: dict) -> list[dict]:
    for agent_record in records:
        agent = _agent_use(agent_record)
        if agent is None:
            continue
        session_id, dispatch_id, name = agent
        execution = _only_execution(ledger, session_id, dispatch_id, name)
        if (
            execution is None
            or execution.get("state") != "queued"
            or execution.get("native_child_id") is not None
        ):
            continue
        witness_records = _ordered_background_witness(records, execution, session_id)
        if witness_records is None:
            continue
        _dispatch_i, _start_i, _launch_i, agent_record, started_record, launch_record = witness_records
        child_id = started_record["task_id"]
        witness = {"agent": agent_record, "started": started_record, "launched": launch_record}
        bound = _event_base(ledger, execution, "child_bound", witness)
        bound["native_child_id"] = child_id
        started = _event_base(ledger, execution, "child_started", witness)
        started["native_child_id"] = child_id
        return [bound, started]
    return []


def _abort_events(records: list[dict], ledger: dict) -> list[dict]:
    for dispatch_index, agent_record in enumerate(records):
        agent = _agent_use(agent_record)
        if agent is None:
            continue
        session_id, dispatch_id, name = agent
        execution = _only_execution(ledger, session_id, dispatch_id, name)
        if (
            execution is None
            or execution.get("state") != "queued"
            or execution.get("native_child_id") is not None
        ):
            continue
        matches = []
        for error_index, item in enumerate(records):
            content = _tool_content(item)
            if _session(item) != session_id or content is None:
                continue
            required = {
                "type": "tool_result",
                "is_error": True,
                "tool_use_id": dispatch_id,
            }
            allowed_keys = {frozenset(required), frozenset((*required, "content"))}
            if (
                all(content.get(key) == value for key, value in required.items())
                and frozenset(content) in allowed_keys
                and (
                    "content" not in content
                    or isinstance(content["content"], str) and bool(content["content"])
                )
            ):
                if dispatch_index < error_index:
                    matches.append(item)
        if len(matches) == 1:
            event = _event_base(ledger, execution, "dispatch_aborted", matches[0])
            event["terminal_reason"] = "native_dispatch_error"
            return [event]
    return []


def _peer_events(records: list[dict], ledger: dict) -> list[dict]:
    events: list[dict] = []
    for record in records:
        session_id = _session(record)
        origin = record.get("origin") if isinstance(record, dict) else None
        if (
            session_id is None
            or not isinstance(origin, dict)
            or origin.get("kind") != "peer"
        ):
            continue
        child_id = _text(origin.get("senderTaskId"))
        name = _text(origin.get("name"))
        if child_id is None or name is None or origin.get("from") != name:
            continue
        matches = [
            item
            for item in ledger.get("executions", [])
            if isinstance(item, dict)
            and ledger.get("parent_session_id") == session_id
            and item.get("host") == "claude"
            and item.get("attempt") == 1
            and item.get("generation") == 1
            and item.get("state") == "running"
            and item.get("native_child_id") == child_id
            and item.get("agent_name") == name
        ]
        if len(matches) != 1:
            continue
        event = _event_base(ledger, matches[0], "activity_completed", record)
        event["native_child_id"] = child_id
        event["activity_kind"] = "assistant_nonempty"
        events.append(event)
    return events


def _terminal_events(records: list[dict], ledger: dict) -> list[dict]:
    events: list[dict] = []
    for terminal_index, record in enumerate(records):
        session_id = _session(record)
        dispatch_id = (
            _text(record.get("tool_use_id")) if isinstance(record, dict) else None
        )
        child_id = _text(record.get("task_id")) if isinstance(record, dict) else None
        terminal_id = _text(record.get("uuid")) if isinstance(record, dict) else None
        if (
            session_id is None
            or dispatch_id is None
            or child_id is None
            or terminal_id is None
            or record.get("type") != "system"
            or record.get("subtype") != "task_notification"
            or record.get("status") != "completed"
        ):
            continue
        matches = [
            item
            for item in ledger.get("executions", [])
            if isinstance(item, dict)
            and ledger.get("parent_session_id") == session_id
            and item.get("host") == "claude"
            and item.get("dispatch_tool_use_id") == dispatch_id
            and item.get("native_child_id") == child_id
            and item.get("state") == "running"
            and item.get("attempt") == 1
            and item.get("generation") == 1
        ]
        if len(matches) != 1:
            continue
        witness = _ordered_background_witness(records, matches[0], session_id)
        if witness is None or witness[2] >= terminal_index:
            continue
        event = _event_base(ledger, matches[0], "child_terminal", record)
        event.update(
            {
                "native_child_id": child_id,
                "terminal_event_id": terminal_id,
                "terminal_reason": "completed",
                "result_digest": _result_digest(record),
            }
        )
        events.append(event)
    return events


def observe_transcript(path: Path, ledger: dict) -> list[dict]:
    """Read one transcript and emit only corroborated Claude lifecycle events."""
    try:
        records = [
            json.loads(line) for line in Path(path).read_text().splitlines() if line
        ]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not all(isinstance(record, dict) for record in records) or not isinstance(
        ledger, dict
    ):
        return []
    spawn = _background_spawn(records, ledger)
    if spawn:
        return spawn
    for record in records:
        spawn = _historical_interactive_spawn(record, ledger)
        if spawn:
            return spawn
    abort = _abort_events(records, ledger)
    if abort:
        return abort
    return _peer_events(records, ledger) + _terminal_events(records, ledger)
