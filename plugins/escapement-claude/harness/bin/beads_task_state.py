#!/usr/bin/env python3
"""Canonical Beads completion checks for task-mode Stop decisions.

The root bead is the durable completion authority.  An empty descendant queue
does not establish that a claimed parent outcome has closed.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Callable, Optional


BdRunner = Callable[[list[str]], Optional[list[dict]]]


def _main_repo_has_beads(cwd: str) -> bool:
    """Whether a linked worktree resolves to a main repository with Beads."""
    if not cwd:
        return False
    try:
        git_path = pathlib.Path(cwd) / ".git"
        if not git_path.is_file():
            return False
        content = git_path.read_text(encoding="utf-8", errors="replace").strip()
        if not content.startswith("gitdir:"):
            return False
        gitdir = pathlib.Path(content[len("gitdir:") :].strip())
        if not gitdir.is_absolute():
            gitdir = (pathlib.Path(cwd) / gitdir).resolve()
        return (gitdir.parent.parent.parent / ".beads").is_dir()
    except OSError:
        return False


def _default_runner(repo_cwd: str) -> BdRunner:
    def run_bd(args: list[str]) -> Optional[list[dict]]:
        try:
            result = subprocess.run(
                ["bd", *args, "--json"],
                cwd=repo_cwd,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return None
            try:
                payload = json.loads(result.stdout)
            except (json.JSONDecodeError, ValueError):
                return []
            return payload if isinstance(payload, list) else None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

    return run_bd


def _closed_root(root_id: str, records: Optional[list[dict]]) -> bool:
    """Return true only for a canonical closed record matching the requested root."""
    if not isinstance(records, list) or len(records) != 1:
        return False
    record = records[0]
    return (
        isinstance(record, dict)
        and record.get("id") == root_id
        and record.get("status") == "closed"
    )


def task_root_status(session_mode: dict, run_bd: BdRunner | None = None) -> str | None:
    """Return the exact canonical root status, or None when it is unresolved."""
    repo_cwd = (
        session_mode.get("repo_cwd", "") if isinstance(session_mode, dict) else ""
    )
    root_id = (
        session_mode.get("parent_id") or session_mode.get("task_id")
        if isinstance(session_mode, dict)
        else None
    )
    if not repo_cwd or not root_id:
        return None
    record = _one_exact_root(
        root_id, (run_bd or _default_runner(repo_cwd))(["show", root_id])
    )
    status = record.get("status") if record is not None else None
    return status if isinstance(status, str) and status else None


def _one_exact_root(root_id: str, records: Optional[list[dict]]) -> dict | None:
    if not isinstance(records, list) or len(records) != 1:
        return None
    record = records[0]
    if not isinstance(record, dict) or record.get("id") != root_id:
        return None
    return record


def check_task_root_outcome(
    session_mode: dict, run_bd: BdRunner | None = None
) -> tuple[str, str]:
    """Check canonical root closure without applying descendant queue policy.

    This is used by the wakeup path: a wake can defer a verified blocker, but it
    cannot turn an in-progress or untrustworthy parent record into completion.
    """
    repo_cwd = (
        session_mode.get("repo_cwd", "") if isinstance(session_mode, dict) else ""
    )
    root_id = (
        session_mode.get("parent_id") or session_mode.get("task_id")
        if isinstance(session_mode, dict)
        else None
    )
    if not repo_cwd:
        return ("block", "task_mode_no_cwd")
    if not root_id:
        return ("block", "parent_outcome_unresolved")

    root = (run_bd or _default_runner(repo_cwd))(["show", root_id])
    if _closed_root(root_id, root):
        return ("allow", "parent_outcome_closed")
    return ("block", "parent_outcome_unresolved")


def check_task_scope(
    session_mode: dict, run_bd: BdRunner | None = None
) -> tuple[str, str]:
    """Return whether a scoped task-mode session has a verified clean drain.

    The root Beads record must be exactly ``closed`` before empty ready/blocked
    descendant queries can authorize ``queue_drained``.  Unknown canonical state
    fails closed in a Beads context; a genuinely non-Beads cwd keeps the existing
    graceful degradation when no ``bd`` query can resolve.
    """
    repo_cwd = (
        session_mode.get("repo_cwd", "") if isinstance(session_mode, dict) else ""
    )
    root_id = (
        session_mode.get("parent_id") or session_mode.get("task_id")
        if isinstance(session_mode, dict)
        else None
    )
    if not repo_cwd:
        return ("block", "task_mode_no_cwd")
    if not root_id:
        return ("block", "parent_outcome_unresolved")

    has_beads_dir = (
        pathlib.Path(repo_cwd) / ".beads"
    ).exists() or _main_repo_has_beads(repo_cwd)
    runner = run_bd or _default_runner(repo_cwd)

    scope = ["--parent", root_id]
    ready = runner(["ready", *scope])
    if ready is None:
        if has_beads_dir:
            return ("block", "task_mode_bd_ready_failed")
        return ("allow", "task_mode_bd_unavailable")
    if len(ready) > 0:
        return ("block", "tasks_remain_in_queue")

    # A root lookup is required before granting a drain, but ready work is the
    # more actionable denial when it is already present.  This also keeps the
    # capability-probe degradation for a genuinely non-Beads cwd intact.
    root = runner(["show", root_id])
    if not _closed_root(root_id, root):
        return ("block", "parent_outcome_unresolved")

    blocked = runner(["blocked", *scope])
    if blocked is None:
        if has_beads_dir:
            return ("block", "task_mode_bd_ready_failed")
        return ("allow", "queue_drained")
    if len(blocked) > 0:
        return ("block", "blocked_tasks_no_wakeup")
    return ("allow", "queue_drained")
