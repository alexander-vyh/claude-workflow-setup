#!/usr/bin/env python3
"""Final independent-review behavioral controls for delegated Stop authority."""

from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib
import sys

import pytest

from harness.tests import test_execution_stop_gate as oracle

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger  # noqa: E402
import supervisor_health  # noqa: E402
import wakeup_waker as public_waker  # noqa: E402

UTC = dt.timezone.utc


def _fresh_health(now: dt.datetime, *, generation: int = 12, **updates) -> dict:
    value = oracle._health(
        completed_generation=generation,
        reconcile_started_at=(now - dt.timedelta(seconds=10)).isoformat(),
        last_successful_reconcile_started_at=(
            now - dt.timedelta(seconds=10)
        ).isoformat(),
        last_successful_reconcile_at=(now - dt.timedelta(seconds=5)).isoformat(),
    )
    value.update(updates)
    return value


def _live_wake(execution: dict, now: dt.datetime, **updates) -> dict:
    return oracle._wake(
        execution,
        wake_at=(now + dt.timedelta(minutes=5)).isoformat(),
        registered_at=(now - dt.timedelta(seconds=20)).isoformat(),
        **updates,
    )


def _live_execution(name: str, now: dt.datetime) -> dict:
    execution = oracle._execution(name)
    execution.update(
        {
            "last_activity_at": (now - dt.timedelta(minutes=5)).isoformat(),
            "start_deadline": (now + dt.timedelta(minutes=2)).isoformat(),
            "idle_deadline": (now + dt.timedelta(minutes=10)).isoformat(),
            "hard_deadline": (now + dt.timedelta(hours=1)).isoformat(),
        }
    )
    return execution


def test_wake_registered_after_public_waker_scan_needs_next_covering_pass(
    monkeypatch, capsys, tmp_path
):
    """The generation whose schedule scan missed the wake cannot certify it."""
    harness_root = tmp_path / "waker-harness"
    threads_root = harness_root / "threads"
    thread_dir = threads_root / oracle.SESSION
    thread_dir.mkdir(parents=True)
    schedule_path = thread_dir / "scheduled.json"
    schedule_path.write_text("[]", encoding="utf-8")
    ledger_path = thread_dir / "executions.json"
    ledger_path.write_text(
        json.dumps(execution_ledger.new_ledger(oracle.SESSION)), encoding="utf-8"
    )
    ledger_path.chmod(0o600)
    now = dt.datetime.now(UTC)
    health_path = harness_root / "supervisor-health.json"
    health_path.write_text(
        json.dumps(_fresh_health(now, generation=7)), encoding="utf-8"
    )
    health_path.chmod(0o600)
    execution = oracle._execution("exec-interleaved-1")
    producer_input = {
        "parent_session_id": oracle.SESSION,
        **{
            key: execution[key]
            for key in ("watchdog_id", "execution_id", "attempt", "generation")
        },
    }
    produced: list[dict] = []
    injected = False
    real_try_lock = public_waker._try_lock

    class InterleavingLock:
        def __init__(self, inner):
            self.inner = inner

        def close(self):
            nonlocal injected
            self.inner.close()
            if not injected:
                injected = True
                entry = oracle.wake_bridge.persist_managed_wakeup(
                    producer_input,
                    thread_dir,
                    dt.datetime.now(UTC) + dt.timedelta(minutes=5),
                )
                assert entry is not None
                produced.append(entry)

    def interleaving_try_lock(path):
        lock = real_try_lock(path)
        return None if lock is None else InterleavingLock(lock)

    monkeypatch.setattr(public_waker, "_try_lock", interleaving_try_lock)
    assert public_waker.main(["--threads-root", str(threads_root), "--fire"]) == 0
    capsys.readouterr()
    assert len(produced) == 1
    first_health = json.loads(health_path.read_text())
    assert first_health["completed_generation"] == 8
    persisted = json.loads(schedule_path.read_text())
    assert persisted == produced

    first_stop = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path / "first-consumer",
        root_status="in_progress",
        ledger=oracle._ledger(execution),
        scheduled=persisted,
        health=first_health,
        refresh_health=False,
    )
    assert "supervisor_health_unresolved" in first_stop

    assert public_waker.main(["--threads-root", str(threads_root), "--fire"]) == 0
    capsys.readouterr()
    second_health = json.loads(health_path.read_text())
    assert second_health["completed_generation"] == 9
    second_stop = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path / "second-consumer",
        root_status="in_progress",
        ledger=oracle._ledger(execution),
        scheduled=json.loads(schedule_path.read_text()),
        health=second_health,
        refresh_health=False,
    )
    assert second_stop == ""


@pytest.mark.parametrize(
    ("thread_id", "expected"),
    [
        ("foreign-thread", ("block", "managed_wake_unresolved")),
        (oracle.SESSION, ("allow", "delegated_execution_bounded_pause")),
    ],
    ids=["foreign-thread", "exact-thread"],
)
def test_pure_managed_wake_thread_must_match_ledger_parent(thread_id, expected):
    execution = oracle._execution("exec-thread-1")
    assert (
        oracle.decide(
            "in_progress",
            oracle._ledger(execution),
            oracle._health(),
            [oracle._wake(execution, thread_id=thread_id)],
        )
        == expected
    )


@pytest.mark.parametrize(
    ("thread_id", "expected_reason"),
    [("foreign-thread", "managed_wake_unresolved"), (oracle.SESSION, None)],
    ids=["foreign-thread", "exact-thread"],
)
def test_public_managed_wake_thread_must_match_ledger_parent(
    monkeypatch, capsys, tmp_path, thread_id, expected_reason
):
    execution = oracle._execution("exec-thread-1")
    output = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=oracle._ledger(execution),
        scheduled=[oracle._wake(execution, thread_id=thread_id)],
        health=oracle._health(),
    )
    if expected_reason is None:
        assert output == ""
    else:
        assert expected_reason in output


def _terminal_with_application(
    application_state: str, name: str = "exec-result-1"
) -> dict:
    execution = oracle._execution(name, state="terminal")
    application = execution["result_application"]
    if application_state == "applied":
        return execution
    application.update(
        {
            "state": application_state,
            "claim": None,
            "claim_generation": 0,
            "applied_at": None,
        }
    )
    if application_state == "applying":
        application["claim_generation"] = 2
        application["claim"] = {
            "owner": "result-applier",
            "execution_id": execution["execution_id"],
            "attempt": execution["attempt"],
            "generation": execution["generation"],
            "claimed_at": "2026-08-09T20:08:10Z",
            "expires_at": "2026-08-09T20:18:10Z",
            "claim_generation": 2,
        }
    return execution


def _outcome_execution(state: str, name: str) -> dict:
    if state == "cancelled":
        return oracle._execution(name, state="cancelled")
    return _terminal_with_application(state, name)


@pytest.mark.parametrize(
    ("execution", "expected"),
    [
        (
            _terminal_with_application("unapplied"),
            ("block", "delegated_execution_unresolved"),
        ),
        (
            _terminal_with_application("applying"),
            ("block", "delegated_execution_unresolved"),
        ),
        (
            _terminal_with_application("applied"),
            ("allow", "delegated_outcome_complete"),
        ),
        (
            oracle._execution("exec-cancelled-1", state="cancelled"),
            ("allow", "delegated_outcome_complete"),
        ),
    ],
    ids=["terminal-unapplied", "terminal-applying", "terminal-applied", "cancelled"],
)
def test_closed_root_completion_requires_terminal_result_application(
    execution, expected
):
    assert oracle.decide("closed", oracle._ledger(execution), None, []) == expected


@pytest.mark.parametrize(
    ("execution", "expected_reason"),
    [
        (_terminal_with_application("unapplied"), "delegated_execution_unresolved"),
        (_terminal_with_application("applying"), "delegated_execution_unresolved"),
        (_terminal_with_application("applied"), None),
        (oracle._execution("exec-cancelled-1", state="cancelled"), None),
    ],
    ids=["terminal-unapplied", "terminal-applying", "terminal-applied", "cancelled"],
)
def test_public_closed_root_requires_terminal_result_application(
    monkeypatch, capsys, tmp_path, execution, expected_reason
):
    output = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="closed",
        ledger=oracle._ledger(copy.deepcopy(execution)),
        scheduled=[],
        health=None,
    )
    if expected_reason is None:
        assert output == ""
    else:
        assert expected_reason in output


@pytest.mark.parametrize(
    ("application_states", "expected"),
    [
        (("applied", "unapplied"), ("block", "delegated_execution_unresolved")),
        (("applied", "applying"), ("block", "delegated_execution_unresolved")),
        (("unapplied", "applied"), ("block", "delegated_execution_unresolved")),
        (("applying", "applied"), ("block", "delegated_execution_unresolved")),
        (("cancelled", "unapplied"), ("block", "delegated_execution_unresolved")),
        (("unapplied", "cancelled"), ("block", "delegated_execution_unresolved")),
        (("cancelled", "applying"), ("block", "delegated_execution_unresolved")),
        (("applying", "cancelled"), ("block", "delegated_execution_unresolved")),
        (("applied", "applied"), ("allow", "delegated_outcome_complete")),
        (("cancelled", "applied"), ("allow", "delegated_outcome_complete")),
        (("applied", "cancelled"), ("allow", "delegated_outcome_complete")),
    ],
    ids=[
        "applied-then-unapplied",
        "applied-then-applying",
        "unapplied-then-applied",
        "applying-then-applied",
        "cancelled-then-unapplied",
        "unapplied-then-cancelled",
        "cancelled-then-applying",
        "applying-then-cancelled",
        "all-applied",
        "cancelled-and-applied",
        "applied-and-cancelled",
    ],
)
def test_closed_root_checks_result_application_for_every_terminal(
    application_states, expected
):
    executions = [
        _outcome_execution(state, f"exec-result-{index}")
        for index, state in enumerate(application_states, start=1)
    ]
    assert oracle.decide("closed", oracle._ledger(*executions), None, []) == expected


@pytest.mark.parametrize(
    ("application_states", "expected_reason"),
    [
        (("applied", "unapplied"), "delegated_execution_unresolved"),
        (("applied", "applying"), "delegated_execution_unresolved"),
        (("unapplied", "applied"), "delegated_execution_unresolved"),
        (("applying", "applied"), "delegated_execution_unresolved"),
        (("cancelled", "unapplied"), "delegated_execution_unresolved"),
        (("unapplied", "cancelled"), "delegated_execution_unresolved"),
        (("cancelled", "applying"), "delegated_execution_unresolved"),
        (("applying", "cancelled"), "delegated_execution_unresolved"),
        (("applied", "applied"), None),
        (("cancelled", "applied"), None),
        (("applied", "cancelled"), None),
    ],
    ids=[
        "applied-then-unapplied",
        "applied-then-applying",
        "unapplied-then-applied",
        "applying-then-applied",
        "cancelled-then-unapplied",
        "unapplied-then-cancelled",
        "cancelled-then-applying",
        "applying-then-cancelled",
        "all-applied",
        "cancelled-and-applied",
        "applied-and-cancelled",
    ],
)
def test_public_closed_root_checks_result_application_for_every_terminal(
    monkeypatch, capsys, tmp_path, application_states, expected_reason
):
    executions = [
        _outcome_execution(state, f"exec-result-{index}")
        for index, state in enumerate(application_states, start=1)
    ]
    output = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="closed",
        ledger=oracle._ledger(*executions),
        scheduled=[],
        health=None,
    )
    if expected_reason is None:
        assert output == ""
    else:
        assert expected_reason in output


def _invalid_chronology(name: str, now: dt.datetime) -> dict:
    if name == "start-after-completion":
        return _fresh_health(
            now,
            last_successful_reconcile_started_at=(
                now - dt.timedelta(seconds=2)
            ).isoformat(),
            last_successful_reconcile_at=(now - dt.timedelta(seconds=5)).isoformat(),
        )
    if name == "future-success":
        return _fresh_health(
            now,
            reconcile_started_at=(now + dt.timedelta(minutes=1)).isoformat(),
            last_successful_reconcile_started_at=(
                now + dt.timedelta(minutes=1)
            ).isoformat(),
            last_successful_reconcile_at=(now + dt.timedelta(minutes=2)).isoformat(),
        )
    return _fresh_health(
        now,
        last_successful_reconcile_started_at=(
            now - dt.timedelta(seconds=2)
        ).isoformat(),
        last_successful_reconcile_at=(now + dt.timedelta(minutes=1)).isoformat(),
    )


FUTURE_CHRONOLOGY_CASES = [
    "future-success",
    "future-completion",
]
CHRONOLOGY_CASES = [
    "start-after-completion",
    *FUTURE_CHRONOLOGY_CASES,
]


def test_health_trust_rejects_inverted_success_chronology(tmp_path):
    now = dt.datetime.now(UTC)
    health = _invalid_chronology("start-after-completion", now)
    assert not supervisor_health.is_valid(health)
    path = tmp_path / "start-after-completion.json"
    path.write_text(json.dumps(health), encoding="utf-8")
    path.chmod(0o600)
    assert supervisor_health.load_trusted(path) is None


@pytest.mark.parametrize(
    ("successful_start", "successful_completion"),
    [(None, "completion"), ("start", None)],
    ids=["completion-without-start", "start-without-completion"],
)
def test_health_trust_rejects_incomplete_success_timestamp_pair(
    tmp_path, successful_start, successful_completion
):
    now = dt.datetime.now(UTC)
    health = _fresh_health(
        now,
        last_successful_reconcile_started_at=(
            None
            if successful_start is None
            else (now - dt.timedelta(seconds=10)).isoformat()
        ),
        last_successful_reconcile_at=(
            None
            if successful_completion is None
            else (now - dt.timedelta(seconds=5)).isoformat()
        ),
    )
    assert not supervisor_health.is_valid(health)
    path = tmp_path / f"{successful_start}-{successful_completion}.json"
    path.write_text(json.dumps(health), encoding="utf-8")
    path.chmod(0o600)
    assert supervisor_health.load_trusted(path) is None


@pytest.mark.parametrize("name", FUTURE_CHRONOLOGY_CASES)
def test_health_freshness_rejects_future_success_chronology(tmp_path, name):
    now = dt.datetime.now(UTC)
    health = _invalid_chronology(name, now)
    assert supervisor_health.is_valid(health)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(health), encoding="utf-8")
    path.chmod(0o600)
    assert supervisor_health.load_trusted(path) == health
    assert not supervisor_health.is_fresh_successful(health, now, 120)


def test_health_trust_accepts_fresh_ordered_success_chronology(tmp_path):
    now = dt.datetime.now(UTC)
    chronological = _fresh_health(now)
    assert supervisor_health.is_valid(chronological)
    path = tmp_path / "chronological.json"
    path.write_text(json.dumps(chronological), encoding="utf-8")
    path.chmod(0o600)
    assert supervisor_health.load_trusted(path) == chronological


@pytest.mark.parametrize("name", CHRONOLOGY_CASES)
def test_pure_pause_rejects_inverted_and_future_health(name):
    now = dt.datetime.now(UTC)
    execution = _live_execution("exec-chronology-1", now)
    wake = _live_wake(execution, now)
    assert oracle.stop_policy.execution_stop_decision(
        "in_progress",
        oracle._ledger(execution),
        _invalid_chronology(name, now),
        [wake],
        now,
    ) == ("block", "supervisor_health_unresolved")


def test_pure_pause_accepts_fresh_ordered_health():
    now = dt.datetime.now(UTC)
    execution = _live_execution("exec-chronology-1", now)
    wake = _live_wake(execution, now)
    assert oracle.stop_policy.execution_stop_decision(
        "in_progress", oracle._ledger(execution), _fresh_health(now), [wake], now
    ) == ("allow", "delegated_execution_bounded_pause")


@pytest.mark.parametrize("name", CHRONOLOGY_CASES)
def test_public_pause_rejects_inverted_and_future_health(
    monkeypatch, capsys, tmp_path, name
):
    now = dt.datetime.now(UTC)
    execution = _live_execution("exec-chronology-1", now)
    wake = _live_wake(execution, now)
    output = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=oracle._ledger(execution),
        scheduled=[wake],
        health=_invalid_chronology(name, now),
        refresh_health=False,
    )
    assert "supervisor_health_unresolved" in output


def test_public_pause_accepts_fresh_ordered_health(monkeypatch, capsys, tmp_path):
    now = dt.datetime.now(UTC)
    execution = _live_execution("exec-chronology-1", now)
    wake = _live_wake(execution, now)
    positive = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="in_progress",
        ledger=oracle._ledger(execution),
        scheduled=[wake],
        health=_fresh_health(now),
        refresh_health=False,
    )
    assert positive == ""
