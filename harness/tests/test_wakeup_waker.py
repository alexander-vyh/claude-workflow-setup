"""Tests for wakeup_waker.plan() — due-selection, prune-after-fire, cheap reschedule.

The prune-after-fire assertions are the direct regression guard for the observed
25× resume / 45× block storms: a one-shot wake must NOT survive in the schedule to
re-fire; only a not-ready poll is re-armed.
"""

import datetime as dt
import fcntl
import importlib.util
import json
import os
import pathlib
import shlex
import sys

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))


def _load_wakeup_waker():
    spec = importlib.util.spec_from_file_location(
        "wakeup_waker", BIN / "wakeup_waker.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load harness/bin/wakeup_waker.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ww = _load_wakeup_waker()

import execution_ledger as execution_ledger_api  # noqa: E402

NOW = dt.datetime(2026, 6, 4, 9, 0, 0, tzinfo=dt.timezone.utc)
PAST = (NOW - dt.timedelta(minutes=1)).isoformat()
FUTURE = (NOW + dt.timedelta(hours=1)).isoformat()
CLI_PAST = "2000-01-01T00:00:00+00:00"
CLI_FUTURE = "2999-01-01T00:00:00+00:00"


def _entry(**kw):
    base = {
        "wake_at": PAST,
        "prompt": "p",
        "thread_id": "T",
        "created_by": "x",
        "crash_count": 0,
    }
    base.update(kw)
    return base


def _runner(code):
    return lambda command: (code, "")


def _write_empty_execution_ledger(
    thread_dir: pathlib.Path, session_id: str
) -> pathlib.Path:
    path = thread_dir / "executions.json"
    path.write_text(json.dumps(execution_ledger_api.new_ledger(session_id)))
    return path


def _write_session_repo_context(thread_dir: pathlib.Path, session_id: str) -> None:
    (thread_dir / "session_mode.json").write_text(
        json.dumps(
            {
                "mode": "task",
                "repo_cwd": str(thread_dir),
                "task_id": "escapement-test-child",
                "parent_id": "escapement-test-parent",
                "session_id": session_id,
            }
        )
    )


def _supervisor_health_path(threads_root: pathlib.Path) -> pathlib.Path:
    return threads_root.parent / "supervisor-health.json"


# --- due-selection --------------------------------------------------------


def test_not_due_entry_untouched_no_spawn():
    e = _entry(wake_at=FUTURE, kind="check", command="x")
    kept, spawns = ww.plan([e], NOW, run_cmd=_runner(0))
    assert kept == [e] and spawns == []  # future entry never dispatched


# --- the GCP-wait core: not ready → cheap reschedule, NO spawn ------------


def test_not_ready_poll_rearmed_no_claude():
    e = _entry(kind="check", command="poll", poll_interval=600)
    kept, spawns = ww.plan([e], NOW, run_cmd=_runner(1))  # non-zero = not ready
    assert spawns == []  # NO Claude spawned (the whole point)
    assert len(kept) == 1  # re-armed, not dropped
    assert kept[0]["wake_at"] == (NOW + dt.timedelta(seconds=600)).isoformat()


# --- ready → fresh cheap handoff, AND pruned (no re-fire) -----------------


def test_ready_poll_spawns_handoff_and_prunes():
    e = _entry(
        kind="check", command="poll", escalate_prompt="PR #5 merged — finish up."
    )
    kept, spawns = ww.plan([e], NOW, run_cmd=_runner(0))  # exit 0 = ready
    assert kept == []  # PRUNED — cannot re-fire
    assert len(spawns) == 1
    assert spawns[0]["type"] == "handoff"
    assert spawns[0]["model"] == ww.wd.DEFAULT_HANDOFF_MODEL
    assert spawns[0]["prompt"] == "PR #5 merged — finish up."


def test_resume_kind_spawns_resume_and_prunes():
    # one-shot resume fires once then is pruned (regression guard for the 25× storm).
    e = _entry(kind="resume", prompt="continue")
    kept, spawns = ww.plan([e], NOW, run_cmd=_runner(0))
    assert kept == []
    assert spawns[0]["type"] == "resume" and spawns[0]["prompt"] == "continue"


def test_past_deadline_escalates_once_and_prunes():
    e = _entry(kind="check", command="poll", deadline=PAST, escalate_prompt="look")
    kept, spawns = ww.plan([e], NOW, run_cmd=_runner(1))  # not ready, but past deadline
    assert kept == []
    assert len(spawns) == 1 and spawns[0]["type"] == "handoff"


# --- fail-safe ------------------------------------------------------------


def test_malformed_and_bad_wake_at_dropped_no_spawn():
    kept, spawns = ww.plan(
        ["not-a-dict", {"wake_at": "garbage", "kind": "check", "command": "x"}],
        NOW,
        run_cmd=_runner(0),
    )
    assert kept == [] and spawns == []


def test_empty_schedule():
    assert ww.plan([], NOW, run_cmd=_runner(0)) == ([], [])


# --- dry-run contract -----------------------------------------------------


def test_dry_run_does_not_execute_due_check_commands(tmp_path):
    root = tmp_path / "threads"
    thread_dir = root / "thread-1"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    sentinel = tmp_path / "check-ran"
    script = f"from pathlib import Path; Path({str(sentinel)!r}).write_text('ran')"
    entry = _entry(
        kind="check",
        wake_at=CLI_PAST,
        command=f"{sys.executable} -c {shlex.quote(script)}",
        escalate_prompt="condition met",
    )
    schedule.write_text(json.dumps([entry]))

    assert ww.main(["--threads-root", str(root)]) == 0

    assert not sentinel.exists()
    assert json.loads(schedule.read_text()) == [entry]


def test_dry_run_still_reports_due_resume_without_rewriting_schedule(
    tmp_path, capsys, monkeypatch
):
    root = tmp_path / "threads"
    thread_dir = root / "thread-1"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    entry = _entry(kind="resume", wake_at=CLI_PAST, prompt="continue")
    schedule.write_text(json.dumps([entry]))

    def fail_if_spawned(argv, cwd):
        raise AssertionError(f"dry-run spawned unexpectedly: {argv} in {cwd}")

    monkeypatch.setattr(ww.es, "launch_in_repo", fail_if_spawned)

    assert ww.main(["--threads-root", str(root)]) == 0

    assert json.loads(schedule.read_text()) == [entry]
    out = capsys.readouterr().out
    assert '"would_spawn"' in out
    assert "DRY-RUN: 1 spawn(s) planned" in out


def test_dry_run_respects_future_resume_wake_at(tmp_path, capsys):
    root = tmp_path / "threads"
    thread_dir = root / "thread-1"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    entry = _entry(kind="resume", wake_at=CLI_FUTURE, prompt="continue later")
    schedule.write_text(json.dumps([entry]))

    assert ww.main(["--threads-root", str(root)]) == 0

    assert json.loads(schedule.read_text()) == [entry]
    out = capsys.readouterr().out
    assert '"would_spawn"' not in out
    assert "DRY-RUN: 0 spawn(s) planned" in out


def test_fire_executes_due_check_and_rearms_when_not_ready(tmp_path, capsys):
    root = tmp_path / "threads"
    thread_dir = root / "thread-1"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    sentinel = tmp_path / "check-ran"
    script = (
        f"from pathlib import Path; Path({str(sentinel)!r}).write_text('ran'); "
        "raise SystemExit(1)"
    )
    entry = _entry(
        kind="check",
        wake_at=CLI_PAST,
        command=f"{sys.executable} -c {shlex.quote(script)}",
        escalate_prompt="condition met",
        poll_interval=600,
    )
    schedule.write_text(json.dumps([entry]))

    assert ww.main(["--threads-root", str(root), "--fire"]) == 0

    assert sentinel.read_text() == "ran"
    kept = json.loads(schedule.read_text())
    assert len(kept) == 1
    assert kept[0]["command"] == entry["command"]
    assert kept[0]["wake_at"] != CLI_PAST
    assert "FIRED: 0 spawn(s) planned" in capsys.readouterr().out


def test_fire_preserves_due_entry_when_spawn_fails(tmp_path, monkeypatch):
    root = tmp_path / "threads"
    thread_dir = root / "thread-1"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    entry = _entry(kind="resume", wake_at=CLI_PAST, prompt="continue")
    schedule.write_text(json.dumps([entry]))
    _write_session_repo_context(thread_dir, "thread-1")

    def fail_spawn(argv, _cwd):
        raise OSError("claude unavailable")

    monkeypatch.setattr(ww.es, "launch_in_repo", fail_spawn)

    assert ww.main(["--threads-root", str(root), "--fire"]) == 1

    assert json.loads(schedule.read_text()) == [entry]


def test_fire_returns_nonzero_when_execution_reconciliation_raises(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "threads"
    root.mkdir()

    def fail_reconcile(*_args, **_kwargs):
        raise ValueError("trusted execution state is invalid")

    monkeypatch.setattr(ww.es, "reconcile_all", fail_reconcile)

    assert ww.main(["--threads-root", str(root), "--fire"]) == 1


def test_fire_resamples_time_for_execution_reconciliation(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "threads"
    thread_dir = root / "clock-order"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    sentinel = tmp_path / "scheduled-command-finished"
    command = (
        f"{sys.executable} -c "
        f"{shlex.quote(f'from pathlib import Path; Path({str(sentinel)!r}).write_text("done"); raise SystemExit(1)')}"
    )
    schedule.write_text(
        json.dumps(
            [
                _entry(
                    kind="check",
                    wake_at=CLI_PAST,
                    command=command,
                    poll_interval=600,
                    escalate_prompt="condition met",
                )
            ]
        )
    )
    scan_started = dt.datetime(2026, 8, 9, 20, 0, tzinfo=dt.timezone.utc)
    schedules_finished = dt.datetime(2026, 8, 9, 20, 4, tzinfo=dt.timezone.utc)
    observations = iter((scan_started, schedules_finished))

    class SequenceClock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            observed = next(observations)
            if observed == schedules_finished:
                assert sentinel.read_text() == "done"
                assert json.loads(schedule.read_text())[0]["wake_at"] != CLI_PAST
            return observed if tz is not None else observed.replace(tzinfo=None)

    reconciled_at: list[dt.datetime] = []

    def record_reconcile(_root, now, _owner, _spawn, **_kwargs):
        reconciled_at.append(now)
        return {"status": "ok", "recoveries": 0}

    monkeypatch.setattr(ww._dt, "datetime", SequenceClock)
    monkeypatch.setattr(ww.es, "reconcile_all", record_reconcile)

    assert ww.main(["--threads-root", str(root), "--fire"]) == 0
    assert reconciled_at == [schedules_finished]


def test_fire_skips_untrusted_schedule_and_does_not_execute_command(tmp_path, capsys):
    # Security guard: a world-writable scheduled.json could have its `command`
    # rewritten by another local user; the waker must NOT shell-execute it.
    # Negative control for trusted_source — an existence-only check would fail this.
    import os

    if not hasattr(os, "geteuid"):
        import pytest

        pytest.skip("perms/ownership guard is POSIX-only")
    root = tmp_path / "threads"
    thread_dir = root / "thread-1"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    sentinel = tmp_path / "untrusted-check-ran"
    script = f"from pathlib import Path; Path({str(sentinel)!r}).write_text('ran')"
    entry = _entry(
        kind="check",
        wake_at=CLI_PAST,
        command=f"{sys.executable} -c {shlex.quote(script)}",
        escalate_prompt="condition met",
    )
    schedule.write_text(json.dumps([entry]))
    schedule.chmod(0o666)  # group + other writable -> untrusted

    assert ww.main(["--threads-root", str(root), "--fire"]) == 1

    assert not sentinel.exists()  # the command must NOT have run
    assert json.loads(schedule.read_text()) == [entry]  # schedule left untouched
    assert "untrusted" in capsys.readouterr().err.lower()


def test_fire_skips_locked_schedule_to_avoid_duplicate_wakers(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "threads"
    thread_dir = root / "thread-1"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    entry = _entry(kind="resume", wake_at=CLI_PAST, prompt="continue")
    schedule.write_text(json.dumps([entry]))
    _write_empty_execution_ledger(thread_dir, "thread-1")
    health_path = _supervisor_health_path(root)
    seeded_health = {
        "reconcile_started_at": "2026-08-09T19:58:00Z",
        "last_successful_reconcile_at": "2026-08-09T19:59:00Z",
        "completed_generation": 7,
        "installation_id": "installed-supervisor-alpha",
        "counts": {"successful_passes": 7, "threads": 1},
    }
    health_path.write_text(json.dumps(seeded_health))
    lock_path = schedule.with_suffix(".json.lock")

    def fail_if_spawned(argv, cwd):
        raise AssertionError(f"locked schedule spawned unexpectedly: {argv} in {cwd}")

    monkeypatch.setattr(ww.es, "launch_in_repo", fail_if_spawned)
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert ww.main(["--threads-root", str(root), "--fire"]) == 1

    assert json.loads(schedule.read_text()) == [entry]
    assert "skipped locked schedule" in capsys.readouterr().err
    assert json.loads(health_path.read_text()) == seeded_health


# --- public --fire supervisor boundary -----------------------------------


def test_fire_advances_health_once_after_scheduled_and_execution_work_succeed(tmp_path):
    """A healthy tick means the entire public useful-work pass completed.

    Mutation killed: calling the supervisor (or stamping health) before the
    scheduled check and execution-ledger scans have both produced durable
    outcomes.
    """
    root = tmp_path / "threads"
    thread_dir = root / "thread-success"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    entry = _entry(
        kind="check",
        thread_id="thread-success",
        wake_at=CLI_PAST,
        command=f"{sys.executable} -c {shlex.quote('raise SystemExit(1)')}",
        poll_interval=600,
        escalate_prompt="condition met",
    )
    schedule.write_text(json.dumps([entry]))
    _write_empty_execution_ledger(thread_dir, "thread-success")

    assert ww.main(["--threads-root", str(root), "--fire"]) == 0

    kept = json.loads(schedule.read_text())
    assert len(kept) == 1
    assert kept[0]["command"] == entry["command"]
    assert kept[0]["wake_at"] != CLI_PAST
    first = json.loads(_supervisor_health_path(root).read_text())
    assert first["completed_generation"] == 1
    assert first["last_successful_reconcile_at"]
    assert first["installation_id"]

    assert ww.main(["--threads-root", str(root), "--fire"]) == 0

    second = json.loads(_supervisor_health_path(root).read_text())
    assert second["completed_generation"] == 2
    assert second["installation_id"] == first["installation_id"]
    assert (
        second["last_successful_reconcile_at"] >= first["last_successful_reconcile_at"]
    )


def test_fire_partial_schedule_failure_withholds_authoritative_health(
    tmp_path, monkeypatch
):
    """One unreadable scheduled-work input invalidates the global health tick.

    The successful sibling is a positive control: an empty implementation that
    simply refuses all work cannot satisfy this test.
    """
    root = tmp_path / "threads"
    good_dir = root / "a-good-thread"
    bad_dir = root / "b-bad-thread"
    good_dir.mkdir(parents=True)
    bad_dir.mkdir(parents=True)

    good_schedule = good_dir / "scheduled.json"
    good_entry = _entry(
        kind="check",
        thread_id="a-good-thread",
        wake_at=CLI_PAST,
        command=f"{sys.executable} -c {shlex.quote('raise SystemExit(1)')}",
        poll_interval=600,
        escalate_prompt="condition met",
    )
    good_schedule.write_text(json.dumps([good_entry]))
    _write_empty_execution_ledger(good_dir, "a-good-thread")

    bad_schedule = bad_dir / "scheduled.json"
    bad_schedule.write_text(json.dumps({"malformed": "schedule-must-be-an-array"}))
    _write_empty_execution_ledger(bad_dir, "b-bad-thread")

    health_path = _supervisor_health_path(root)
    seeded = {
        "reconcile_started_at": "2026-08-09T19:59:00Z",
        "last_successful_reconcile_at": "2026-08-09T20:00:00Z",
        "completed_generation": 7,
        "installation_id": "installed-supervisor-alpha",
        "counts": {"successful_passes": 7, "threads": 2},
    }
    health_path.write_text(json.dumps(seeded))
    lifecycle_scans = []
    monkeypatch.setattr(
        ww.wls,
        "reconcile",
        lambda harness_root: lifecycle_scans.append(harness_root)
        or {"status": "ok", "checked": 0},
    )

    assert ww.main(["--threads-root", str(root), "--fire"]) == 1

    kept = json.loads(good_schedule.read_text())
    assert len(kept) == 1
    assert kept[0]["wake_at"] != CLI_PAST
    assert json.loads(bad_schedule.read_text()) == {
        "malformed": "schedule-must-be-an-array"
    }
    assert json.loads(health_path.read_text()) == seeded
    assert lifecycle_scans == [root.parent]


def test_fire_fsyncs_schedule_source_and_directory_before_certifying_health(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "threads"
    thread_dir = root / "durable-schedule"
    thread_dir.mkdir(parents=True)
    schedule = thread_dir / "scheduled.json"
    schedule.write_text(
        json.dumps(
            [
                _entry(
                    kind="check",
                    wake_at=CLI_PAST,
                    command=f"{sys.executable} -c {shlex.quote('raise SystemExit(1)')}",
                    poll_interval=600,
                    escalate_prompt="condition met",
                )
            ]
        )
    )
    _write_empty_execution_ledger(thread_dir, "durable-schedule")

    real_fsync = ww.os.fsync
    real_replace = ww.os.replace
    events: list[tuple] = []

    def recording_fsync(fd: int) -> None:
        observed = os.fstat(fd)
        events.append(("fsync", observed.st_dev, observed.st_ino, observed.st_mode))
        real_fsync(fd)

    def recording_replace(source, destination) -> None:
        source_stat = os.stat(source)
        destination_path = pathlib.Path(destination)
        health_payload = None
        if destination_path == _supervisor_health_path(root):
            health_payload = json.loads(pathlib.Path(source).read_text())
        events.append(
            (
                "replace",
                destination_path,
                source_stat.st_dev,
                source_stat.st_ino,
                health_payload,
            )
        )
        real_replace(source, destination)

    monkeypatch.setattr(ww.os, "fsync", recording_fsync)
    monkeypatch.setattr(ww.os, "replace", recording_replace)

    assert ww.main(["--threads-root", str(root), "--fire"]) == 0

    schedule_replace_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "replace" and event[1] == schedule
    )
    advanced_health_replace_index = min(
        index
        for index, event in enumerate(events)
        if event[0] == "replace"
        and event[1] == _supervisor_health_path(root)
        and event[4]["completed_generation"] > 0
    )
    replacement = events[schedule_replace_index]
    assert (
        "fsync",
        replacement[2],
        replacement[3],
    ) in {event[:3] for event in events[:schedule_replace_index] if event[0] == "fsync"}
    directory_stat = os.stat(thread_dir)
    directory_fsync_index = next(
        index
        for index, event in enumerate(events)
        if index > schedule_replace_index
        and event[0] == "fsync"
        and event[1:3] == (directory_stat.st_dev, directory_stat.st_ino)
    )
    assert directory_fsync_index < advanced_health_replace_index
    assert all(
        event[4]["completed_generation"] == 0
        and event[4]["last_successful_reconcile_at"] is None
        for event in events[: directory_fsync_index + 1]
        if event[0] == "replace" and event[1] == _supervisor_health_path(root)
    )
    assert json.loads(schedule.read_text())[0]["wake_at"] != CLI_PAST
    health = json.loads(_supervisor_health_path(root).read_text())
    assert health["completed_generation"] == 1
