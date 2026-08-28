#!/usr/bin/env python3
"""Public PostToolUse/store controls for captured Claude lifecycle evidence."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
PUBLIC_FIXTURE = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures"
    / "claude-post-tool-2.1.248.jsonl"
)
PUBLIC_PROVENANCE = PUBLIC_FIXTURE.with_suffix(".provenance.json")
PUBLIC_RAW_DIGEST = "e30710835fb4942d7cbad13170eb866b2132ff9327f3aa1d7ee2a6e6857dae13"
PUBLIC_POINTERS = [
    "/hook_event_name",
    "/session_id",
    "/tool_name",
    "/tool_use_id",
    "/tool_input/name",
    "/tool_input/run_in_background",
    "/tool_input/subagent_type",
    "/tool_response/status",
    "/tool_response/isAsync",
    "/tool_response/agentId",
]
sys.path.insert(0, str(BIN))

import delegation_hook  # noqa: E402
import execution_ledger as ledger_api  # noqa: E402
import execution_stop_adapter  # noqa: E402


def at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def public_payload() -> dict:
    return json.loads(PUBLIC_FIXTURE.read_text(encoding="utf-8"))


def registered(captured: dict) -> dict:
    dispatch_tool_use_id = captured["tool_use_id"]
    ledger = ledger_api.new_ledger(captured["session_id"])
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": captured["session_id"],
            "bead_id": "escapement-xncx",
            "execution_id": "exec-captured-interactive",
            "host": "claude",
            "agent_name": captured["tool_input"]["name"],
            "dispatch_tool_use_id": dispatch_tool_use_id,
            "watchdog_id": "watch-captured-interactive",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-27T18:00:22Z"),
    )
    return ledger


def test_public_fixture_has_the_reviewed_digest_and_leaf_allowlist() -> None:
    captured = public_payload()
    provenance = json.loads(PUBLIC_PROVENANCE.read_text(encoding="utf-8"))

    assert provenance["host"] == {"product": "Claude Code", "version": "2.1.248"}
    assert provenance["raw_record_sha256"] == PUBLIC_RAW_DIGEST
    assert provenance["retained_json_pointers"] == PUBLIC_POINTERS
    assert captured == {
        "hook_event_name": "PostToolUse",
        "session_id": "5c970cf6-35a0-4cc2-8cb3-0c7651d565ed",
        "tool_name": "Agent",
        "tool_use_id": "toolu_01Qr9gowayjY11HDBNSzyVPB",
        "tool_input": {
            "name": "xncx-posttool-capture",
            "run_in_background": True,
            "subagent_type": "general-purpose",
        },
        "tool_response": {
            "agentId": "a2a10d4b4a50dbedc",
            "isAsync": True,
            "status": "async_launched",
        },
    }


def test_public_posttool_durably_applies_captured_async_spawn_prefix(
    tmp_path,
) -> None:
    captured = public_payload()
    path = tmp_path / "executions.json"
    path.write_text(json.dumps(registered(captured)), encoding="utf-8")
    path.chmod(0o600)

    observed = delegation_hook.post_tool(captured, path)

    assert observed["status"] == "observed"
    durable = ledger_api.load_trusted(path, captured["session_id"])
    assert durable is not None
    active = durable["executions"][0]
    assert active["state"] == "running"
    assert active["native_child_id"] == captured["tool_response"]["agentId"]
    assert active["terminal_at"] is None
    assert execution_stop_adapter.execution_stop_decision(
        "closed", durable, None, [], at("2026-08-27T18:00:23Z")
    ) == ("block", "delegated_execution_unresolved")


def test_public_posttool_store_failure_never_denies_claude_capacity(tmp_path) -> None:
    path = tmp_path / "executions.json"
    path.mkdir()

    observed = delegation_hook.post_tool(public_payload(), path)

    assert observed == {
        "status": "unresolved",
        "reason": "lifecycle_observation_persistence_failed",
    }
    assert observed.get("decision") != "deny"


def test_public_posttool_adapter_failure_never_denies_claude_capacity(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "executions.json"
    path.write_text(json.dumps(registered(public_payload())), encoding="utf-8")
    path.chmod(0o600)
    before = path.read_bytes()
    adapter = types.SimpleNamespace(
        observe_post_tool=lambda _payload, _ledger: (_ for _ in ()).throw(
            RuntimeError("adapter drift")
        )
    )
    monkeypatch.setitem(sys.modules, "claude_agent_lifecycle", adapter)

    observed = delegation_hook.post_tool(public_payload(), path)

    assert observed == {
        "status": "unresolved",
        "reason": "lifecycle_observation_adapter_failed",
    }
    assert observed.get("decision") != "deny"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    (
        "event",
        "tool",
        "tool-use-id",
        "name",
        "background",
        "subagent-type",
        "status",
        "agent-id",
        "conflicting-native-child-id",
        "surplus-agent-id",
    ),
)
def test_public_posttool_rejects_any_unproven_envelope_identity(
    tmp_path, mutation
) -> None:
    captured = copy.deepcopy(public_payload())
    if mutation == "event":
        captured["hook_event_name"] = "PreToolUse"
    elif mutation == "tool":
        captured["tool_name"] = "Bash"
    elif mutation == "tool-use-id":
        captured.pop("tool_use_id")
    elif mutation == "name":
        captured["tool_input"].pop("name")
    elif mutation == "background":
        captured["tool_input"]["run_in_background"] = False
    elif mutation == "subagent-type":
        captured["tool_input"].pop("subagent_type")
    elif mutation == "status":
        captured["tool_response"]["status"] = "completed"
    elif mutation == "agent-id":
        captured["tool_response"].pop("agentId")
    elif mutation == "conflicting-native-child-id":
        captured["tool_response"]["native_child_id"] = "conflicting-child"
    else:
        captured["tool_response"]["agent_id"] = captured["tool_response"]["agentId"]
    path = tmp_path / "executions.json"
    path.write_text(json.dumps(registered(public_payload())), encoding="utf-8")
    path.chmod(0o600)
    before = path.read_bytes()

    observed = delegation_hook.post_tool(captured, path)

    assert observed == {
        "status": "unresolved",
        "reason": "native_child_identity_unverified",
    }
    assert path.read_bytes() == before


def test_hook_main_applies_the_actual_posttool_envelope_end_to_end(tmp_path) -> None:
    captured = public_payload()
    harness_root = tmp_path / "harness"
    path = harness_root / "threads" / captured["session_id"] / "executions.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(registered(captured)), encoding="utf-8")
    path.chmod(0o600)
    env = os.environ.copy()
    env["HARNESS_ROOT"] = str(harness_root)

    result = subprocess.run(
        [sys.executable, str(BIN / "delegation_hook.py")],
        input=json.dumps(captured),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    durable = ledger_api.load_trusted(path, captured["session_id"])
    assert durable is not None
    assert durable["executions"][0]["state"] == "running"
    assert (
        durable["executions"][0]["native_child_id"]
        == captured["tool_response"]["agentId"]
    )
