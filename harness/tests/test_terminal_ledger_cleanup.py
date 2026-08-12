"""Public regression for completed execution state after worktree cleanup."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger  # noqa: E402

WAKER = BIN / "wakeup_waker.py"
SESSION = "cleaned-terminal-session"


def _ledger(state: str) -> dict:
    now = dt.datetime(2026, 8, 11, tzinfo=dt.timezone.utc)
    ledger = execution_ledger.new_ledger(SESSION)
    execution_ledger.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": SESSION,
            "bead_id": "escapement-completed-child",
            "execution_id": "completed-execution",
            "host": "claude",
            "agent_name": "completed-worker",
            "dispatch_tool_use_id": "tool-completed",
            "watchdog_id": "watch-completed",
            "attempt": 1,
            "generation": 1,
        },
        now,
    )
    if state in {"terminal", "cancelled"}:
        for kind in ("child_bound", "child_started"):
            execution_ledger.apply_event(
                ledger,
                {
                    "kind": kind,
                    "parent_session_id": SESSION,
                    "execution_id": "completed-execution",
                    "attempt": 1,
                    "generation": 1,
                    "native_child_id": "completed-native-child",
                },
                now,
            )
        event = {
            "kind": f"child_{state}",
            "parent_session_id": SESSION,
            "execution_id": "completed-execution",
            "attempt": 1,
            "generation": 1,
            "terminal_event_id": f"{state}-event",
            "terminal_reason": "completed elsewhere",
            "native_child_id": "completed-native-child",
        }
        if state == "terminal":
            event["result_digest"] = "sha256:completed"
        execution_ledger.apply_event(ledger, event, now)
    return ledger


def _run(
    tmp_path: pathlib.Path, state: str
) -> tuple[subprocess.CompletedProcess, pathlib.Path]:
    harness = tmp_path / state / "harness"
    thread = harness / "threads" / SESSION
    thread.mkdir(parents=True)
    for name, value in (("executions.json", _ledger(state)), ("scheduled.json", [])):
        path = thread / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(WAKER),
            "--fire",
            "--threads-root",
            str(harness / "threads"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result, harness / "supervisor-health.json"


def test_completed_ledgers_do_not_require_deleted_repo_context(tmp_path) -> None:
    for state in ("terminal", "cancelled"):
        result, health = _run(tmp_path, state)
        assert result.returncode == 0, result.stderr
        assert json.loads(health.read_text())["last_successful_reconcile_at"]


def test_active_ledger_without_repo_context_remains_unresolved(tmp_path) -> None:
    result, health = _run(tmp_path, "queued")
    assert result.returncode == 1
    assert json.loads(health.read_text())["last_successful_reconcile_at"] is None
