#!/usr/bin/env python3
"""Adversarial public controls for Task 5 production-review findings."""

from __future__ import annotations

import datetime as dt
import fcntl
import io
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from harness.tests import test_execution_stop_gate as oracle

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
UTC = dt.timezone.utc


def _bridge_payload() -> dict:
    return {
        "session_id": oracle.SESSION,
        "tool_name": "ScheduleWakeup",
        "tool_input": {"delaySeconds": 600, "prompt": "resume delegated work"},
        "tool_response": {},
    }


def _write_bridge_state(root: pathlib.Path, ledger: object) -> pathlib.Path:
    thread_dir = root / "threads" / oracle.SESSION
    thread_dir.mkdir(parents=True)
    ledger_path = thread_dir / "executions.json"
    ledger_path.write_text(
        ledger if isinstance(ledger, str) else json.dumps(ledger), encoding="utf-8"
    )
    ledger_path.chmod(0o600)
    health_path = root / "supervisor-health.json"
    health_path.write_text(
        json.dumps(oracle._health(completed_generation=11)), encoding="utf-8"
    )
    health_path.chmod(0o600)
    return thread_dir


def _run_registered_bridge(root: pathlib.Path, *, timeout: float = 5.0):
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(root)
    env.pop("HARNESS_THREAD_DIR", None)
    return subprocess.run(
        [sys.executable, str(BIN / "schedule_wakeup_bridge.py")],
        input=json.dumps(_bridge_payload()),
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
    )


def test_health_pass_must_start_after_managed_wake_is_durable(
    monkeypatch, capsys, tmp_path
):
    """Several passes completed after registration cannot help if they began before."""
    bridge_root = tmp_path / "bridge"
    execution = oracle._execution("exec-research-1")
    thread_dir = _write_bridge_state(bridge_root, oracle._ledger(execution))
    health_path = bridge_root / "supervisor-health.json"
    live_now = dt.datetime.now(UTC)
    registered_at = live_now - dt.timedelta(seconds=5)
    pre_registration_start = registered_at - dt.timedelta(seconds=5)
    post_registration_completion = registered_at + dt.timedelta(seconds=2)
    qualifying_start = registered_at + dt.timedelta(seconds=3)
    qualifying_completion = registered_at + dt.timedelta(seconds=4)
    initial_generation = 41
    in_flight_completed_generation = 45
    qualifying_generation = 46
    health_path.write_text(
        json.dumps(
            oracle._health(
                completed_generation=initial_generation,
                reconcile_started_at=(
                    registered_at - dt.timedelta(seconds=20)
                ).isoformat(),
                last_successful_reconcile_started_at=(
                    registered_at - dt.timedelta(seconds=20)
                ).isoformat(),
                last_successful_reconcile_at=(
                    registered_at - dt.timedelta(seconds=10)
                ).isoformat(),
            )
        ),
        encoding="utf-8",
    )

    def sample_then_complete_pre_registration_passes(path):
        sampled = json.loads(path.read_text())
        health_path.write_text(
            json.dumps(
                oracle._health(
                    completed_generation=in_flight_completed_generation,
                    reconcile_started_at=qualifying_start.isoformat(),
                    last_successful_reconcile_started_at=(
                        pre_registration_start.isoformat()
                    ),
                    last_successful_reconcile_at=post_registration_completion.isoformat(),
                )
            ),
            encoding="utf-8",
        )
        health_path.chmod(0o600)
        return sampled

    monkeypatch.setattr(
        oracle.wake_bridge.supervisor_health,
        "load_trusted",
        sample_then_complete_pre_registration_passes,
    )
    monkeypatch.setattr(oracle.wake_bridge, "_now", lambda: registered_at)
    monkeypatch.setattr(
        oracle.wake_bridge.sys, "stdin", io.StringIO(json.dumps(_bridge_payload()))
    )
    monkeypatch.setenv("HARNESS_ROOT", str(bridge_root))
    assert oracle.wake_bridge.main([]) == 0

    persisted = json.loads((thread_dir / "scheduled.json").read_text())
    managed = persisted[0]
    assert managed["supervisor_generation"] == initial_generation
    persisted_registration = dt.datetime.fromisoformat(managed["registered_at"])
    assert persisted_registration == registered_at
    assert pre_registration_start < persisted_registration
    assert qualifying_start > persisted_registration
    assert post_registration_completion > persisted_registration
    blocked = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path / "consumer-in-flight",
        root_status="in_progress",
        ledger=oracle._ledger(execution),
        scheduled=persisted,
        health=oracle._health(
            completed_generation=in_flight_completed_generation,
            reconcile_started_at=qualifying_start.isoformat(),
            last_successful_reconcile_started_at=pre_registration_start.isoformat(),
            last_successful_reconcile_at=post_registration_completion.isoformat(),
        ),
        refresh_health=False,
    )
    assert "supervisor_health_unresolved" in blocked

    allowed = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path / "consumer-qualifying",
        root_status="in_progress",
        ledger=oracle._ledger(execution),
        scheduled=persisted,
        health=oracle._health(
            completed_generation=qualifying_generation,
            reconcile_started_at=qualifying_start.isoformat(),
            last_successful_reconcile_started_at=qualifying_start.isoformat(),
            last_successful_reconcile_at=qualifying_completion.isoformat(),
        ),
        refresh_health=False,
    )
    assert allowed == ""


def test_successful_pass_start_is_part_of_the_trusted_health_wire_contract():
    health = oracle._health()
    assert oracle.wake_bridge.supervisor_health.is_valid(health)
    health.pop("last_successful_reconcile_started_at")
    assert not oracle.wake_bridge.supervisor_health.is_valid(health)


@pytest.mark.parametrize(
    "invalid_kind", ["missing", "malformed", "world-writable", "symlink"]
)
def test_valid_execution_ledger_with_invalid_health_mints_no_bridge_proof(
    tmp_path, invalid_kind
):
    root = tmp_path / f"invalid-health-{invalid_kind}"
    thread_dir = _write_bridge_state(
        root, oracle._ledger(oracle._execution("exec-research-1"))
    )
    health_path = root / "supervisor-health.json"
    if invalid_kind == "missing":
        health_path.unlink()
    elif invalid_kind == "malformed":
        health_path.write_text("{malformed", encoding="utf-8")
    elif invalid_kind == "world-writable":
        health_path.chmod(0o666)
    else:
        target = root / "redirected-health.json"
        target.write_text(
            json.dumps(oracle._health(completed_generation=11)), encoding="utf-8"
        )
        target.chmod(0o600)
        health_path.unlink()
        health_path.symlink_to(target)

    result = _run_registered_bridge(root)
    assert result.returncode == 0, result.stderr
    schedule_path = thread_dir / "scheduled.json"
    entries = json.loads(schedule_path.read_text()) if schedule_path.exists() else []
    assert not {
        entry.get("created_by") for entry in entries if isinstance(entry, dict)
    } & {"ScheduleWakeup", "execution-supervisor"}


@pytest.mark.parametrize(
    "invalid_kind",
    ["malformed", "world-writable", "symlink"],
)
def test_existing_invalid_execution_ledger_mints_no_bridge_proof(
    tmp_path, invalid_kind
):
    root = tmp_path / invalid_kind
    execution = oracle._execution("exec-research-1")
    thread_dir = _write_bridge_state(root, oracle._ledger(execution))
    ledger_path = thread_dir / "executions.json"
    if invalid_kind == "malformed":
        ledger_path.write_text("{malformed", encoding="utf-8")
    elif invalid_kind == "world-writable":
        ledger_path.chmod(0o666)
    else:
        target = root / "redirected-executions.json"
        target.write_text(json.dumps(oracle._ledger(execution)), encoding="utf-8")
        target.chmod(0o600)
        ledger_path.unlink()
        ledger_path.symlink_to(target)

    result = _run_registered_bridge(root)
    assert result.returncode == 0, result.stderr
    schedule_path = thread_dir / "scheduled.json"
    entries = json.loads(schedule_path.read_text()) if schedule_path.exists() else []
    assert not {
        entry.get("created_by") for entry in entries if isinstance(entry, dict)
    } & {"ScheduleWakeup", "execution-supervisor"}


def test_nonzero_bd_with_plausible_closed_json_cannot_authorize_public_stop(
    monkeypatch, capsys, tmp_path
):
    """The oracle is the process result, not plausible bytes on failed stdout."""

    def failed_bd(fake_root, root_status, root_record_id=oracle.ROOT_BEAD):
        fakebin = fake_root / "bin"
        fakebin.mkdir()
        script = fakebin / "bd"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"print(json.dumps([{{'id': {root_record_id!r}, 'status': 'closed'}}]))\n"
            "raise SystemExit(17)\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return fakebin

    monkeypatch.setattr(oracle, "_write_fake_bd", failed_bd)
    execution = oracle._execution("exec-research-1", state="terminal")
    output = oracle._public_stop(
        monkeypatch,
        capsys,
        tmp_path,
        root_status="closed",
        ledger=oracle._ledger(execution),
        scheduled=[],
        health=None,
    )
    assert "parent_outcome_unresolved" in output


def _public_stop_with_session_mode(
    monkeypatch,
    capsys,
    tmp_path: pathlib.Path,
    *,
    invalid_kind: str,
    recent_user_message: str | None = None,
) -> str:
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / ".beads").mkdir()
    thread_dir = root / "threads" / oracle.SESSION
    thread_dir.mkdir(parents=True)
    mode = {
        "mode": "task",
        "session_id": oracle.SESSION,
        "repo_cwd": str(repo),
        "parent_id": oracle.ROOT_BEAD,
    }
    mode_path = thread_dir / "session_mode.json"
    if invalid_kind == "malformed":
        mode_path.write_text("{malformed", encoding="utf-8")
    elif invalid_kind == "world-writable":
        mode_path.write_text(json.dumps(mode), encoding="utf-8")
        mode_path.chmod(0o666)
    else:
        target = tmp_path / "redirected-session-mode.json"
        target.write_text(json.dumps(mode), encoding="utf-8")
        target.chmod(0o600)
        mode_path.symlink_to(target)
    ledger_path = thread_dir / "executions.json"
    ledger_path.write_text(
        json.dumps(
            oracle._ledger(oracle._execution("exec-research-1", state="terminal"))
        ),
        encoding="utf-8",
    )
    ledger_path.chmod(0o600)
    (thread_dir / "scheduled.json").write_text("[]", encoding="utf-8")

    fakebin = oracle._write_fake_bd(tmp_path, "closed")
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(oracle.stop_hook, "HARNESS_ROOT", root)
    monkeypatch.setattr(oracle.stop_hook, "INCIDENTS_LOG", root / "incidents.jsonl")
    monkeypatch.setattr(
        oracle.stop_hook.session_isolation, "write_checkout", lambda *args: None
    )
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
        oracle.stop_hook.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {"session_id": oracle.SESSION, "transcript_path": transcript_path}
            )
        ),
    )
    assert oracle.stop_hook.main() == 0
    return capsys.readouterr().out


@pytest.mark.parametrize(
    "invalid_kind",
    ["malformed", "world-writable", "symlink"],
)
def test_invalid_session_mode_fails_closed_at_public_stop(
    monkeypatch, capsys, tmp_path, invalid_kind
):
    output = _public_stop_with_session_mode(
        monkeypatch, capsys, tmp_path, invalid_kind=invalid_kind
    )
    assert '"decision": "block"' in output


@pytest.mark.parametrize(
    "invalid_kind",
    ["malformed", "world-writable", "symlink"],
)
def test_user_release_remains_unconditional_with_invalid_session_mode(
    monkeypatch, capsys, tmp_path, invalid_kind
):
    output = _public_stop_with_session_mode(
        monkeypatch,
        capsys,
        tmp_path,
        invalid_kind=invalid_kind,
        recent_user_message="stop",
    )
    assert output == ""


def test_registered_bridge_returns_promptly_when_schedule_lock_is_held(tmp_path):
    root = tmp_path / "contended"
    thread_dir = _write_bridge_state(
        root, oracle._ledger(oracle._execution("exec-research-1"))
    )
    lock_path = thread_dir / "scheduled.json.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.chmod(lock_path, 0o600)
    with os.fdopen(lock_fd, "a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        started = time.monotonic()
        try:
            result = _run_registered_bridge(root, timeout=1.0)
        except subprocess.TimeoutExpired:
            pytest.fail("registered bridge stalled behind the schedule lock")
        elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert elapsed < 1.0
    assert not (thread_dir / "scheduled.json").exists()


def test_managed_registration_clock_and_durable_write_share_one_lock(
    monkeypatch, tmp_path
):
    """No scanner may acquire after registered_at is sampled and before replace."""
    root = tmp_path / "registration-order"
    thread_dir = root / "threads" / oracle.SESSION
    thread_dir.mkdir(parents=True)
    schedule_path = thread_dir / "scheduled.json"
    lock_path = schedule_path.with_suffix(".json.lock")
    clock_base = dt.datetime.now(UTC)
    events: list[str] = []
    probes: list[dict] = []
    probe_code = (
        "import fcntl, os, pathlib, sys, time\n"
        "lock, ready, acquired, stop = map(pathlib.Path, sys.argv[1:])\n"
        "fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)\n"
        "with os.fdopen(fd, 'a+') as handle:\n"
        "    first = True\n"
        "    while not stop.exists():\n"
        "        try:\n"
        "            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "        except BlockingIOError:\n"
        "            if first:\n"
        "                ready.write_text('blocked')\n"
        "                first = False\n"
        "            time.sleep(0.001)\n"
        "        else:\n"
        "            acquired.write_text('acquired')\n"
        "            if first:\n"
        "                ready.write_text('acquired')\n"
        "            fcntl.flock(handle, fcntl.LOCK_UN)\n"
        "            break\n"
        "    if first:\n"
        "        ready.write_text('stopped')\n"
    )

    def registration_clock():
        index = len(probes)
        sample = clock_base + dt.timedelta(microseconds=index)
        ready_path = tmp_path / f"probe-{index}-ready"
        acquired_path = tmp_path / f"probe-{index}-acquired"
        stop_path = tmp_path / f"probe-{index}-stop"
        events.append(f"clock-{index}")
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                probe_code,
                str(lock_path),
                str(ready_path),
                str(acquired_path),
                str(stop_path),
            ]
        )
        record = {
            "index": index,
            "sample": sample,
            "ready": ready_path,
            "acquired": acquired_path,
            "stop": stop_path,
            "process": process,
        }
        probes.append(record)
        deadline = time.monotonic() + 1.0
        while not ready_path.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert ready_path.exists(), "lock probe did not reach its first acquisition"
        return sample

    real_write = oracle.wake_bridge.schedule_store.write_durable

    def observed_durable_write(path, entries):
        events.append("write")
        real_write(path, entries)
        for record in probes:
            record["stop"].touch()
        for record in probes:
            record["process"].wait(timeout=1.0)

    monkeypatch.setattr(oracle.wake_bridge, "_now", registration_clock)
    monkeypatch.setattr(
        oracle.wake_bridge.supervisor_health,
        "load_trusted",
        lambda _path: oracle._health(completed_generation=41),
    )
    monkeypatch.setattr(
        oracle.wake_bridge.schedule_store, "write_durable", observed_durable_write
    )
    try:
        entry = oracle.wake_bridge.persist_managed_wakeup(
            {
                "parent_session_id": oracle.SESSION,
                "watchdog_id": "watch-order",
                "execution_id": "exec-order",
                "attempt": 1,
                "generation": 1,
            },
            thread_dir,
            clock_base + dt.timedelta(minutes=5),
        )
    finally:
        for record in probes:
            record["stop"].touch()
            if record["process"].poll() is None:
                record["process"].wait(timeout=1.0)

    assert entry is not None
    registration_probe = next(
        record
        for record in probes
        if record["sample"].isoformat() == entry["registered_at"]
    )
    assert not registration_probe["acquired"].exists(), (
        "registration clock was sampled before the stable schedule lock, or the "
        "lock was released before durable replace"
    )
    assert events[-2:] == [f"clock-{registration_probe['index']}", "write"]
    assert json.loads(schedule_path.read_text()) == [entry]


@pytest.mark.parametrize("operation", ["mark_started", "mark_success"])
def test_legacy_partial_health_mutation_never_persists_consumer_invalid_state(
    tmp_path, operation
):
    path = tmp_path / "supervisor-health.json"
    legacy = oracle._health(completed_generation=11)
    legacy["counts"].pop("recoveries")
    path.write_text(json.dumps(legacy), encoding="utf-8")
    path.chmod(0o600)
    before = path.read_bytes()
    now = dt.datetime(2026, 8, 9, 20, 11, tzinfo=UTC)
    try:
        if operation == "mark_started":
            oracle.wake_bridge.supervisor_health.mark_started(path, now)
        else:
            oracle.wake_bridge.supervisor_health.mark_success(
                path, now, threads=2, recoveries=1
            )
    except Exception:  # fail-closed/no-mutation is an accepted compatibility policy
        assert path.read_bytes() == before
    else:
        persisted = json.loads(path.read_text())
        assert oracle.wake_bridge.supervisor_health.is_valid(persisted)
        assert oracle.wake_bridge.supervisor_health.load_trusted(path) == persisted


def test_complete_health_remains_valid_across_started_and_success_mutations(tmp_path):
    path = tmp_path / "supervisor-health.json"
    path.write_text(
        json.dumps(oracle._health(completed_generation=11)), encoding="utf-8"
    )
    path.chmod(0o600)
    started = oracle.wake_bridge.supervisor_health.mark_started(
        path, dt.datetime(2026, 8, 9, 20, 11, tzinfo=UTC)
    )
    assert oracle.wake_bridge.supervisor_health.is_valid(started)
    completed = oracle.wake_bridge.supervisor_health.mark_success(
        path,
        dt.datetime(2026, 8, 9, 20, 12, tzinfo=UTC),
        threads=2,
        recoveries=1,
    )
    assert oracle.wake_bridge.supervisor_health.load_trusted(path) == completed
    assert completed["completed_generation"] == 12
    assert (
        completed["last_successful_reconcile_started_at"]
        == started["reconcile_started_at"]
    )
    assert completed["counts"] == {
        "successful_passes": 13,
        "threads": 2,
        "recoveries": 1,
    }


def test_failed_later_pass_cannot_advance_successful_pass_start(tmp_path):
    path = tmp_path / "supervisor-health.json"
    health = oracle._health(completed_generation=45)
    successful_start = health["last_successful_reconcile_started_at"]
    successful_completion = health["last_successful_reconcile_at"]
    path.write_text(json.dumps(health), encoding="utf-8")
    path.chmod(0o600)
    later_start = dt.datetime(2026, 8, 9, 20, 11, tzinfo=UTC)

    started = oracle.wake_bridge.supervisor_health.mark_started(path, later_start)

    assert started["reconcile_started_at"] == "2026-08-09T20:11:00Z"
    assert started["completed_generation"] == 45
    assert started["last_successful_reconcile_started_at"] == successful_start
    assert started["last_successful_reconcile_at"] == successful_completion


def test_stop_hook_complexity_waiver_is_current_and_task5_wiring_stays_thin():
    source = (BIN / "stop_hook.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    waiver = next(
        (line for line in lines[:5] if "file-complexity-waiver:" in line), None
    )
    assert waiver is not None
    assert f"{len(lines)} lines" in waiver
    assert "delegated" in waiver.lower()
    assert "execution_stop_adapter.py" in waiver
    assert "only adds a one-line per-session isolation steer" not in waiver
    assert source.count("decide_task_mode") == 2
