#!/usr/bin/env python3
"""Worktree false-allow regression for the task-mode queue-drain Stop gate.

Source / oracle: the 2026-06-01 two-live-stops incident. Session 75be09cc ran
inside the git worktree .worktrees/cake-m95.4-eventlog and allowed Stop eight
times with reason `task_mode_no_beads_in_cwd` while a ready sibling task
(cake-m95.4.2) remained under the molecule root.

Root cause
----------
`_check_task_mode_queue` skipped the entire queue check when the cwd had no
literal `.beads/` directory:

    if not (repo_path / ".beads").exists():
        return ("allow", "task_mode_no_beads_in_cwd")

A git worktree has no `.beads/` dir but `bd` still resolves the shared Dolt DB
via the redirect file / BEADS_DIR env (see beads-worktree-integration rule). So
the gate tested the wrong proxy — a *directory* — instead of the *capability*
(`can bd see the queue?`), and every worktree session was silently ungated.

Business invariant
------------------
A task-mode session with ready, in-scope work remaining must BLOCK Stop,
whether or not the cwd is a worktree. The gate must consult the actual `bd`
queue, degrading to allow ONLY when bd genuinely cannot resolve a queue.

Fragile implementations these tests REJECT
-------------------------------------------
- "no `.beads/` dir -> allow"  (the shipped bug)        -> fails test_worktree_with_ready_work_blocks
- "bd unavailable -> always block"                      -> fails test_non_beads_cwd_degrades_to_allow
- "bd unavailable -> always allow"                      -> fails test_real_beads_repo_bd_failure_blocks
Only a capability probe (run bd; degrade on failure, but stay blocked inside a
real beads repo whose bd merely hiccupped) survives all three.

Run: python3 -m pytest harness/tests/test_task_mode_queue.py -q
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness" / "bin"))

import stop_hook  # noqa: E402


def _fake_runner(responses):
    """Build a run_bd(args)->Optional[list] from {first-arg: response} mapping.

    responses maps the bd subcommand (e.g. "ready", "list") to the list it
    returns, or None to simulate a bd failure (no parseable JSON).
    """

    def run_bd(args):
        if args[:1] == ["show"]:
            return [{"id": args[1], "status": "closed"}]
        return responses.get(args[0])

    return run_bd


def test_worktree_with_ready_work_blocks(tmp_path) -> None:
    """NEGATIVE CONTROL / the bug: worktree cwd (no .beads/), ready work exists."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    assert not (tmp_path / ".beads").exists()  # worktree: no literal .beads dir
    run_bd = _fake_runner({"ready": [{"id": "cake-m95.4.2"}]})
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert (decision, reason) == ("block", "tasks_remain_in_queue"), (
        "a worktree session with a ready sibling task must BLOCK, not allow via "
        f"the .beads-directory proxy; got {decision}/{reason}"
    )


def test_worktree_drained_allows(tmp_path) -> None:
    """POSITIVE CONTROL: worktree cwd, queue genuinely drained -> allow."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": [], "blocked": []})
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert (decision, reason) == ("allow", "queue_drained"), (
        f"a drained worktree queue must allow Stop; got {decision}/{reason}"
    )


def test_worktree_blocked_deps_blocks(tmp_path) -> None:
    """A real scoped blocked result is not a clean drain without a wakeup."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": [], "blocked": [{"id": "cake-m95.4.9"}]})
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert (decision, reason) == ("block", "blocked_tasks_no_wakeup"), (
        f"scoped blocked work must not look drained; got {decision}/{reason}"
    )


def test_non_beads_cwd_degrades_to_allow(tmp_path) -> None:
    """POSITIVE CONTROL: not a beads context at all (bd cannot run) -> graceful allow.

    Preserves the original graceful-degradation intent so the gate never
    permanently traps a session in a genuinely non-beads cwd.
    """
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": None})  # bd produced no parseable queue
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert decision == "allow", (
        "a genuine non-beads cwd (bd unavailable, no .beads/ dir) must degrade to "
        f"allow, not trap the session; got {decision}/{reason}"
    )


def test_real_beads_repo_bd_failure_blocks(tmp_path) -> None:
    """A real beads repo (.beads/ present) whose bd hiccups must stay BLOCKED.

    Distinguishes 'transient bd error inside a real beads repo' (stay) from
    'not a beads context' (allow) — the safety the .beads/ check used to give,
    preserved without the worktree false-allow.
    """
    (tmp_path / ".beads").mkdir()
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": None})
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert decision == "block", (
        "a real beads repo with a transient bd failure must not let the agent "
        f"sneak out; got {decision}/{reason}"
    )


def test_no_cwd_blocks() -> None:
    session_mode = {"repo_cwd": "", "parent_id": "cake-m95.4"}
    decision, reason = stop_hook._check_task_mode_queue(session_mode)
    assert (decision, reason) == ("block", "task_mode_no_cwd")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
