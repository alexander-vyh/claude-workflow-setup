#!/usr/bin/env python3
"""Public actor-scoped delegated-execution integration oracle.

Test Oracle Brief
-----------------
1. Business invariant: a parent and each same-session actor retain independent
   execution state, while the global supervisor discovers and recovers every
   supported state directory using the real parent session and repository.
2. Independent source of truth: public hook/CLI output, exact durable files,
   external fake-host argv/cwd records, and global supervisor health.
3. Constraints: preserve legacy parent state; support exactly one actor layer;
   trust exact-session task context; never recursively scan arbitrary paths;
   use the one global health record; keep source/rendered surfaces equivalent.
4. Invalid solution classes: legacy-only paths, actor-key resume identity,
   ambient-cwd launch, recursive rglob, parent-session deduplication, or actor-
   local supervisor health.
5. Fragile shortcut rejected: add actor schedule scanning only. That leaves
   dispatch, SessionStart, execution recovery, and Stop proof disconnected.
6. Negative controls: missing/foreign actor context and arbitrary deep ledgers
   must remain byte-exact and never launch.
7. Positive controls: legacy plus two sibling actors all recover, and a managed
   actor wake authorizes one bounded pause only after a later global pass.
8. Missing handling: existing invalid actor execution state fails closed.
9. Final verification: this module, full harness, rendered parity, then an
   installed parent/subagent launchd canary.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger  # noqa: E402
import execution_store  # noqa: E402
from harness.tests.test_execution_stop_gate import (  # noqa: E402
    INSTALLATION,
    _execution,
    _health,
    _ledger,
)

UTC = dt.timezone.utc
SESSION = "actor-parent-session-91"
ACTOR = "delegated.actor.alpha"
CHILD = "escapement-actor-child-91"
ROOT_BEAD = "escapement-actor-root-91"


def _write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def _actor_env(harness_root: pathlib.Path, actor: str = ACTOR) -> dict[str, str]:
    env = os.environ.copy()
    env["HARNESS_ROOT"] = str(harness_root)
    env["CLAUDE_CODE_SESSION_ID"] = SESSION
    env["CLAUDE_AGENT_ID"] = actor
    env.pop("HARNESS_THREAD_DIR", None)
    return env


def _establish_actor_dir(
    harness_root: pathlib.Path, repo: pathlib.Path, actor: str = ACTOR
) -> pathlib.Path:
    before = (
        set(harness_root.rglob("contract.json")) if harness_root.exists() else set()
    )
    result = subprocess.run(
        [
            sys.executable,
            str(BIN / "init_contract.py"),
            "--goal",
            f"actor contract {actor}",
            "--verify",
            "test -d .",
        ],
        cwd=repo,
        env=_actor_env(harness_root, actor),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    created = set(harness_root.rglob("contract.json")) - before
    assert len(created) == 1
    return created.pop().parent


def _mode(
    actor_dir: pathlib.Path, repo: pathlib.Path, session_id: str = SESSION
) -> None:
    _write_json(
        actor_dir / "session_mode.json",
        {
            "mode": "task",
            "repo_cwd": str(repo),
            "task_id": CHILD,
            "parent_id": ROOT_BEAD,
            "session_id": session_id,
        },
    )


def _due_ledger(
    session_id: str, child: str, execution_id: str, native_id: str | None = None
) -> dict:
    ledger = execution_ledger.new_ledger(session_id)
    execution_ledger.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": session_id,
            "bead_id": child,
            "execution_id": execution_id,
            "host": "claude",
            "agent_name": f"worker-{execution_id}",
            "dispatch_tool_use_id": f"tool-{execution_id}",
            "watchdog_id": f"watch-{execution_id}",
            "attempt": 1,
            "generation": 1,
        },
        dt.datetime(2026, 8, 9, 20, 0, tzinfo=UTC),
    )
    if native_id is not None:
        execution_ledger.apply_event(
            ledger,
            {
                "kind": "child_bound",
                "parent_session_id": session_id,
                "execution_id": execution_id,
                "attempt": 1,
                "generation": 1,
                "native_child_id": native_id,
            },
            dt.datetime(2026, 8, 9, 20, 0, 5, tzinfo=UTC),
        )
        ledger["executions"][0]["reconcile_due"] = "idle"
    else:
        ledger["executions"][0]["reconcile_due"] = "start"
    return ledger


def _fake_bd_and_claude(
    tmp_path: pathlib.Path, relationships: dict[str, str]
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    fixture = tmp_path / "beads-fixture.json"
    fixture.write_text(json.dumps(relationships), encoding="utf-8")
    bd_record = tmp_path / "bd-record.jsonl"
    spawn_record = tmp_path / "spawn-record.jsonl"
    bd = fake_bin / "bd"
    bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "mapping = json.loads(pathlib.Path(os.environ['BEADS_FIXTURE']).read_text())\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        "with pathlib.Path(os.environ['BD_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'args': args}) + '\\n')\n"
        "if len(args) != 2 or args[0] != 'show':\n"
        "    raise SystemExit(71)\n"
        "item = args[1]\n"
        "if item in mapping:\n"
        "    print(json.dumps([{'id': item, 'status': 'in_progress', "
        "'parent': mapping[item]}]))\n"
        "elif item in set(mapping.values()):\n"
        "    print(json.dumps([{'id': item, 'status': 'in_progress'}]))\n"
        "else:\n"
        "    raise SystemExit(72)\n",
        encoding="utf-8",
    )
    bd.chmod(0o755)
    claude = fake_bin / "claude"
    claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "with pathlib.Path(os.environ['SPAWN_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}) + '\\n')\n",
        encoding="utf-8",
    )
    claude.chmod(0o755)
    return fake_bin, bd_record, spawn_record


def test_public_actor_prepare_pretool_and_sessionstart_share_one_ledger(
    tmp_path,
) -> None:
    harness_root = tmp_path / "harness"
    repo = tmp_path / "repo"
    repo.mkdir()
    actor_dir = _establish_actor_dir(harness_root, repo)
    _mode(actor_dir, repo)
    env = _actor_env(harness_root)
    queued_at = dt.datetime.now(UTC) - dt.timedelta(minutes=3)

    prepared = subprocess.run(
        [
            sys.executable,
            str(BIN / "delegation_hook.py"),
            "prepare",
            "--bead-id",
            CHILD,
            "--session",
            SESSION,
            "--host",
            "claude",
            "--agent-name",
            "actor-worker",
            "--execution-id",
            "exec-actor-public-91",
            "--watchdog-id",
            "watch-actor-public-91",
            "--now",
            queued_at.isoformat(),
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert prepared.returncode == 0, prepared.stderr
    ledger_path = actor_dir / "executions.json"
    assert ledger_path.is_file()
    assert not (harness_root / "threads" / SESSION / "executions.json").exists()

    fake_bin, _bd_record, _spawn_record = _fake_bd_and_claude(
        tmp_path, {CHILD: ROOT_BEAD}
    )
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["BEADS_FIXTURE"] = str(tmp_path / "beads-fixture.json")
    env["BD_RECORD"] = str(tmp_path / "bd-record.jsonl")
    env["SPAWN_RECORD"] = str(tmp_path / "spawn-record.jsonl")
    pretool_payload = {
        "session_id": SESSION,
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_use_id": "toolu-actor-public-91",
        "tool_input": {"name": "actor-worker", "run_in_background": True},
    }
    dispatched = subprocess.run(
        [sys.executable, str(BIN / "delegation_hook.py")],
        input=json.dumps(pretool_payload),
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert dispatched.returncode == 0, dispatched.stderr
    assert (
        json.loads(dispatched.stdout)["hookSpecificOutput"]["permissionDecision"]
        == "allow"
    )
    after_dispatch = execution_store.load_trusted(ledger_path, SESSION)
    assert after_dispatch is not None
    assert after_dispatch["executions"][0]["dispatch_tool_use_id"] == (
        "toolu-actor-public-91"
    )

    started = subprocess.run(
        [sys.executable, str(BIN / "execution_reconcile.py")],
        input=json.dumps(
            {
                "session_id": SESSION,
                "cwd": str(repo),
                "hook_event_name": "SessionStart",
                "source": "startup",
            }
        ),
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert started.returncode == 0, started.stderr
    assert "missing or untrusted" not in started.stdout
    reconciled = execution_store.load_trusted(ledger_path, SESSION)
    assert reconciled is not None
    assert reconciled["executions"][0]["reconcile_due"] == "start"
    assert not (harness_root / "threads" / SESSION / "executions.json").exists()


def test_public_supervisor_recovers_legacy_and_two_sibling_actors_only(
    tmp_path,
) -> None:
    harness_root = tmp_path / "harness"
    threads_root = harness_root / "threads"
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    specs = [
        (threads_root / SESSION, "legacy-child", "legacy-root", "exec-legacy", None),
        (
            threads_root / SESSION / "agents" / "actor-a",
            "actor-a-child",
            "actor-a-root",
            "exec-actor-a",
            "native-actor-a",
        ),
        (
            threads_root / SESSION / "agents" / "actor-b",
            "actor-b-child",
            "actor-b-root",
            "exec-actor-b",
            "native-actor-b",
        ),
    ]
    relationships: dict[str, str] = {}
    ledgers: dict[str, pathlib.Path] = {}
    expected_cwds: dict[str, str] = {}
    for index, (state_dir, child, parent, execution_id, native_id) in enumerate(specs):
        repo = tmp_path / f"repo-{index}"
        repo.mkdir()
        relationships[child] = parent
        path = state_dir / "executions.json"
        _write_json(path, _due_ledger(SESSION, child, execution_id, native_id))
        _write_json(
            state_dir / "session_mode.json",
            {
                "mode": "task",
                "repo_cwd": str(repo),
                "task_id": child,
                "parent_id": parent,
                "session_id": SESSION,
            },
        )
        ledgers[execution_id] = path
        expected_cwds[execution_id] = str(repo.resolve())

    deep = threads_root / SESSION / "agents" / "actor-b" / "arbitrary" / "depth"
    deep_ledger = _due_ledger(SESSION, "deep-child", "exec-deep", "native-deep")
    deep_path = deep / "executions.json"
    _write_json(deep_path, deep_ledger)
    deep_before = deep_path.read_bytes()
    relationships["deep-child"] = "deep-root"

    fake_bin, bd_record, spawn_record = _fake_bd_and_claude(tmp_path, relationships)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["BEADS_FIXTURE"] = str(tmp_path / "beads-fixture.json")
    env["BD_RECORD"] = str(bd_record)
    env["SPAWN_RECORD"] = str(spawn_record)
    result = subprocess.run(
        [
            sys.executable,
            str(BIN / "execution_supervisor.py"),
            "--threads-root",
            str(threads_root),
            "--now",
            "2026-08-09T20:16:00Z",
            "--owner",
            "actor-public-supervisor",
        ],
        cwd=ambient,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 3
    while (
        not spawn_record.exists() or len(spawn_record.read_text().splitlines()) < 3
    ) and time.monotonic() < deadline:
        time.sleep(0.01)
    spawned = [json.loads(line) for line in spawn_record.read_text().splitlines()]
    assert len(spawned) == 3
    assert {tuple(item["argv"][:2]) for item in spawned} == {("--resume", SESSION)}
    for item in spawned:
        prompt = " ".join(item["argv"])
        matched = [key for key in expected_cwds if key in prompt]
        assert len(matched) == 1
        assert item["cwd"] == expected_cwds[matched[0]]
    for execution_id, path in ledgers.items():
        durable = execution_store.load_trusted(path, SESSION)
        assert durable is not None
        execution = next(
            item
            for item in durable["executions"]
            if item["execution_id"] == execution_id
        )
        assert execution["recovery_claim"]["owner"] == "actor-public-supervisor"
    assert deep_path.read_bytes() == deep_before
    bd_calls = [json.loads(line)["args"] for line in bd_record.read_text().splitlines()]
    assert "deep-child" not in {args[1] for args in bd_calls}


@pytest.mark.parametrize(
    "invalid_context", ["missing-mode", "foreign-mode", "foreign-ledger"]
)
def test_public_actor_execution_context_failure_is_unresolved_and_nonmutating(
    tmp_path, invalid_context
) -> None:
    harness_root = tmp_path / "harness"
    threads_root = harness_root / "threads"
    actor_dir = threads_root / SESSION / "agents" / "actor-invalid"
    repo = tmp_path / "repo"
    ambient = tmp_path / "ambient"
    repo.mkdir()
    ambient.mkdir()
    ledger_session = (
        "foreign-ledger-session" if invalid_context == "foreign-ledger" else SESSION
    )
    ledger_path = actor_dir / "executions.json"
    _write_json(
        ledger_path,
        _due_ledger(ledger_session, CHILD, "exec-invalid-actor", "native-invalid"),
    )
    before = ledger_path.read_bytes()
    if invalid_context != "missing-mode":
        _mode(
            actor_dir,
            repo,
            "foreign-mode-session" if invalid_context == "foreign-mode" else SESSION,
        )

    fake_bin, bd_record, spawn_record = _fake_bd_and_claude(
        tmp_path, {CHILD: ROOT_BEAD}
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["BEADS_FIXTURE"] = str(tmp_path / "beads-fixture.json")
    env["BD_RECORD"] = str(bd_record)
    env["SPAWN_RECORD"] = str(spawn_record)
    result = subprocess.run(
        [
            sys.executable,
            str(BIN / "execution_supervisor.py"),
            "--threads-root",
            str(threads_root),
            "--now",
            "2026-08-09T20:16:00Z",
        ],
        cwd=ambient,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "unresolved"
    assert not spawn_record.exists()
    assert not bd_record.exists()
    assert ledger_path.read_bytes() == before


def test_invalid_present_actor_never_falls_back_to_parent_execution_state(
    tmp_path,
) -> None:
    bad_actor = "../parent-execution-state"

    prepare_root = tmp_path / "prepare-harness"
    prepare_repo = tmp_path / "prepare-repo"
    prepare_repo.mkdir()
    prepare_env = _actor_env(prepare_root, bad_actor)
    prepared = subprocess.run(
        [
            sys.executable,
            str(BIN / "delegation_hook.py"),
            "prepare",
            "--bead-id",
            CHILD,
            "--session",
            SESSION,
            "--host",
            "claude",
            "--agent-name",
            "invalid-actor-worker",
        ],
        cwd=prepare_repo,
        env=prepare_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert prepared.returncode != 0
    assert not list(prepare_root.rglob("executions.json"))

    hook_root = tmp_path / "hook-harness"
    hook_repo = tmp_path / "hook-repo"
    hook_repo.mkdir()
    parent_ledger = hook_root / "threads" / SESSION / "executions.json"
    clean_env = _actor_env(hook_root, ACTOR)
    clean_env.pop("CLAUDE_AGENT_ID")
    explicit_prepare = subprocess.run(
        [
            sys.executable,
            str(BIN / "delegation_hook.py"),
            "prepare",
            "--ledger-path",
            str(parent_ledger),
            "--bead-id",
            CHILD,
            "--session",
            SESSION,
            "--host",
            "claude",
            "--agent-name",
            "invalid-actor-worker",
            "--execution-id",
            "exec-invalid-actor-fallback",
            "--watchdog-id",
            "watch-invalid-actor-fallback",
            "--now",
            "2026-08-09T20:00:00Z",
        ],
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert explicit_prepare.returncode == 0
    before_pretool = parent_ledger.read_bytes()
    fake_bin, _bd_record, _spawn_record = _fake_bd_and_claude(
        tmp_path, {CHILD: ROOT_BEAD}
    )
    invalid_env = _actor_env(hook_root, bad_actor)
    invalid_env["PATH"] = f"{fake_bin}{os.pathsep}{invalid_env.get('PATH', '')}"
    invalid_env["BEADS_FIXTURE"] = str(tmp_path / "beads-fixture.json")
    invalid_env["BD_RECORD"] = str(tmp_path / "bd-record.jsonl")
    invalid_env["SPAWN_RECORD"] = str(tmp_path / "spawn-record.jsonl")
    pretool = subprocess.run(
        [sys.executable, str(BIN / "delegation_hook.py")],
        input=json.dumps(
            {
                "session_id": SESSION,
                "cwd": str(hook_repo),
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
                "tool_use_id": "toolu-invalid-actor-fallback",
                "tool_input": {
                    "name": "invalid-actor-worker",
                    "run_in_background": True,
                },
            }
        ),
        cwd=hook_repo,
        env=invalid_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert pretool.returncode == 0
    assert (
        json.loads(pretool.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    )
    assert parent_ledger.read_bytes() == before_pretool

    session_root = tmp_path / "sessionstart-harness"
    session_repo = tmp_path / "sessionstart-repo"
    session_repo.mkdir()
    session_ledger = session_root / "threads" / SESSION / "executions.json"
    unresolved = _due_ledger(SESSION, CHILD, "exec-invalid-sessionstart")
    unresolved["executions"][0]["reconcile_due"] = None
    _write_json(session_ledger, unresolved)
    before_sessionstart = session_ledger.read_bytes()
    session_env = _actor_env(session_root, bad_actor)
    session_env["PATH"] = f"{fake_bin}{os.pathsep}{session_env.get('PATH', '')}"
    session_env["BEADS_FIXTURE"] = str(tmp_path / "beads-fixture.json")
    session_env["BD_RECORD"] = str(tmp_path / "bd-record.jsonl")
    session_env["SPAWN_RECORD"] = str(tmp_path / "spawn-record.jsonl")
    sessionstart = subprocess.run(
        [sys.executable, str(BIN / "execution_reconcile.py")],
        input=json.dumps(
            {
                "session_id": SESSION,
                "cwd": str(session_repo),
                "hook_event_name": "SessionStart",
                "source": "startup",
            }
        ),
        cwd=session_repo,
        env=session_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert sessionstart.returncode == 0
    assert "unresolved" in sessionstart.stdout or "invalid actor" in sessionstart.stdout
    assert session_ledger.read_bytes() == before_sessionstart


def test_valid_actor_without_ledger_never_falls_back_to_parent_execution_state(
    tmp_path,
) -> None:
    harness_root = tmp_path / "harness"
    repo = tmp_path / "repo"
    repo.mkdir()
    actor_dir = _establish_actor_dir(harness_root, repo)
    assert not (actor_dir / "executions.json").exists()
    parent_ledger = harness_root / "threads" / SESSION / "executions.json"
    prepared = subprocess.run(
        [
            sys.executable,
            str(BIN / "delegation_hook.py"),
            "prepare",
            "--ledger-path",
            str(parent_ledger),
            "--bead-id",
            CHILD,
            "--session",
            SESSION,
            "--host",
            "claude",
            "--agent-name",
            "parent-only-worker",
            "--execution-id",
            "exec-parent-only-fallback",
            "--watchdog-id",
            "watch-parent-only-fallback",
            "--now",
            "2026-08-09T20:00:00Z",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert prepared.returncode == 0, prepared.stderr
    before = parent_ledger.read_bytes()
    fake_bin, bd_record, spawn_record = _fake_bd_and_claude(
        tmp_path, {CHILD: ROOT_BEAD}
    )
    env = _actor_env(harness_root)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["BEADS_FIXTURE"] = str(tmp_path / "beads-fixture.json")
    env["BD_RECORD"] = str(bd_record)
    env["SPAWN_RECORD"] = str(spawn_record)
    pretool = subprocess.run(
        [sys.executable, str(BIN / "delegation_hook.py")],
        input=json.dumps(
            {
                "session_id": SESSION,
                "cwd": str(repo),
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
                "tool_use_id": "toolu-valid-actor-parent-fallback",
                "tool_input": {
                    "name": "parent-only-worker",
                    "run_in_background": True,
                },
            }
        ),
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert pretool.returncode == 0
    assert (
        json.loads(pretool.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    )
    assert parent_ledger.read_bytes() == before
    assert not bd_record.exists()

    sessionstart = subprocess.run(
        [sys.executable, str(BIN / "execution_reconcile.py")],
        input=json.dumps(
            {
                "session_id": SESSION,
                "cwd": str(repo),
                "hook_event_name": "SessionStart",
                "source": "startup",
            }
        ),
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert sessionstart.returncode == 0
    assert "missing or untrusted" in sessionstart.stdout
    assert parent_ledger.read_bytes() == before
    assert not (actor_dir / "executions.json").exists()


def test_actor_managed_wake_and_public_stop_use_global_supervisor_health(
    tmp_path,
) -> None:
    harness_root = tmp_path / "harness"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    actor_dir = _establish_actor_dir(harness_root, repo)
    _mode(actor_dir, repo)
    execution = _execution("exec-actor-1")
    execution["bead_id"] = CHILD
    live_now = dt.datetime.now(UTC)
    queued_at = live_now - dt.timedelta(minutes=5)
    execution["queued_at"] = queued_at.isoformat()
    execution["started_at"] = (queued_at + dt.timedelta(seconds=5)).isoformat()
    execution["last_activity_at"] = (live_now - dt.timedelta(minutes=1)).isoformat()
    execution["start_deadline"] = (queued_at + dt.timedelta(minutes=2)).isoformat()
    execution["idle_deadline"] = (live_now + dt.timedelta(minutes=14)).isoformat()
    execution["hard_deadline"] = (queued_at + dt.timedelta(hours=2)).isoformat()
    ledger = _ledger(execution)
    ledger["parent_session_id"] = SESSION
    ledger["updated_at"] = live_now.isoformat()
    _write_json(actor_dir / "executions.json", ledger)
    _write_json(
        harness_root / "supervisor-health.json",
        _health(completed_generation=11, installation_id=INSTALLATION),
    )

    fake_bin, _bd_record, _spawn_record = _fake_bd_and_claude(
        tmp_path, {ROOT_BEAD: ROOT_BEAD}
    )
    # This public Stop needs the exact root record without a parent field.
    bd = fake_bin / "bd"
    bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        f"if args == ['show', {ROOT_BEAD!r}]:\n"
        f"    print(json.dumps([{{'id': {ROOT_BEAD!r}, 'status': 'in_progress'}}]))\n"
        "elif args and args[0] in {'ready', 'blocked'}:\n"
        "    print('[]')\n"
        "else:\n"
        "    raise SystemExit(73)\n",
        encoding="utf-8",
    )
    bd.chmod(0o755)
    env = _actor_env(harness_root)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    bridge = subprocess.run(
        [sys.executable, str(BIN / "schedule_wakeup_bridge.py")],
        input=json.dumps(
            {
                "session_id": SESSION,
                "tool_name": "ScheduleWakeup",
                "tool_input": {"delaySeconds": 600, "prompt": "resume actor work"},
            }
        ),
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert bridge.returncode == 0, bridge.stderr
    scheduled = json.loads((actor_dir / "scheduled.json").read_text())
    managed = [
        item for item in scheduled if item.get("created_by") == "execution-supervisor"
    ]
    assert len(managed) == 1
    registered_at = dt.datetime.fromisoformat(managed[0]["registered_at"])
    _write_json(
        harness_root / "supervisor-health.json",
        _health(
            completed_generation=12,
            reconcile_started_at=(
                registered_at + dt.timedelta(microseconds=1)
            ).isoformat(),
            last_successful_reconcile_started_at=(
                registered_at + dt.timedelta(microseconds=1)
            ).isoformat(),
            last_successful_reconcile_at=(
                registered_at + dt.timedelta(microseconds=2)
            ).isoformat(),
        ),
    )
    stopped = subprocess.run(
        [sys.executable, str(BIN / "stop_hook.py")],
        input=json.dumps({"session_id": SESSION, "transcript_path": ""}),
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert stopped.returncode == 0, stopped.stderr
    assert stopped.stdout == ""

    (harness_root / "supervisor-health.json").unlink()
    blocked = subprocess.run(
        [sys.executable, str(BIN / "stop_hook.py")],
        input=json.dumps({"session_id": SESSION, "transcript_path": ""}),
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert blocked.returncode == 0
    assert "supervisor_health_unresolved" in blocked.stdout
