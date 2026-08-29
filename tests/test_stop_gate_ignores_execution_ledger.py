"""The Stop gate must not block on delegated-execution state.

Business outcome
----------------
A session is stopped only for a reason someone can act on. Before this, 19 of
19 threads holding an active execution were blocked with
``delegated_execution_overdue`` on 83 records that could never complete: every
one had ``native_child_id: null``, so no child was ever bound and no result
could arrive. The repair mechanism could not clear them either -- the reaper
marks a thread "unresolved" precisely when it has active executions and no
resolvable repo, which is the state it existed to clean up.

Independent source of truth
---------------------------
``decide_task_mode`` itself, driven with thread directories written to disk in
the shapes the harness actually produced. Not the removed policy function, and
not a mock of it.

Invalid solution classes this suite rejects
-------------------------------------------
- Any ledger file resurrecting the block -> ``test_*_does_not_block``
- Silently swallowing a real task-mode incident -> ``test_task_mode_incident_still_blocks``
- Losing the session-mode context the Stop gate needs downstream
  -> ``test_session_mode_is_still_loaded``
- Trusting a session_mode record belonging to another session
  -> ``test_foreign_session_mode_is_not_trusted``
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "harness" / "bin"


def _load(name: str):
    if str(BIN) not in sys.path:
        sys.path.insert(0, str(BIN))
    spec = importlib.util.spec_from_file_location(name, BIN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


adapter = _load("execution_stop_adapter")

SESSION = "11111111-2222-3333-4444-555555555555"
NOW = dt.datetime(2026, 8, 29, 6, 0, tzinfo=dt.timezone.utc)


def make_thread(tmp_path: Path, *, session_mode: bool = True) -> Path:
    thread = tmp_path / SESSION
    thread.mkdir(parents=True, exist_ok=True)
    if session_mode:
        (thread / "session_mode.json").write_text(json.dumps({
            "mode": "task",
            "repo_cwd": str(tmp_path),
            "task_id": "demo-1",
            "parent_id": None,
            "entered_at": "2026-08-29T05:00:00Z",
            "session_id": SESSION,
        }))
    return thread


def decide(thread: Path):
    return adapter.decide_task_mode(
        SESSION, thread, NOW, harness_root=thread.parent.parent
    )


def stranded_ledger() -> str:
    """The exact shape that was blocking: queued, overdue, never bound."""
    return json.dumps({
        "version": 1,
        "parent_session_id": SESSION,
        "updated_at": "2026-08-29T02:21:05Z",
        "executions": [{
            "execution_id": "c8b38061e3304d09ad164757e6e31780",
            "agent_name": "reviewer",
            "attempt": 1,
            "generation": 1,
            "bead_id": "demo-1",
            "host": "claude",
            "state": "queued",
            "native_child_id": None,
            "started_at": None,
            "terminal_at": None,
            "queued_at": "2026-08-29T02:21:05Z",
            "start_deadline": "2026-08-29T02:23:05Z",
            "idle_deadline": "2026-08-29T02:36:05Z",
            "hard_deadline": "2026-08-29T04:21:05Z",
        }],
        "incidents": [],
    })


# --- the block is gone ----------------------------------------------------

def test_stranded_execution_does_not_block(tmp_path):
    """The regression itself: 83 of these blocked 19 threads indefinitely."""
    thread = make_thread(tmp_path)
    (thread / "executions.json").write_text(stranded_ledger())
    session_mode, decision = decide(thread)
    assert decision is None, f"ledger still gates Stop: {decision}"
    assert session_mode is not None


def test_expectation_file_does_not_block(tmp_path):
    thread = make_thread(tmp_path)
    (thread / "execution_expectation.json").write_text(json.dumps({
        "session_id": SESSION, "expectations": [{"bead_id": "demo-1"}],
    }))
    assert decide(thread)[1] is None


def test_execution_incident_does_not_block(tmp_path):
    thread = make_thread(tmp_path)
    (thread / "execution_incident.json").write_text(json.dumps({
        "session_id": SESSION, "kind": "whatever",
    }))
    assert decide(thread)[1] is None


def test_unparseable_ledger_does_not_block(tmp_path):
    """Untrusted ledger state used to be a block; it is now simply ignored."""
    thread = make_thread(tmp_path)
    (thread / "executions.json").write_text("{ not json")
    assert decide(thread)[1] is None


def test_no_ledger_at_all_does_not_block(tmp_path):
    assert decide(make_thread(tmp_path))[1] is None


# --- the teeth that must remain -------------------------------------------

def test_task_mode_incident_still_blocks(tmp_path):
    """Negative control: a real unresolved incident in THIS session still stops."""
    thread = make_thread(tmp_path)
    (thread / "task_mode_incident.json").write_text(json.dumps({
        "session_id": SESSION, "reason": "something genuinely unresolved",
    }))
    _, decision = decide(thread)
    assert decision is not None and decision[0] == "block"


def test_unreadable_task_mode_incident_still_blocks(tmp_path):
    """Absence is a decision; unreadable is not."""
    thread = make_thread(tmp_path)
    (thread / "task_mode_incident.json").write_text("{ not json")
    _, decision = decide(thread)
    assert decision is not None and decision[0] == "block"


def test_session_mode_is_still_loaded(tmp_path):
    """The Stop gate's downstream queue-drain check needs this context."""
    session_mode, _ = decide(make_thread(tmp_path))
    assert session_mode is not None
    assert session_mode.get("task_id") == "demo-1"


def test_foreign_session_mode_is_not_trusted(tmp_path):
    """A record belonging to another session must not be adopted.

    It is deliberately not a block on its own: untrusted state alone must not be
    able to freeze a session (harness/tests/test_task_mode_scope.py holds the
    same line for task_mode_incident.json). The security property under test is
    that the planted repo_cwd and parent_id are never adopted.
    """
    thread = tmp_path / SESSION
    thread.mkdir(parents=True)
    (thread / "session_mode.json").write_text(json.dumps({
        "mode": "task", "repo_cwd": str(tmp_path), "task_id": "demo-1",
        "parent_id": None, "entered_at": "2026-08-29T05:00:00Z",
        "session_id": "99999999-9999-9999-9999-999999999999",
    }))
    session_mode, decision = decide(thread)
    assert session_mode is None
    assert decision is None


def test_adapter_no_longer_depends_on_the_ledger(tmp_path):
    """Structural: the removed modules must not creep back in via import."""
    source = (BIN / "execution_stop_adapter.py").read_text()
    for gone in (
        "execution_store",
        "execution_expectation",
        "supervisor_health",
        "execution_stop_decision",
    ):
        assert f"import {gone}" not in source and f"from {gone}" not in source, (
            f"{gone} is back in the Stop path"
        )
