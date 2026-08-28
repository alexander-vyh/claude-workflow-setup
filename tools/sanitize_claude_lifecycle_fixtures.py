#!/usr/bin/env python3
"""Copy a reviewed structural allowlist from raw Claude JSONL captures.

This tool deliberately knows nothing about the lifecycle adapter or its event
constants.  Its only authority is the reviewed source-line and JSON-pointer
allowlist below.  Sensitive prompts, prose results, filesystem paths, model
metadata, and message bodies are omitted from the generated fixture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SANITIZER_VERSION = "1"
SANITIZER_COMMAND = (
    "python3 tools/sanitize_claude_lifecycle_fixtures.py "
    "--terminal-stream \"$XNCX_TERMINAL_STREAM\" "
    "--no-spawn-stream \"$XNCX_NO_SPAWN_STREAM\" "
    "--historical-transcript \"$XNCX_HISTORICAL_TRANSCRIPT\" "
    "--fixture harness/tests/fixtures/claude-agent-lifecycle-2.1.247.jsonl "
    "--provenance "
    "harness/tests/fixtures/claude-agent-lifecycle-2.1.247.provenance.json"
)
POST_TOOL_POINTERS = (
    "/hook_event_name",
    "/session_id",
    "/tool_name",
    "/tool_use_id",
    "/tool_input/name",
    "/tool_input/run_in_background",
    "/tool_input/subagent_type",
    "/tool_response/status",
    "/tool_response/isAsync",
    "/tool_response/agentId",
)
ABORT_COMMAND = (
    "python3 tools/sanitize_claude_lifecycle_fixtures.py "
    "--abort-stream "
    "harness/tests/fixtures/captures/claude-agent-abort-2.1.248.raw.jsonl "
    "--abort-fixture harness/tests/fixtures/claude-agent-abort-2.1.248.jsonl "
    "--abort-provenance "
    "harness/tests/fixtures/claude-agent-abort-2.1.248.provenance.json"
)


@dataclass(frozen=True)
class Witness:
    fixture_id: str
    event_kind: str
    source_name: str
    line: int
    capture_timestamp: str
    pointers: tuple[str, ...]


COMMON = ("/type", "/uuid", "/session_id")
TOOL_USE = COMMON + (
    "/timestamp",
    "/message/role",
    "/message/content/0/type",
    "/message/content/0/id",
    "/message/content/0/name",
    "/message/content/0/input/name",
    "/message/content/0/input/subagent_type",
    "/message/content/0/input/run_in_background",
)
WITNESSES = (
    Witness(
        "background_agent_tool_use",
        "dispatch",
        "terminal_stream",
        4,
        "2026-08-27T22:10:07.587Z",
        TOOL_USE,
    ),
    Witness(
        "background_task_started",
        "spawn_started",
        "terminal_stream",
        6,
        "2026-08-27T22:10:07.611Z",
        COMMON
        + (
            "/subtype",
            "/task_id",
            "/tool_use_id",
            "/subagent_type",
            "/is_backgrounded",
            "/spawn_depth",
            "/task_type",
        ),
    ),
    Witness(
        "background_async_result",
        "spawn_result",
        "terminal_stream",
        7,
        "2026-08-27T22:10:07.611Z",
        COMMON
        + (
            "/timestamp",
            "/message/role",
            "/message/content/0/type",
            "/message/content/0/tool_use_id",
            "/tool_use_result/isAsync",
            "/tool_use_result/status",
            "/tool_use_result/agentId",
        ),
    ),
    Witness(
        "background_task_terminal",
        "terminal",
        "terminal_stream",
        18,
        "2026-08-27T22:10:12.304Z",
        COMMON
        + (
            "/subtype",
            "/task_id",
            "/tool_use_id",
            "/status",
        ),
    ),
    Witness(
        "background_peer_activity",
        "peer_activity",
        "terminal_stream",
        27,
        "2026-08-27T22:10:16.531Z",
        COMMON
        + (
            "/subtype",
            "/origin/kind",
            "/origin/from",
            "/origin/senderTaskId",
            "/origin/name",
        ),
    ),
    Witness(
        "no_spawn_agent_tool_use",
        "dispatch",
        "no_spawn_stream",
        4,
        "2026-08-27T22:10:55.409Z",
        TOOL_USE,
    ),
    Witness(
        "no_spawn_error_result",
        "dispatch_aborted",
        "no_spawn_stream",
        5,
        "2026-08-27T22:10:55.423Z",
        COMMON
        + (
            "/timestamp",
            "/message/role",
            "/message/content/0/type",
            "/message/content/0/is_error",
            "/message/content/0/tool_use_id",
        ),
    ),
    Witness(
        "interactive_spawn_result",
        "interactive_spawn",
        "historical_transcript",
        233,
        "2026-08-27T18:00:22.945Z",
        (
            "/type",
            "/uuid",
            "/timestamp",
            "/session_id",
            "/version",
            "/message/role",
            "/message/content/0/type",
            "/message/content/0/tool_use_id",
            "/toolUseResult/status",
            "/toolUseResult/teammate_id",
            "/toolUseResult/agent_id",
            "/toolUseResult/agent_type",
            "/toolUseResult/name",
        ),
    ),
    Witness(
        "historical_idle_notification",
        "idle_negative",
        "historical_transcript",
        293,
        "2026-08-27T18:10:00.068Z",
        (
            "/type",
            "/uuid",
            "/timestamp",
            "/version",
            "/message/role",
            "/message/content",
        ),
    ),
    Witness(
        "historical_later_child_idle",
        "later_child_activity",
        "historical_transcript",
        454,
        "2026-08-27T18:13:16.741Z",
        (
            "/type",
            "/uuid",
            "/timestamp",
            "/version",
            "/message/role",
            "/message/content",
        ),
    ),
)
ABORT_WITNESSES = (
    Witness(
        "abort_agent_tool_use",
        "dispatch",
        "task3_live_canary_managed_stream",
        205,
        "2026-08-28T01:28:28.942Z",
        TOOL_USE,
    ),
    Witness(
        "abort_error_result",
        "dispatch_aborted",
        "task3_live_canary_managed_stream",
        214,
        "2026-08-28T01:28:29.070Z",
        COMMON
        + (
            "/timestamp",
            "/message/role",
            "/message/content/0/type",
            "/message/content/0/is_error",
            "/message/content/0/tool_use_id",
            "/message/content/0/content",
        ),
    ),
)


def _decode_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _lookup(value: Any, pointer: str) -> Any:
    current = value
    for part in _decode_pointer(pointer):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(pointer)
    return current


def _assign(target: dict[str, Any], pointer: str, value: Any) -> None:
    parts = _decode_pointer(pointer)
    current: Any = target
    for index, part in enumerate(parts):
        final = index == len(parts) - 1
        next_is_list = not final and parts[index + 1].isdigit()
        if isinstance(current, list):
            position = int(part)
            while len(current) <= position:
                current.append(None)
            if final:
                current[position] = value
            else:
                if current[position] is None:
                    current[position] = [] if next_is_list else {}
                current = current[position]
        else:
            if final:
                current[part] = value
            else:
                current = current.setdefault(part, [] if next_is_list else {})


def _read_lines(path: Path) -> list[bytes]:
    return path.read_bytes().splitlines(keepends=True)


def _record_and_digest(lines: list[bytes], line_number: int) -> tuple[dict, str]:
    raw = lines[line_number - 1]
    if not raw.endswith(b"\n"):
        raw += b"\n"
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise ValueError(f"line {line_number} is not a JSON object")
    return record, hashlib.sha256(raw).hexdigest()


def build(source_paths: dict[str, Path]) -> tuple[list[dict], dict]:
    sources = {name: _read_lines(path) for name, path in source_paths.items()}
    fixtures: list[dict] = []
    provenance_records: list[dict] = []
    for witness in WITNESSES:
        raw, digest = _record_and_digest(sources[witness.source_name], witness.line)
        retained: dict[str, Any] = {}
        for pointer in witness.pointers:
            _assign(retained, pointer, _lookup(raw, pointer))
        fixtures.append({"fixture_id": witness.fixture_id, "record": retained})
        provenance_records.append(
            {
                "fixture_id": witness.fixture_id,
                "event_kind": witness.event_kind,
                "source": {
                    "capture_id": witness.source_name,
                    "line": witness.line,
                    "capture_timestamp": witness.capture_timestamp,
                },
                "raw_record_sha256": digest,
                "retained_json_pointers": list(witness.pointers),
            }
        )
    provenance = {
        "schema_version": 1,
        "host": {"product": "Claude Code", "version": "2.1.247"},
        "raw_digest_mode": "sha256 of exact UTF-8 JSONL record bytes including LF",
        "sanitizer": {
            "name": Path(__file__).name,
            "version": SANITIZER_VERSION,
            "command": SANITIZER_COMMAND,
        },
        "records": provenance_records,
    }
    return fixtures, provenance


def build_post_tool(source_path: Path) -> tuple[dict, dict]:
    """Sanitize the one reviewed Claude 2.1.248 PostToolUse capture."""
    raw, digest = _record_and_digest(_read_lines(source_path), 1)
    retained: dict[str, Any] = {}
    for pointer in POST_TOOL_POINTERS:
        _assign(retained, pointer, _lookup(raw, pointer))
    provenance = {
        "schema_version": 1,
        "host": {"product": "Claude Code", "version": "2.1.248"},
        "raw_digest_mode": "sha256 of exact UTF-8 JSONL record bytes including LF",
        "raw_record_sha256": digest,
        "source": {"capture_id": "post_tool_stream", "line": 1},
        "retained_json_pointers": list(POST_TOOL_POINTERS),
        "sanitizer": {
            "name": Path(__file__).name,
            "version": SANITIZER_VERSION,
            "command": (
                "python3 tools/sanitize_claude_lifecycle_fixtures.py "
                "--post-tool-stream \"$XNCX_POST_TOOL_STREAM\" "
                "--post-tool-fixture "
                "harness/tests/fixtures/claude-post-tool-2.1.248.jsonl "
                "--post-tool-provenance "
                "harness/tests/fixtures/claude-post-tool-2.1.248.provenance.json"
            ),
        },
    }
    return retained, provenance


def build_abort(source_path: Path) -> tuple[list[dict], dict]:
    """Sanitize the reviewed abort records from the Task 3 live canary."""
    lines = _read_lines(source_path)
    fixtures = []
    records = []
    if len(lines) == 2:
        source_lines = (1, 2)
    elif len(lines) >= max(witness.line for witness in ABORT_WITNESSES):
        source_lines = tuple(witness.line for witness in ABORT_WITNESSES)
    else:
        raise ValueError("abort capture must be the exact two records or full stream")
    for witness, source_line in zip(ABORT_WITNESSES, source_lines, strict=True):
        raw, digest = _record_and_digest(lines, source_line)
        retained: dict[str, Any] = {}
        for pointer in witness.pointers:
            _assign(retained, pointer, _lookup(raw, pointer))
        fixtures.append({"fixture_id": witness.fixture_id, "record": retained})
        records.append(
            {
                "fixture_id": witness.fixture_id,
                "event_kind": witness.event_kind,
                "source": {
                    "capture_id": witness.source_name,
                    "line": witness.line,
                    "capture_timestamp": witness.capture_timestamp,
                },
                "raw_record_sha256": digest,
                "retained_json_pointers": list(witness.pointers),
            }
        )
    provenance = {
        "schema_version": 1,
        "host": {"product": "Claude Code", "version": "2.1.248"},
        "raw_digest_mode": "sha256 of exact UTF-8 JSONL record bytes including LF",
        "sanitizer": {
            "name": Path(__file__).name,
            "version": SANITIZER_VERSION,
            "command": ABORT_COMMAND,
        },
        "records": records,
    }
    return fixtures, provenance


def _fixture_text(fixtures: list[dict]) -> str:
    return "".join(json.dumps(item, sort_keys=True) + "\n" for item in fixtures)


def _provenance_text(provenance: dict) -> str:
    return json.dumps(provenance, indent=2, sort_keys=True) + "\n"


def _emit_pair(
    fixture_path: Path,
    provenance_path: Path,
    expected_fixture: str,
    expected_provenance: str,
    *,
    check: bool,
    label: str,
) -> None:
    if check:
        if fixture_path.read_text(encoding="utf-8") != expected_fixture:
            raise SystemExit(f"{label} fixture differs from reviewed raw capture allowlist")
        if provenance_path.read_text(encoding="utf-8") != expected_provenance:
            raise SystemExit(f"{label} provenance differs from reviewed raw capture allowlist")
        return
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(expected_fixture, encoding="utf-8")
    provenance_path.write_text(expected_provenance, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal-stream", type=Path)
    parser.add_argument("--no-spawn-stream", type=Path)
    parser.add_argument("--historical-transcript", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--post-tool-stream", type=Path)
    parser.add_argument("--post-tool-fixture", type=Path)
    parser.add_argument("--post-tool-provenance", type=Path)
    parser.add_argument("--abort-stream", type=Path)
    parser.add_argument("--abort-fixture", type=Path)
    parser.add_argument("--abort-provenance", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    lifecycle_args = (
        args.terminal_stream,
        args.no_spawn_stream,
        args.historical_transcript,
        args.fixture,
        args.provenance,
    )
    if any(lifecycle_args) and not all(lifecycle_args):
        raise SystemExit("all lifecycle stream, fixture, and provenance paths are required")
    if all(lifecycle_args):
        fixtures, provenance = build(
            {
                "terminal_stream": args.terminal_stream,
                "no_spawn_stream": args.no_spawn_stream,
                "historical_transcript": args.historical_transcript,
            }
        )
        expected_fixture = _fixture_text(fixtures)
        expected_provenance = _provenance_text(provenance)
        _emit_pair(
            args.fixture, args.provenance, expected_fixture, expected_provenance,
            check=args.check, label="lifecycle",
        )
    post_tool_args = (
        args.post_tool_stream,
        args.post_tool_fixture,
        args.post_tool_provenance,
    )
    if any(post_tool_args) and not all(post_tool_args):
        raise SystemExit("post-tool stream, fixture, and provenance must be supplied together")
    if all(post_tool_args):
        post_fixture, post_provenance = build_post_tool(args.post_tool_stream)
        expected_post_fixture = json.dumps(post_fixture, sort_keys=True) + "\n"
        expected_post_provenance = _provenance_text(post_provenance)
        _emit_pair(
            args.post_tool_fixture, args.post_tool_provenance,
            expected_post_fixture, expected_post_provenance,
            check=args.check, label="post-tool",
        )
    abort_args = (args.abort_stream, args.abort_fixture, args.abort_provenance)
    if any(abort_args) and not all(abort_args):
        raise SystemExit("abort stream, fixture, and provenance must be supplied together")
    if all(abort_args):
        abort_fixtures, abort_provenance = build_abort(args.abort_stream)
        expected_abort_fixture = _fixture_text(abort_fixtures)
        expected_abort_provenance = _provenance_text(abort_provenance)
        _emit_pair(
            args.abort_fixture, args.abort_provenance,
            expected_abort_fixture, expected_abort_provenance,
            check=args.check, label="abort",
        )
    if not any((all(lifecycle_args), all(post_tool_args), all(abort_args))):
        raise SystemExit("at least one complete capture group is required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
