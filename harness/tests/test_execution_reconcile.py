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
import json
import os
import pathlib
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger as ledger_api  # noqa: E402
import execution_reconcile  # noqa: E402


SESSION = "parent-session-reconcile-7"
CODEX_SESSION = "019c8a3b-codex-thread-9"
BEAD = "escapement-e3ai.5"
ROOT = "escapement-e3ai"
EXECUTION = "exec-reconcile-alpha"


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
                return [{"id": BEAD, "status": "closed"}]
            return [{"id": BEAD, "status": "closed", "parent": ROOT}]
        if args == ["show", ROOT]:
            if missing == "parent":
                return []
            return [{"id": ROOT, "status": parent_status}]
        return None

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


def test_missing_canonical_parent_relationship_is_actionable_and_unresolved() -> None:
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
