"""Behavioral oracle for the level-triggered delegated-execution supervisor.

Health is evidence of a complete useful pass, not process life. Durable ledgers,
literal deadlines, canonical Beads records, and spawn-boundary observations are
the independent source of truth.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger as ledger_api  # noqa: E402
import execution_store  # noqa: E402
import execution_supervisor as supervisor  # noqa: E402

SESSION = "parent-supervisor-7"
ROOT_BEAD = "escapement-e3ai"
CHILD_BEAD = "escapement-e3ai.6"
EXECUTION = "exec-supervisor-alpha"
NOW = dt.datetime(2026, 8, 9, 20, 16, tzinfo=dt.timezone.utc)
OLD_HEALTH = {
    "reconcile_started_at": "2026-08-09T19:58:00Z",
    "last_successful_reconcile_at": "2026-08-09T19:59:00Z",
    "completed_generation": 7,
    "installation_id": "installed-supervisor-alpha",
    "counts": {"successful_passes": 7, "threads": 2, "recoveries": 3},
}


def at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def health_path(threads_root: pathlib.Path) -> pathlib.Path:
    return threads_root.parent / "supervisor-health.json"


def write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def registered(
    session_id: str = SESSION,
    *,
    host: str = "claude",
    execution_id: str = EXECUTION,
    agent_name: str = "supervised-worker",
    bead_id: str = CHILD_BEAD,
) -> dict:
    ledger = ledger_api.new_ledger(session_id)
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": session_id,
            "bead_id": bead_id,
            "execution_id": execution_id,
            "host": host,
            "agent_name": agent_name,
            "dispatch_tool_use_id": f"toolu-{execution_id}",
            "watchdog_id": f"watch-{execution_id}",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-09T20:00:00Z"),
    )
    return ledger


def running(*, polled_at: dt.datetime | None = None) -> dict:
    ledger = registered()
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_bound",
            "parent_session_id": SESSION,
            "execution_id": EXECUTION,
            "attempt": 1,
            "generation": 1,
            "native_child_id": "native-supervisor-alpha",
        },
        at("2026-08-09T20:00:04Z"),
    )
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_started",
            "parent_session_id": SESSION,
            "execution_id": EXECUTION,
            "attempt": 1,
            "generation": 1,
            "native_child_id": "native-supervisor-alpha",
        },
        at("2026-08-09T20:00:05Z"),
    )
    if polled_at is not None:
        ledger_api.apply_event(
            ledger,
            {
                "kind": "status_polled",
                "parent_session_id": SESSION,
                "execution_id": EXECUTION,
                "attempt": 1,
                "generation": 1,
            },
            polled_at,
        )
    return ledger


def write_thread(threads_root: pathlib.Path, name: str, ledger: dict) -> pathlib.Path:
    thread_dir = threads_root / name
    write_json(thread_dir / "executions.json", ledger)
    write_json(
        thread_dir / "session_mode.json",
        {
            "mode": "task",
            "repo_cwd": str(thread_dir),
            "task_id": CHILD_BEAD,
            "parent_id": ROOT_BEAD,
            "session_id": name,
        },
    )
    return thread_dir


def run_bd_recorder(parent_status: str = "in_progress"):
    calls: list[list[str]] = []

    def run_bd(args: list[str]):
        calls.append(args)
        if args == ["show", CHILD_BEAD]:
            return [{"id": CHILD_BEAD, "status": "in_progress", "parent": ROOT_BEAD}]
        if args == ["show", ROOT_BEAD]:
            return [{"id": ROOT_BEAD, "status": parent_status}]
        return None

    return run_bd, calls


def no_native_status(_execution: dict):
    return None


def assert_authoritative_health_unchanged(observed: dict) -> None:
    assert observed["reconcile_started_at"] != OLD_HEALTH["reconcile_started_at"]
    for field in (
        "last_successful_reconcile_at",
        "completed_generation",
        "installation_id",
        "counts",
    ):
        assert observed[field] == OLD_HEALTH[field]


@pytest.mark.parametrize("phase", ["after_plan", "after_claim", "after_spawn"])
def test_failed_action_pass_never_refreshes_authoritative_health(
    tmp_path, phase
) -> None:
    threads_root = tmp_path / "harness" / "threads"
    write_thread(threads_root, SESSION, registered())
    write_json(health_path(threads_root), OLD_HEALTH)
    run_bd, _calls = run_bd_recorder()
    spawned: list[dict] = []

    def phase_hook(observed_phase: str, _context: dict) -> None:
        if observed_phase == phase:
            raise RuntimeError(f"injected {phase} failure")

    def spawn(descriptor: dict) -> None:
        spawned.append(descriptor)

    with pytest.raises(RuntimeError, match="injected"):
        supervisor.reconcile_all(
            threads_root,
            at("2026-08-09T20:03:00Z"),
            "supervisor-a",
            spawn,
            native_status=no_native_status,
            run_bd=run_bd,
            phase_hook=phase_hook,
        )

    assert_authoritative_health_unchanged(
        json.loads(health_path(threads_root).read_text())
    )
    if phase == "after_spawn":
        assert len(spawned) == 1


@pytest.mark.parametrize(
    ("good_name", "bad_name"),
    [("a-good", "z-bad"), ("z-good", "a-bad")],
)
def test_one_bad_thread_invalidates_the_whole_root_pass(
    tmp_path, good_name, bad_name
) -> None:
    threads_root = tmp_path / "harness" / "threads"
    write_thread(threads_root, good_name, ledger_api.new_ledger(good_name))
    bad = threads_root / bad_name / "executions.json"
    write_json(bad, {"not": "an execution ledger"})
    write_json(health_path(threads_root), OLD_HEALTH)

    result = supervisor.reconcile_all(
        threads_root,
        NOW,
        "supervisor-a",
        lambda _descriptor: None,
        native_status=no_native_status,
        run_bd=lambda _args: [],
    )

    assert result["status"] == "unresolved"
    assert bad_name in result["unresolved_threads"]
    assert_authoritative_health_unchanged(
        json.loads(health_path(threads_root).read_text())
    )


def test_scheduled_inspection_failure_invalidates_the_whole_pass(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    write_thread(threads_root, SESSION, ledger_api.new_ledger(SESSION))
    write_json(health_path(threads_root), OLD_HEALTH)

    def fail_scheduled() -> dict:
        raise OSError("scheduled state unreadable")

    with pytest.raises(OSError, match="scheduled"):
        supervisor.reconcile_all(
            threads_root,
            NOW,
            "supervisor-a",
            lambda _descriptor: None,
            native_status=no_native_status,
            run_bd=lambda _args: [],
            inspect_scheduled=fail_scheduled,
        )

    assert_authoritative_health_unchanged(
        json.loads(health_path(threads_root).read_text())
    )


def test_missing_threads_root_is_unresolved_not_a_healthy_empty_scan(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads-missing"

    result = supervisor.reconcile_all(
        threads_root,
        NOW,
        "supervisor-a",
        lambda _descriptor: None,
        native_status=no_native_status,
        run_bd=lambda _args: [],
    )

    assert result["status"] == "unresolved"
    observed = json.loads(health_path(threads_root).read_text())
    assert observed["reconcile_started_at"] == "2026-08-09T20:16:00Z"
    assert observed["last_successful_reconcile_at"] is None
    assert observed["completed_generation"] == 0
    assert observed["counts"]["successful_passes"] == 0


def test_existing_empty_threads_root_is_a_complete_noop_pass(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    threads_root.mkdir(parents=True)
    spawned: list[dict] = []

    result = supervisor.reconcile_all(
        threads_root,
        NOW,
        "supervisor-a",
        spawned.append,
        native_status=no_native_status,
        run_bd=lambda _args: [],
    )

    assert result["status"] == "ok"
    assert spawned == []
    observed = json.loads(health_path(threads_root).read_text())
    assert observed["last_successful_reconcile_at"] == "2026-08-09T20:16:00Z"
    assert observed["completed_generation"] == 1
    assert observed["counts"]["successful_passes"] == 1
    assert observed["counts"]["threads"] == 0


def test_complete_noop_and_action_pass_each_advance_health_exactly_once(
    tmp_path,
) -> None:
    threads_root = tmp_path / "harness" / "threads"
    thread_dir = write_thread(threads_root, SESSION, ledger_api.new_ledger(SESSION))
    write_json(health_path(threads_root), OLD_HEALTH)
    run_bd, _calls = run_bd_recorder()
    spawned: list[dict] = []

    first = supervisor.reconcile_all(
        threads_root,
        NOW,
        "supervisor-a",
        spawned.append,
        native_status=no_native_status,
        run_bd=run_bd,
    )
    first_health = json.loads(health_path(threads_root).read_text())
    assert first["status"] == "ok"
    assert first_health["completed_generation"] == 8
    assert spawned == []

    write_json(thread_dir / "executions.json", registered())
    second = supervisor.reconcile_all(
        threads_root,
        at("2026-08-09T20:03:00Z"),
        "supervisor-a",
        spawned.append,
        native_status=no_native_status,
        run_bd=run_bd,
    )
    second_health = json.loads(health_path(threads_root).read_text())
    assert second["status"] == "ok"
    assert second_health["completed_generation"] == 9
    assert second_health["installation_id"] == OLD_HEALTH["installation_id"]
    assert len(spawned) == 1


def test_claim_is_durable_before_spawn_and_crash_window_is_recovered(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    thread_dir = write_thread(threads_root, SESSION, registered())
    run_bd, _calls = run_bd_recorder()
    observed_claims: list[dict] = []

    def crash_after_claim(phase: str, _context: dict) -> None:
        if phase != "after_claim":
            return
        durable = execution_store.load_trusted(thread_dir / "executions.json", SESSION)
        assert durable is not None
        observed_claims.append(
            copy.deepcopy(durable["executions"][0]["recovery_claim"])
        )
        raise RuntimeError("crash after durable claim")

    with pytest.raises(RuntimeError, match="durable claim"):
        supervisor.reconcile_all(
            threads_root,
            at("2026-08-09T20:03:00Z"),
            "supervisor-a",
            lambda _descriptor: pytest.fail("spawn ran before crash"),
            native_status=no_native_status,
            run_bd=run_bd,
            phase_hook=crash_after_claim,
        )
    assert observed_claims[0]["generation"] == 1

    immediate: list[dict] = []
    supervisor.reconcile_all(
        threads_root,
        at("2026-08-09T20:03:10Z"),
        "supervisor-b",
        immediate.append,
        native_status=no_native_status,
        run_bd=run_bd,
    )
    assert immediate == []

    recovered: list[dict] = []
    supervisor.reconcile_all(
        threads_root,
        at("2026-08-09T20:04:01Z"),
        "supervisor-b",
        recovered.append,
        native_status=no_native_status,
        run_bd=run_bd,
    )
    durable = execution_store.load_trusted(thread_dir / "executions.json", SESSION)
    assert durable is not None
    assert len(recovered) == 1
    assert durable["executions"][0]["generation"] == 2
    assert recovered[0]["generation"] == 2


def test_two_public_reconcilers_contend_for_one_recovery_spawn(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    thread_dir = write_thread(threads_root, SESSION, registered())
    run_bd, _calls = run_bd_recorder()
    barrier = threading.Barrier(2)
    spawned: list[dict] = []
    spawn_lock = threading.Lock()

    def phase_hook(phase: str, _context: dict) -> None:
        if phase == "before_claim":
            barrier.wait(timeout=2)

    def spawn(descriptor: dict) -> None:
        with spawn_lock:
            spawned.append(descriptor)

    def reconcile(owner: str) -> dict:
        return supervisor.reconcile_all(
            threads_root,
            at("2026-08-09T20:03:00Z"),
            owner,
            spawn,
            native_status=no_native_status,
            run_bd=run_bd,
            phase_hook=phase_hook,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reconcile, ["supervisor-a", "supervisor-b"]))

    durable = execution_store.load_trusted(thread_dir / "executions.json", SESSION)
    assert durable is not None
    assert all(result["status"] == "ok" for result in results)
    assert len(spawned) == 1
    assert durable["executions"][0]["recovery_claim"]["generation"] == 1


def test_recovery_claim_preserves_a_concurrent_semantic_ledger_write(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    thread_dir = write_thread(threads_root, SESSION, registered())
    ledger_path = thread_dir / "executions.json"
    run_bd, _calls = run_bd_recorder()
    wrote = False

    def phase_hook(phase: str, _context: dict) -> None:
        nonlocal wrote
        if phase != "before_claim" or wrote:
            return
        wrote = True

        def concurrent_write(current: dict) -> dict:
            current["incidents"].append(
                {
                    "type": "concurrent_semantic_write",
                    "marker": "must-survive-recovery-claim",
                }
            )
            return current

        execution_store.mutate_atomic(ledger_path, concurrent_write)

    spawned: list[dict] = []
    supervisor.reconcile_all(
        threads_root,
        at("2026-08-09T20:03:00Z"),
        "supervisor-a",
        spawned.append,
        native_status=no_native_status,
        run_bd=run_bd,
        phase_hook=phase_hook,
    )

    durable = execution_store.load_trusted(ledger_path, SESSION)
    assert durable is not None
    assert len(spawned) == 1
    assert durable["executions"][0]["recovery_claim"] is not None
    assert {
        "type": "concurrent_semantic_write",
        "marker": "must-survive-recovery-claim",
    } in durable["incidents"]


def test_spawn_failure_leaves_bounded_retryable_claim(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    thread_dir = write_thread(threads_root, SESSION, registered())
    run_bd, _calls = run_bd_recorder()

    def fail_spawn(_descriptor: dict) -> None:
        raise OSError("host CLI unavailable")

    result = supervisor.reconcile_all(
        threads_root,
        at("2026-08-09T20:03:00Z"),
        "supervisor-a",
        fail_spawn,
        native_status=no_native_status,
        run_bd=run_bd,
    )
    durable = execution_store.load_trusted(thread_dir / "executions.json", SESSION)
    assert durable is not None
    assert result["status"] == "unresolved"
    assert durable["executions"][0]["recovery_claim"] is not None
    assert durable["executions"][0]["generation"] == 1
    assert not any(
        item.get("type") == "recovery_budget_exhausted" for item in durable["incidents"]
    )


def test_recovery_budget_exhaustion_creates_one_durable_escalation(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    ledger = registered()
    item = ledger["executions"][0]
    item["reconcile_due"] = "start"
    item["recovery_count"] = 3
    item["recovery_claim"] = {
        "owner": "expired-owner",
        "execution_id": EXECUTION,
        "attempt": 1,
        "generation": 1,
        "claimed_at": "2026-08-09T20:00:00Z",
        "expires_at": "2026-08-09T20:01:00Z",
    }
    thread_dir = write_thread(threads_root, SESSION, ledger)
    run_bd, _calls = run_bd_recorder()
    spawned: list[dict] = []

    for owner in ("supervisor-a", "supervisor-b"):
        supervisor.reconcile_all(
            threads_root,
            NOW,
            owner,
            spawned.append,
            native_status=no_native_status,
            run_bd=run_bd,
            recovery_budget=3,
        )

    durable = execution_store.load_trusted(thread_dir / "executions.json", SESSION)
    assert durable is not None
    escalations = [
        incident
        for incident in durable["incidents"]
        if incident.get("type") == "recovery_budget_exhausted"
    ]
    assert spawned == []
    assert len(escalations) == 1
    assert escalations[0]["execution_id"] == EXECUTION
    assert escalations[0]["attempt"] == 1
    assert escalations[0]["generation"] == 1


def test_recovery_budget_allows_the_last_bounded_takeover(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    ledger = registered()
    item = ledger["executions"][0]
    item["reconcile_due"] = "start"
    item["recovery_count"] = 2
    item["recovery_claim"] = {
        "owner": "expired-owner",
        "execution_id": EXECUTION,
        "attempt": 1,
        "generation": 1,
        "claimed_at": "2026-08-09T20:00:00Z",
        "expires_at": "2026-08-09T20:01:00Z",
    }
    thread_dir = write_thread(threads_root, SESSION, ledger)
    run_bd, _calls = run_bd_recorder()
    spawned: list[dict] = []

    supervisor.reconcile_all(
        threads_root,
        NOW,
        "supervisor-a",
        spawned.append,
        native_status=no_native_status,
        run_bd=run_bd,
        recovery_budget=3,
    )

    durable = execution_store.load_trusted(thread_dir / "executions.json", SESSION)
    assert durable is not None
    assert len(spawned) == 1
    assert durable["executions"][0]["recovery_count"] == 3
    assert not any(
        incident.get("type") == "recovery_budget_exhausted"
        for incident in durable["incidents"]
    )


def test_concurrent_budget_exhaustion_emits_one_escalation(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    ledger = registered()
    item = ledger["executions"][0]
    item["reconcile_due"] = "start"
    item["recovery_count"] = 3
    item["recovery_claim"] = {
        "owner": "expired-owner",
        "execution_id": EXECUTION,
        "attempt": 1,
        "generation": 1,
        "claimed_at": "2026-08-09T20:00:00Z",
        "expires_at": "2026-08-09T20:01:00Z",
    }
    thread_dir = write_thread(threads_root, SESSION, ledger)
    barrier = threading.Barrier(2)
    spawned: list[dict] = []

    def phase_hook(phase: str, _context: dict) -> None:
        if phase == "before_escalation":
            barrier.wait(timeout=2)

    def reconcile(owner: str) -> dict:
        run_bd, _calls = run_bd_recorder()
        return supervisor.reconcile_all(
            threads_root,
            NOW,
            owner,
            spawned.append,
            native_status=no_native_status,
            run_bd=run_bd,
            phase_hook=phase_hook,
            recovery_budget=3,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(reconcile, ["supervisor-a", "supervisor-b"]))

    durable = execution_store.load_trusted(thread_dir / "executions.json", SESSION)
    assert durable is not None
    assert spawned == []
    assert (
        len(
            [
                incident
                for incident in durable["incidents"]
                if incident.get("type") == "recovery_budget_exhausted"
            ]
        )
        == 1
    )


def test_fresh_mtime_and_poll_timestamp_do_not_hide_initial_idle_breach(
    tmp_path,
) -> None:
    threads_root = tmp_path / "harness" / "threads"
    ledger = running(polled_at=NOW)
    path = write_thread(threads_root, SESSION, ledger) / "executions.json"
    os.utime(path, None)
    run_bd, _calls = run_bd_recorder()
    spawned: list[dict] = []

    supervisor.reconcile_all(
        threads_root,
        NOW,
        "supervisor-a",
        spawned.append,
        native_status=no_native_status,
        run_bd=run_bd,
    )

    durable = execution_store.load_trusted(path, SESSION)
    assert durable is not None
    assert durable["executions"][0]["reconcile_due"] == "idle"
    assert len(spawned) == 1


def test_completed_activity_is_the_positive_idle_renewal_control(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    ledger = running()
    ledger_api.apply_event(
        ledger,
        {
            "kind": "activity_completed",
            "parent_session_id": SESSION,
            "execution_id": EXECUTION,
            "attempt": 1,
            "generation": 1,
            "native_child_id": "native-supervisor-alpha",
            "activity_kind": "tool_completed",
        },
        NOW,
    )
    path = write_thread(threads_root, SESSION, ledger) / "executions.json"
    run_bd, _calls = run_bd_recorder()
    spawned: list[dict] = []

    supervisor.reconcile_all(
        threads_root,
        NOW,
        "supervisor-a",
        spawned.append,
        native_status=no_native_status,
        run_bd=run_bd,
    )

    durable = execution_store.load_trusted(path, SESSION)
    assert durable is not None
    assert durable["executions"][0]["reconcile_due"] is None
    assert durable["executions"][0]["idle_deadline"] == "2026-08-09T20:31:00Z"
    assert spawned == []


def test_late_generation_terminal_is_incident_only_after_takeover(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    ledger = registered()
    ledger_api.reconcile_deadlines(ledger, at("2026-08-09T20:03:00Z"))
    ledger_api.claim_recovery(
        ledger, EXECUTION, at("2026-08-09T20:03:00Z"), "old-owner", 30
    )
    ledger_api.claim_recovery(
        ledger, EXECUTION, at("2026-08-09T20:03:31Z"), "new-owner", 30
    )
    active_before = copy.deepcopy(ledger["executions"][0])
    path = write_thread(threads_root, SESSION, ledger) / "executions.json"
    run_bd, _calls = run_bd_recorder()
    late = {
        "kind": "child_terminal",
        "parent_session_id": SESSION,
        "execution_id": EXECUTION,
        "attempt": 1,
        "generation": 1,
        "native_child_id": "native-generation-one",
        "terminal_event_id": "late-terminal-generation-one",
        "terminal_reason": "completed",
        "result_digest": "sha256:late-generation-one",
        "host_event_id": "claude:terminal:late-terminal-generation-one",
    }

    supervisor.reconcile_all(
        threads_root,
        at("2026-08-09T20:03:40Z"),
        "supervisor-c",
        lambda _descriptor: None,
        native_status=lambda _execution: late,
        run_bd=run_bd,
    )

    durable = execution_store.load_trusted(path, SESSION)
    assert durable is not None
    active = durable["executions"][0]
    assert active == active_before
    assert any(
        incident.get("type") == "old_generation_event"
        and incident.get("event_id") == "late-terminal-generation-one"
        for incident in durable["incidents"]
    )


def test_current_terminal_evidence_never_closes_beads(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    ledger = running()
    path = write_thread(threads_root, SESSION, ledger) / "executions.json"
    run_bd, calls = run_bd_recorder()
    terminal = {
        "kind": "child_terminal",
        "parent_session_id": SESSION,
        "execution_id": EXECUTION,
        "attempt": 1,
        "generation": 1,
        "native_child_id": "native-supervisor-alpha",
        "terminal_event_id": "terminal-needs-verification",
        "terminal_reason": "completed",
        "result_digest": "sha256:unverified-result",
        "host_event_id": "claude:terminal:needs-verification",
    }

    supervisor.reconcile_all(
        threads_root,
        NOW,
        "supervisor-a",
        lambda _descriptor: None,
        native_status=lambda _execution: terminal,
        run_bd=run_bd,
    )

    durable = execution_store.load_trusted(path, SESSION)
    assert durable is not None
    item = durable["executions"][0]
    assert item["state"] == "terminal"
    assert item["result_application"]["state"] == "unapplied"
    assert all(call[:1] == ["show"] for call in calls)


def test_fresh_process_restart_recovers_once_with_canonical_context(tmp_path) -> None:
    harness_root = tmp_path / "harness"
    threads_root = harness_root / "threads"
    session_id = "restart-session-91"
    child_bead = "escapement-child-canonical-17"
    parent_bead = "escapement-parent-canonical-42"
    execution_id = "exec-restart-public-91"
    ledger = registered(
        session_id,
        execution_id=execution_id,
        bead_id=child_bead,
        agent_name="restart-worker",
    )
    native_child_id = "native-restart-public-91"
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_bound",
            "parent_session_id": session_id,
            "execution_id": execution_id,
            "attempt": 1,
            "generation": 1,
            "native_child_id": native_child_id,
        },
        at("2026-08-09T20:00:04Z"),
    )
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_started",
            "parent_session_id": session_id,
            "execution_id": execution_id,
            "attempt": 1,
            "generation": 1,
            "native_child_id": native_child_id,
        },
        at("2026-08-09T20:00:05Z"),
    )
    item = ledger["executions"][0]
    item["reconcile_due"] = "idle"
    ledger_path = write_thread(threads_root, session_id, ledger) / "executions.json"

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    record_path = tmp_path / "spawn-record.jsonl"
    bd_record_path = tmp_path / "bd-record.jsonl"
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "path = pathlib.Path(os.environ['SPAWN_RECORD'])\n"
        "with path.open('a') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        "with pathlib.Path(os.environ['BD_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps(args) + '\\n')\n"
        f"if args == ['show', {child_bead!r}]:\n"
        f"    print(json.dumps([{{'id': {child_bead!r}, 'status': "
        f"'in_progress', 'parent': {parent_bead!r}}}]))\n"
        f"elif args == ['show', {parent_bead!r}]:\n"
        f"    print(json.dumps([{{'id': {parent_bead!r}, 'status': "
        "'in_progress'}]))\n"
        "else:\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SPAWN_RECORD"] = str(record_path)
    env["BD_RECORD"] = str(bd_record_path)
    command = [
        sys.executable,
        str(BIN / "execution_supervisor.py"),
        "--threads-root",
        str(threads_root),
        "--now",
        "2026-08-09T20:03:00Z",
        "--owner",
        "fresh-process-supervisor",
    ]

    first = subprocess.run(command, capture_output=True, text=True, env=env)
    assert first.returncode == 0, first.stderr
    deadline = time.monotonic() + 3
    while not record_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert record_path.exists()
    first_records = [json.loads(line) for line in record_path.read_text().splitlines()]
    assert len(first_records) == 1

    second = subprocess.run(command, capture_output=True, text=True, env=env)
    assert second.returncode == 0, second.stderr
    time.sleep(0.05)
    records = [json.loads(line) for line in record_path.read_text().splitlines()]
    assert records == first_records

    argv = records[0]
    assert argv == ["--resume", session_id, "-p", argv[3]]
    prompt = argv[3]
    for literal in (
        parent_bead,
        child_bead,
        execution_id,
        "attempt 1",
        "generation 1",
        "idle deadline",
        item["idle_deadline"],
        native_child_id,
        "native status unknown",
        str(ledger_path),
    ):
        assert literal in prompt
    durable = execution_store.load_trusted(ledger_path, session_id)
    assert durable is not None
    assert durable["executions"][0]["recovery_claim"]["generation"] == 1
    bd_calls = [json.loads(line) for line in bd_record_path.read_text().splitlines()]
    assert bd_calls
    assert all(call[:1] == ["show"] for call in bd_calls)
    assert ["show", child_bead] in bd_calls
    assert ["show", parent_bead] in bd_calls

    def record_terminal(current: dict) -> dict:
        return ledger_api.apply_event(
            current,
            {
                "kind": "child_terminal",
                "parent_session_id": session_id,
                "execution_id": execution_id,
                "attempt": 1,
                "generation": 1,
                "native_child_id": native_child_id,
                "terminal_event_id": "public-terminal-needs-verification",
                "terminal_reason": "completed",
                "result_digest": "sha256:public-unverified-result",
                "host_event_id": "claude:terminal:public-needs-verification",
            },
            at("2026-08-09T20:04:00Z"),
        )

    execution_store.mutate_atomic(ledger_path, record_terminal)
    before_terminal_pass_records = list(records)
    terminal_pass = subprocess.run(
        [*command[:-3], "2026-08-09T20:04:01Z", *command[-2:]],
        capture_output=True,
        text=True,
        env=env,
    )
    assert terminal_pass.returncode == 0, terminal_pass.stderr
    time.sleep(0.05)
    assert [json.loads(line) for line in record_path.read_text().splitlines()] == (
        before_terminal_pass_records
    )
    terminal_ledger = execution_store.load_trusted(ledger_path, session_id)
    assert terminal_ledger is not None
    terminal_item = terminal_ledger["executions"][0]
    assert terminal_item["state"] == "terminal"
    assert terminal_item["terminal_event_id"] == "public-terminal-needs-verification"
    assert terminal_item["result_application"]["state"] == "unapplied"
    terminal_bd_calls = [
        json.loads(line) for line in bd_record_path.read_text().splitlines()
    ]
    assert terminal_bd_calls
    assert all(call[:1] == ["show"] for call in terminal_bd_calls)
