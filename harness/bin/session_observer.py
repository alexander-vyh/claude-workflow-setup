#!/usr/bin/env python3
"""Read-only native transcript observer for completed Claude and Codex turns."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
from typing import Optional

import trusted_source


@dataclasses.dataclass(frozen=True)
class Candidate:
    host: str
    session_id: str
    terminal_id: str
    transcript: pathlib.Path
    cwd: str
    request: str
    response: str
    completed_at: float

    @property
    def delivery_key(self) -> str:
        return f"{self.host}:{self.session_id}:{self.terminal_id}"


def _rows(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _payload(row: dict) -> dict:
    value = row.get("payload")
    return value if isinstance(value, dict) else {}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (
                "text", "Text", "input_text", "output_text",
            ):
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
    return "\n".join(parts)


def _epoch(value, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp()
        except ValueError:
            pass
    return fallback


def _human_claude(row: dict) -> bool:
    if (
        row.get("isSidechain") or row.get("isMeta") is True
        or "toolUseResult" in row or row.get("sourceToolAssistantUUID")
    ):
        return False
    origin = row.get("origin")
    if isinstance(origin, dict) and (
        origin.get("kind") != "human"
        or origin.get("promptSource") not in ("typed", "queued")
    ):
        return False
    message = row.get("message", row)
    return isinstance(message, dict) and message.get("role") == "user"


def _claude_user_activity(row: dict) -> bool:
    """Any later main-chain user-shaped activity supersedes an old terminal."""
    message = row.get("message", row)
    return (
        not row.get("isSidechain")
        and isinstance(message, dict)
        and message.get("role") == "user"
    )


def _claude_checkout_cwd(session_id: str) -> tuple[bool, Optional[str]]:
    harness = pathlib.Path(
        os.environ.get("CONTINUATION_HARNESS_HOME", pathlib.Path.home() / ".claude" / "harness")
    )
    checkout = harness / "threads" / session_id / "checkout.json"
    if not os.path.lexists(checkout):
        return False, None
    if not trusted_source.is_trusted_file(checkout):
        return True, None
    try:
        value = json.loads(checkout.read_text())
    except (OSError, json.JSONDecodeError):
        return True, None
    if not isinstance(value, dict):
        return True, None
    root = value.get("worktree_root")
    if value.get("session_id") != session_id or not isinstance(root, str):
        return True, None
    return True, root if pathlib.Path(root).is_absolute() else None


def _not_scratchpad(value: str) -> bool:
    return "scratchpad" not in pathlib.Path(value).parts


def parse_claude(path: pathlib.Path) -> Optional[Candidate]:
    rows = _rows(path)
    if not rows:
        return None
    by_uuid = {
        row.get("uuid"): (index, row)
        for index, row in enumerate(rows)
        if isinstance(row.get("uuid"), str)
    }
    summaries = [
        (index, row) for index, row in enumerate(rows)
        if row.get("type") == "system"
        and row.get("subtype") == "stop_hook_summary"
        and row.get("preventedContinuation") is False
    ]
    for summary_index, summary in reversed(summaries):
        parent_id = summary.get("parentUuid")
        parent = by_uuid.get(parent_id)
        if not parent:
            continue
        assistant_index, assistant = parent
        message = assistant.get("message", {})
        response = _text(message.get("content")) if isinstance(message, dict) else ""
        if (
            assistant.get("isSidechain")
            or not isinstance(message, dict)
            or message.get("role") != "assistant"
            or message.get("stop_reason") != "end_turn"
            or not response.strip()
        ):
            continue
        if any(_claude_user_activity(row) for row in rows[summary_index + 1:]):
            return None
        request = ""
        cwd = ""
        cursor: Optional[dict] = assistant
        visited: set[str] = set()
        while isinstance(cursor, dict):
            value = cursor.get("cwd")
            if (
                not cwd and isinstance(value, str) and pathlib.Path(value).is_absolute()
                and _not_scratchpad(value)
            ):
                cwd = value
            if not request and _human_claude(cursor):
                request = _text(cursor.get("message", cursor).get("content"))
            parent_uuid = cursor.get("parentUuid")
            if not isinstance(parent_uuid, str) or parent_uuid in visited:
                break
            visited.add(parent_uuid)
            parent_record = by_uuid.get(parent_uuid)
            cursor = parent_record[1] if parent_record else None
        session_id = path.stem
        checkout_present, checkout_cwd = _claude_checkout_cwd(session_id)
        if checkout_present and checkout_cwd is None:
            return None
        cwd = checkout_cwd or cwd
        try:
            fallback = path.stat().st_mtime
        except OSError:
            fallback = 0.0
        return Candidate(
            "claude", session_id, str(parent_id), path, cwd, request, response,
            _epoch(summary.get("timestamp"), fallback),
        )
    return None


def parse_codex(path: pathlib.Path) -> Optional[Candidate]:
    rows = _rows(path)
    if not rows:
        return None
    settings_ids = {
        _payload(row).get("thread_id")
        for row in rows
        if row.get("type") == "event_msg"
        and _payload(row).get("type") == "thread_settings_applied"
    }
    metas = [_payload(row) for row in rows if row.get("type") == "session_meta" and _payload(row)]
    meta = next((item for item in metas if item.get("id") in settings_ids), metas[0] if metas else {})
    if meta.get("thread_source") != "user":
        return None
    session_id = meta.get("id")
    if not isinstance(session_id, str) or not session_id:
        return None

    starts: dict[str, int] = {}
    completed: dict[str, tuple[int, dict]] = {}
    aborted: set[str] = set()
    finals: dict[str, tuple[int, str]] = {}
    for index, row in enumerate(rows):
        payload = _payload(row)
        if row.get("type") != "event_msg" or not isinstance(payload, dict):
            continue
        kind, turn_id = payload.get("type"), payload.get("turn_id")
        if kind == "task_started" and isinstance(turn_id, str):
            starts[turn_id] = index
        elif kind == "task_complete" and isinstance(turn_id, str):
            completed[turn_id] = (index, payload)
        elif kind == "turn_aborted" and isinstance(turn_id, str):
            aborted.add(turn_id)
        elif kind == "item_completed" and isinstance(turn_id, str):
            item = payload.get("item", {})
            if isinstance(item, dict) and item.get("type") == "AgentMessage" and item.get("phase") == "final_answer":
                value = _text(item.get("content"))
                if value:
                    finals[turn_id] = (index, value)
    if not starts:
        return None
    turn_id = max(starts, key=starts.get)
    if turn_id in aborted or turn_id not in completed or turn_id not in finals:
        return None
    complete_index, complete = completed[turn_id]
    final_index, response = finals[turn_id]
    if final_index > complete_index:
        return None
    request = ""
    cwd = meta.get("cwd") if isinstance(meta.get("cwd"), str) else ""
    for row in rows[starts[turn_id]:complete_index]:
        payload = _payload(row)
        if row.get("type") == "turn_context" and isinstance(payload.get("cwd"), str):
            cwd = payload["cwd"]
        if row.get("type") == "response_item" and payload.get("type") == "message" \
                and payload.get("role") == "user":
            request = _text(payload.get("content"))
    try:
        fallback = path.stat().st_mtime
    except OSError:
        fallback = 0.0
    return Candidate(
        "codex", session_id, turn_id, path, cwd, request, response,
        _epoch(complete.get("completed_at"), fallback),
    )


def discover(
    claude_root: pathlib.Path, codex_root: pathlib.Path, *, modified_since: float,
) -> list[Candidate]:
    found: list[Candidate] = []
    for root, parser in ((claude_root, parse_claude), (codex_root, parse_codex)):
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                if path.stat().st_mtime < modified_since:
                    continue
            except OSError:
                continue
            try:
                candidate = parser(path)
            except (OSError, ValueError, TypeError, AttributeError):
                candidate = None
            if candidate is not None:
                found.append(candidate)
    return found


def contains_marker(path: pathlib.Path, marker: str) -> bool:
    try:
        return marker in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
