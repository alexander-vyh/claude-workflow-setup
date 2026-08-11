#!/usr/bin/env python3
"""Public RED controls for the Task 7 actor-merge review boundaries."""

from __future__ import annotations

import ast
import datetime as dt
import inspect
import io
import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger  # noqa: E402
import execution_supervisor  # noqa: E402
import schedule_wakeup_bridge  # noqa: E402
import session_isolation  # noqa: E402
import stop_hook  # noqa: E402
import thread_identity  # noqa: E402
import wakeup_waker  # noqa: E402
from harness.tests import test_execution_stop_gate as stop_oracle  # noqa: E402

UTC = dt.timezone.utc
SESSION = "actor-merge-parent-73"
ACTOR = "actor.merge.review"
ROOT_BEAD = "escapement-actor-merge-root"
CHILD_BEAD = "escapement-actor-merge-child"
REGISTERED_AT = dt.datetime(2026, 8, 9, 20, 9, 50, tzinfo=UTC)


def _write_json(path: pathlib.Path, value: object, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2 if pretty else None) + ("\n" if pretty else ""),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _mode(state_dir: pathlib.Path, repo: pathlib.Path, session_id: str = SESSION) -> None:
    _write_json(
        state_dir / "session_mode.json",
        {
            "mode": "task",
            "session_id": session_id,
            "repo_cwd": str(repo),
            "task_id": CHILD_BEAD,
            "parent_id": ROOT_BEAD,
        },
    )


def _empty_ledger(state_dir: pathlib.Path) -> None:
    _write_json(state_dir / "executions.json", execution_ledger.new_ledger(SESSION))


def _health(generation: int, installation: str = "root-health-authority") -> dict:
    return {
        "reconcile_started_at": "2026-08-09T20:09:55Z",
        "last_successful_reconcile_started_at": "2026-08-09T20:09:55Z",
        "last_successful_reconcile_at": "2026-08-09T20:10:00Z",
        "completed_generation": generation,
        "installation_id": installation,
        "counts": {
            "successful_passes": generation,
            "threads": 1,
            "recoveries": 0,
        },
    }


def _due_check(command: str) -> dict:
    return {
        "wake_at": "2000-01-01T00:00:00+00:00",
        "kind": "check",
        "command": command,
        "poll_interval": 600,
        "escalate_prompt": "condition met",
        "thread_id": SESSION,
        "created_by": "actor-merge-review",
        "crash_count": 0,
    }


def _actor_state(root: pathlib.Path) -> pathlib.Path:
    return root / "threads" / SESSION / "agents" / "actor-review-key"


def _run_waker(root: pathlib.Path, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(BIN / "wakeup_waker.py"),
            "--threads-root",
            str(root / "threads"),
            "--fire",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_due_actor_check_executes_once_in_exact_trusted_repo_and_rearms(tmp_path):
    root = tmp_path / "harness"
    repo = tmp_path / "trusted-repo"
    ambient = tmp_path / "daemon-cwd"
    repo.mkdir()
    ambient.mkdir()
    state_dir = _actor_state(root)
    sentinel = tmp_path / "observed-check-cwd"
    script = (
        "import os, pathlib; "
        f"pathlib.Path({str(sentinel)!r}).write_text(os.getcwd()); "
        "raise SystemExit(1)"
    )
    entry = _due_check(f"{sys.executable} -c {shlex.quote(script)}")
    _write_json(state_dir / "scheduled.json", [entry])
    _mode(state_dir, repo)
    _empty_ledger(state_dir)

    result = _run_waker(root, ambient)

    assert result.returncode == 0, result.stderr
    assert sentinel.read_text(encoding="utf-8") == str(repo.resolve())
    kept = json.loads((state_dir / "scheduled.json").read_text(encoding="utf-8"))
    assert len(kept) == 1
    assert kept[0]["command"] == entry["command"]
    assert kept[0]["wake_at"] != entry["wake_at"]
    health = json.loads((root / "supervisor-health.json").read_text())
    assert health["completed_generation"] == 1


@pytest.mark.parametrize(
    "context",
    ["missing", "foreign", "malformed", "mode-0666", "symlink"],
    ids=str,
)
def test_due_actor_check_without_exact_context_is_byte_exact_and_never_runs(
    tmp_path, context
):
    root = tmp_path / "harness"
    repo = tmp_path / "trusted-repo"
    ambient = tmp_path / "daemon-cwd"
    repo.mkdir()
    ambient.mkdir()
    state_dir = _actor_state(root)
    sentinel = tmp_path / "check-must-not-run"
    script = (
        f"import pathlib; pathlib.Path({str(sentinel)!r}).write_text('ran'); "
        "raise SystemExit(17)"
    )
    entry = _due_check(f"{sys.executable} -c {shlex.quote(script)}")
    schedule = state_dir / "scheduled.json"
    _write_json(schedule, [entry], pretty=True)
    _empty_ledger(state_dir)
    if context == "foreign":
        _mode(state_dir, repo, session_id="foreign-parent-session")
    elif context == "malformed":
        mode_path = state_dir / "session_mode.json"
        mode_path.write_text("{malformed", encoding="utf-8")
        mode_path.chmod(0o600)
    elif context == "mode-0666":
        _mode(state_dir, repo)
        (state_dir / "session_mode.json").chmod(0o666)
    elif context == "symlink":
        target = tmp_path / "redirected-session-mode.json"
        _write_json(
            target,
            {
                "mode": "task",
                "session_id": SESSION,
                "repo_cwd": str(repo),
                "task_id": CHILD_BEAD,
                "parent_id": ROOT_BEAD,
            },
        )
        (state_dir / "session_mode.json").symlink_to(target)
    health_path = root / "supervisor-health.json"
    _write_json(health_path, _health(7))
    schedule_before = schedule.read_bytes()
    health_before = health_path.read_bytes()

    result = _run_waker(root, ambient)

    assert result.returncode == 1
    assert not sentinel.exists()
    assert schedule.read_bytes() == schedule_before
    assert health_path.read_bytes() == health_before


def _seed_poll_state(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, dict]:
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    repo.mkdir()
    state_dir = root / "threads" / SESSION
    entry = _due_check("poll external state")
    _write_json(state_dir / "scheduled.json", [entry])
    _mode(state_dir, repo)
    _empty_ledger(state_dir)
    _write_json(root / "supervisor-health.json", _health(7))
    return root, state_dir, entry


@pytest.mark.parametrize("fault", ["exception", "timeout"], ids=str)
def test_runner_infrastructure_failure_rearms_but_exits_one_without_health_advance(
    tmp_path, monkeypatch, fault
):
    root, state_dir, entry = _seed_poll_state(tmp_path)
    health_path = root / "supervisor-health.json"
    health_before = health_path.read_bytes()

    def failed_runner(_command: str, **_kwargs):
        if fault == "timeout":
            raise subprocess.TimeoutExpired("poll external state", 120)
        raise RuntimeError("runner infrastructure failed")

    monkeypatch.setattr(wakeup_waker.wd, "_default_runner", failed_runner)

    result = wakeup_waker.main(
        ["--threads-root", str(root / "threads"), "--fire"]
    )

    kept = json.loads((state_dir / "scheduled.json").read_text())
    assert result == 1
    assert len(kept) == 1
    assert kept[0]["command"] == entry["command"]
    assert kept[0]["wake_at"] != entry["wake_at"]
    assert health_path.read_bytes() == health_before


def test_ordinary_nonzero_check_is_a_healthy_poll_and_advances_health(
    tmp_path, monkeypatch
):
    root, state_dir, entry = _seed_poll_state(tmp_path)
    monkeypatch.setattr(
        wakeup_waker.wd,
        "_default_runner",
        lambda _command, **_kwargs: (17, "not ready"),
    )

    result = wakeup_waker.main(
        ["--threads-root", str(root / "threads"), "--fire"]
    )

    kept = json.loads((state_dir / "scheduled.json").read_text())
    health = json.loads((root / "supervisor-health.json").read_text())
    assert result == 0
    assert len(kept) == 1
    assert kept[0]["command"] == entry["command"]
    assert kept[0]["wake_at"] != entry["wake_at"]
    assert health["completed_generation"] == 8
    assert health["counts"]["successful_passes"] == 8
    assert health["installation_id"] == "root-health-authority"


def _calls_shared_enumerator(function) -> bool:
    tree = ast.parse(inspect.getsource(function))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "iter_state_dirs":
            return True
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "iter_state_dirs"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "thread_identity"
        ):
            return True
    return False


def _independently_walks_filesystem(function) -> bool:
    tree = ast.parse(inspect.getsource(function))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "glob",
            "rglob",
            "iterdir",
            "listdir",
            "scandir",
            "walk",
        }:
            return True
        if isinstance(node.func, ast.Name) and node.func.id in {
            "listdir",
            "scandir",
            "walk",
        }:
            return True
    return False


def _module_filesystem_walkers(module) -> set[str]:
    walkers = set()
    for name, function in inspect.getmembers(module, inspect.isfunction):
        if function.__module__ != module.__name__:
            continue
        if _independently_walks_filesystem(function):
            walkers.add(name)
    return walkers


def _module_attribute_walker_probe(root: pathlib.Path):
    return os.listdir(root), os.scandir(root), os.walk(root)


def _reachable_module_functions(module, boundary) -> set[str]:
    pending = [boundary]
    seen: set[str] = set()
    while pending:
        function = pending.pop()
        name = function.__name__
        if name in seen:
            continue
        seen.add(name)
        tree = ast.parse(inspect.getsource(function))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called_name = node.func.id if isinstance(node.func, ast.Name) else None
            called = getattr(module, called_name, None) if called_name else None
            if (
                inspect.isfunction(called)
                and called.__module__ == module.__name__
                and called.__name__ not in seen
            ):
                pending.append(called)
    return seen


def test_one_thread_identity_enumerator_owns_exact_supported_state_depth(tmp_path):
    shared = getattr(thread_identity, "iter_state_dirs", None)
    assert callable(shared), "thread_identity must own iter_state_dirs"
    root = tmp_path / "threads"
    legacy = root / "legacy-session"
    actor_parent = root / "actor-session"
    actor = actor_parent / "agents" / "actor-key"
    deeper = actor / "nested" / "must-be-ignored"
    for path in (legacy, actor, deeper):
        path.mkdir(parents=True)

    assert list(shared(root)) == sorted((legacy, actor_parent, actor))

    consumers = (
        (wakeup_waker, wakeup_waker.iter_schedule_paths),
        (execution_supervisor, execution_supervisor.reconcile_all),
        (session_isolation, session_isolation.read_checkouts),
    )
    for module, boundary in consumers:
        imported = getattr(module, "iter_state_dirs", None)
        qualified = getattr(module, "thread_identity", None)
        assert imported is shared or getattr(qualified, "iter_state_dirs", None) is shared
        assert _calls_shared_enumerator(boundary)
        assert not _independently_walks_filesystem(boundary)
        walkers = _module_filesystem_walkers(module)
        reachable = _reachable_module_functions(module, boundary)
        assert not walkers & reachable
        assert not walkers, f"retained private filesystem walkers: {sorted(walkers)}"


def test_consumers_retain_no_private_or_module_qualified_filesystem_walker():
    assert _independently_walks_filesystem(_module_attribute_walker_probe)
    retained = {
        module.__name__: sorted(_module_filesystem_walkers(module))
        for module in (wakeup_waker, execution_supervisor, session_isolation)
        if _module_filesystem_walkers(module)
    }
    assert retained == {}


def _consumer_tree(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    root = tmp_path / "threads"
    legacy = root / "legacy-consumer"
    actor_parent = root / "actor-consumer"
    actor = actor_parent / "agents" / "actor-key"
    deep = actor / "nested" / "deep-decoy"
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    for path in (legacy, actor, deep):
        path.mkdir(parents=True)

    _write_json(legacy / "scheduled.json", [{"source": "legacy"}])
    _write_json(actor / "scheduled.json", [{"source": "actor"}])
    _write_json(deep / "scheduled.json", [{"source": "deep-decoy"}])
    _write_json(legacy / "checkout.json", {"session_id": "legacy-observed"})
    _write_json(actor / "checkout.json", {"session_id": "actor-observed"})
    _write_json(deep / "checkout.json", {"session_id": "deep-must-not-appear"})
    _write_json(
        legacy / "executions.json", execution_ledger.new_ledger(legacy.name)
    )
    _write_json(
        actor / "executions.json", execution_ledger.new_ledger(actor_parent.name)
    )
    _write_json(
        actor / "session_mode.json",
        {
            "mode": "task",
            "session_id": actor_parent.name,
            "repo_cwd": str(repo),
            "task_id": "actor-consumer-child",
            "parent_id": "actor-consumer-root",
        },
    )
    deep_ledger = deep / "executions.json"
    deep_ledger.write_text("{deep malformed decoy", encoding="utf-8")
    deep_ledger.chmod(0o600)
    return {
        "root": root,
        "legacy": legacy,
        "actor": actor,
        "deep": deep,
        "deep_ledger": deep_ledger,
    }


def _replace_consumer_enumerator(monkeypatch, module, replacement) -> None:
    shared = getattr(thread_identity, "iter_state_dirs", None)
    imported = getattr(module, "iter_state_dirs", None)
    qualified = getattr(module, "thread_identity", None)
    if callable(shared) and imported is shared:
        monkeypatch.setattr(module, "iter_state_dirs", replacement)
        return
    if callable(shared) and getattr(qualified, "iter_state_dirs", None) is shared:
        monkeypatch.setattr(qualified, "iter_state_dirs", replacement)
        return
    pytest.fail(f"{module.__name__} does not consume thread_identity.iter_state_dirs")


def test_waker_schedule_discovery_uses_shared_exact_state_dirs(tmp_path, monkeypatch):
    fixture = _consumer_tree(tmp_path)

    assert wakeup_waker.iter_schedule_paths(fixture["root"]) == sorted(
        (
            fixture["legacy"] / "scheduled.json",
            fixture["actor"] / "scheduled.json",
        )
    )

    _replace_consumer_enumerator(monkeypatch, wakeup_waker, lambda _root: [])
    assert wakeup_waker.iter_schedule_paths(fixture["root"]) == []


def test_checkout_discovery_uses_shared_exact_state_dirs(tmp_path, monkeypatch):
    fixture = _consumer_tree(tmp_path)

    records = session_isolation.read_checkouts(fixture["root"].parent)
    assert {record["session_id"] for record in records} == {
        "legacy-observed",
        "actor-observed",
    }

    _replace_consumer_enumerator(monkeypatch, session_isolation, lambda _root: [])
    assert session_isolation.read_checkouts(fixture["root"].parent) == []


def test_supervisor_discovery_counts_shared_legacy_and_actor_only(
    tmp_path, monkeypatch
):
    fixture = _consumer_tree(tmp_path)
    deep_before = fixture["deep_ledger"].read_bytes()
    now = dt.datetime(2026, 8, 9, 20, 10, tzinfo=UTC)

    result = execution_supervisor.reconcile_all(
        fixture["root"],
        now,
        "actor-merge-review",
        lambda _descriptor: pytest.fail("empty ledgers must not spawn"),
    )

    assert result["status"] == "ok"
    assert result["health"]["counts"]["threads"] == 2
    assert fixture["deep_ledger"].read_bytes() == deep_before

    _replace_consumer_enumerator(monkeypatch, execution_supervisor, lambda _root: [])
    second = execution_supervisor.reconcile_all(
        fixture["root"],
        now + dt.timedelta(seconds=1),
        "actor-merge-review",
        lambda _descriptor: pytest.fail("empty shared output must not spawn"),
    )
    assert second["status"] == "ok"
    assert second["health"]["counts"]["threads"] == 0
    assert fixture["deep_ledger"].read_bytes() == deep_before


def _set_state_variant(monkeypatch, root: pathlib.Path, tmp_path, variant: str):
    monkeypatch.setenv("HARNESS_ROOT", str(root))
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_AGENT_ID", raising=False)
    if variant == "override":
        state_dir = tmp_path / "external" / "odd" / "thread-state"
        monkeypatch.setenv("HARNESS_THREAD_DIR", str(state_dir))
    elif variant == "actor":
        monkeypatch.setenv("CLAUDE_AGENT_ID", ACTOR)
        state_dir = thread_identity.resolve_thread_dir(SESSION, root)
    else:
        state_dir = root / "threads" / SESSION
    return state_dir


def _active_ledger() -> tuple[dict, dict]:
    execution = stop_oracle._execution("exec-actor-merge-1")
    execution["bead_id"] = CHILD_BEAD
    ledger = stop_oracle._ledger(execution)
    ledger["parent_session_id"] = SESSION
    return execution, ledger


def _seed_managed_state(state_dir: pathlib.Path, repo: pathlib.Path) -> tuple[dict, dict]:
    execution, ledger = _active_ledger()
    _mode(state_dir, repo)
    _write_json(state_dir / "executions.json", ledger)
    return execution, ledger


@pytest.mark.parametrize("variant", ["legacy", "actor", "override"], ids=str)
def test_public_bridge_uses_configured_root_health_for_every_state_shape(
    tmp_path, monkeypatch, variant
):
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    repo.mkdir()
    state_dir = _set_state_variant(monkeypatch, root, tmp_path, variant)
    _seed_managed_state(state_dir, repo)
    _write_json(root / "supervisor-health.json", _health(11))
    if variant == "override":
        _write_json(
            tmp_path / "external" / "supervisor-health.json",
            _health(91, installation="adjacent-decoy"),
        )
    monkeypatch.setattr(schedule_wakeup_bridge, "_now", lambda: REGISTERED_AT)
    payload = {
        "session_id": SESSION,
        "tool_name": "ScheduleWakeup",
        "tool_input": {"delaySeconds": 600, "prompt": "continue actor work"},
        "tool_response": {},
    }

    monkeypatch.setattr(
        schedule_wakeup_bridge.sys, "stdin", io.StringIO(json.dumps(payload))
    )
    assert schedule_wakeup_bridge.main([]) == 0

    written = state_dir / "scheduled.json"
    assert written.is_file()
    entries = json.loads(written.read_text())
    assert len(entries) == 1
    assert entries[0]["supervisor_installation_id"] == "root-health-authority"
    assert entries[0]["supervisor_generation"] == 11


def _write_fake_bd(tmp_path: pathlib.Path, monkeypatch) -> None:
    fakebin = tmp_path / "fake-bin"
    fakebin.mkdir(exist_ok=True)
    bd = fakebin / "bd"
    bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"print(json.dumps([{{'id': {ROOT_BEAD!r}, 'status': 'closed'}}]))\n",
        encoding="utf-8",
    )
    bd.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")


@pytest.mark.parametrize("variant", ["legacy", "actor", "override"], ids=str)
def test_public_stop_uses_configured_root_health_for_every_state_shape(
    tmp_path, monkeypatch, capsys, variant
):
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".beads").mkdir()
    state_dir = _set_state_variant(monkeypatch, root, tmp_path, variant)
    execution, ledger = _seed_managed_state(state_dir, repo)
    live_now = dt.datetime.now(UTC)
    execution.update(
        {
            "queued_at": (live_now - dt.timedelta(minutes=10)).isoformat(),
            "started_at": (live_now - dt.timedelta(minutes=9)).isoformat(),
            "last_activity_at": (live_now - dt.timedelta(minutes=5)).isoformat(),
            "start_deadline": (live_now + dt.timedelta(minutes=1)).isoformat(),
            "idle_deadline": (live_now + dt.timedelta(minutes=10)).isoformat(),
            "hard_deadline": (live_now + dt.timedelta(hours=1)).isoformat(),
        }
    )
    ledger["updated_at"] = (live_now - dt.timedelta(seconds=1)).isoformat()
    _write_json(state_dir / "executions.json", ledger)
    wake = stop_oracle._wake(
        execution,
        wake_at=(live_now + dt.timedelta(minutes=5)).isoformat(),
        registered_at=(live_now - dt.timedelta(seconds=10)).isoformat(),
        thread_id=SESSION,
        parent_session_id=SESSION,
        supervisor_installation_id="root-health-authority",
        supervisor_generation=11,
    )
    _write_json(state_dir / "scheduled.json", [wake])
    live_health = _health(12)
    live_health.update(
        {
            "reconcile_started_at": (live_now - dt.timedelta(seconds=5)).isoformat(),
            "last_successful_reconcile_started_at": (
                live_now - dt.timedelta(seconds=5)
            ).isoformat(),
            "last_successful_reconcile_at": (
                live_now - dt.timedelta(seconds=2)
            ).isoformat(),
        }
    )
    _write_json(root / "supervisor-health.json", live_health)
    if variant == "override":
        _write_json(
            tmp_path / "external" / "supervisor-health.json",
            _health(91, installation="adjacent-decoy"),
        )
    _write_fake_bd(tmp_path, monkeypatch)
    monkeypatch.setattr(stop_hook, "HARNESS_ROOT", root)
    monkeypatch.setattr(stop_hook, "INCIDENTS_LOG", root / "incidents.jsonl")
    monkeypatch.setattr(stop_hook.session_isolation, "write_checkout", lambda *a: None)
    monkeypatch.setattr(
        stop_hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": SESSION, "transcript_path": ""})),
    )

    assert stop_hook.main() == 0

    output = capsys.readouterr().out
    assert '"decision": "block"' not in output
    assert "supervisor_health_unresolved" not in output
