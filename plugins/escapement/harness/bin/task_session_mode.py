#!/usr/bin/env python3
"""Trusted exact-session task-mode loading shared by execution adapters."""

from __future__ import annotations

import json
import pathlib
import re
import shlex
from collections.abc import Mapping

from trusted_json import mutate_trusted_atomic
from trusted_source import is_trusted_file


def is_issue_id(value: object) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value)
    )


def extract_exact_claim_task_id(command: str) -> str | None:
    """Return the scope from an exact unchained ``bd update <id> --claim``."""
    if "\n" in command or "\r" in command:
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    if (
        len(tokens) != 4
        or pathlib.Path(tokens[0]).name != "bd"
        or tokens[1] != "update"
        or tokens[3] != "--claim"
        or not is_issue_id(tokens[2])
    ):
        return None
    return tokens[2]


def _valid_task_context(value: object, expected_session_id: str) -> bool:
    if not isinstance(value, dict) or value.get("mode") != "task":
        return False
    if value.get("session_id") != expected_session_id:
        return False
    repo_cwd = value.get("repo_cwd")
    root_id = value.get("parent_id") or value.get("task_id")
    return (
        isinstance(repo_cwd, str)
        and bool(repo_cwd)
        and isinstance(root_id, str)
        and bool(root_id)
    )


def load_task_context(
    path: pathlib.Path,
    expected_session_id: str,
) -> dict | None:
    """Load one trusted task binding for the exact host session."""
    path = pathlib.Path(path)
    if path.is_symlink() or not is_trusted_file(path):
        return None
    try:
        context = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return context if _valid_task_context(context, expected_session_id) else None


def record_task_context_first_claim(path: pathlib.Path, proposed: Mapping) -> dict:
    """Atomically persist one immutable first-claim scope across hook processes."""
    candidate = dict(proposed)
    session_id = candidate.get("session_id")
    if not isinstance(session_id, str) or not _valid_task_context(candidate, session_id):
        raise ValueError("task context candidate is invalid")
    return mutate_trusted_atomic(
        path,
        lambda: candidate,
        lambda current: current,
        lambda value: _valid_task_context(value, session_id),
    )


def _valid_task_mode_incident(value: object, expected_session_id: str) -> bool:
    return (
        isinstance(value, dict)
        and value.get("version") == 1
        and value.get("parent_session_id") == expected_session_id
        and value.get("reason") == "task_mode_persistence_failed"
        and isinstance(value.get("recorded_at"), str)
        and bool(value["recorded_at"])
    )


def record_task_mode_incident(
    path: pathlib.Path, *, parent_session_id: str, recorded_at: str
) -> dict:
    incident = {
        "version": 1,
        "parent_session_id": parent_session_id,
        "reason": "task_mode_persistence_failed",
        "recorded_at": recorded_at,
    }
    return mutate_trusted_atomic(
        path,
        lambda: incident,
        lambda current: current,
        lambda value: _valid_task_mode_incident(value, parent_session_id),
    )


def load_task_mode_incident(
    path: pathlib.Path, expected_session_id: str
) -> dict | None:
    path = pathlib.Path(path)
    if path.is_symlink() or not is_trusted_file(path):
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if _valid_task_mode_incident(value, expected_session_id) else None


def transcript_has_successful_exact_claim(
    path: pathlib.Path | str | None, expected_session_id: str
) -> bool:
    """Recover claim intent from a trusted Claude parent transcript."""
    if not path:
        return False
    transcript = pathlib.Path(path)
    if transcript.is_symlink() or not is_trusted_file(transcript):
        return False
    claim_ids: set[str] = set()
    successful_results: set[str] = set()
    try:
        with transcript.open(encoding="utf-8") as lines:
            for line in lines:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if entry.get("sessionId") != expected_session_id:
                    continue
                message = entry.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "tool_use" and item.get("name") == "Bash":
                        tool_input = item.get("input")
                        command = (
                            tool_input.get("command")
                            if isinstance(tool_input, dict)
                            else None
                        )
                        tool_id = item.get("id")
                        if (
                            isinstance(command, str)
                            and extract_exact_claim_task_id(command) is not None
                            and isinstance(tool_id, str)
                            and tool_id
                        ):
                            claim_ids.add(tool_id)
                    elif (
                        item.get("type") == "tool_result"
                        and item.get("is_error") is False
                        and isinstance(item.get("tool_use_id"), str)
                    ):
                        successful_results.add(item["tool_use_id"])
    except (OSError, UnicodeError):
        return False
    return bool(claim_ids & successful_results)


def session_repo_cwd(thread_dir: pathlib.Path, session_id: str) -> pathlib.Path | None:
    """Resolve the existing task-mode repository binding for daemon Beads calls.

    Moved here from execution_supervisor when the delegated-execution ledger was
    removed: reading session_mode.json is this module's contract, and the waker
    still needs the binding to run Beads in the right repository.
    """
    mode_path = pathlib.Path(thread_dir) / "session_mode.json"
    if mode_path.is_symlink() or not is_trusted_file(mode_path):
        return None
    try:
        mode = json.loads(mode_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(mode, dict) or mode.get("mode") != "task":
        return None
    if mode.get("session_id") != session_id:
        return None
    raw_cwd = mode.get("repo_cwd")
    if not isinstance(raw_cwd, str) or not raw_cwd:
        return None
    repo_cwd = pathlib.Path(raw_cwd)
    if not repo_cwd.is_absolute() or not repo_cwd.is_dir():
        return None
    return repo_cwd.resolve()
