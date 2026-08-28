#!/usr/bin/env python3
"""Behavioral oracle for delegated-work Stop and bounded pauses."""

from __future__ import annotations

import copy
import datetime as dt
import io
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(BIN))

import stop_hook  # noqa: E402
import would_block_stop as stop_policy  # noqa: E402
import schedule_wakeup_bridge as wake_bridge  # noqa: E402
from harness.tests.task5_health_fixtures import (  # noqa: E402
    qualifying_health_after_registration,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 8, 9, 20, 10, tzinfo=UTC)
SESSION = "incident-parent-session"
ROOT_BEAD = "escapement-e3ai"
INSTALLATION = "installed-supervisor-alpha"


def _execution(
    name: str,
    *,
    state: str = "running",
    attempt: int = 1,
    generation: int = 1,
    watchdog_id: str | None = None,
    idle_deadline: str = "2026-08-09T20:20:00Z",
    hard_deadline: str = "2026-08-09T22:00:00Z",
) -> dict:
    terminal = state in {"terminal", "cancelled"}
    queued = state == "queued"
    result_applied = state == "terminal"
    return {
        "bead_id": f"{ROOT_BEAD}.{name[-1]}",
        "execution_id": name,
        "host": "claude",
        "agent_name": f"worker-{name}",
        "native_child_id": None if queued else f"native-{name}",
        "dispatch_tool_use_id": f"toolu-{name}",
        "watchdog_id": watchdog_id or f"watch-{name}",
        "attempt": attempt,
        "generation": generation,
        "state": state,
        "queued_at": "2026-08-09T20:00:00Z",
        "started_at": None if queued else "2026-08-09T20:00:05Z",
        "last_activity_at": None if queued else "2026-08-09T20:05:00Z",
        "last_activity_kind": (
            None if queued else "terminal_event" if terminal else "child_started"
        ),
        # Resolved executions have no live deadline or recovery residue; keep
        # this fixture structurally valid under the durable Task 1 contract.
        "start_deadline": None if terminal else "2026-08-09T20:02:00Z",
        "idle_deadline": None if terminal else idle_deadline,
        "hard_deadline": None if terminal else hard_deadline,
        "reconcile_due": None,
        "terminal_at": "2026-08-09T20:08:00Z" if terminal else None,
        "terminal_reason": "completed" if terminal else None,
        "terminal_event_id": f"terminal-{name}" if terminal else None,
        "result_digest": f"sha256:{name}" if state == "terminal" else None,
        "recovery_count": 0,
        "recovery_claim": None,
        "result_application": {
            "state": "applied" if result_applied else "unapplied",
            "claim": None,
            "claim_generation": 1 if result_applied else 0,
            "idempotency_key": f"execution:{name}:attempt:{attempt}:generation:{generation}",
            "applied_at": "2026-08-09T20:08:30Z" if result_applied else None,
        },
    }


def _ledger(*executions: dict) -> dict:
    return {
        "version": 1,
        "parent_session_id": SESSION,
        "updated_at": "2026-08-09T20:09:59Z",
        "executions": list(executions),
        "incidents": [],
    }


def _wake(execution: dict, **updates) -> dict:
    value = {
        "wake_at": "2026-08-09T20:15:00Z",
        "registered_at": "2026-08-09T20:09:50Z",
        "prompt": "reconcile delegated execution",
        "thread_id": SESSION,
        "created_by": "execution-supervisor",
        "crash_count": 0,
        "supervisor_installation_id": INSTALLATION,
        "supervisor_generation": 11,
        "parent_session_id": SESSION,
        "watchdog_id": execution["watchdog_id"],
        "execution_id": execution["execution_id"],
        "attempt": execution["attempt"],
        "generation": execution["generation"],
    }
    value.update(updates)
    return value


def _health(**updates) -> dict:
    value = {
        "reconcile_started_at": "2026-08-09T20:09:55Z",
        "last_successful_reconcile_started_at": "2026-08-09T20:09:55Z",
        "last_successful_reconcile_at": "2026-08-09T20:10:00Z",
        "completed_generation": 12,
        "installation_id": INSTALLATION,
        "counts": {"successful_passes": 12, "threads": 1, "recoveries": 0},
    }
    value.update(updates)
    return value


def decide(root_status: str, ledger: dict | None, health: dict | None, scheduled: list):
    """Call only the published Task 5 policy boundary."""
    return stop_policy.execution_stop_decision(
        root_status,
        ledger,
        health,
        scheduled,
        NOW,
    )


def test_incident_queue_drain_with_two_nonterminal_children_blocks_without_health():
    """Incident A: a drained Beads queue is not terminal native execution proof."""
    ledger = _ledger(_execution("exec-research-1"), _execution("exec-research-2"))
    assert decide("closed", ledger, None, []) == (
        "block",
        "delegated_execution_unresolved",
    )


def test_incident_closed_children_with_open_parent_blocks_completion():
    """Incident B: terminal children do not complete an in-progress root Bead."""
    ledger = _ledger(
        _execution("exec-research-1", state="terminal"),
        _execution("exec-research-2", state="terminal"),
    )
    assert decide("in_progress", ledger, None, []) == (
        "block",
        "parent_outcome_unresolved",
    )


def test_closed_parent_and_terminal_attempts_allow_verified_completion():
    """Positive completion control: the new gate is not an always-block."""
    ledger = _ledger(
        _execution("exec-research-1", state="terminal"),
        _execution("exec-research-2", state="cancelled"),
    )
    assert decide("closed", ledger, None, []) == (
        "allow",
        "delegated_outcome_complete",
    )


@pytest.mark.parametrize(
    "health",
    [
        None,
        _health(last_successful_reconcile_at="2026-08-09T19:59:59Z"),
        _health(last_successful_reconcile_at=None),
        _health(completed_generation=11),
        {
            "reconcile_started_at": "2026-08-09T20:09:55Z",
            "last_successful_reconcile_at": "not-a-time",
            "completed_generation": 12,
            "installation_id": INSTALLATION,
            "counts": {},
        },
        _health(installation_id="foreign-installation"),
    ],
    ids=[
        "missing",
        "stale",
        "pre-scan-only",
        "successful-scan-predates-wake",
        "malformed",
        "wrong-installation",
    ],
)
def test_future_managed_wake_without_current_installed_supervisor_proof_blocks(health):
    execution = _execution("exec-research-1")
    assert decide("in_progress", _ledger(execution), health, [_wake(execution)]) == (
        "block",
        "supervisor_health_unresolved",
    )


@pytest.mark.parametrize(
    ("wake_generation", "health_generation"),
    [(0, 10), (11, 11), (12, 11)],
    ids=["zero-snapshot-is-invalid", "equal-is-pre-scan", "rollback"],
)
def test_health_generation_must_be_positive_and_strictly_after_wake_snapshot(
    wake_generation, health_generation
):
    execution = _execution("exec-research-1")
    assert decide(
        "in_progress",
        _ledger(execution),
        _health(completed_generation=health_generation),
        [_wake(execution, supervisor_generation=wake_generation)],
    ) == ("block", "supervisor_health_unresolved")


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("parent_session_id", "other-parent"),
        ("watchdog_id", "watch-other"),
        ("execution_id", "exec-other"),
        ("attempt", 2),
        ("generation", 2),
    ],
)
def test_only_exact_current_generation_managed_wake_can_authorize_pause(field, wrong):
    execution = _execution("exec-research-1")
    scheduled = [_wake(execution, **{field: wrong})]
    assert decide("in_progress", _ledger(execution), _health(), scheduled) == (
        "block",
        "managed_wake_unresolved",
    )


def test_unrelated_future_ci_wake_and_fresh_global_health_do_not_authorize_pause():
    execution = _execution("exec-research-1")
    ci_wake = {
        "wake_at": "2026-08-09T20:15:00Z",
        "prompt": "check CI",
        "thread_id": SESSION,
        "created_by": "ScheduleWakeup",
        "crash_count": 0,
    }
    assert decide("in_progress", _ledger(execution), _health(), [ci_wake]) == (
        "block",
        "managed_wake_unresolved",
    )


def test_hard_overdue_blocks_even_when_diagnostic_chatter_is_recent():
    execution = _execution(
        "exec-research-1",
        idle_deadline="2026-08-09T20:20:00Z",
        hard_deadline="2026-08-09T20:09:00Z",
    )
    assert decide("in_progress", _ledger(execution), _health(), [_wake(execution)]) == (
        "block",
        "delegated_execution_overdue",
    )


def test_current_running_attempt_with_exact_wake_and_health_allows_bounded_pause():
    execution = _execution("exec-research-1")
    assert decide("in_progress", _ledger(execution), _health(), [_wake(execution)]) == (
        "allow",
        "delegated_execution_bounded_pause",
    )


def test_every_nonterminal_attempt_requires_its_own_exact_managed_wake():
    first = _execution("exec-research-1")
    second = _execution("exec-research-2")
    assert decide("in_progress", _ledger(first, second), _health(), [_wake(first)]) == (
        "block",
        "managed_wake_unresolved",
    )


@pytest.mark.parametrize(
    "execution",
    [
        _execution("exec-queued-1", state="queued"),
        _execution(
            "exec-running-1",
            idle_deadline="2026-08-09T20:09:00Z",
        ),
        _execution("exec-sticky-1"),
    ],
    ids=["queued-start-overdue", "running-idle-overdue", "sticky-reconcile-due"],
)
def test_each_execution_reconciliation_class_blocks_pause(execution):
    execution = copy.deepcopy(execution)
    if execution["state"] == "queued":
        execution["start_deadline"] = "2026-08-09T20:09:00Z"
    if execution["execution_id"] == "exec-running-1":
        execution["started_at"] = "2026-08-09T19:50:00Z"
        execution["last_activity_at"] = "2026-08-09T19:54:00Z"
    if execution["execution_id"] == "exec-sticky-1":
        execution["reconcile_due"] = "idle"
    assert decide("in_progress", _ledger(execution), _health(), [_wake(execution)]) == (
        "block",
        "delegated_execution_overdue",
    )


def test_past_managed_wake_never_authorizes_pause():
    execution = _execution("exec-research-1")
    assert decide(
        "in_progress",
        _ledger(execution),
        _health(),
        [_wake(execution, wake_at="2026-08-09T20:09:59Z")],
    ) == ("block", "managed_wake_unresolved")


def test_closed_root_without_managed_ledger_preserves_legacy_completion():
    assert decide("closed", None, None, []) == ("allow", "no_managed_executions")


def _write_fake_bd(
    tmp_path: pathlib.Path,
    root_status: str,
    root_record_id: str | None = ROOT_BEAD,
) -> pathlib.Path:
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    script = fakebin / "bd"
    root_body = (
        "    print('[]')\n"
        if root_record_id is None
        else (
            "    print(json.dumps([{'id': "
            f"{root_record_id!r}, 'status': {root_status!r}}}]))\n"
        )
    )
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = tuple(a for a in sys.argv[1:] if a != '--json')\n"
        "if args[:1] == ('show',):\n"
        + root_body
        + "elif args[:1] in (('ready',), ('blocked',)):\n"
        "    print('[]')\n"
        "else:\n"
        "    raise SystemExit(1)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return fakebin


def _public_stop(
    monkeypatch,
    capsys,
    tmp_path: pathlib.Path,
    *,
    root_status: str,
    ledger: object,
    scheduled: object,
    health: object,
    root_record_id: str | None = ROOT_BEAD,
    recent_user_message: str | None = None,
    ledger_mode: int = 0o600,
    scheduled_mode: int = 0o600,
    health_mode: int = 0o600,
    write_ledger: bool = True,
    refresh_health: bool = True,
    refresh_ledger_deadlines: bool = True,
    refresh_schedule: bool = True,
) -> str:
    # Public Stop uses the actual wall clock. Preserve the hand-authored identity
    # and state while making the public fixture's live deadlines/health current.
    live_now = dt.datetime.now(UTC)
    ledger = copy.deepcopy(ledger)
    scheduled = copy.deepcopy(scheduled)
    health = copy.deepcopy(health)
    if isinstance(ledger, dict) and isinstance(ledger.get("executions"), list):
        ledger["updated_at"] = (live_now - dt.timedelta(seconds=1)).isoformat()
        for execution in ledger["executions"]:
            if refresh_ledger_deadlines and execution["state"] not in {
                "terminal",
                "cancelled",
            }:
                execution["last_activity_at"] = (
                    live_now - dt.timedelta(minutes=5)
                ).isoformat()
                execution["start_deadline"] = (
                    live_now + dt.timedelta(minutes=2)
                ).isoformat()
                execution["idle_deadline"] = (
                    live_now + dt.timedelta(minutes=10)
                ).isoformat()
                execution["hard_deadline"] = (
                    live_now + dt.timedelta(hours=1)
                ).isoformat()
    if isinstance(scheduled, list) and refresh_schedule:
        for entry in scheduled:
            if isinstance(entry, dict):
                entry["wake_at"] = (live_now + dt.timedelta(minutes=5)).isoformat()
    if isinstance(health, dict) and refresh_health:
        health["reconcile_started_at"] = (
            live_now - dt.timedelta(seconds=2)
        ).isoformat()
        health["last_successful_reconcile_started_at"] = (
            live_now - dt.timedelta(seconds=2)
        ).isoformat()
        health["last_successful_reconcile_at"] = (
            live_now - dt.timedelta(seconds=1)
        ).isoformat()
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / ".beads").mkdir()
    thread_dir = root / "threads" / SESSION
    thread_dir.mkdir(parents=True)
    (thread_dir / "session_mode.json").write_text(
        json.dumps(
            {
                "mode": "task",
                "session_id": SESSION,
                "repo_cwd": str(repo),
                "parent_id": ROOT_BEAD,
            }
        ),
        encoding="utf-8",
    )
    if write_ledger:
        (thread_dir / "executions.json").write_text(
            ledger if isinstance(ledger, str) else json.dumps(ledger), encoding="utf-8"
        )
        (thread_dir / "executions.json").chmod(ledger_mode)
    (thread_dir / "scheduled.json").write_text(
        scheduled if isinstance(scheduled, str) else json.dumps(scheduled),
        encoding="utf-8",
    )
    (thread_dir / "scheduled.json").chmod(scheduled_mode)
    if health is not None:
        (root / "supervisor-health.json").write_text(
            health if isinstance(health, str) else json.dumps(health), encoding="utf-8"
        )
        (root / "supervisor-health.json").chmod(health_mode)

    fakebin = _write_fake_bd(tmp_path, root_status, root_record_id)
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(stop_hook, "HARNESS_ROOT", root)
    monkeypatch.setattr(stop_hook, "INCIDENTS_LOG", root / "incidents.jsonl")
    monkeypatch.setattr(stop_hook.session_isolation, "write_checkout", lambda *a: None)
    transcript_path = ""
    if recent_user_message is not None:
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": recent_user_message},
                }
            ),
            encoding="utf-8",
        )
        transcript_path = str(transcript)
    monkeypatch.setattr(
        stop_hook.sys,
        "stdin",
        io.StringIO(
            json.dumps({"session_id": SESSION, "transcript_path": transcript_path})
        ),
    )
    assert stop_hook.main() == 0
    return capsys.readouterr().out


def test_public_stop_replays_nonterminal_children_without_supervisor_incident(
    monkeypatch, capsys, tmp_path
):
    ledger = _ledger(_execution("exec-research-1"), _execution("exec-research-2"))
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="closed",
        ledger=ledger,
        scheduled=[],
        health=None,
    )
    assert "delegated_execution_unresolved" in output


@pytest.mark.parametrize(
    ("ledger", "write_ledger", "ledger_mode"),
    [
        ({"version": 1, "parent_session_id": SESSION}, True, 0o600),
        ("{malformed", True, 0o600),
        (_ledger(_execution("exec-research-1")), True, 0o666),
    ],
    ids=["partial", "malformed", "untrusted-world-writable"],
)
def test_public_stop_rejects_untrusted_or_invalid_execution_ledger(
    monkeypatch, capsys, tmp_path, ledger, write_ledger, ledger_mode
):
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="closed",
        ledger=ledger,
        scheduled=[],
        health=None,
        write_ledger=write_ledger,
        ledger_mode=ledger_mode,
    )
    assert "delegated_execution_unresolved" in output


def test_public_closed_root_without_execution_ledger_preserves_legacy_completion(
    monkeypatch, capsys, tmp_path
):
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="closed",
        ledger=None,
        scheduled=[],
        health=None,
        write_ledger=False,
    )
    assert output == ""


def test_public_stop_replays_open_parent_after_children_terminal(
    monkeypatch, capsys, tmp_path
):
    ledger = _ledger(
        _execution("exec-research-1", state="terminal"),
        _execution("exec-research-2", state="terminal"),
    )
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=ledger,
        scheduled=[],
        health=None,
    )
    assert "parent_outcome_unresolved" in output


@pytest.mark.parametrize(
    "root_record_id",
    [None, "foreign-parent"],
    ids=["missing-root", "foreign-root"],
)
def test_public_stop_fails_closed_when_exact_root_record_is_unresolved(
    monkeypatch, capsys, tmp_path, root_record_id
):
    execution = _execution("exec-research-1", state="terminal")
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="closed",
        root_record_id=root_record_id,
        ledger=_ledger(execution),
        scheduled=[],
        health=None,
    )
    assert "parent_outcome_unresolved" in output


def test_public_explicit_user_release_remains_unconditional(
    monkeypatch, capsys, tmp_path
):
    execution = _execution("exec-research-1")
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=[],
        health=None,
        recent_user_message="stop",
    )
    assert output == ""


@pytest.mark.parametrize(
    ("ledger", "scheduled", "health", "write_ledger", "ledger_mode"),
    [
        (None, [], None, False, 0o600),
        ("{malformed", "not-json", "{malformed", True, 0o600),
        (_ledger(_execution("exec-research-1")), [], None, True, 0o666),
    ],
    ids=["missing-all", "malformed-all", "untrusted-ledger"],
)
def test_public_user_release_bypasses_missing_untrusted_or_malformed_supervision(
    monkeypatch,
    capsys,
    tmp_path,
    ledger,
    scheduled,
    health,
    write_ledger,
    ledger_mode,
):
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=ledger,
        scheduled=scheduled,
        health=health,
        write_ledger=write_ledger,
        ledger_mode=ledger_mode,
        recent_user_message="stop",
    )
    assert output == ""


def test_public_user_release_bypasses_untrusted_schedule_and_health(
    monkeypatch, capsys, tmp_path
):
    execution = _execution("exec-research-1")
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=[_wake(execution)],
        health=_health(),
        scheduled_mode=0o666,
        health_mode=0o666,
        recent_user_message="stop",
    )
    assert output == ""


def test_public_stop_accepts_exact_managed_bounded_pause(monkeypatch, capsys, tmp_path):
    execution = _execution("exec-research-1")
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=[_wake(execution)],
        health=_health(),
    )
    assert output == ""


def test_public_stop_rejects_foreign_installed_supervisor_health(
    monkeypatch, capsys, tmp_path
):
    """Public adapter must use the installed job identity, not accept any fresh file."""
    execution = _execution("exec-research-1")
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=[_wake(execution)],
        health=_health(installation_id="foreign-installation"),
    )
    assert "supervisor_health_unresolved" in output


def test_public_stop_rejects_successful_scan_that_predates_managed_wake(
    monkeypatch, capsys, tmp_path
):
    """A recent success that never observed this wake is not firing proof."""
    execution = _execution("exec-research-1")
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=[_wake(execution)],
        health=_health(completed_generation=11),
    )
    assert "supervisor_health_unresolved" in output


@pytest.mark.parametrize(
    "execution",
    [
        _execution("exec-queued-1", state="queued"),
        _execution("exec-running-1", idle_deadline="2026-08-09T20:09:00Z"),
        _execution("exec-sticky-1"),
    ],
    ids=["queued-start", "running-idle", "sticky-reconcile"],
)
def test_public_stop_blocks_each_execution_reconciliation_class(
    monkeypatch, capsys, tmp_path, execution
):
    execution = copy.deepcopy(execution)
    if execution["state"] == "queued":
        execution["start_deadline"] = "2026-08-09T20:09:00Z"
    if execution["execution_id"] == "exec-running-1":
        execution["started_at"] = "2026-08-09T19:50:00Z"
        execution["last_activity_at"] = "2026-08-09T19:54:00Z"
    if execution["execution_id"] == "exec-sticky-1":
        execution["reconcile_due"] = "hard"
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=[_wake(execution)],
        health=_health(),
        refresh_ledger_deadlines=False,
    )
    assert "delegated_execution_overdue" in output


def test_public_stop_rejects_past_managed_wake(monkeypatch, capsys, tmp_path):
    execution = _execution("exec-research-1")
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=[_wake(execution, wake_at="2026-08-09T20:09:59Z")],
        health=_health(),
        refresh_schedule=False,
    )
    assert "managed_wake_unresolved" in output


@pytest.mark.parametrize(
    ("scheduled_mode", "health_mode", "expected_reason"),
    [
        (0o666, 0o600, "managed_wake_unresolved"),
        (0o600, 0o666, "supervisor_health_unresolved"),
    ],
    ids=["world-writable-schedule", "world-writable-health"],
)
def test_public_stop_rejects_untrusted_pause_proof_files(
    monkeypatch,
    capsys,
    tmp_path,
    scheduled_mode,
    health_mode,
    expected_reason,
):
    execution = _execution("exec-research-1")
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=[_wake(execution)],
        health=_health(),
        scheduled_mode=scheduled_mode,
        health_mode=health_mode,
    )
    assert expected_reason in output


def test_public_stop_rejects_supervisor_generation_rollback(
    monkeypatch, capsys, tmp_path
):
    execution = _execution("exec-research-1")
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=[_wake(execution, supervisor_generation=12)],
        health=_health(completed_generation=11),
    )
    assert "supervisor_health_unresolved" in output


@pytest.mark.parametrize(
    ("ledger", "scheduled", "health", "expected_reason"),
    [
        (
            _ledger(_execution("exec-research-1")),
            [_wake(_execution("exec-research-1"), watchdog_id="wrong-watchdog")],
            _health(),
            "managed_wake_unresolved",
        ),
        (
            _ledger(_execution("exec-research-1"), _execution("exec-research-2")),
            [_wake(_execution("exec-research-1"))],
            _health(),
            "managed_wake_unresolved",
        ),
        (
            _ledger(_execution("exec-research-1")),
            [
                {
                    "wake_at": "2026-08-09T20:15:00Z",
                    "prompt": "check CI",
                    "thread_id": SESSION,
                    "created_by": "ScheduleWakeup",
                    "crash_count": 0,
                }
            ],
            _health(),
            "managed_wake_unresolved",
        ),
        (
            _ledger(_execution("exec-research-1")),
            [_wake(_execution("exec-research-1"))],
            _health(last_successful_reconcile_at="2026-08-09T19:00:00Z"),
            "supervisor_health_unresolved",
        ),
    ],
    ids=["wrong-identity", "two-active-one-wake", "generic-wake", "stale-health"],
)
def test_public_stop_cannot_bypass_delegated_pause_policy(
    monkeypatch,
    capsys,
    tmp_path,
    ledger,
    scheduled,
    health,
    expected_reason,
):
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=ledger,
        scheduled=scheduled,
        health=health,
        refresh_health=expected_reason != "supervisor_health_unresolved",
    )
    assert expected_reason in output


def test_public_managed_wakeup_file_is_consumed_by_public_stop(
    monkeypatch, capsys, tmp_path
):
    """Producer-to-consumer contract: persisted wire data grants one bounded pause."""
    producer_root = tmp_path / "producer-harness"
    producer_thread = producer_root / "threads" / SESSION
    producer_thread.mkdir(parents=True)
    initial_health = _health(completed_generation=11)
    (producer_root / "supervisor-health.json").write_text(
        json.dumps(initial_health), encoding="utf-8"
    )
    (producer_root / "supervisor-health.json").chmod(0o600)
    execution = _execution("exec-research-1")
    producer_input = {
        "parent_session_id": SESSION,
        **{
            key: execution[key]
            for key in ("watchdog_id", "execution_id", "attempt", "generation")
        },
    }
    wake_bridge.persist_managed_wakeup(
        producer_input,
        producer_thread,
        dt.datetime.now(UTC) + dt.timedelta(minutes=5),
    )
    persisted = json.loads((producer_thread / "scheduled.json").read_text())
    entry = persisted[0]
    assert {
        key: entry[key]
        for key in (
            "parent_session_id",
            "watchdog_id",
            "execution_id",
            "attempt",
            "generation",
            "supervisor_installation_id",
            "supervisor_generation",
        )
    } == {
        **producer_input,
        "supervisor_installation_id": INSTALLATION,
        "supervisor_generation": 11,
    }

    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path / "consumer",
        root_status="in_progress",
        ledger=_ledger(execution),
        scheduled=persisted,
        health=qualifying_health_after_registration(entry, 12, _health),
        refresh_health=False,
    )
    assert output == ""


def test_registered_bridge_subprocess_covers_every_active_execution_then_public_stop(
    monkeypatch, capsys, tmp_path
):
    """Real hook boundary: one ScheduleWakeup creates exact proof for all active work."""
    bridge_root = tmp_path / "registered-bridge"
    thread_dir = bridge_root / "threads" / SESSION
    thread_dir.mkdir(parents=True)
    first = _execution("exec-research-1")
    second = _execution("exec-research-2")
    ledger = _ledger(first, second)
    (thread_dir / "executions.json").write_text(json.dumps(ledger), encoding="utf-8")
    (thread_dir / "executions.json").chmod(0o600)
    (bridge_root / "supervisor-health.json").write_text(
        json.dumps(_health(completed_generation=11)), encoding="utf-8"
    )
    (bridge_root / "supervisor-health.json").chmod(0o600)
    payload = {
        "session_id": SESSION,
        "tool_name": "ScheduleWakeup",
        "tool_input": {"delaySeconds": 600, "prompt": "resume all delegated work"},
    }
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(bridge_root)
    env.pop("HARNESS_THREAD_DIR", None)
    command = [sys.executable, str(BIN / "schedule_wakeup_bridge.py")]
    for _ in range(2):
        result = subprocess.run(
            command,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
    persisted = json.loads((thread_dir / "scheduled.json").read_text())
    managed = [
        entry
        for entry in persisted
        if entry.get("created_by") == "execution-supervisor"
    ]
    assert len(managed) == 2, "repeated calls must not duplicate managed proofs"
    assert {
        (entry["execution_id"], entry["attempt"], entry["generation"])
        for entry in managed
    } == {
        (first["execution_id"], 1, 1),
        (second["execution_id"], 1, 1),
    }
    output = _public_stop(
        monkeypatch,
        capsys,
        tmp_path / "registered-consumer",
        root_status="in_progress",
        ledger=ledger,
        scheduled=persisted,
        health=qualifying_health_after_registration(managed[0], 12, _health),
        refresh_health=False,
    )
    assert output == ""


def test_registered_bridge_without_ledger_preserves_generic_legacy_proof(tmp_path):
    bridge_root = tmp_path / "missing-ledger"
    thread_dir = bridge_root / "threads" / SESSION
    thread_dir.mkdir(parents=True)
    payload = {
        "session_id": SESSION,
        "tool_name": "ScheduleWakeup",
        "tool_input": {"delaySeconds": 600, "prompt": "ordinary wake preserved"},
    }
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(bridge_root)
    env.pop("HARNESS_THREAD_DIR", None)
    result = subprocess.run(
        [sys.executable, str(BIN / "schedule_wakeup_bridge.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0
    persisted = json.loads((thread_dir / "scheduled.json").read_text())
    assert any(entry.get("created_by") == "ScheduleWakeup" for entry in persisted)
    assert not any(
        entry.get("created_by") == "execution-supervisor" for entry in persisted
    )
