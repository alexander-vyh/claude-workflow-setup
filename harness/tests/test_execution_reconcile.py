#!/usr/bin/env python3
"""Behavioral oracle for SessionStart delegated-work reconciliation.

The user-facing invariant is level-triggered continuation context: canonical
unresolved Beads work or an execution whose deadline is due must be visible at
SessionStart, while a canonically closed parent with no due attempt stays quiet.
Missing or ambiguous state fails closed as unresolved rather than becoming an
empty queue.

Hand-authored Claude/Codex lifecycle fixtures and literal Beads records are the
source of truth.  The generation controls reject the tempting adapter shortcut
of filling an omitted generation with the active ledger generation.
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

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger as ledger_api  # noqa: E402
import execution_reconcile  # noqa: E402


SESSION = "parent-session-reconcile-7"
CODEX_SESSION = "019c8a3b-codex-thread-9"
BEAD = "escapement-e3ai.5"
ROOT = "escapement-e3ai"
EXECUTION = "exec-reconcile-alpha"

# Wall-clock hang guard for subprocess completion. Deliberately generous: it
# exists so a genuine deadlock fails the run instead of hanging it forever, and
# it asserts nothing about how fast a healthy run is. The real timing invariant
# is the bounded `select` while the lock is held.
SUBPROCESS_HANG_GUARD_SECONDS = 60


def at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def claude_session_start() -> dict:
    return {
        "session_id": SESSION,
        "transcript_path": "/tmp/parent-session-reconcile-7.jsonl",
        "cwd": "/repo",
        "hook_event_name": "SessionStart",
        "source": "startup",
    }


def codex_session_start() -> dict:
    """Codex-specific fixture: no Claude transcript path or Stop fields."""
    return {
        "session_id": CODEX_SESSION,
        "cwd": "/repo",
        "hook_event_name": "SessionStart",
        "source": "startup",
        "host": "codex",
    }


def registered(session_id: str = SESSION) -> dict:
    ledger = ledger_api.new_ledger(session_id)
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": session_id,
            "bead_id": BEAD,
            "execution_id": EXECUTION,
            "host": "codex" if session_id == CODEX_SESSION else "claude",
            "agent_name": "task-3-host-adapter",
            "dispatch_tool_use_id": "toolu-reconcile-1",
            "watchdog_id": "watch-reconcile-1",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-09T20:00:00Z"),
    )
    return ledger


def generation_two(session_id: str = SESSION) -> dict:
    ledger = registered(session_id)
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_bound",
            "parent_session_id": session_id,
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
            "parent_session_id": session_id,
            "execution_id": EXECUTION,
            "attempt": 1,
            "generation": 2,
            "native_child_id": "native-generation-2",
        },
        at("2026-08-09T20:02:32Z"),
    )
    return ledger


def late_generation_one_terminal(session_id: str = SESSION) -> dict:
    return {
        "kind": "child_terminal",
        "parent_session_id": session_id,
        "execution_id": EXECUTION,
        "attempt": 1,
        "generation": 1,
        "native_child_id": "native-generation-1",
        "terminal_event_id": "literal-late-generation-one",
        "terminal_reason": "completed",
        "result_digest": "sha256:late-generation-one",
        "host_event_id": "claude:terminal:literal-late-generation-one",
    }


def beads_runner(parent_status: str = "closed", *, missing: str | None = None):
    calls: list[list[str]] = []

    def run_bd(args: list[str]):
        calls.append(args)
        if missing == "all":
            return None
        if args == ["show", BEAD]:
            if missing == "child":
                return []
            if missing == "canonical_parent":
                return [{"id": BEAD, "status": "closed", "parent": ""}]
            return [{"id": BEAD, "status": "closed", "parent": ROOT}]
        if args == ["show", ROOT]:
            if missing == "parent":
                return []
            return [{"id": ROOT, "status": parent_status}]
        return None

    return run_bd, calls


def beads_runner_with_parent_value(*, present: bool, value=None):
    calls: list[list[str]] = []

    def run_bd(args: list[str]):
        calls.append(args)
        if args == ["show", BEAD]:
            child = {"id": BEAD, "status": "closed"}
            if present:
                child["parent"] = value
            return [child]
        if isinstance(value, str) and value and args == ["show", value]:
            return [{"id": value, "status": "closed"}]
        return []

    return run_bd, calls


def loader_for(ledger: dict | None, calls: list[str]):
    def load(session_id: str):
        calls.append(session_id)
        return ledger

    return load


def test_unresolved_parent_emits_actionable_continuation_context() -> None:
    ledger = registered()
    load_calls: list[str] = []
    run_bd, bd_calls = beads_runner("in_progress")

    result = execution_reconcile.reconcile_session(
        claude_session_start(),
        run_bd,
        loader_for(ledger, load_calls),
        at("2026-08-09T20:01:00Z"),
    )

    assert result["status"] == "continue"
    assert (
        "parent outcome escapement-e3ai is unresolved" in result["additional_context"]
    )
    assert "bd show escapement-e3ai" in result["additional_context"]
    assert "verify the outcome before closing" in result["additional_context"]
    assert load_calls == [SESSION]
    assert bd_calls == [["show", BEAD], ["show", ROOT]]


def test_due_execution_emits_identity_and_reconciliation_action() -> None:
    ledger = registered()
    run_bd, _calls = beads_runner("closed")

    result = execution_reconcile.reconcile_session(
        claude_session_start(),
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:02:00Z"),
    )

    assert result["status"] == "continue"
    context = result["additional_context"]
    assert "execution exec-reconcile-alpha" in context
    assert "attempt 1 generation 1" in context
    assert "start deadline" in context
    assert "reconcile before continuing or yielding" in context
    assert ledger["executions"][0]["reconcile_due"] == "start"
    assert ledger["executions"][0]["state"] == "queued"


def test_closed_parent_with_no_due_attempts_emits_nothing() -> None:
    ledger = registered()
    run_bd, calls = beads_runner("closed")

    result = execution_reconcile.reconcile_session(
        claude_session_start(),
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:01:00Z"),
    )

    assert result == {"status": "clear", "additional_context": ""}
    assert calls == [["show", BEAD], ["show", ROOT]]


@pytest.mark.parametrize(
    ("present", "value"),
    [(False, None), (True, None)],
    ids=["absent-parent", "explicit-null-parent"],
)
def test_standalone_bead_is_canonical_and_silent_at_session_start(
    present: bool, value
) -> None:
    ledger = registered()
    run_bd, calls = beads_runner_with_parent_value(present=present, value=value)

    result = execution_reconcile.reconcile_session(
        claude_session_start(),
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:01:00Z"),
    )

    assert result == {"status": "clear", "additional_context": ""}
    assert calls == [["show", BEAD]]


@pytest.mark.parametrize(
    "malformed_parent",
    ["", False, True, [], {}, 17],
    ids=["empty", "false", "true", "list", "mapping", "integer"],
)
def test_malformed_parent_never_becomes_standalone_at_session_start(
    malformed_parent,
) -> None:
    ledger = registered()
    run_bd, calls = beads_runner_with_parent_value(
        present=True, value=malformed_parent
    )

    result = execution_reconcile.reconcile_session(
        claude_session_start(),
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:01:00Z"),
    )

    assert result["status"] == "continue"
    assert f"canonical parent relationship for {BEAD} is unresolved" in result[
        "additional_context"
    ]
    assert calls == [["show", BEAD]]


def test_codex_sessionstart_uses_the_same_reconciliation_without_stop_claims() -> None:
    ledger = registered(CODEX_SESSION)
    run_bd, _calls = beads_runner("in_progress")

    result = execution_reconcile.reconcile_session(
        codex_session_start(),
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:01:00Z"),
    )

    assert result["status"] == "continue"
    assert (
        "parent outcome escapement-e3ai is unresolved" in result["additional_context"]
    )
    lowered = result["additional_context"].lower()
    assert "stop hook" not in lowered
    assert "stop support" not in lowered
    assert "subagentstop" not in lowered


def test_missing_ledger_emits_unresolved_context() -> None:
    result = execution_reconcile.reconcile_session(
        claude_session_start(),
        lambda _args: [],
        lambda _session: None,
        at("2026-08-09T20:01:00Z"),
    )
    assert result["status"] == "continue"
    assert "execution ledger is missing or untrusted" in result["additional_context"]
    assert "inspect executions.json" in result["additional_context"]


def test_missing_beads_state_emits_unresolved_context() -> None:
    ledger = registered()
    run_bd, calls = beads_runner(missing="child")
    result = execution_reconcile.reconcile_session(
        claude_session_start(),
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:01:00Z"),
    )
    assert result["status"] == "continue"
    assert (
        f"canonical Beads state for {BEAD} is unresolved"
        in result["additional_context"]
    )
    assert f"bd show {BEAD}" in result["additional_context"]
    assert calls == [["show", BEAD]]


def test_malformed_canonical_parent_relationship_is_actionable_and_unresolved() -> None:
    ledger = registered()
    run_bd, calls = beads_runner(missing="canonical_parent")
    payload = claude_session_start()
    payload["parent_id"] = "payload-parent-must-not-be-used"
    payload["parent_bead_id"] = "payload-parent-must-not-be-used-either"

    result = execution_reconcile.reconcile_session(
        payload,
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:01:00Z"),
    )

    assert result["status"] == "continue"
    assert (
        f"canonical parent relationship for {BEAD} is unresolved"
        in result["additional_context"]
    )
    assert f"bd show {BEAD}" in result["additional_context"]
    assert (
        "repair its Beads parent relationship before continuing"
        in result["additional_context"]
    )
    assert "payload-parent-must-not-be-used" not in result["additional_context"]
    assert calls == [["show", BEAD]]


def test_named_canonical_parent_with_missing_record_is_actionable_and_unresolved() -> (
    None
):
    ledger = registered()
    run_bd, calls = beads_runner(missing="parent")
    payload = claude_session_start()
    payload["parent_id"] = "payload-parent-must-not-be-used"

    result = execution_reconcile.reconcile_session(
        payload,
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:01:00Z"),
    )

    assert result["status"] == "continue"
    assert (
        f"canonical Beads state for parent {ROOT} is unresolved"
        in result["additional_context"]
    )
    assert f"bd show {ROOT}" in result["additional_context"]
    assert "resolve the parent record before continuing" in result["additional_context"]
    assert "payload-parent-must-not-be-used" not in result["additional_context"]
    assert calls == [["show", BEAD], ["show", ROOT]]


def test_host_payload_parent_fields_never_override_canonical_beads_relationship() -> (
    None
):
    cases = [
        (claude_session_start(), registered()),
        (codex_session_start(), registered(CODEX_SESSION)),
    ]

    for payload, ledger in cases:
        payload["parent_id"] = "payload-parent-must-not-be-used"
        payload["parent_bead_id"] = "payload-parent-must-not-be-used-either"
        payload["task_id"] = "payload-task-must-not-be-used"
        run_bd, calls = beads_runner("in_progress")

        result = execution_reconcile.reconcile_session(
            payload,
            run_bd,
            lambda _session, value=ledger: value,
            at("2026-08-09T20:01:00Z"),
        )

        assert result["status"] == "continue"
        assert (
            "parent outcome escapement-e3ai is unresolved"
            in result["additional_context"]
        )
        assert "payload-parent-must-not-be-used" not in result["additional_context"]
        assert "payload-task-must-not-be-used" not in result["additional_context"]
        assert calls == [["show", BEAD], ["show", ROOT]]


def test_late_generation_one_terminal_cannot_mutate_generation_two() -> None:
    ledger = generation_two()
    active_before = copy.deepcopy(ledger["executions"][0])
    run_bd, _calls = beads_runner("closed")
    payload = claude_session_start()
    payload["execution_events"] = [late_generation_one_terminal()]

    result = execution_reconcile.reconcile_session(
        payload,
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:02:33Z"),
    )

    assert result["status"] == "continue"
    assert ledger["executions"][0] == active_before
    assert ledger["incidents"][-1] == {
        "type": "old_generation_event",
        "execution_id": EXECUTION,
        "event_kind": "child_terminal",
        "event_id": "literal-late-generation-one",
        "event_attempt": 1,
        "event_generation": 1,
        "active_attempt": 1,
        "active_generation": 2,
        "recorded_at": "2026-08-09T20:02:33Z",
    }
    assert ledger["executions"][0]["native_child_id"] == "native-generation-2"
    assert (
        ledger["executions"][0]["result_application"]
        == active_before["result_application"]
    )


def test_terminal_without_generation_is_unresolved_not_defaulted_to_current() -> None:
    ledger = generation_two()
    before = copy.deepcopy(ledger)
    ambiguous = late_generation_one_terminal()
    ambiguous.pop("generation")
    payload = claude_session_start()
    payload["execution_events"] = [ambiguous]
    run_bd, _calls = beads_runner("closed")

    result = execution_reconcile.reconcile_session(
        payload,
        run_bd,
        lambda _session: ledger,
        at("2026-08-09T20:02:33Z"),
    )

    assert result["status"] == "continue"
    assert "terminal event identity is unresolved" in result["additional_context"]
    assert ledger == before
    assert ledger["executions"][0]["state"] == "queued"
    assert ledger["executions"][0]["generation"] == 2
    assert ledger["executions"][0]["result_application"]["state"] == "unapplied"


def test_public_sessionstart_hook_emits_native_additional_context(tmp_path) -> None:
    harness_root = tmp_path / "harness"
    ledger_path = harness_root / "threads" / SESSION / "executions.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(json.dumps(registered()), encoding="utf-8")
    ledger_path.chmod(0o600)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        f"if args == ['show', {BEAD!r}]:\n"
        f"    print(json.dumps([{{'id': {BEAD!r}, 'status': 'closed', "
        f"'parent': {ROOT!r}}}]))\n"
        f"elif args == ['show', {ROOT!r}]:\n"
        f"    print(json.dumps([{{'id': {ROOT!r}, 'status': 'in_progress'}}]))\n"
        "else:\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)
    env = os.environ.copy()
    env["HARNESS_ROOT"] = str(harness_root)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [sys.executable, str(BIN / "execution_reconcile.py")],
        input=json.dumps(claude_session_start()),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "parent outcome escapement-e3ai is unresolved" in context
    assert "bd show escapement-e3ai" in context


def test_public_sessionstart_durably_merges_reconciliation_with_concurrent_change(
    tmp_path,
) -> None:
    """Reject in-memory-only reconcile and unsafe whole-snapshot replacement."""
    harness_root = tmp_path / "harness"
    ledger_path = harness_root / "threads" / SESSION / "executions.json"
    ledger_path.parent.mkdir(parents=True)
    ledger = generation_two()
    active_before = copy.deepcopy(ledger["executions"][0])
    active_before["reconcile_due"] = None
    active_before["start_deadline"] = "2020-01-01T00:00:00Z"
    active_before["idle_deadline"] = "2020-01-01T00:00:00Z"
    active_before["hard_deadline"] = "2020-01-01T00:00:00Z"
    ledger["executions"][0] = copy.deepcopy(active_before)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    ledger_path.chmod(0o600)
    assert ledger_api.load_trusted(ledger_path, SESSION) is not None

    mutation_marker = tmp_path / "concurrent-mutation-recorded"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        "#!/usr/bin/env python3\n"
        "import datetime as dt, json, pathlib, sys\n"
        f"sys.path.insert(0, {str(BIN)!r})\n"
        "import execution_ledger, execution_store\n"
        f"ledger_path = pathlib.Path({str(ledger_path)!r})\n"
        f"marker = pathlib.Path({str(mutation_marker)!r})\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        f"if args == ['show', {BEAD!r}]:\n"
        "    if not marker.exists():\n"
        "        event = {\n"
        "            'kind': 'dispatch_registered',\n"
        f"            'parent_session_id': {SESSION!r},\n"
        "            'bead_id': 'escapement-concurrent-child',\n"
        "            'execution_id': 'exec-concurrent-durable',\n"
        "            'host': 'claude',\n"
        "            'agent_name': 'concurrent-writer',\n"
        "            'dispatch_tool_use_id': 'toolu-concurrent-durable',\n"
        "            'watchdog_id': 'watch-concurrent-durable',\n"
        "            'attempt': 1,\n"
        "            'generation': 1,\n"
        "        }\n"
        "        def add(current):\n"
        "            return execution_ledger.register_execution(\n"
        "                current, event, dt.datetime(2026, 8, 9, 20, 3, "
        "tzinfo=dt.timezone.utc))\n"
        "        execution_store.mutate_atomic(ledger_path, add)\n"
        "        marker.write_text('done')\n"
        f"    print(json.dumps([{{'id': {BEAD!r}, 'status': 'closed', "
        f"'parent': {ROOT!r}}}]))\n"
        f"elif args == ['show', {ROOT!r}]:\n"
        f"    print(json.dumps([{{'id': {ROOT!r}, 'status': 'closed'}}]))\n"
        "elif args == ['show', 'escapement-concurrent-child']:\n"
        "    print(json.dumps([{'id': 'escapement-concurrent-child', "
        f"'status': 'open', 'parent': {ROOT!r}}}]))\n"
        "else:\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)
    env = os.environ.copy()
    env["HARNESS_ROOT"] = str(harness_root)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    payload = claude_session_start()
    payload["execution_events"] = [late_generation_one_terminal()]

    result = subprocess.run(
        [sys.executable, str(BIN / "execution_reconcile.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    persisted = ledger_api.load_trusted(ledger_path, SESSION)
    assert persisted is not None
    assert {item["execution_id"] for item in persisted["executions"]} == {
        EXECUTION,
        "exec-concurrent-durable",
    }
    active = next(
        item for item in persisted["executions"] if item["execution_id"] == EXECUTION
    )
    assert active["reconcile_due"] == "hard"
    active_without_due = copy.deepcopy(active)
    active_without_due["reconcile_due"] = None
    assert active_without_due == active_before
    assert any(
        incident
        == {
            "type": "old_generation_event",
            "execution_id": EXECUTION,
            "event_kind": "child_terminal",
            "event_id": "literal-late-generation-one",
            "event_attempt": 1,
            "event_generation": 1,
            "active_attempt": 1,
            "active_generation": 2,
            "recorded_at": incident["recorded_at"],
        }
        for incident in persisted["incidents"]
        if incident["type"] == "old_generation_event"
    )
    assert active["generation"] == 2
    assert active["native_child_id"] == "native-generation-2"
    assert active["result_application"] == active_before["result_application"]


def test_public_sessionstart_emits_only_after_durable_reconciliation(tmp_path) -> None:
    """The native hook response cannot outrun the ledger's exclusive lock."""
    harness_root = tmp_path / "harness"
    ledger_path = harness_root / "threads" / SESSION / "executions.json"
    ledger_path.parent.mkdir(parents=True)
    ledger = registered()
    ledger["executions"][0]["hard_deadline"] = "2020-01-01T00:00:00Z"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    ledger_path.chmod(0o600)
    assert ledger_api.load_trusted(ledger_path, SESSION) is not None

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        f"if args == ['show', {BEAD!r}]:\n"
        f"    print(json.dumps([{{'id': {BEAD!r}, 'status': 'closed', "
        f"'parent': {ROOT!r}}}]))\n"
        f"elif args == ['show', {ROOT!r}]:\n"
        f"    print(json.dumps([{{'id': {ROOT!r}, 'status': 'closed'}}]))\n"
        "else:\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)
    payload_path = tmp_path / "session-start.json"
    payload_path.write_text(json.dumps(claude_session_start()), encoding="utf-8")
    env = os.environ.copy()
    env["HARNESS_ROOT"] = str(harness_root)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    lock_path = ledger_path.with_name(f".{ledger_path.name}.lock")
    process: subprocess.Popen[str] | None = None
    with lock_path.open("w+") as lock_file, payload_path.open() as payload_file:
        lock_path.chmod(0o600)
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            process = subprocess.Popen(
                [sys.executable, str(BIN / "execution_reconcile.py")],
                stdin=payload_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            assert process.stdout is not None
            readable, _writable, _exceptional = select.select(
                [process.stdout], [], [], 1.0
            )
            assert readable == []
            assert process.poll() is None
            locked = ledger_api.load_trusted(ledger_path, SESSION)
            assert locked is not None
            assert locked["executions"][0]["reconcile_due"] is None
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)

    assert process is not None
    # Hang guard, NOT part of the oracle. The invariant is the pair of
    # assertions above — nothing readable and no exit while the lock is held.
    # This budget only has to be longer than a healthy run, and a healthy run
    # here starts two interpreters (this hook, then the fake `bd`). At 5s it
    # went red on a machine running concurrent suites; the same pattern
    # elsewhere in this suite already allows 15s.
    stdout, stderr = process.communicate(timeout=SUBPROCESS_HANG_GUARD_SECONDS)
    assert process.returncode == 0, stderr
    output = json.loads(stdout)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "crossed its hard deadline" in context
    persisted = ledger_api.load_trusted(ledger_path, SESSION)
    assert persisted is not None
    assert persisted["executions"][0]["reconcile_due"] == "hard"
