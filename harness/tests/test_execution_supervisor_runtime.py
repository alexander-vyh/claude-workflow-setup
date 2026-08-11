"""Public runtime controls for repository binding and untrusted ledger discovery."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger  # noqa: E402
import execution_store  # noqa: E402
import execution_supervisor  # noqa: E402


def _write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def _due_ledger(session_id: str, child_bead: str) -> dict:
    ledger = execution_ledger.new_ledger(session_id)
    execution_ledger.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": session_id,
            "bead_id": child_bead,
            "execution_id": "exec-repo-boundary-77",
            "host": "claude",
            "agent_name": "repo-boundary-worker",
            "dispatch_tool_use_id": "tool-repo-boundary-77",
            "watchdog_id": "watch-repo-boundary-77",
            "attempt": 1,
            "generation": 1,
        },
        dt.datetime(2026, 8, 9, 20, 0, tzinfo=dt.timezone.utc),
    )
    ledger["executions"][0]["reconcile_due"] = "start"
    return ledger


def test_host_recovery_argv_is_exact_and_host_specific() -> None:
    session_id = "host-routing-session-12"
    base = {"parent_session_id": session_id, "prompt": "reconcile exact work"}
    assert execution_supervisor._spawn({**base, "host": "claude"}) == [
        "claude",
        "--resume",
        session_id,
        "-p",
        "reconcile exact work",
    ]
    assert execution_supervisor._spawn({**base, "host": "codex"}) == [
        "codex",
        "exec",
        "resume",
        session_id,
        "reconcile exact work",
    ]


def test_supervisor_architecture_rejects_false_activity_and_beads_closure() -> None:
    supervisor_source = (BIN / "execution_supervisor.py").read_text()
    assert "workflow_status" not in supervisor_source
    assert "st_mtime" not in supervisor_source
    assert "getmtime" not in supervisor_source
    for path in (
        BIN / "execution_ledger.py",
        BIN / "delegation_hook.py",
        BIN / "execution_reconcile.py",
        BIN / "execution_supervisor.py",
    ):
        source = path.read_text()
        assert "bd close" not in source
        assert "--status closed" not in source
        assert "close_bead" not in source


@pytest.mark.parametrize("kind", ["resume", "handoff"])
def test_public_scheduled_wake_uses_durable_session_repo_from_foreign_cwd(
    tmp_path, kind
) -> None:
    session_id = f"scheduled-{kind}-session-31"
    threads_root = tmp_path / "harness" / "threads"
    thread_dir = threads_root / session_id
    repo_cwd = tmp_path / f"scheduled-{kind}-originating-worktree"
    ambient_cwd = tmp_path / f"scheduled-{kind}-launchd-ambient"
    repo_cwd.mkdir()
    ambient_cwd.mkdir()
    entry = {
        "kind": "resume" if kind == "resume" else "check",
        "thread_id": session_id,
        "wake_at": "2000-01-01T00:00:00Z",
        "prompt": "resume the exact scheduled work",
    }
    if kind == "handoff":
        entry.update(
            {
                "command": "true",
                "escalate_prompt": "handoff the exact scheduled work",
            }
        )
    schedule = thread_dir / "scheduled.json"
    _write_json(schedule, [entry])
    _write_json(
        thread_dir / "session_mode.json",
        {
            "mode": "task",
            "repo_cwd": str(repo_cwd),
            "task_id": "escapement-scheduled-child-31",
            "parent_id": "escapement-scheduled-parent-31",
            "session_id": session_id,
        },
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    spawn_record = tmp_path / "scheduled-claude-record.jsonl"
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "with pathlib.Path(os.environ['SPAWN_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}) + '\\n')\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SPAWN_RECORD"] = str(spawn_record)

    result = subprocess.run(
        [
            sys.executable,
            str(BIN / "wakeup_waker.py"),
            "--threads-root",
            str(threads_root),
            "--fire",
        ],
        cwd=ambient_cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 3
    while not spawn_record.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    spawned = [json.loads(line) for line in spawn_record.read_text().splitlines()]
    assert len(spawned) == 1
    assert spawned[0]["cwd"] == str(repo_cwd.resolve())
    if kind == "resume":
        assert spawned[0]["argv"][:2] == ["--resume", session_id]
    else:
        assert spawned[0]["argv"][0] == "-p"
        assert "--resume" not in spawned[0]["argv"]
    assert json.loads(schedule.read_text()) == []


@pytest.mark.parametrize(
    "invalid_context", ["missing", "foreign-session", "relative-repo", "untrusted"]
)
def test_public_scheduled_wake_without_repo_context_fails_closed(
    tmp_path, invalid_context
) -> None:
    session_id = "scheduled-missing-context-32"
    threads_root = tmp_path / "harness" / "threads"
    schedule = threads_root / session_id / "scheduled.json"
    entry = {
        "kind": "resume",
        "thread_id": session_id,
        "wake_at": "2000-01-01T00:00:00Z",
        "prompt": "must not launch without repository context",
    }
    _write_json(schedule, [entry])
    repo_cwd = tmp_path / "scheduled-context-repo"
    repo_cwd.mkdir()
    mode_path = schedule.parent / "session_mode.json"
    if invalid_context != "missing":
        _write_json(
            mode_path,
            {
                "mode": "task",
                "repo_cwd": (
                    "relative/repo"
                    if invalid_context == "relative-repo"
                    else str(repo_cwd)
                ),
                "task_id": "escapement-scheduled-child-32",
                "parent_id": "escapement-scheduled-parent-32",
                "session_id": (
                    "foreign-session"
                    if invalid_context == "foreign-session"
                    else session_id
                ),
            },
        )
        if invalid_context == "untrusted":
            if not hasattr(os, "geteuid"):
                pytest.skip("permission trust control is POSIX-only")
            mode_path.chmod(0o666)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    spawn_record = tmp_path / "must-not-spawn.jsonl"
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "with pathlib.Path(os.environ['SPAWN_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SPAWN_RECORD"] = str(spawn_record)

    result = subprocess.run(
        [
            sys.executable,
            str(BIN / "wakeup_waker.py"),
            "--threads-root",
            str(threads_root),
            "--fire",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert not spawn_record.exists()
    assert json.loads(schedule.read_text()) == [entry]


def test_authoritative_health_uses_post_reconciliation_completion_time(
    tmp_path,
) -> None:
    threads_root = tmp_path / "harness" / "threads"
    repo_cwd = tmp_path / "completion-originating-worktree"
    repo_cwd.mkdir()
    specs = [
        (
            "a-completion-clock-session-41",
            "escapement-completion-child-41a",
            "escapement-completion-parent-41a",
        ),
        (
            "z-completion-clock-session-41",
            "escapement-completion-child-41z",
            "escapement-completion-parent-41z",
        ),
    ]
    ledger_paths: dict[str, pathlib.Path] = {}
    for session_id, child_bead, parent_bead in specs:
        thread_dir = threads_root / session_id
        ledger_path = thread_dir / "executions.json"
        ledger_paths[session_id] = ledger_path
        _write_json(ledger_path, _due_ledger(session_id, child_bead))
        _write_json(
            thread_dir / "session_mode.json",
            {
                "mode": "task",
                "repo_cwd": str(repo_cwd),
                "task_id": child_bead,
                "parent_id": parent_bead,
                "session_id": session_id,
            },
        )
    children = {child: parent for _session, child, parent in specs}
    parents = {parent for _session, _child, parent in specs}
    completed_lookups: set[str] = set()
    completed_spawns: set[str] = set()

    def run_bd(args: list[str]):
        if len(args) == 2 and args[0] == "show" and args[1] in children:
            child_bead = args[1]
            return [
                {
                    "id": child_bead,
                    "status": "in_progress",
                    "parent": children[child_bead],
                }
            ]
        if len(args) == 2 and args[0] == "show" and args[1] in parents:
            parent_bead = args[1]
            completed_lookups.add(parent_bead)
            return [{"id": parent_bead, "status": "in_progress"}]
        return None

    completed_at = dt.datetime(2026, 8, 9, 20, 1, 30, tzinfo=dt.timezone.utc)

    def completion_clock() -> dt.datetime:
        assert completed_lookups == parents
        assert completed_spawns == {session for session, _child, _parent in specs}
        for session_id, ledger_path in ledger_paths.items():
            durable = execution_store.load_trusted(ledger_path, session_id)
            assert durable is not None
            assert durable["executions"][0]["recovery_claim"] is not None
        return completed_at

    def record_spawn(descriptor: dict) -> None:
        completed_spawns.add(descriptor["parent_session_id"])

    result = execution_supervisor.reconcile_all(
        threads_root,
        dt.datetime(2026, 8, 9, 20, 1, tzinfo=dt.timezone.utc),
        "completion-clock-supervisor",
        record_spawn,
        native_status=lambda _execution: None,
        run_bd=run_bd,
        completion_clock=completion_clock,
    )

    assert result["status"] == "ok"
    health = json.loads((threads_root.parent / "supervisor-health.json").read_text())
    assert health["last_successful_reconcile_at"] == "2026-08-09T20:01:30Z"


@pytest.mark.parametrize("entrypoint", ["execution_supervisor.py", "wakeup_waker.py"])
def test_public_daemon_uses_durable_session_repo_from_foreign_cwd(
    tmp_path, entrypoint
) -> None:
    """The launchd process cwd must never select the Beads authority."""
    session_id = "repo-bound-session-77"
    child_bead = "escapement-child-repo-77"
    parent_bead = "escapement-parent-repo-77"
    threads_root = tmp_path / "harness" / "threads"
    thread_dir = threads_root / session_id
    repo_cwd = tmp_path / "originating-worktree"
    ambient_cwd = tmp_path / "launchd-ambient"
    repo_cwd.mkdir()
    ambient_cwd.mkdir()
    (repo_cwd / "beads-authority.marker").write_text("bound", encoding="utf-8")
    ledger_path = thread_dir / "executions.json"
    _write_json(ledger_path, _due_ledger(session_id, child_bead))
    _write_json(
        thread_dir / "session_mode.json",
        {
            "mode": "task",
            "repo_cwd": str(repo_cwd),
            "task_id": child_bead,
            "parent_id": parent_bead,
            "session_id": session_id,
        },
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    bd_record = tmp_path / "bd-cwd-record.jsonl"
    spawn_record = tmp_path / "claude-record.jsonl"
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "if not pathlib.Path('beads-authority.marker').is_file():\n"
        "    raise SystemExit(72)\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        "with pathlib.Path(os.environ['BD_CWD_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'args': args}) + '\\n')\n"
        f"if args == ['show', {child_bead!r}]:\n"
        f"    print(json.dumps([{{'id': {child_bead!r}, 'status': 'in_progress', "
        f"'parent': {parent_bead!r}}}]))\n"
        f"elif args == ['show', {parent_bead!r}]:\n"
        f"    print(json.dumps([{{'id': {parent_bead!r}, 'status': 'in_progress'}}]))\n"
        "else:\n"
        "    raise SystemExit(73)\n",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "with pathlib.Path(os.environ['SPAWN_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}) + '\\n')\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["BD_CWD_RECORD"] = str(bd_record)
    env["SPAWN_RECORD"] = str(spawn_record)

    command = [
        sys.executable,
        str(BIN / entrypoint),
        "--threads-root",
        str(threads_root),
    ]
    if entrypoint == "execution_supervisor.py":
        command.extend(
            [
                "--now",
                "2026-08-09T20:03:00Z",
                "--owner",
                "repo-bound-public-supervisor",
            ]
        )
    else:
        command.append("--fire")
    result = subprocess.run(
        command,
        cwd=ambient_cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 3
    while not spawn_record.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert spawn_record.exists()
    spawned = [json.loads(line) for line in spawn_record.read_text().splitlines()]
    assert len(spawned) == 1
    assert spawned[0]["cwd"] == str(repo_cwd.resolve())
    assert spawned[0]["argv"][:2] == ["--resume", session_id]
    bd_calls = [json.loads(line) for line in bd_record.read_text().splitlines()]
    assert [call["args"] for call in bd_calls] == [
        ["show", child_bead],
        ["show", parent_bead],
    ]
    assert {call["cwd"] for call in bd_calls} == {str(repo_cwd.resolve())}
    durable = execution_store.load_trusted(ledger_path, session_id)
    assert durable is not None
    assert durable["executions"][0]["recovery_claim"]["generation"] == 1


def test_dangling_ledger_is_unresolved_and_cannot_certify_health(tmp_path) -> None:
    threads_root = tmp_path / "harness" / "threads"
    thread_dir = threads_root / "dangling-ledger-session"
    thread_dir.mkdir(parents=True)
    os.symlink(tmp_path / "missing-ledger-target", thread_dir / "executions.json")
    health_path = threads_root.parent / "supervisor-health.json"
    seeded = {
        "reconcile_started_at": "2026-08-09T19:58:00Z",
        "last_successful_reconcile_at": "2026-08-09T19:59:00Z",
        "completed_generation": 7,
        "installation_id": "installed-supervisor-alpha",
        "counts": {"successful_passes": 7, "threads": 2, "recoveries": 3},
    }
    _write_json(health_path, seeded)

    result = execution_supervisor.reconcile_all(
        threads_root,
        dt.datetime(2026, 8, 9, 20, 16, tzinfo=dt.timezone.utc),
        "dangling-ledger-supervisor",
        lambda _descriptor: None,
        native_status=lambda _execution: None,
        run_bd=lambda _args: (_ for _ in ()).throw(
            AssertionError("dangling ledger must not reach Beads")
        ),
    )

    assert result["status"] == "unresolved"
    assert result["unresolved_threads"] == ["dangling-ledger-session"]
    observed = json.loads(health_path.read_text())
    assert observed["reconcile_started_at"] != seeded["reconcile_started_at"]
    for field in (
        "last_successful_reconcile_at",
        "completed_generation",
        "installation_id",
        "counts",
    ):
        assert observed[field] == seeded[field]


@pytest.mark.parametrize(
    "invalid_context", ["missing", "foreign-session", "relative-repo", "untrusted"]
)
def test_execution_repo_binding_fails_closed_before_beads_lookup(
    tmp_path, invalid_context
) -> None:
    session_id = "context-session-88"
    child_bead = "escapement-child-context-88"
    parent_bead = "escapement-parent-context-88"
    thread_dir = tmp_path / "threads" / session_id
    repo_cwd = tmp_path / "trusted-repo"
    repo_cwd.mkdir(parents=True)
    _write_json(thread_dir / "executions.json", _due_ledger(session_id, child_bead))
    mode_path = thread_dir / "session_mode.json"
    if invalid_context != "missing":
        _write_json(
            mode_path,
            {
                "mode": "task",
                "repo_cwd": (
                    "relative/repo"
                    if invalid_context == "relative-repo"
                    else str(repo_cwd)
                ),
                "task_id": child_bead,
                "parent_id": parent_bead,
                "session_id": (
                    "foreign-session"
                    if invalid_context == "foreign-session"
                    else session_id
                ),
            },
        )
        if invalid_context == "untrusted":
            if not hasattr(os, "geteuid"):
                pytest.skip("permission trust control is POSIX-only")
            mode_path.chmod(0o666)

    calls: list[list[str]] = []

    def run_bd(args: list[str]):
        calls.append(args)
        if args == ["show", child_bead]:
            return [
                {
                    "id": child_bead,
                    "status": "in_progress",
                    "parent": parent_bead,
                }
            ]
        if args == ["show", parent_bead]:
            return [{"id": parent_bead, "status": "in_progress"}]
        return None

    result = execution_supervisor.plan_thread(
        thread_dir,
        dt.datetime(2026, 8, 9, 20, 16, tzinfo=dt.timezone.utc),
        lambda _execution: None,
        run_bd,
    )

    assert result["status"] == "unresolved"
    assert calls == []
