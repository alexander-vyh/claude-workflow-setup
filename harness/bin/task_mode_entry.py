#!/usr/bin/env python3
"""
Claude Code PreToolUse hook — task-mode entry detector.

Watches for bd claim operations. When the agent claims a beads task, writes
session_mode.json to the thread dir so the stop hook can switch to queue-drain
gating for this session.

First-claim-wins: the first bd claim in a session fixes the repo_cwd. Subsequent
claims in the same session do not overwrite the existing record. This ensures the
stop hook always runs bd ready in the original project directory.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from would_block_stop import thread_dir_for_session, harness_home

HARNESS_ROOT = harness_home()

_CLAIM_PATTERNS = [
    re.compile(r'\bbd\s+update\b.*\s--claim\b'),
    re.compile(r'\bbd\s+update\b.*\s-s\s+in_progress\b'),
    re.compile(r'\bbd\s+update\b.*\s--status\s+in_progress\b'),
    re.compile(r'\bbd\s+ready\b.*\s--claim\b'),
]

_ISSUE_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


def _is_claim_command(command: str) -> bool:
    return any(p.search(command) for p in _CLAIM_PATTERNS)


def _is_issue_id(value) -> bool:
    return isinstance(value, str) and _ISSUE_ID_RE.fullmatch(value) is not None


def _extract_task_id(command: str) -> Optional[str]:
    """Extract the beads task ID from 'bd update <id> --claim'."""
    m = re.search(r'\bbd\s+update\s+(\S+)', command)
    if not m:
        return None
    task_id = m.group(1)
    # Molecule steps use dotted suffixes (for example escapement-858.3).
    return task_id if _is_issue_id(task_id) else None


_MAX_PARENT_LOOKUPS = 20  # cycle / runaway-chain backstop so the hook never hangs


class _ParentWalkError(Exception):
    """A bd response that cannot safely advance the molecule root walk."""


def _run_bd_show(issue_id: str) -> dict:
    """Return one decoded ``bd show`` issue or raise a categorized walk error."""
    try:
        result = subprocess.run(
            ["bd", "show", issue_id, "--json"],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise _ParentWalkError(f"lookup failed for {issue_id}") from exc
    if result.returncode != 0:
        raise _ParentWalkError(
            f"lookup failed for {issue_id} (bd exit {result.returncode})"
        )
    try:
        stdout = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _ParentWalkError(f"bd show {issue_id} output is not valid UTF-8") from exc
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _ParentWalkError(f"invalid JSON from bd show {issue_id}") from exc
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise _ParentWalkError(f"malformed issue record from bd show {issue_id}")
    return data[0]


def _decode_parent(data: dict, expected_issue_id: str) -> Optional[str]:
    """Validate one issue record and return its single parent, if any."""
    issue_id = data.get("id")
    if not _is_issue_id(issue_id) or issue_id != expected_issue_id:
        raise _ParentWalkError(f"malformed issue ID from bd show {expected_issue_id}")

    parent_fields = [data[key] for key in ("parent_id", "parent") if key in data]
    if not parent_fields:
        return None
    for parent in parent_fields:
        if parent is not None and not _is_issue_id(parent):
            raise _ParentWalkError(f"malformed parent for {issue_id}")
    parents = {parent for parent in parent_fields if parent is not None}
    if len(parents) > 1:
        raise _ParentWalkError(f"malformed parent for {issue_id}: conflicting IDs")
    return next(iter(parents), None)


def _diagnose_parent_walk(reason: str, safe_scope: str) -> None:
    """Emit the walk's single observable degradation signal."""
    print(
        f"task_mode_entry: parent walk {reason}; using safe scope {safe_scope}",
        file=sys.stderr,
    )


def _lookup_parent_id(task_id: str, run_show=None) -> Optional[str]:
    """Walk the parent chain to the molecule ROOT (bead 858.3, closes FN-1).

    Returns the TOPMOST ancestor id (the bead with no parent), or None when
    task_id has no parent. Reading only ONE level scoped `bd ready --parent` to
    the immediate sub-epic and missed ready siblings under sibling sub-epics —
    a premature stop. `bd ready --parent <root>` is transitive, so the root
    covers the whole molecule.

    `run_show(id) -> dict|None` is injectable for tests; production runs
    `bd show <id> --json`. Capped at _MAX_PARENT_LOOKUPS with a seen-set so
    a parent cycle terminates instead of hanging the Stop hook. A malformed or
    failed lookup falls back to the last issue that was actually decoded, never
    an unverified parent ID. Beads exposes one parent field here; a multi-parent
    DAG therefore follows only the single path selected by `bd show`.
    """
    if run_show is None:
        run_show = _run_bd_show

    current = task_id
    # The parsed claim ID is the initial safe scope if the first lookup fails.
    last_decoded = task_id
    seen: set[str] = set()
    for lookup_index in range(_MAX_PARENT_LOOKUPS):
        try:
            data = run_show(current)
            if not isinstance(data, dict):
                raise _ParentWalkError(f"lookup failed for {current}")
            parent_id = _decode_parent(data, current)
        except _ParentWalkError as exc:
            _diagnose_parent_walk(str(exc), last_decoded)
            return last_decoded

        last_decoded = current
        seen.add(current)
        if parent_id is None:
            # Preserve the established standalone-task representation.
            return None if current == task_id and lookup_index == 0 else current
        if parent_id in seen:
            _diagnose_parent_walk(
                f"cycle detected at {parent_id}", last_decoded
            )
            return last_decoded
        if lookup_index == _MAX_PARENT_LOOKUPS - 1:
            _diagnose_parent_walk(
                f"capped at {_MAX_PARENT_LOOKUPS} lookups", last_decoded
            )
            return last_decoded
        current = parent_id

    raise AssertionError("parent walk exhausted without returning")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    tool_input = payload.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not command or not _is_claim_command(command):
        return 0

    session_id = payload.get("session_id") or ""
    thread_dir = thread_dir_for_session(session_id, HARNESS_ROOT)
    thread_dir.mkdir(parents=True, exist_ok=True)
    mode_file = thread_dir / "session_mode.json"

    # First-claim-wins: never overwrite an existing mode record.
    if mode_file.exists():
        return 0

    task_id = _extract_task_id(command)
    # e9v.11 root cause: a claim with no parseable task id (e.g. `bd ready --claim`)
    # cannot be scoped. Writing a scopeless task-mode record makes the Stop gate run
    # `bd ready` unscoped = the whole-repo backlog, trapping a finished session. No
    # scope -> do not enter task mode; the contract gate still covers the session.
    if task_id is None:
        return 0
    parent_id = _lookup_parent_id(task_id)

    try:
        mode_file.write_text(json.dumps({
            "mode": "task",
            "repo_cwd": os.getcwd(),
            "task_id": task_id,
            "parent_id": parent_id,
            "entered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": session_id,
        }))
    except OSError:
        pass  # Silent fail: don't block the tool call

    return 0


if __name__ == "__main__":
    sys.exit(main())
