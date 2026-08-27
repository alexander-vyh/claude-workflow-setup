#!/usr/bin/env python3
"""Behavioral oracle for independently captured Claude lifecycle records.

The sanitized Claude 2.1.247 records and their raw-record provenance are the
source of truth.  Adapter output is accepted only when it preserves the exact
dispatch, child, and event identities witnessed by those records.  Prompt and
result prose are deliberately absent from positive identity fixtures.
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import pathlib
import re
import sys

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
FIXTURE_PATH = FIXTURES / "claude-agent-lifecycle-2.1.247.jsonl"
PROVENANCE_PATH = FIXTURES / "claude-agent-lifecycle-2.1.247.provenance.json"
sys.path.insert(0, str(BIN))

import execution_ledger as ledger_api  # noqa: E402
import would_block_stop as stop_policy  # noqa: E402

BACKGROUND_EXECUTION = "fixture-background-execution"
INTERACTIVE_EXECUTION = "fixture-interactive-execution"
NOW = dt.datetime(2026, 8, 27, 22, 10, 8, tzinfo=dt.timezone.utc)


def at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def fixtures() -> dict[str, dict]:
    records = [json.loads(line) for line in FIXTURE_PATH.read_text().splitlines()]
    return {item["fixture_id"]: item["record"] for item in records}


def load_adapter():
    path = BIN / "claude_agent_lifecycle.py"
    assert path.is_file(), "Claude lifecycle adapter is not implemented"
    spec = importlib.util.spec_from_file_location("claude_agent_lifecycle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tool_use(record: dict) -> dict:
    return record["message"]["content"][0]


def tool_result(record: dict) -> dict:
    return record["message"]["content"][0]


def write_transcript(path: pathlib.Path, *records: dict) -> pathlib.Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def background_ledger() -> dict:
    captured = fixtures()
    dispatch = tool_use(captured["background_agent_tool_use"])
    session_id = captured["background_agent_tool_use"]["session_id"]
    ledger = ledger_api.new_ledger(session_id)
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": session_id,
            "bead_id": "escapement-xncx",
            "execution_id": BACKGROUND_EXECUTION,
            "host": "claude",
            "agent_name": dispatch["input"]["name"],
            "dispatch_tool_use_id": dispatch["id"],
            "watchdog_id": "watch-fixture-background",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-27T22:10:07Z"),
    )
    return ledger


def interactive_ledger() -> dict:
    captured = fixtures()["interactive_spawn_result"]
    result = captured["toolUseResult"]
    dispatch_id = tool_result(captured)["tool_use_id"]
    ledger = ledger_api.new_ledger(captured["session_id"])
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": captured["session_id"],
            "bead_id": "escapement-xncx",
            "execution_id": INTERACTIVE_EXECUTION,
            "host": "claude",
            "agent_name": result["name"],
            "dispatch_tool_use_id": dispatch_id,
            "watchdog_id": "watch-fixture-interactive",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-27T18:00:22Z"),
    )
    return ledger


def item(ledger: dict) -> dict:
    assert len(ledger["executions"]) == 1
    return ledger["executions"][0]


def apply_events(ledger: dict, events: list[dict], when: str) -> None:
    for offset, event in enumerate(events):
        ledger_api.apply_event(
            ledger,
            event,
            at(when) + dt.timedelta(milliseconds=offset),
        )


def completion(ledger: dict) -> tuple[str, str]:
    return stop_policy.execution_stop_decision("closed", ledger, None, [], NOW)


def test_fixture_contract_is_independently_witnessed_and_structurally_exact() -> None:
    captured = fixtures()
    provenance = json.loads(PROVENANCE_PATH.read_text())
    expected_ids = {
        "background_agent_tool_use",
        "background_task_started",
        "background_async_result",
        "background_task_terminal",
        "background_peer_activity",
        "no_spawn_agent_tool_use",
        "no_spawn_error_result",
        "interactive_spawn_result",
        "historical_idle_notification",
        "historical_later_child_idle",
    }
    assert set(captured) == expected_ids
    assert provenance["host"] == {"product": "Claude Code", "version": "2.1.247"}
    assert provenance["sanitizer"]["version"] == "1"
    assert provenance["sanitizer"]["name"] == "sanitize_claude_lifecycle_fixtures.py"
    assert {record["fixture_id"] for record in provenance["records"]} == expected_ids
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", record["raw_record_sha256"])
        for record in provenance["records"]
    )
    assert all(record["retained_json_pointers"] for record in provenance["records"])

    dispatch = tool_use(captured["background_agent_tool_use"])
    started = captured["background_task_started"]
    async_record = captured["background_async_result"]
    async_result = async_record["tool_use_result"]
    terminal = captured["background_task_terminal"]
    peer = captured["background_peer_activity"]["origin"]
    assert dispatch["name"] == "Agent"
    assert dispatch["input"]["name"] == peer["name"] == peer["from"]
    assert async_result["status"] == "async_launched"
    assert async_result["isAsync"] is True
    assert (
        dispatch["id"]
        == tool_result(async_record)["tool_use_id"]
        == started["tool_use_id"]
        == terminal["tool_use_id"]
    )
    assert (
        async_result["agentId"]
        == started["task_id"]
        == terminal["task_id"]
        == peer["senderTaskId"]
    )
    assert terminal["type"] == "system"
    assert terminal["subtype"] == "task_notification"
    assert terminal["status"] == "completed"
    assert peer["kind"] == "peer"

    no_spawn_dispatch = tool_use(captured["no_spawn_agent_tool_use"])
    no_spawn_result = tool_result(captured["no_spawn_error_result"])
    assert no_spawn_result == {
        "type": "tool_result",
        "is_error": True,
        "tool_use_id": no_spawn_dispatch["id"],
    }
    assert not any(
        key in json.dumps(captured["no_spawn_error_result"])
        for key in ("agentId", "agent_id", "teammate_id", "native_child_id")
    )

    interactive = captured["interactive_spawn_result"]["toolUseResult"]
    assert interactive["status"] == "teammate_spawned"
    assert interactive["agent_id"] == interactive["teammate_id"]
    assert interactive["agent_id"]
    assert interactive["name"] == "prop-drilling-research"

    idle = captured["historical_idle_notification"]
    later = captured["historical_later_child_idle"]
    first_payload = json.loads(re.search(r"\{[^\n]+\}", idle["message"]["content"])[0])
    later_payload = json.loads(re.search(r"\{[^\n]+\}", later["message"]["content"])[0])
    assert first_payload["type"] == later_payload["type"] == "idle_notification"
    assert first_payload["from"] == later_payload["from"]
    assert at(first_payload["timestamp"]) < at(later_payload["timestamp"])
    assert idle.get("subtype") is None and idle.get("status") is None


def test_fixture_allowlist_excludes_identity_prose_and_sensitive_surfaces() -> None:
    captured = fixtures()
    for fixture_id, record in captured.items():
        if fixture_id.startswith("historical_"):
            continue
        serialized = json.dumps(record)
        assert '"prompt"' not in serialized
        assert "output_file" not in serialized
        assert '"model"' not in serialized
        assert "Available agents" not in serialized
        assert "internal metadata" not in serialized
        assert "/Users/" not in serialized
        assert "/private/" not in serialized


def test_background_spawn_prefix_binds_starts_and_blocks_completion(tmp_path) -> None:
    adapter = load_adapter()
    captured = fixtures()
    ledger = background_ledger()
    transcript = write_transcript(
        tmp_path / "spawn.jsonl",
        captured["background_agent_tool_use"],
        captured["background_task_started"],
        captured["background_async_result"],
    )

    events = adapter.observe_transcript(transcript, ledger)

    assert [event["kind"] for event in events] == ["child_bound", "child_started"]
    child_id = captured["background_task_started"]["task_id"]
    assert all(event["native_child_id"] == child_id for event in events)
    apply_events(ledger, events, "2026-08-27T22:10:08Z")
    durable = item(ledger)
    assert durable["state"] == "running"
    assert durable["native_child_id"] == child_id
    assert durable["terminal_at"] is None
    assert durable["terminal_reason"] is None
    assert durable["terminal_event_id"] is None
    assert durable["result_digest"] is None
    assert completion(ledger) == ("block", "delegated_execution_unresolved")


def test_matching_peer_activity_and_terminal_are_separate_prefixes(tmp_path) -> None:
    adapter = load_adapter()
    captured = fixtures()
    ledger = background_ledger()
    spawn = write_transcript(
        tmp_path / "spawn.jsonl",
        captured["background_agent_tool_use"],
        captured["background_task_started"],
        captured["background_async_result"],
    )
    apply_events(ledger, adapter.observe_transcript(spawn, ledger), "2026-08-27T22:10:08Z")

    peer = write_transcript(
        tmp_path / "peer.jsonl", captured["background_peer_activity"]
    )
    peer_events = adapter.observe_transcript(peer, ledger)
    assert [event["kind"] for event in peer_events] == ["activity_completed"]
    apply_events(ledger, peer_events, "2026-08-27T22:10:11Z")
    assert item(ledger)["state"] == "running"
    assert completion(ledger) == ("block", "delegated_execution_unresolved")

    terminal = write_transcript(
        tmp_path / "terminal.jsonl", captured["background_task_terminal"]
    )
    terminal_events = adapter.observe_transcript(terminal, ledger)
    assert [event["kind"] for event in terminal_events] == ["child_terminal"]
    apply_events(ledger, terminal_events, "2026-08-27T22:10:12Z")
    durable = item(ledger)
    assert durable["state"] == "terminal"
    assert all(
        durable[key] is None
        for key in (
            "start_deadline",
            "idle_deadline",
            "hard_deadline",
            "reconcile_due",
            "recovery_claim",
        )
    )
    assert completion(ledger) == ("block", "delegated_execution_unresolved")
    durable["result_application"] = {
        "state": "applied",
        "claim": None,
        "claim_generation": 1,
        "idempotency_key": (
            f"execution:{BACKGROUND_EXECUTION}:attempt:1:generation:1"
        ),
        "applied_at": "2026-08-27T22:10:13Z",
    }
    assert completion(ledger) == ("allow", "delegated_outcome_complete")


def test_historical_idle_text_is_nonterminal_and_later_peer_activity_is_accepted(
    tmp_path,
) -> None:
    adapter = load_adapter()
    captured = fixtures()
    ledger = background_ledger()
    spawn = write_transcript(
        tmp_path / "background-spawn.jsonl",
        captured["background_agent_tool_use"],
        captured["background_task_started"],
        captured["background_async_result"],
    )
    apply_events(ledger, adapter.observe_transcript(spawn, ledger), "2026-08-27T22:10:08Z")
    before_idle = copy.deepcopy(ledger)
    idle = write_transcript(
        tmp_path / "idle.jsonl",
        captured["historical_idle_notification"],
    )

    idle_events = adapter.observe_transcript(idle, ledger)

    assert idle_events == []
    assert ledger == before_idle
    assert item(ledger)["state"] == "running"
    assert item(ledger)["terminal_at"] is None
    assert completion(ledger) == ("block", "delegated_execution_unresolved")

    later = write_transcript(
        tmp_path / "later-peer.jsonl", captured["background_peer_activity"]
    )
    later_events = adapter.observe_transcript(later, ledger)
    assert [event["kind"] for event in later_events] == ["activity_completed"]
    apply_events(ledger, later_events, "2026-08-27T22:10:11Z")
    assert item(ledger)["state"] == "running"
    assert item(ledger)["last_activity_at"] == "2026-08-27T22:10:11Z"


def test_proven_no_spawn_aborts_without_fabricating_child_identity(tmp_path) -> None:
    adapter = load_adapter()
    captured = fixtures()
    dispatch = tool_use(captured["no_spawn_agent_tool_use"])
    session_id = captured["no_spawn_agent_tool_use"]["session_id"]
    ledger = ledger_api.new_ledger(session_id)
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": session_id,
            "bead_id": "escapement-xncx",
            "execution_id": "fixture-no-spawn-execution",
            "host": "claude",
            "agent_name": dispatch["input"]["name"],
            "dispatch_tool_use_id": dispatch["id"],
            "watchdog_id": "watch-fixture-no-spawn",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-27T22:10:55Z"),
    )
    transcript = write_transcript(
        tmp_path / "no-spawn.jsonl",
        captured["no_spawn_agent_tool_use"],
        captured["no_spawn_error_result"],
    )

    events = adapter.observe_transcript(transcript, ledger)

    assert [event["kind"] for event in events] == ["dispatch_aborted"]
    apply_events(ledger, events, "2026-08-27T22:10:56Z")
    assert item(ledger)["state"] == "aborted"
    assert item(ledger)["native_child_id"] is None
    assert completion(ledger) == ("allow", "delegated_outcome_complete")


@pytest.mark.parametrize(
    "mutation",
    [
        "agent-only",
        "teammate-only",
        "unequal",
        "empty",
        "prose-only",
        "invented-native-child",
    ],
)
def test_invalid_interactive_spawn_identity_shapes_produce_no_events_or_mutation(
    mutation: str,
) -> None:
    adapter = load_adapter()
    captured = fixtures()["interactive_spawn_result"]
    payload = copy.deepcopy(captured)
    result = payload["toolUseResult"]
    if mutation == "agent-only":
        result.pop("teammate_id")
    elif mutation == "teammate-only":
        result.pop("agent_id")
    elif mutation == "unequal":
        result["teammate_id"] = "different-native-child"
    elif mutation == "empty":
        result["agent_id"] = result["teammate_id"] = ""
    elif mutation == "prose-only":
        result.pop("agent_id")
        result.pop("teammate_id")
        payload["message"]["content"][0]["content"] = (
            "agent_id: invented-in-prose teammate_id: invented-in-prose"
        )
    elif mutation == "invented-native-child":
        result["native_child_id"] = "invented-surplus-child"
    ledger = interactive_ledger()
    before = copy.deepcopy(ledger)

    observed = adapter.observe_post_tool(payload, ledger)

    assert observed["events"] == []
    assert ledger == before
    assert item(ledger)["native_child_id"] is None
    assert completion(ledger) == ("block", "delegated_execution_unresolved")
    assert observed.get("decision") != "deny"


@pytest.mark.parametrize(
    "mutation",
    ["agent-name", "tool-use-id", "task-id", "late-generation"],
)
def test_background_identity_mismatch_or_late_generation_mutates_nothing(
    tmp_path, mutation: str
) -> None:
    adapter = load_adapter()
    captured = fixtures()
    records = [
        copy.deepcopy(captured["background_agent_tool_use"]),
        copy.deepcopy(captured["background_task_started"]),
        copy.deepcopy(captured["background_async_result"]),
    ]
    ledger = background_ledger()
    if mutation == "agent-name":
        tool_use(records[0])["input"]["name"] = "foreign-agent"
    elif mutation == "tool-use-id":
        records[1]["tool_use_id"] = "foreign-tool-use"
    elif mutation == "task-id":
        records[1]["task_id"] = "foreign-task-id"
    else:
        ledger["executions"][0]["generation"] = 2
        ledger["executions"][0]["result_application"]["idempotency_key"] = (
            f"execution:{BACKGROUND_EXECUTION}:attempt:1:generation:2"
        )
    before = copy.deepcopy(ledger)
    transcript = write_transcript(tmp_path / f"{mutation}.jsonl", *records)

    events = adapter.observe_transcript(transcript, ledger)

    assert events == []
    assert ledger == before
    assert item(ledger)["native_child_id"] is None


def test_terminal_requires_exact_tool_and_bound_task_identity(tmp_path) -> None:
    adapter = load_adapter()
    captured = fixtures()
    ledger = background_ledger()
    spawn = write_transcript(
        tmp_path / "spawn.jsonl",
        captured["background_agent_tool_use"],
        captured["background_task_started"],
        captured["background_async_result"],
    )
    apply_events(ledger, adapter.observe_transcript(spawn, ledger), "2026-08-27T22:10:08Z")
    for field in ("tool_use_id", "task_id"):
        malformed = copy.deepcopy(captured["background_task_terminal"])
        malformed[field] = f"foreign-{field}"
        before = copy.deepcopy(ledger)
        terminal = write_transcript(tmp_path / f"terminal-{field}.jsonl", malformed)

        assert adapter.observe_transcript(terminal, ledger) == []
        assert ledger == before
        assert item(ledger)["state"] == "running"
