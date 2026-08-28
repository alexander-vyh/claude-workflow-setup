#!/usr/bin/env python3
"""Host-record evidence for the isolated delegation canary."""

from __future__ import annotations

import json
import re
from pathlib import Path

DEPENDENCY_RE = re.compile(r"\bDEPENDENCY-[A-Za-z0-9-]+\b")


class CanaryFailure(Exception):
    """One externally visible canary invariant was not proven."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def plugin_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def parse_records(stdout: str) -> list[dict]:
    records = []
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def tool_content(record: dict) -> dict | None:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list) or len(content) != 1:
        return None
    item = content[0]
    return item if isinstance(item, dict) else None


def dispatches(records: list[dict]) -> list[tuple[int, dict, dict]]:
    found = []
    for index, record in enumerate(records):
        item = tool_content(record)
        tool_input = item.get("input") if isinstance(item, dict) else None
        if (
            isinstance(item, dict)
            and item.get("type") == "tool_use"
            and item.get("name") == "Agent"
            and isinstance(item.get("id"), str)
            and isinstance(tool_input, dict)
            and tool_input.get("run_in_background") is True
        ):
            found.append((index, record, item))
    return found


def verify_dispatch_hook_responses(records: list[dict]) -> None:
    """Require one successful candidate PreToolUse registration per dispatch."""
    observed = dispatches(records)
    if len(observed) != 4:
        raise CanaryFailure("managed_completion_unresolved")
    expected_output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "dispatch_registered",
        }
    }
    for offset, (dispatch_index, dispatch_record, _item) in enumerate(observed):
        boundary = observed[offset + 1][0] if offset + 1 < len(observed) else len(records)
        matches = []
        for record in records[dispatch_index + 1 : boundary]:
            stdout = record.get("stdout") if isinstance(record, dict) else None
            try:
                parsed = json.loads(stdout) if isinstance(stdout, str) else None
            except json.JSONDecodeError:
                parsed = None
            if parsed != expected_output:
                continue
            if (
                record.get("type") == "system"
                and record.get("subtype") == "hook_response"
                and record.get("hook_event") == "PreToolUse"
                and record.get("hook_name") == "PreToolUse:Agent"
                and record.get("session_id") == dispatch_record.get("session_id")
                and record.get("output") == stdout
                and record.get("stderr") == ""
                and record.get("exit_code") == 0
                and record.get("outcome") == "success"
            ):
                matches.append(record)
        if len(matches) != 1:
            raise CanaryFailure("managed_completion_unresolved")


def session_and_version(records: list[dict]) -> tuple[str, str]:
    init = next(
        (
            record for record in records
            if record.get("type") == "system" and record.get("subtype") == "init"
        ),
        None,
    )
    if not isinstance(init, dict):
        raise CanaryFailure("host_capability_unresolved")
    session_id = init.get("session_id")
    version = init.get("claude_code_version")
    if not isinstance(session_id, str) or not isinstance(version, str):
        raise CanaryFailure("host_capability_unresolved")
    return session_id, version


def root_result(records: list[dict]) -> str:
    results = [
        record.get("result") for record in records
        if record.get("type") == "result"
        and record.get("subtype") == "success"
        and isinstance(record.get("result"), str)
    ]
    return results[-1] if results else ""


def verify_candidate_plugin(records: list[dict], candidate_root: Path) -> None:
    expected = [
        {
            "name": "escapement",
            "path": str(candidate_root),
            "source": "escapement@inline",
        }
    ]
    inits = [
        record for record in records
        if record.get("type") == "system" and record.get("subtype") == "init"
    ]
    if not inits or any(
        "plugin_errors" in record
        or record.get("plugins") != expected
        for record in inits
    ):
        raise CanaryFailure("host_capability_unresolved")


def spawn_witness(records: list[dict], tool_id: str) -> tuple[str, int, int] | None:
    starts = [
        (index, record) for index, record in enumerate(records)
        if record.get("type") == "system"
        and record.get("subtype") == "task_started"
        and record.get("tool_use_id") == tool_id
        and isinstance(record.get("task_id"), str)
        and record.get("is_backgrounded") is True
        and record.get("task_type") == "local_agent"
    ]
    terminals = [
        (index, record) for index, record in enumerate(records)
        if record.get("type") == "system"
        and record.get("subtype") == "task_notification"
        and record.get("tool_use_id") == tool_id
        and record.get("status") == "completed"
    ]
    if len(starts) != 1 or len(terminals) != 1:
        return None
    child_id = starts[0][1]["task_id"]
    if terminals[0][1].get("task_id") != child_id:
        return None
    return child_id, starts[0][0], terminals[0][0]


def verify_unmanaged(
    records: list[dict], harness: Path, version: str, candidate_root: Path
) -> dict:
    _session, stream_version = session_and_version(records)
    verify_candidate_plugin(records, candidate_root)
    found = dispatches(records)
    if stream_version != version or not found:
        raise CanaryFailure("native_agent_first_attempt_failed")
    if spawn_witness(records, found[0][2]["id"]) is None:
        raise CanaryFailure("native_agent_first_attempt_failed")
    if "UNMANAGED_OK" not in root_result(records):
        raise CanaryFailure("native_agent_first_attempt_failed")
    if any(harness.rglob("executions.json")):
        raise CanaryFailure("unmanaged_state_created")
    return {"first_attempt": True, "escapement_state_created": False}


def verify_overlap(records: list[dict], terminal: list[dict]) -> None:
    intervals = []
    for execution in terminal:
        witness = spawn_witness(records, execution["dispatch_tool_use_id"])
        if witness is None:
            raise CanaryFailure("managed_completion_unresolved")
        intervals.append((witness[1], witness[2]))
    if len(intervals) != 3 or max(start for start, _ in intervals) >= min(
        end for _, end in intervals
    ):
        raise CanaryFailure("children_do_not_overlap")


def terminal_record(records: list[dict], execution: dict) -> tuple[int, dict] | None:
    matches = [
        (index, record) for index, record in enumerate(records)
        if record.get("type") == "system"
        and record.get("subtype") == "task_notification"
        and record.get("status") == "completed"
        and record.get("tool_use_id") == execution.get("dispatch_tool_use_id")
        and record.get("task_id") == execution.get("native_child_id")
        and record.get("uuid") == execution.get("terminal_event_id")
    ]
    return matches[0] if len(matches) == 1 else None


def _peer_acknowledgement(item: dict) -> dict | None:
    expected_keys = {"type", "tool_use_id", "content"}
    if item.get("is_error", False) is not False:
        return None
    if set(item) not in (expected_keys, expected_keys | {"is_error"}):
        return None
    content = item.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    text_item = content[0]
    if (
        not isinstance(text_item, dict)
        or set(text_item) != {"type", "text"}
        or text_item.get("type") != "text"
        or not isinstance(text_item.get("text"), str)
    ):
        return None
    try:
        acknowledgement = json.loads(text_item["text"])
    except json.JSONDecodeError:
        return None
    if not isinstance(acknowledgement, dict) or set(acknowledgement) != {
        "success", "message", "pin"
    }:
        return None
    pin = acknowledgement.get("pin")
    if (
        acknowledgement.get("success") is not True
        or not isinstance(acknowledgement.get("message"), str)
        or not acknowledgement["message"]
        or not isinstance(pin, dict)
        or set(pin) != {"id", "name", "ref"}
        or not isinstance(pin.get("ref"), str)
        or not pin["ref"]
    ):
        return None
    return acknowledgement


def verify_peer_dependency(records: list[dict], terminal: list[dict]) -> None:
    by_name = {item["agent_name"]: item for item in terminal}
    sender = by_name.get("canary-child-1")
    recipient = by_name.get("canary-child-2")
    if sender is None or recipient is None:
        raise CanaryFailure("peer_dependency_unproven")
    terminal_match = terminal_record(records, recipient)
    if terminal_match is None:
        raise CanaryFailure("peer_dependency_unproven")
    terminal_index, terminal_event = terminal_match
    summary = terminal_event.get("summary", "")
    conclusion = root_result(records)
    requests: dict[str, tuple[int, set[str]]] = {}
    for index, record in enumerate(records):
        if record.get("parent_tool_use_id") != sender["dispatch_tool_use_id"]:
            continue
        item = tool_content(record)
        if not isinstance(item, dict):
            continue
        if item.get("type") == "tool_use" and item.get("name") == "SendMessage":
            tool_input = item.get("input")
            if not isinstance(tool_input, dict):
                continue
            body = tool_input.get("message")
            tokens = DEPENDENCY_RE.findall(body) if isinstance(body, str) else []
            if (
                tool_input.get("recipient") == recipient["agent_name"]
                and isinstance(item.get("id"), str)
                and len(tokens) == 1
            ):
                requests[item["id"]] = (index, set(tokens))
            continue
        tool_id = item.get("tool_use_id")
        request = requests.get(tool_id)
        if item.get("type") != "tool_result" or request is None:
            continue
        acknowledgement = _peer_acknowledgement(item)
        pin = acknowledgement.get("pin") if isinstance(acknowledgement, dict) else None
        tokens = request[1]
        if (
            request[0] < index < terminal_index
            and isinstance(acknowledgement, dict)
            and acknowledgement.get("success") is True
            and isinstance(pin, dict)
            and pin.get("id") == recipient["native_child_id"]
            and pin.get("name") == recipient["agent_name"]
            and any(token in summary and token in conclusion for token in tokens)
        ):
            return
    raise CanaryFailure("peer_dependency_unproven")
