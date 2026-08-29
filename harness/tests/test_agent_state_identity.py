#!/usr/bin/env python3
"""Actor-scoped continuation state (bead escapement-egc).

Test Oracle Brief
-----------------
1. Business invariant: a Claude subagent sharing its parent's session id reads and
   writes only its own continuation state; it cannot replace or satisfy the
   parent's contract, task mode, watermark, wakeup, checkout, or Stop decision.
2. Independent source of truth: literal files below a temporary HARNESS_ROOT and
   the public hook/CLI exit decisions. Expected separation is derived from the
   distinct parent/actor identities, not a production path-builder.
3. Constraints: HARNESS_THREAD_DIR is an exact override; absent CLAUDE_AGENT_ID
   preserves threads/{session}; valid actor ids are stable across processes and
   cwd/PYTHONHASHSEED; invalid present ids never fall back to parent state; daemon
   scans include only supported parent and actor state shapes.
4. Invalid solutions: writer-only fixes; contract-only fixes; Python hash(); a
   lossy sanitized actor id; invalid-id fallback; one-level daemon scans; scanning
   arbitrary recursive scheduled.json files.
5. Fragile shortcut rejected: key only init_contract.py by CLAUDE_AGENT_ID. The
   Stop, verify, task-mode, watermark, wakeup, checkout, and waker checks below
   still fail that implementation.
6. Negative controls: no-agent parent compatibility; invalid/blank actor ids;
   parent-pass/actor-fail Stop; an unrelated deeply nested schedule.
7. Positive controls: parent and two actors retain independent contracts; actor
   verify updates only its own contract; the waker fires exactly actor B's due
   entry while preserving parent A.
8. Missing handling: absent actor id is explicitly legacy-compatible; present
   invalid actor identity fails closed with no parent read or write.
9. Final verification: this suite plus the full harness suite and installed
   plugin parent/subagent smoke after deployment.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(BIN))

import derive_contract  # noqa: E402
import session_isolation  # noqa: E402
import wakeup_waker  # noqa: E402
from would_block_stop import thread_dir_for_session  # noqa: E402

SESSION = "shared-session-42"
AGENT_A = "oracle.alpha"
AGENT_B = "oracle-alpha"


def _load_rendered_waker():
    path = (
        REPO / "plugins" / "escapement-claude" / "harness" / "bin" / "wakeup_waker.py"
    )
    spec = importlib.util.spec_from_file_location("rendered_wakeup_waker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WAKER_SURFACES = (
    pytest.param(wakeup_waker, id="canonical"),
    pytest.param(_load_rendered_waker(), id="rendered-claude"),
)


def _env(root: pathlib.Path, agent: str | None, *, seed: str = "17") -> dict[str, str]:
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(root)
    env["CLAUDE_CODE_SESSION_ID"] = SESSION
    env["PYTHONHASHSEED"] = seed
    env.pop("HARNESS_THREAD_DIR", None)
    if agent is None:
        env.pop("CLAUDE_AGENT_ID", None)
    else:
        env["CLAUDE_AGENT_ID"] = agent
    return env


def _run(
    script: str,
    args: list[str],
    root: pathlib.Path,
    agent: str | None,
    *,
    payload: dict | None = None,
    cwd: pathlib.Path | None = None,
    seed: str = "17",
) -> subprocess.CompletedProcess[str]:
    executable = BIN / script
    command = (
        [sys.executable, str(executable), *args]
        if executable.suffix == ".py"
        else [str(executable), *args]
    )
    return subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        cwd=str(cwd or REPO),
        env=_env(root, agent, seed=seed),
        timeout=30,
    )


def _init(
    root: pathlib.Path,
    agent: str | None,
    goal: str,
    verify: str = "test -f definitely-not-present",
) -> pathlib.Path:
    before = set(root.rglob("contract.json")) if root.exists() else set()
    proc = _run("init_contract.py", ["--goal", goal, "--verify", verify], root, agent)
    assert proc.returncode == 0, proc.stderr
    created = set(root.rglob("contract.json")) - before
    assert len(created) == 1, (proc.stdout, created)
    return created.pop()


def _contract(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


def _passing_contract(goal: str) -> dict:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        "goal": goal,
        "verification_command": "test -d .",
        "expected_exit": 0,
        "source": "agent-declared",
        "thread_id": SESSION,
        "created_at": now,
        "last_run": {"exit_code": 0, "timestamp": now, "output_excerpt": ""},
    }


def test_parent_and_same_session_agents_keep_independent_contracts(tmp_path) -> None:
    """Break caught: agent B overwrites parent/A because all key only on session."""
    parent = _init(tmp_path, None, "parent")
    a = _init(tmp_path, AGENT_A, "agent-a")
    b = _init(tmp_path, AGENT_B, "agent-b")

    assert parent == tmp_path / "threads" / SESSION / "contract.json"
    assert len({parent.parent, a.parent, b.parent}) == 3
    assert a.parent.parent == b.parent.parent
    assert {_contract(p)["goal"] for p in (parent, a, b)} == {
        "parent",
        "agent-a",
        "agent-b",
    }


def test_actor_resolution_is_stable_across_cwd_process_and_hash_seed(tmp_path) -> None:
    """Break caught: hash(actor) changes the state path across Python processes."""
    code = (
        "import pathlib,sys; sys.path.insert(0,sys.argv[1]); "
        "from would_block_stop import thread_dir_for_session; "
        "print(thread_dir_for_session(sys.argv[2], pathlib.Path(sys.argv[3])))"
    )
    outputs = []
    for cwd, seed in ((REPO, "1"), (tmp_path, "911")):
        proc = subprocess.run(
            [sys.executable, "-c", code, str(BIN), SESSION, str(tmp_path)],
            cwd=str(cwd),
            env=_env(tmp_path, AGENT_A, seed=seed),
            text=True,
            capture_output=True,
            check=True,
        )
        outputs.append(proc.stdout.strip())
    assert outputs[0] == outputs[1]
    resolved = pathlib.Path(outputs[0])
    assert resolved.is_relative_to(tmp_path / "threads" / SESSION)
    assert resolved != tmp_path / "threads" / SESSION


def test_explicit_thread_override_wins_even_with_invalid_actor(
    tmp_path, monkeypatch
) -> None:
    exact = tmp_path / "explicit" / "thread"
    monkeypatch.setenv("HARNESS_THREAD_DIR", str(exact))
    monkeypatch.setenv("CLAUDE_AGENT_ID", "  ")
    assert thread_dir_for_session(SESSION, tmp_path) == exact

    env = _env(tmp_path, "  ")
    env["HARNESS_THREAD_DIR"] = str(exact)
    init = subprocess.run(
        [
            sys.executable,
            str(BIN / "init_contract.py"),
            "--goal",
            "exact",
            "--verify",
            "test -f absent",
        ],
        env=env,
        text=True,
        capture_output=True,
    )
    start = subprocess.run(
        [sys.executable, str(BIN / "session_watermark.py")],
        input=json.dumps({"session_id": SESSION, "cwd": str(REPO)}),
        env=env,
        text=True,
        capture_output=True,
    )
    stop = subprocess.run(
        [sys.executable, str(BIN / "stop_hook.py")],
        input=json.dumps({"session_id": SESSION, "transcript_path": ""}),
        env=env,
        text=True,
        capture_output=True,
    )

    assert init.returncode == 0 and start.returncode == 0
    assert (exact / "contract.json").is_file()
    assert (exact / "scope_watermark.json").is_file()
    decision = json.loads(stop.stdout)
    assert decision["decision"] == "block"
    assert "invalid_actor_identity" not in decision["reason"]


@pytest.mark.parametrize(
    "bad_actor", ["", "   ", "../parent", "agent/name", "agent\nname"]
)
def test_present_invalid_actor_never_falls_back_to_parent(
    tmp_path,
    monkeypatch,
    bad_actor,
) -> None:
    """Break caught: sanitizing or ignoring an invalid actor reads parent state."""
    parent = tmp_path / "threads" / SESSION
    parent.mkdir(parents=True)
    (parent / "contract.json").write_text(json.dumps(_passing_contract("parent")))

    init = _run(
        "init_contract.py",
        ["--goal", "bad actor", "--verify", "test -d ."],
        tmp_path,
        bad_actor,
    )
    verify = _run("verify", [], tmp_path, bad_actor)
    task = _run(
        "task_mode_entry.py",
        [],
        tmp_path,
        bad_actor,
            payload={
                "session_id": SESSION,
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "bd update escapement-egc --claim"},
                "tool_response": {"interrupted": False, "stderr": "", "stdout": ""},
            },
    )
    watermark = _run(
        "session_watermark.py",
        [],
        tmp_path,
        bad_actor,
        payload={"session_id": SESSION, "cwd": str(REPO)},
    )
    schedule = _run(
        "schedule_wakeup_bridge.py",
        [],
        tmp_path,
        bad_actor,
        payload={
            "session_id": SESSION,
            "tool_name": "ScheduleWakeup",
            "tool_input": {"delaySeconds": 600},
        },
    )
    stop = _run(
        "stop_hook.py",
        [],
        tmp_path,
        bad_actor,
        payload={"session_id": SESSION, "transcript_path": ""},
    )

    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv("CLAUDE_AGENT_ID", bad_actor)
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    bead = {
        "id": "escapement-egc",
        "title": "bad actor",
        "acceptance_criteria": "```verify\ntest -d .\n```",
    }
    derived = derive_contract.main(
        ["--bead", "escapement-egc"],
        _fetch=lambda _id: bead,
    )

    assert all(
        proc.returncode != 0 for proc in (init, verify, task, watermark, schedule)
    )
    assert derived != 0
    assert _contract(parent / "contract.json")["goal"] == "parent"
    assert not list(parent.glob("agents/*/contract.json"))
    assert not (parent / "session_mode.json").exists()
    assert not (parent / "scope_watermark.json").exists()
    assert not (parent / "scheduled.json").exists()
    decision = json.loads(stop.stdout)
    assert decision["decision"] == "block"
    assert "actor" in decision["reason"].lower()


def test_verify_updates_only_calling_actor_contract(tmp_path) -> None:
    parent = _init(tmp_path, None, "parent", "test -d .")
    actor = _init(tmp_path, AGENT_A, "actor", "test -d .")
    parent_before = parent.read_bytes()

    proc = _run("verify", [], tmp_path, AGENT_A)

    assert proc.returncode == 0, proc.stderr
    assert parent.read_bytes() == parent_before
    assert _contract(actor)["last_run"]["exit_code"] == 0


def test_stop_uses_actor_failure_not_green_parent(tmp_path) -> None:
    parent_dir = tmp_path / "threads" / SESSION
    parent_dir.mkdir(parents=True)
    (parent_dir / "contract.json").write_text(json.dumps(_passing_contract("parent")))
    actor_contract = _init(tmp_path, AGENT_A, "actor red")

    proc = _run(
        "stop_hook.py",
        [],
        tmp_path,
        AGENT_A,
        payload={"session_id": SESSION, "transcript_path": ""},
    )

    assert _contract(actor_contract)["goal"] == "actor red"
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert "no_completion_or_resumption_proof" in out["reason"]


def test_stop_allows_green_actor_despite_red_parent(tmp_path) -> None:
    """Positive control: isolation must not make every subagent Stop red."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("clean")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)

    root = tmp_path / "harness"
    parent = _init(root, None, "parent red")
    actor = _init(root, AGENT_A, "actor")
    actor.write_text(json.dumps(_passing_contract("actor")))

    proc = _run(
        "stop_hook.py",
        [],
        root,
        AGENT_A,
        payload={"session_id": SESSION, "transcript_path": ""},
        cwd=repo,
    )

    assert _contract(parent)["last_run"] is None
    assert proc.returncode == 0 and proc.stdout == ""


def test_actor_writers_share_one_state_directory(tmp_path) -> None:
    """Break caught: individual writer fixes disagree on the actor namespace."""
    contract = _init(tmp_path, AGENT_A, "actor")
    actor_dir = contract.parent

    claim = {
        "session_id": SESSION,
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "bd update escapement-egc --claim"},
        "tool_response": {"interrupted": False, "stderr": "", "stdout": ""},
    }
    assert (
        _run("task_mode_entry.py", [], tmp_path, AGENT_A, payload=claim).returncode == 0
    )
    start = {"session_id": SESSION, "cwd": str(REPO)}
    assert (
        _run("session_watermark.py", [], tmp_path, AGENT_A, payload=start).returncode
        == 0
    )
    wake = {
        "session_id": SESSION,
        "tool_name": "ScheduleWakeup",
        "tool_input": {"delaySeconds": 600, "prompt": "resume actor"},
    }
    assert (
        _run(
            "schedule_wakeup_bridge.py", [], tmp_path, AGENT_A, payload=wake
        ).returncode
        == 0
    )

    assert (actor_dir / "session_mode.json").is_file()
    assert (actor_dir / "scope_watermark.json").is_file()
    assert (actor_dir / "checkout.json").is_file()
    assert (actor_dir / "scheduled.json").is_file()
    assert not (tmp_path / "threads" / SESSION / "session_mode.json").exists()


def test_derive_contract_uses_actor_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv("CLAUDE_AGENT_ID", AGENT_A)
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    bead = {
        "id": "escapement-egc",
        "title": "isolate actor",
        "acceptance_criteria": "```verify\ntest -d .\n```",
    }
    assert (
        derive_contract.main(["--bead", "escapement-egc"], _fetch=lambda _id: bead) == 0
    )
    contracts = list((tmp_path / "threads" / SESSION).glob("agents/*/contract.json"))
    assert len(contracts) == 1
    assert _contract(contracts[0])["source"] == "bead-derived"


def test_nested_actor_checkouts_participate_in_collision_detection(tmp_path) -> None:
    root = tmp_path / "harness"
    actor_dir = root / "threads" / SESSION / "agents" / "actor-key"
    actor_dir.mkdir(parents=True)
    (actor_dir / "checkout.json").write_text(
        json.dumps(
            {
                "session_id": SESSION,
                "worktree_root": "/repo",
                "git_common_dir": "/repo/.git",
                "is_linked_worktree": False,
                "heartbeat": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        )
    )
    records = session_isolation.read_checkouts(root)
    assert [r["worktree_root"] for r in records] == ["/repo"]


@pytest.mark.parametrize("waker", WAKER_SURFACES)
def test_waker_fires_only_due_actor_schedule_and_ignores_arbitrary_depth(
    tmp_path,
    monkeypatch,
    waker,
) -> None:
    root = tmp_path / "threads"
    parent = root / "parent" / "scheduled.json"
    actor_a = root / SESSION / "agents" / "state-key-a" / "scheduled.json"
    actor_b = root / SESSION / "agents" / "state-key-b" / "scheduled.json"
    unrelated = root / "x" / "arbitrary" / "depth" / "scheduled.json"
    for path in (parent, actor_a, actor_b, unrelated):
        path.parent.mkdir(parents=True, exist_ok=True)
    parent_entry = {
        "wake_at": "2999-01-01T00:00:00+00:00",
        "kind": "resume",
        "prompt": "parent",
        "thread_id": "parent",
        "created_by": "x",
        "crash_count": 0,
    }
    actor_a_entry = {
        "wake_at": "2999-01-01T00:00:00+00:00",
        "kind": "resume",
        "prompt": "future-actor-prompt",
        "thread_id": SESSION,
        "created_by": "x",
        "crash_count": 0,
    }
    actor_b_entry = {
        "wake_at": "2000-01-01T00:00:00+00:00",
        "kind": "resume",
        "prompt": "due-actor-prompt-unique",
        "thread_id": SESSION,
        "created_by": "x",
        "crash_count": 0,
    }
    # Deliberately preserve distinct serializations. A semantic JSON equality
    # assertion misses sibling rewrites that perturb bytes/watchers/racing writers.
    parent.write_text(json.dumps([parent_entry], indent=2) + "\n")
    actor_a.write_text(json.dumps([actor_a_entry], separators=(",", ":")) + "\n")
    actor_b.write_text(json.dumps([actor_b_entry]))
    unrelated.write_text(json.dumps([actor_b_entry]))
    repo = tmp_path / "actor-b-repo"
    repo.mkdir()
    (actor_b.parent / "session_mode.json").write_text(
        json.dumps(
            {
                "mode": "task",
                "repo_cwd": str(repo),
                "task_id": "actor-b-task",
                "parent_id": "actor-b-root",
                "session_id": SESSION,
            }
        )
    )
    parent_before = parent.read_bytes()
    actor_a_before = actor_a.read_bytes()
    spawned: list[tuple[list[str], pathlib.Path]] = []
    monkeypatch.setattr(waker.ts, "is_trusted_file", lambda _path: True)
    monkeypatch.setattr(
        waker.subprocess,
        "Popen",
        lambda argv, cwd=None: spawned.append((argv, cwd)),
    )

    assert waker.main(["--fire", "--threads-root", str(root)]) == 0

    assert parent.read_bytes() == parent_before
    assert actor_a.read_bytes() == actor_a_before
    assert json.loads(actor_b.read_text()) == []
    assert json.loads(unrelated.read_text()) == [actor_b_entry]
    assert len(spawned) == 1
    assert spawned[0][0] == [
        "claude",
        "--resume",
        SESSION,
        "-p",
        "due-actor-prompt-unique",
    ]
    assert spawned[0][1] == repo.resolve()


@pytest.mark.parametrize("waker", WAKER_SURFACES)
@pytest.mark.parametrize("context_case", ["missing", "foreign-session"])
def test_actor_due_schedule_without_exact_session_context_fails_closed(
    tmp_path,
    monkeypatch,
    waker,
    context_case,
) -> None:
    """Actor discovery must validate the parent session, not the actor directory key."""
    root = tmp_path / "threads"
    schedule = root / SESSION / "agents" / "actor-key" / "scheduled.json"
    schedule.parent.mkdir(parents=True)
    entry = {
        "wake_at": "2000-01-01T00:00:00+00:00",
        "kind": "resume",
        "prompt": "must not resume",
        "thread_id": SESSION,
        "created_by": "x",
        "crash_count": 0,
    }
    schedule.write_text(json.dumps([entry], separators=(",", ":")) + "\n")
    before = schedule.read_bytes()
    if context_case == "foreign-session":
        repo = tmp_path / "repo"
        repo.mkdir()
        (schedule.parent / "session_mode.json").write_text(
            json.dumps(
                {
                    "mode": "task",
                    "repo_cwd": str(repo),
                    "task_id": "actor-child",
                    "parent_id": "actor-root",
                    "session_id": "foreign-parent-session",
                }
            )
        )

    launched: list[tuple[list[str], pathlib.Path]] = []
    monkeypatch.setattr(waker.ts, "is_trusted_file", lambda _path: True)
    monkeypatch.setattr(
        waker.subprocess,
        "Popen",
        lambda argv, cwd=None: launched.append((argv, cwd)),
    )

    assert waker.main(["--fire", "--threads-root", str(root)]) == 1
    assert launched == []
    assert schedule.read_bytes() == before
