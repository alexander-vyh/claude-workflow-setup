#!/usr/bin/env python3
"""Public PostToolUse/store controls for captured Claude lifecycle evidence."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
FIXTURE = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures"
    / "claude-agent-lifecycle-2.1.247.jsonl"
)
sys.path.insert(0, str(BIN))

import delegation_hook  # noqa: E402
import execution_ledger as ledger_api  # noqa: E402
import execution_stop_adapter  # noqa: E402


def at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def interactive_spawn() -> dict:
    return next(
        item["record"]
        for item in (json.loads(line) for line in FIXTURE.read_text().splitlines())
        if item["fixture_id"] == "interactive_spawn_result"
    )


def registered(captured: dict) -> dict:
    result = captured["toolUseResult"]
    dispatch_tool_use_id = captured["message"]["content"][0]["tool_use_id"]
    ledger = ledger_api.new_ledger(captured["session_id"])
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": captured["session_id"],
            "bead_id": "escapement-xncx",
            "execution_id": "exec-captured-interactive",
            "host": "claude",
            "agent_name": result["name"],
            "dispatch_tool_use_id": dispatch_tool_use_id,
            "watchdog_id": "watch-captured-interactive",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-27T18:00:22Z"),
    )
    return ledger


def test_public_posttool_durably_applies_captured_interactive_spawn_prefix(
    tmp_path,
) -> None:
    captured = interactive_spawn()
    path = tmp_path / "executions.json"
    path.write_text(json.dumps(registered(captured)), encoding="utf-8")
    path.chmod(0o600)

    observed = delegation_hook.post_tool(captured, path)

    assert observed["status"] == "observed"
    durable = ledger_api.load_trusted(path, captured["session_id"])
    assert durable is not None
    active = durable["executions"][0]
    assert active["state"] == "running"
    assert active["native_child_id"] == captured["toolUseResult"]["agent_id"]
    assert active["terminal_at"] is None
    assert execution_stop_adapter.execution_stop_decision(
        "closed", durable, None, [], at("2026-08-27T18:00:23Z")
    ) == ("block", "delegated_execution_unresolved")


def test_public_posttool_store_failure_never_denies_claude_capacity(tmp_path) -> None:
    path = tmp_path / "executions.json"
    path.mkdir()

    observed = delegation_hook.post_tool(interactive_spawn(), path)

    assert observed == {
        "status": "unresolved",
        "reason": "lifecycle_observation_persistence_failed",
    }
    assert observed.get("decision") != "deny"
