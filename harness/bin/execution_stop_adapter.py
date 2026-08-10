#!/usr/bin/env python3
"""Filesystem/Beads adapter for the pure delegated Stop policy."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib

from beads_task_state import task_root_status
import execution_store
import schedule_store
import supervisor_health
from thread_identity import supervisor_health_path
from trusted_source import is_trusted_file
from would_block_stop import execution_stop_decision


def _load_task_context(
    path: pathlib.Path,
    expected_session_id: str,
) -> dict | None:
    """Load one trusted task binding for the exact hook session."""
    if path.is_symlink() or not is_trusted_file(path):
        return None
    try:
        context = json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(context, dict) or context.get("mode") != "task":
        return None
    if context.get("session_id") != expected_session_id:
        return None
    repo_cwd = context.get("repo_cwd")
    root_id = context.get("parent_id") or context.get("task_id")
    if (
        not isinstance(repo_cwd, str)
        or not repo_cwd
        or not isinstance(root_id, str)
        or not root_id
    ):
        return None
    return context


def decide_task_mode(
    session_id: str,
    thread_dir: pathlib.Path,
    now: dt.datetime,
    *,
    harness_root: pathlib.Path,
) -> tuple[dict | None, tuple[str, str] | None]:
    """Load task context and decide managed execution state when it exists."""
    thread_dir = pathlib.Path(thread_dir)
    ledger_path = pathlib.Path(thread_dir) / "executions.json"
    session_mode = _load_task_context(
        thread_dir / "session_mode.json", expected_session_id=session_id
    )
    if not os.path.lexists(ledger_path):
        return session_mode, None
    if session_mode is None:
        return None, ("block", "delegated_execution_unresolved")
    if ledger_path.is_symlink():
        return session_mode, ("block", "delegated_execution_unresolved")
    ledger = execution_store.load_trusted(ledger_path, expected_parent=session_id)
    if ledger is None:
        return session_mode, ("block", "delegated_execution_unresolved")

    scheduled = schedule_store.load(pathlib.Path(thread_dir) / "scheduled.json")
    health = supervisor_health.load_trusted(supervisor_health_path(harness_root))
    return session_mode, execution_stop_decision(
        task_root_status(session_mode), ledger, health, scheduled, now
    )
