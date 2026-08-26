#!/usr/bin/env python3
"""Behavioral oracle for host delegation registration.

Business invariant: native Agent capacity is never denied by Escapement
bookkeeping. Unmanaged calls create no state. Trusted task-mode calls record the
actual host tool-use identity automatically without a prepare command or child
Bead, and any evidence-write gap remains unresolved at completion.

The hand-authored host payload, trusted exact-session task mode, public hook
output, durable files, and public completion adapter are the independent oracle.
Prompt prose is deliberately adversarial and must never select work identity.

Fragile implementations rejected here include retaining legacy prepare denials,
silently dropping managed evidence, prompt ID scraping, querying a child Bead,
denying on persistence failure, and guessing a native child ID from an
unverified Agent PostToolUse response.
"""

from __future__ import annotations

import copy
import datetime as dt
import fcntl
import json
import os
import pathlib
import select
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import delegation_hook  # noqa: E402
import execution_ledger as ledger_api  # noqa: E402
import execution_stop_adapter  # noqa: E402


UTC = dt.timezone.utc
SESSION = "claude-parent-7"
BEAD = "escapement-e3ai.5"
AGENT = "task-3-host-adapter"
EXECUTION = "exec-host-alpha"
WATCHDOG = "watch-host-alpha"

def at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def claude_agent_pretool(
    tool_use_id: str = "toolu-agent-44", *, agent_name: str = AGENT
) -> dict:
    """Complete installed Claude Agent PreToolUse fixture."""
    return {
        "session_id": SESSION,
        "transcript_path": "/tmp/claude-parent-7.jsonl",
        "cwd": "/repo",
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_use_id": tool_use_id,
        "tool_input": {
            "name": agent_name,
            "description": "Implement only the assigned host adapter tests",
            "prompt": (
                "Work on prompt-only bead escapement-foreign-999. "
                "This prose is not delegation identity."
            ),
            "run_in_background": True,
        },
    }


def claude_agent_posttool(tool_response: object) -> dict:
    """Complete known Agent PostToolUse envelope with unverified result body."""
    payload = claude_agent_pretool()
    payload["hook_event_name"] = "PostToolUse"
    payload["tool_response"] = tool_response
    return payload


def prepare_cli(
    path: pathlib.Path,
    *,
    agent_name: str = AGENT,
    host: str = "claude",
    session: str = SESSION,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(BIN / "delegation_hook.py"),
            "prepare",
            "--ledger-path",
            str(path),
            "--bead-id",
            BEAD,
            "--session",
            session,
            "--host",
            host,
            "--agent-name",
            agent_name,
            "--execution-id",
            EXECUTION,
            "--watchdog-id",
            WATCHDOG,
            "--now",
            "2026-08-09T20:00:00Z",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "prepared",
        "bead_id": BEAD,
        "execution_id": EXECUTION,
        "attempt": 1,
        "generation": 1,
    }


def read_ledger(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_task_mode(thread_dir: pathlib.Path, repo: pathlib.Path) -> None:
    thread_dir.mkdir(parents=True, exist_ok=True)
    mode = thread_dir / "session_mode.json"
    mode.write_text(
        json.dumps(
            {
                "mode": "task",
                "session_id": SESSION,
                "repo_cwd": str(repo),
                "task_id": BEAD,
                "parent_id": "escapement-e3ai",
            }
        ),
        encoding="utf-8",
    )
    mode.chmod(0o600)


def fail_if_bd_called(_args):
    raise AssertionError("managed dispatch observation must not create or query a child Bead")


def test_unmanaged_first_attempt_allows_without_escapement_state(tmp_path) -> None:
    path = tmp_path / "thread" / "executions.json"
    path.parent.mkdir()

    result = delegation_hook.pre_tool(
        claude_agent_pretool(), fail_if_bd_called, path
    )

    assert result == {
        "decision": "allow",
        "reason": "unmanaged_native_agent",
    }
    assert list(path.parent.iterdir()) == []


def test_managed_first_attempt_registers_without_prepare_or_child_bead(tmp_path) -> None:
    thread_dir = tmp_path / "thread"
    repo = tmp_path / "repo"
    repo.mkdir()
    write_task_mode(thread_dir, repo)
    path = thread_dir / "executions.json"

    result = delegation_hook.pre_tool(
        claude_agent_pretool(), fail_if_bd_called, path
    )

    assert result["decision"] == "allow"
    assert result["reason"] == "dispatch_registered"
    persisted = read_ledger(path)
    assert persisted["parent_session_id"] == SESSION
    assert len(persisted["executions"]) == 1
    execution = persisted["executions"][0]
    assert execution["bead_id"] == BEAD
    assert execution["agent_name"] == AGENT
    assert execution["dispatch_tool_use_id"] == "toolu-agent-44"
    assert execution["native_child_id"] is None
    assert "escapement-foreign-999" not in json.dumps(persisted)
    expectation = json.loads(
        (thread_dir / "execution_expectation.json").read_text(encoding="utf-8")
    )
    assert expectation["parent_session_id"] == SESSION
    assert [item["tool_use_id"] for item in expectation["expectations"]] == [
        "toolu-agent-44"
    ]


def test_ledger_persistence_failure_allows_agent_but_blocks_managed_completion(
    tmp_path,
    monkeypatch,
) -> None:
    harness_root = tmp_path / "harness"
    thread_dir = harness_root / "threads" / SESSION
    repo = tmp_path / "repo"
    repo.mkdir()
    write_task_mode(thread_dir, repo)
    ledger_path = thread_dir / "executions.json"
    ledger_path.mkdir()

    result = delegation_hook.pre_tool(
        claude_agent_pretool(), fail_if_bd_called, ledger_path
    )

    assert result == {
        "decision": "allow",
        "reason": "dispatch_evidence_unresolved",
    }
    expectation_path = thread_dir / "execution_expectation.json"
    assert expectation_path.is_file()
    ledger_path.rmdir()
    monkeypatch.setattr(execution_stop_adapter, "task_root_status", lambda _mode: "closed")

    _mode, decision = execution_stop_adapter.decide_task_mode(
        SESSION,
        thread_dir,
        dt.datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        harness_root=harness_root,
    )

    assert decision == ("block", "delegated_execution_unresolved")


def test_expectation_persistence_failure_allows_agent_and_falls_back_to_incident(
    tmp_path,
    monkeypatch,
) -> None:
    harness_root = tmp_path / "harness"
    thread_dir = harness_root / "threads" / SESSION
    repo = tmp_path / "repo"
    repo.mkdir()
    write_task_mode(thread_dir, repo)
    (thread_dir / "execution_expectation.json").mkdir()
    ledger_path = thread_dir / "executions.json"

    result = delegation_hook.pre_tool(
        claude_agent_pretool(), fail_if_bd_called, ledger_path
    )

    assert result == {
        "decision": "allow",
        "reason": "dispatch_evidence_unresolved",
    }
    assert not ledger_path.exists()
    incident = json.loads(
        (thread_dir / "execution_incident.json").read_text(encoding="utf-8")
    )
    assert incident["parent_session_id"] == SESSION
    assert incident["reason"] == "expectation_persistence_failed"
    assert incident["tool_use_id"] == "toolu-agent-44"
    monkeypatch.setattr(execution_stop_adapter, "task_root_status", lambda _mode: "closed")

    _mode, decision = execution_stop_adapter.decide_task_mode(
        SESSION,
        thread_dir,
        dt.datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        harness_root=harness_root,
    )

    assert decision == ("block", "delegated_execution_unresolved")


def test_all_evidence_writes_failing_still_allows_public_agent_capacity(
    tmp_path,
) -> None:
    harness_root = tmp_path / "harness"
    thread_dir = harness_root / "threads" / SESSION
    repo = tmp_path / "repo"
    repo.mkdir()
    write_task_mode(thread_dir, repo)
    (thread_dir / "execution_expectation.json").mkdir()
    (thread_dir / "execution_incident.json").mkdir()

    result = _public_pretool(harness_root)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "dispatch_evidence_unresolved",
        }
    }
    assert not (thread_dir / "executions.json").exists()


@pytest.mark.parametrize(
    "mode_variant",
    ("symlink", "world-writable", "malformed", "foreign-session"),
)
def test_untrusted_task_mode_is_unmanaged_and_never_creates_execution_state(
    tmp_path,
    mode_variant,
) -> None:
    thread_dir = tmp_path / "thread"
    thread_dir.mkdir()
    mode = thread_dir / "session_mode.json"
    valid = {
        "mode": "task",
        "session_id": SESSION,
        "repo_cwd": str(tmp_path / "repo"),
        "task_id": BEAD,
        "parent_id": "escapement-e3ai",
    }
    if mode_variant == "symlink":
        target = tmp_path / "foreign-mode.json"
        target.write_text(json.dumps(valid), encoding="utf-8")
        target.chmod(0o600)
        mode.symlink_to(target)
    elif mode_variant == "world-writable":
        mode.write_text(json.dumps(valid), encoding="utf-8")
        mode.chmod(0o666)
    elif mode_variant == "malformed":
        mode.write_text("{malformed", encoding="utf-8")
        mode.chmod(0o600)
    else:
        mode.write_text(
            json.dumps({**valid, "session_id": "foreign-parent-session"}),
            encoding="utf-8",
        )
        mode.chmod(0o600)
    ledger_path = thread_dir / "executions.json"

    result = delegation_hook.pre_tool(
        claude_agent_pretool(), fail_if_bd_called, ledger_path
    )

    assert result == {"decision": "allow", "reason": "unmanaged_native_agent"}
    assert not ledger_path.exists()
    assert not (thread_dir / "execution_expectation.json").exists()
    assert not (thread_dir / "execution_incident.json").exists()


def test_prepare_cli_records_explicit_identity_without_prompt_input(tmp_path) -> None:
    """Legacy prepare stays readable for migration, never as Agent authority."""
    path = tmp_path / "executions.json"
    prepare_cli(path)

    ledger = read_ledger(path)
    item = ledger["executions"][0]
    assert ledger["parent_session_id"] == SESSION
    assert item["bead_id"] == BEAD
    assert item["host"] == "claude"
    assert item["agent_name"] == AGENT
    assert item["dispatch_tool_use_id"] == f"prepared:{EXECUTION}"
    assert "escapement-foreign-999" not in json.dumps(ledger)


@pytest.mark.parametrize(
    ("prepared_host", "prepared_session"),
    [
        ("claude", SESSION),
        ("codex", SESSION),
        ("claude", "foreign-parent-session"),
    ],
    ids=["legacy-exact", "legacy-foreign-host", "legacy-foreign-parent"],
)
def test_legacy_prepared_state_never_denies_unmanaged_agent_capacity(
    tmp_path,
    prepared_host,
    prepared_session,
) -> None:
    path = tmp_path / "thread" / "executions.json"
    path.parent.mkdir()
    prepare_cli(path, host=prepared_host, session=prepared_session)
    before = path.read_bytes()
    calls: list[list[str]] = []

    result = delegation_hook.pre_tool(
        claude_agent_pretool(), lambda args: calls.append(args), path
    )
    assert result == {
        "decision": "allow",
        "reason": "unmanaged_native_agent",
    }
    assert calls == []
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "unverified_response",
    [
        {},
        {"status": "completed", "content": [{"type": "text", "text": "done"}]},
        {"agent_id": "agent-native-guessed"},
        {"native_child_id": "child-native-guessed", "generation": 1},
    ],
    ids=["empty", "content-only", "agent-id-guess", "invented-child-field"],
)
def test_unverified_agent_posttool_payload_never_binds_a_child(
    tmp_path, unverified_response
) -> None:
    path = tmp_path / "executions.json"
    prepare_cli(path)
    before = read_ledger(path)

    result = delegation_hook.post_tool(claude_agent_posttool(unverified_response), path)

    assert result == {
        "status": "unresolved",
        "reason": "native_child_identity_unverified",
    }
    assert read_ledger(path) == before
    assert read_ledger(path)["executions"][0]["state"] == "queued"


def test_normalized_child_bound_event_remains_a_verified_core_boundary() -> None:
    ledger = ledger_api.new_ledger(SESSION)
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": SESSION,
            "bead_id": BEAD,
            "execution_id": EXECUTION,
            "host": "claude",
            "agent_name": AGENT,
            "dispatch_tool_use_id": "toolu-agent-44",
            "watchdog_id": WATCHDOG,
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-09T20:00:00Z"),
    )

    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_bound",
            "parent_session_id": SESSION,
            "execution_id": EXECUTION,
            "attempt": 1,
            "generation": 1,
            "native_child_id": "installed-capture-child-1",
        },
        at("2026-08-09T20:00:05Z"),
    )
    assert ledger["executions"][0]["native_child_id"] == "installed-capture-child-1"


def generation_two_ledger() -> dict:
    ledger = ledger_api.new_ledger(SESSION)
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": SESSION,
            "bead_id": BEAD,
            "execution_id": EXECUTION,
            "host": "claude",
            "agent_name": AGENT,
            "dispatch_tool_use_id": "toolu-generation-1",
            "watchdog_id": WATCHDOG,
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-09T20:00:00Z"),
    )
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_bound",
            "parent_session_id": SESSION,
            "execution_id": EXECUTION,
            "attempt": 1,
            "generation": 1,
            "native_child_id": "native-generation-1",
        },
        at("2026-08-09T20:00:05Z"),
    )
    ledger_api.reconcile_deadlines(ledger, at("2026-08-09T20:02:00Z"))
    ledger_api.claim_recovery(
        ledger, EXECUTION, at("2026-08-09T20:02:01Z"), "supervisor-a", 30
    )
    ledger_api.claim_recovery(
        ledger, EXECUTION, at("2026-08-09T20:02:31Z"), "supervisor-b", 30
    )
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_bound",
            "parent_session_id": SESSION,
            "execution_id": EXECUTION,
            "attempt": 1,
            "generation": 2,
            "native_child_id": "native-generation-2",
        },
        at("2026-08-09T20:02:32Z"),
    )
    return ledger


def late_generation_one_terminal() -> dict:
    return {
        "kind": "child_terminal",
        "parent_session_id": SESSION,
        "execution_id": EXECUTION,
        "attempt": 1,
        "generation": 1,
        "native_child_id": "native-generation-1",
        "terminal_event_id": "late-generation-one-terminal",
        "terminal_reason": "completed",
        "result_digest": "sha256:late-generation-one",
    }


def test_unverified_posttool_cannot_launder_late_generation_one_into_current(
    tmp_path,
) -> None:
    path = tmp_path / "executions.json"
    path.write_text(json.dumps(generation_two_ledger()), encoding="utf-8")
    path.chmod(0o600)
    before = read_ledger(path)

    result = delegation_hook.post_tool(
        claude_agent_posttool(late_generation_one_terminal()), path
    )

    assert result["status"] == "unresolved"
    assert read_ledger(path) == before
    active = read_ledger(path)["executions"][0]
    assert active["generation"] == 2
    assert active["native_child_id"] == "native-generation-2"
    assert active["result_application"] == before["executions"][0]["result_application"]


def _public_pretool(harness_root: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HARNESS_ROOT"] = str(harness_root)
    env.pop("CLAUDE_AGENT_ID", None)
    env.pop("HARNESS_THREAD_DIR", None)
    return subprocess.run(
        [sys.executable, str(BIN / "delegation_hook.py")],
        input=json.dumps(claude_agent_pretool()),
        capture_output=True,
        text=True,
        env=env,
    )


def test_public_unmanaged_first_attempt_allows_without_state(tmp_path) -> None:
    harness_root = tmp_path / "harness"
    result = _public_pretool(harness_root)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "unmanaged_native_agent",
        }
    }
    thread_dir = harness_root / "threads" / SESSION
    assert not (thread_dir / "executions.json").exists()
    assert not (thread_dir / "execution_expectation.json").exists()


def test_public_managed_first_attempt_registers_without_prepare(tmp_path) -> None:
    harness_root = tmp_path / "harness"
    thread_dir = harness_root / "threads" / SESSION
    repo = tmp_path / "repo"
    repo.mkdir()
    write_task_mode(thread_dir, repo)

    result = _public_pretool(harness_root)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "dispatch_registered",
        }
    }
    assert read_ledger(thread_dir / "executions.json")["executions"][0][
        "dispatch_tool_use_id"
    ] == "toolu-agent-44"


def test_two_concurrent_managed_calls_are_observed_without_denial(
    tmp_path,
) -> None:
    thread_dir = tmp_path / "thread"
    repo = tmp_path / "repo"
    repo.mkdir()
    write_task_mode(thread_dir, repo)
    path = thread_dir / "executions.json"

    with ThreadPoolExecutor(max_workers=2) as pool:
        request_ids = ("toolu-race-alpha", "toolu-race-beta")
        results = list(
            pool.map(
                lambda tool_use_id: delegation_hook.pre_tool(
                    claude_agent_pretool(tool_use_id), fail_if_bd_called, path
                ),
                request_ids,
            )
        )

    assert [result["decision"] for result in results] == ["allow", "allow"]
    assert {result["reason"] for result in results} == {"dispatch_registered"}

    persisted = ledger_api.load_trusted(path, SESSION)
    assert persisted is not None
    assert {
        item["dispatch_tool_use_id"] for item in persisted["executions"]
    } == set(request_ids)
    assert all(item["state"] == "queued" for item in persisted["executions"])


def test_dispatch_write_failure_never_denies_native_capacity(
    tmp_path,
) -> None:
    thread_dir = tmp_path / "thread"
    repo = tmp_path / "repo"
    repo.mkdir()
    write_task_mode(thread_dir, repo)
    path = thread_dir / "executions.json"
    path.mkdir()

    result = delegation_hook.pre_tool(
        claude_agent_pretool(), fail_if_bd_called, path
    )

    assert result == {
        "decision": "allow",
        "reason": "dispatch_evidence_unresolved",
    }
    assert (thread_dir / "execution_expectation.json").is_file()


def _public_concurrent_first_preparations(
    path: pathlib.Path, agent_names: tuple[str, str]
) -> list[dict]:
    """Drive two real prepare CLIs through the stable absent-ledger lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    commands = [
        [
            sys.executable,
            str(BIN / "delegation_hook.py"),
            "prepare",
            "--ledger-path",
            str(path),
            "--bead-id",
            BEAD,
            "--session",
            SESSION,
            "--host",
            "claude",
            "--agent-name",
            agent_name,
            "--execution-id",
            f"exec-first-{index}",
            "--watchdog-id",
            f"watch-first-{index}",
            "--now",
            "2026-08-09T20:00:00Z",
        ]
        for index, agent_name in enumerate(agent_names, start=1)
    ]

    processes: list[subprocess.Popen[str]] = []
    with lock_path.open("w+") as lock_file:
        lock_path.chmod(0o600)
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            processes = [
                subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                for command in commands
            ]
            readable, _writable, _exceptional = select.select(
                [process.stdout for process in processes if process.stdout], [], [], 1.0
            )
            polls_while_locked = [process.poll() for process in processes]
            durable_while_locked = path.exists()
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)

    completed = [process.communicate(timeout=5) for process in processes]
    assert readable == []
    assert polls_while_locked == [None, None]
    assert durable_while_locked is False
    for process, (_stdout, stderr) in zip(processes, completed, strict=True):
        assert process.returncode == 0, stderr
    return [json.loads(stdout) for stdout, _stderr in completed]


def test_concurrent_legacy_preparations_never_restore_agent_denial(
    tmp_path,
) -> None:
    path = tmp_path / "executions.json"
    results = _public_concurrent_first_preparations(path, (AGENT, AGENT))

    assert {result["execution_id"] for result in results} == {
        "exec-first-1",
        "exec-first-2",
    }
    persisted = ledger_api.load_trusted(path, SESSION)
    assert persisted is not None
    assert {
        (item["execution_id"], item["agent_name"], item["dispatch_tool_use_id"])
        for item in persisted["executions"]
    } == {
        ("exec-first-1", AGENT, "prepared:exec-first-1"),
        ("exec-first-2", AGENT, "prepared:exec-first-2"),
    }
    before_dispatch = copy.deepcopy(persisted)
    bd_calls: list[list[str]] = []

    result = delegation_hook.pre_tool(
        claude_agent_pretool("toolu-ambiguous-same-agent"),
        lambda args: bd_calls.append(args),
        path,
    )

    assert result == {
        "decision": "allow",
        "reason": "unmanaged_native_agent",
    }
    assert bd_calls == []
    assert ledger_api.load_trusted(path, SESSION) == before_dispatch


def test_distinct_legacy_preparations_remain_completion_evidence_not_dispatch_authority(
    tmp_path,
) -> None:
    path = tmp_path / "executions.json"
    agent_names = ("task-3-agent-alpha", "task-3-agent-beta")
    _public_concurrent_first_preparations(path, agent_names)
    before = ledger_api.load_trusted(path, SESSION)
    assert before is not None
    calls: list[list[str]] = []

    alpha = delegation_hook.pre_tool(
        claude_agent_pretool("toolu-exact-alpha", agent_name=agent_names[0]),
        lambda args: calls.append(args),
        path,
    )
    after_alpha = ledger_api.load_trusted(path, SESSION)
    assert alpha == {"decision": "allow", "reason": "unmanaged_native_agent"}
    assert after_alpha == before

    beta = delegation_hook.pre_tool(
        claude_agent_pretool("toolu-exact-beta", agent_name=agent_names[1]),
        lambda args: calls.append(args),
        path,
    )
    final = ledger_api.load_trusted(path, SESSION)
    assert beta == {"decision": "allow", "reason": "unmanaged_native_agent"}
    assert final == before
    assert calls == []
