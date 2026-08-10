#!/usr/bin/env python3
"""Behavioral oracle for host delegation registration.

Business invariant: a background Agent call may start only after an explicit
prepared attempt names canonical open Beads work, and the actual host tool-use
identity is durably written before the hook allows native execution.

The hand-authored host payloads and canonical ``bd show`` responses are the
independent oracle.  Prompt prose is deliberately adversarial: it contains a
different valid-looking Beads ID and must never select or authorize work.

Fragile implementations rejected here include prompt ID scraping, matching any
queued attempt regardless of agent/host, trusting a closed or foreign ``bd``
record, allowing before the atomic write, and guessing a native child ID from an
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


UTC = dt.timezone.utc
SESSION = "claude-parent-7"
BEAD = "escapement-e3ai.5"
AGENT = "task-3-host-adapter"
EXECUTION = "exec-host-alpha"
WATCHDOG = "watch-host-alpha"

PREPARE_REPAIR = (
    'python3 -B "${CLAUDE_PLUGIN_ROOT}/harness/bin/delegation_hook.py" prepare '
    f"--bead-id <child-bead-id> --session {SESSION} --host claude "
    f"--agent-name {AGENT}"
)


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


def canonical_bead(status: str = "in_progress", bead_id: str = BEAD):
    calls: list[list[str]] = []

    def run_bd(args: list[str]):
        calls.append(args)
        if args == ["show", BEAD]:
            return [{"id": bead_id, "status": status, "parent": "escapement-e3ai"}]
        return None

    return run_bd, calls


def test_prepare_cli_records_explicit_identity_without_prompt_input(tmp_path) -> None:
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


def test_find_prepared_execution_matches_structural_agent_identity_only(
    tmp_path,
) -> None:
    path = tmp_path / "executions.json"
    prepare_cli(path)
    ledger = read_ledger(path)

    assert (
        delegation_hook.find_prepared_execution(
            claude_agent_pretool()["tool_input"], ledger
        )["execution_id"]
        == EXECUTION
    )

    wrong_name = copy.deepcopy(claude_agent_pretool()["tool_input"])
    wrong_name["name"] = "different-agent"
    wrong_name["prompt"] = f"Please use prepared execution {EXECUTION} for {BEAD}"
    assert delegation_hook.find_prepared_execution(wrong_name, ledger) is None

    ledger["executions"][0]["dispatch_tool_use_id"] = "toolu-already-dispatched"
    assert (
        delegation_hook.find_prepared_execution(
            claude_agent_pretool()["tool_input"], ledger
        )
        is None
    )


def test_complete_claude_agent_fixture_registers_dispatch_before_allow(
    tmp_path,
) -> None:
    path = tmp_path / "executions.json"
    prepare_cli(path)
    run_bd, calls = canonical_bead()

    result = delegation_hook.pre_tool(claude_agent_pretool(), run_bd, path)

    assert result == {
        "decision": "allow",
        "reason": "dispatch_registered",
        "execution_id": EXECUTION,
        "attempt": 1,
        "generation": 1,
    }
    assert calls == [["show", BEAD]]
    persisted = read_ledger(path)["executions"][0]
    assert persisted["dispatch_tool_use_id"] == "toolu-agent-44"
    assert persisted["state"] == "queued"
    assert persisted["native_child_id"] is None


@pytest.mark.parametrize(
    ("prepared", "bd_status", "bd_id", "reason"),
    [
        (False, "in_progress", BEAD, "prepared_execution_required"),
        (True, "missing", BEAD, "bead_state_unresolved"),
        (True, "closed", BEAD, "bead_not_dispatchable"),
        (True, "in_progress", "escapement-foreign-1", "bead_state_unresolved"),
    ],
    ids=["missing-attempt", "missing-bead", "closed-bead", "foreign-bead"],
)
def test_missing_closed_or_foreign_work_denies_with_exact_repair(
    tmp_path, prepared, bd_status, bd_id, reason
) -> None:
    path = tmp_path / "executions.json"
    if prepared:
        prepare_cli(path)

    calls: list[list[str]] = []

    def run_bd(args: list[str]):
        calls.append(args)
        if bd_status == "missing":
            return []
        return [{"id": bd_id, "status": bd_status, "parent": "escapement-e3ai"}]

    result = delegation_hook.pre_tool(claude_agent_pretool(), run_bd, path)

    assert result == {
        "decision": "deny",
        "reason": reason,
        "additional_context": PREPARE_REPAIR,
    }
    if prepared:
        assert calls == [["show", BEAD]]
        assert read_ledger(path)["executions"][0]["dispatch_tool_use_id"] == (
            f"prepared:{EXECUTION}"
        )
    else:
        assert calls == []


def test_prompt_bead_id_cannot_substitute_for_prepared_execution(tmp_path) -> None:
    path = tmp_path / "executions.json"
    result = delegation_hook.pre_tool(
        claude_agent_pretool(),
        lambda _args: [{"id": "escapement-foreign-999", "status": "open"}],
        path,
    )
    assert result["decision"] == "deny"
    assert result["reason"] == "prepared_execution_required"


@pytest.mark.parametrize(
    ("prepared_host", "prepared_session"),
    [("codex", SESSION), ("claude", "foreign-parent-session")],
    ids=["foreign-host", "foreign-parent-session"],
)
def test_foreign_host_or_parent_preparation_denies_without_beads_lookup(
    tmp_path, prepared_host, prepared_session
) -> None:
    path = tmp_path / "executions.json"
    prepare_cli(path, host=prepared_host, session=prepared_session)
    before = read_ledger(path)
    calls: list[list[str]] = []

    result = delegation_hook.pre_tool(
        claude_agent_pretool(), lambda args: calls.append(args), path
    )

    assert result == {
        "decision": "deny",
        "reason": "prepared_execution_required",
        "additional_context": PREPARE_REPAIR,
    }
    assert calls == []
    assert read_ledger(path) == before


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


def test_public_pretool_hook_emits_native_permission_decision(tmp_path) -> None:
    harness_root = tmp_path / "harness"
    ledger_path = harness_root / "threads" / SESSION / "executions.json"
    ledger_path.parent.mkdir(parents=True)
    prepare_cli(ledger_path)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        f"expected = ['show', {BEAD!r}]\n"
        "if args == expected:\n"
        f"    print(json.dumps([{{'id': {BEAD!r}, 'status': 'in_progress', "
        "'parent': 'escapement-e3ai'}]))\n"
        "else:\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)
    env = os.environ.copy()
    env["HARNESS_ROOT"] = str(harness_root)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [sys.executable, str(BIN / "delegation_hook.py")],
        input=json.dumps(claude_agent_pretool()),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "dispatch_registered",
        }
    }
    assert read_ledger(ledger_path)["executions"][0]["dispatch_tool_use_id"] == (
        "toolu-agent-44"
    )


def test_two_concurrent_public_pretool_calls_allow_exactly_one_dispatch(
    tmp_path,
) -> None:
    path = tmp_path / "executions.json"
    prepare_cli(path)
    run_bd, _calls = canonical_bead()

    with ThreadPoolExecutor(max_workers=2) as pool:
        request_ids = ("toolu-race-alpha", "toolu-race-beta")
        results = list(
            pool.map(
                lambda tool_use_id: delegation_hook.pre_tool(
                    claude_agent_pretool(tool_use_id), run_bd, path
                ),
                request_ids,
            )
        )

    assert [result["decision"] for result in results].count("allow") == 1
    assert [result["decision"] for result in results].count("deny") == 1
    assert (
        next(result for result in results if result["decision"] == "allow")["reason"]
        == "dispatch_registered"
    )
    loser = next(result for result in results if result["decision"] == "deny")
    assert loser["reason"] == "prepared_execution_required"

    persisted = ledger_api.load_trusted(path, SESSION)
    assert persisted is not None
    durable_tool_use_id = persisted["executions"][0]["dispatch_tool_use_id"]
    assert durable_tool_use_id in request_ids
    assert durable_tool_use_id != f"prepared:{EXECUTION}"
    persisted_request_ids = {
        request_id for request_id in request_ids if request_id in json.dumps(persisted)
    }
    assert persisted_request_ids == {durable_tool_use_id}
    assert persisted["executions"][0]["state"] == "queued"


def test_dispatch_write_failure_never_allows_before_durable_commit(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "executions.json"
    prepare_cli(path)
    before = read_ledger(path)
    run_bd, calls = canonical_bead()

    callback_results: list[dict] = []

    def fail_atomic_write(write_path, mutation):
        assert write_path == path
        candidate = mutation(copy.deepcopy(read_ledger(path)))
        callback_results.append(candidate)
        assert candidate["executions"][0]["dispatch_tool_use_id"] == ("toolu-agent-44")
        raise OSError("injected durable replace failure")

    monkeypatch.setattr(delegation_hook, "mutate_atomic", fail_atomic_write)
    result = delegation_hook.pre_tool(claude_agent_pretool(), run_bd, path)

    assert result == {
        "decision": "deny",
        "reason": "dispatch_persistence_failed",
        "additional_context": PREPARE_REPAIR,
    }
    assert calls == [["show", BEAD]]
    assert len(callback_results) == 1
    assert callback_results[0]["executions"][0]["dispatch_tool_use_id"] != (
        f"prepared:{EXECUTION}"
    )
    assert read_ledger(path) == before
    assert read_ledger(path)["executions"][0]["dispatch_tool_use_id"] == (
        f"prepared:{EXECUTION}"
    )


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


def test_concurrent_first_same_agent_preparations_survive_and_dispatch_is_ambiguous(
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

    assert result["decision"] == "deny"
    assert result["reason"] == "prepared_execution_required"
    assert bd_calls == []
    assert ledger_api.load_trusted(path, SESSION) == before_dispatch


def test_concurrent_first_distinct_agent_preparations_bind_only_exact_agent(
    tmp_path,
) -> None:
    path = tmp_path / "executions.json"
    agent_names = ("task-3-agent-alpha", "task-3-agent-beta")
    _public_concurrent_first_preparations(path, agent_names)
    run_bd, _calls = canonical_bead()

    alpha = delegation_hook.pre_tool(
        claude_agent_pretool("toolu-exact-alpha", agent_name=agent_names[0]),
        run_bd,
        path,
    )
    after_alpha = ledger_api.load_trusted(path, SESSION)
    assert alpha["decision"] == "allow"
    assert after_alpha is not None
    alpha_item = next(
        item
        for item in after_alpha["executions"]
        if item["agent_name"] == agent_names[0]
    )
    beta_item = next(
        item
        for item in after_alpha["executions"]
        if item["agent_name"] == agent_names[1]
    )
    assert alpha_item["dispatch_tool_use_id"] == "toolu-exact-alpha"
    assert beta_item["dispatch_tool_use_id"] == "prepared:exec-first-2"

    beta = delegation_hook.pre_tool(
        claude_agent_pretool("toolu-exact-beta", agent_name=agent_names[1]),
        run_bd,
        path,
    )
    final = ledger_api.load_trusted(path, SESSION)
    assert beta["decision"] == "allow"
    assert final is not None
    assert {
        (item["agent_name"], item["dispatch_tool_use_id"])
        for item in final["executions"]
    } == {
        (agent_names[0], "toolu-exact-alpha"),
        (agent_names[1], "toolu-exact-beta"),
    }
