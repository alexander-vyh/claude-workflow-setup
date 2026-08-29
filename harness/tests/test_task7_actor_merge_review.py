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

import schedule_wakeup_bridge  # noqa: E402
import session_isolation  # noqa: E402
import stop_hook  # noqa: E402
import thread_identity  # noqa: E402
import wakeup_waker  # noqa: E402

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

    result = _run_waker(root, ambient)

    assert result.returncode == 0, result.stderr
    assert sentinel.read_text(encoding="utf-8") == str(repo.resolve())
    kept = json.loads((state_dir / "scheduled.json").read_text(encoding="utf-8"))
    assert len(kept) == 1
    assert kept[0]["command"] == entry["command"]
    assert kept[0]["wake_at"] != entry["wake_at"]


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
    _write_json(root / "supervisor-health.json", _health(7))
    return root, state_dir, entry


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
        for module in (wakeup_waker, session_isolation)
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
        actor / "session_mode.json",
        {
            "mode": "task",
            "session_id": actor_parent.name,
            "repo_cwd": str(repo),
            "task_id": "actor-consumer-child",
            "parent_id": "actor-consumer-root",
        },
    )
    return {
        "root": root,
        "legacy": legacy,
        "actor": actor,
        "deep": deep,
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

