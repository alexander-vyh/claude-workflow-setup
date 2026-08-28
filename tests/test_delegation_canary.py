#!/usr/bin/env python3
"""Contract oracle for the isolated installed-host delegation canary."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CANARY = ROOT / "scripts" / "delegation-canary.py"
PLUGIN = ROOT / "plugins" / "escapement-claude"
FIXTURE = (
    ROOT
    / "harness"
    / "tests"
    / "fixtures"
    / "claude-agent-lifecycle-2.1.247.jsonl"
)


def captured() -> dict[str, dict]:
    return {
        item["fixture_id"]: item["record"]
        for item in (json.loads(line) for line in FIXTURE.read_text().splitlines())
    }


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def load_lifecycle_module():
    path = ROOT / "scripts" / "delegation_canary_lifecycle.py"
    assert path.is_file(), "delegation canary lifecycle module is not implemented"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("delegation_canary_lifecycle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unmanaged_stream() -> list[dict]:
    records = captured()
    session_id = records["background_agent_tool_use"]["session_id"]
    return [
        {
            "type": "system",
            "subtype": "init",
            "claude_code_version": "2.1.247",
            "session_id": session_id,
        },
        copy.deepcopy(records["background_agent_tool_use"]),
        copy.deepcopy(records["background_task_started"]),
        copy.deepcopy(records["background_async_result"]),
        copy.deepcopy(records["background_task_terminal"]),
        {"type": "result", "subtype": "success", "result": "UNMANAGED_OK"},
    ]


def _agent_records(index: int) -> tuple[list[dict], dict]:
    tool_id = f"toolu_canary_{index}"
    task_id = f"canary-native-{index}"
    name = f"canary-child-{index}"
    records = [
        {
            "type": "assistant",
            "timestamp": f"2026-08-27T23:00:0{index}.000Z",
            "session_id": "canary-managed-session",
            "uuid": f"dispatch-{index}",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": "Agent",
                        "input": {
                            "name": name,
                            "subagent_type": "general-purpose",
                            "run_in_background": True,
                        },
                    }
                ],
            },
        },
        {
            "type": "system",
            "subtype": "task_started",
            "task_id": task_id,
            "tool_use_id": tool_id,
            "subagent_type": "general-purpose",
            "is_backgrounded": True,
            "spawn_depth": 1,
            "task_type": "local_agent",
            "uuid": f"started-{index}",
            "session_id": "canary-managed-session",
        },
        {
            "type": "user",
            "timestamp": f"2026-08-27T23:00:0{index}.100Z",
            "session_id": "canary-managed-session",
            "uuid": f"result-{index}",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_id}],
            },
            "tool_use_result": {
                "isAsync": True,
                "status": "async_launched",
                "agentId": task_id,
            },
        },
    ]
    terminal = {
        "type": "system",
        "subtype": "task_notification",
        "task_id": task_id,
        "tool_use_id": tool_id,
        "status": "completed",
        "summary": (
            "used DEPENDENCY-42" if index == 2 else f"child {index} completed"
        ),
        "uuid": f"terminal-{index}",
        "session_id": "canary-managed-session",
    }
    return records, terminal


def _live_peer_records(launches: list[list[dict]], scenario: str) -> list[dict]:
    send_id = "toolu_peer_send"
    sender_index = 2 if scenario == "live-wrong-sender" else 0
    recipient_index = 2 if scenario == "live-wrong-recipient-name" else 1
    pin_index = 2 if scenario == "live-wrong-recipient-id" else recipient_index
    token = "DEPENDENCY-WRONG" if scenario == "live-wrong-nonce" else "DEPENDENCY-42"
    request = {
        "type": "assistant",
        "session_id": "canary-managed-session",
        "parent_tool_use_id": launches[sender_index][0]["message"]["content"][0]["id"],
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": send_id,
                    "name": "SendMessage",
                    "input": {
                        "recipient": f"canary-child-{recipient_index + 1}",
                        "message": token,
                    },
                }
            ],
        },
    }
    if scenario == "live-peer-unacknowledged":
        return [request]
    response = {
        "type": "user",
        "session_id": "canary-managed-session",
        "parent_tool_use_id": request["parent_tool_use_id"],
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": send_id,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "success": True,
                                    "pin": {
                                        "id": launches[pin_index][1]["task_id"],
                                        "name": f"canary-child-{pin_index + 1}",
                                    },
                                }
                            ),
                        }
                    ],
                }
            ],
        },
    }
    return [request, response]


def managed_stream(scenario: str = "ok") -> list[dict]:
    launches: list[list[dict]] = []
    terminals: list[dict] = []
    for index in range(1, 4):
        records, terminal = _agent_records(index)
        launches.append(records)
        terminals.append(terminal)
    if scenario == "duplicate-ids":
        launches[1][1]["task_id"] = launches[0][1]["task_id"]
        launches[1][2]["tool_use_result"]["agentId"] = launches[0][1]["task_id"]
        terminals[1]["task_id"] = launches[0][1]["task_id"]
    terminals_inline = scenario in {"sequential", "native-sequential"}
    if scenario == "sequential":
        launch_and_terminal = []
        for launch, terminal in zip(launches, terminals, strict=True):
            launch_and_terminal.extend([*launch, terminal])
    elif scenario == "native-sequential":
        launch_and_terminal = [launch[0] for launch in launches]
        for launch, terminal in zip(launches, terminals, strict=True):
            launch_and_terminal.extend([launch[1], launch[2], terminal])
    else:
        launch_and_terminal = [record for launch in launches for record in launch]
    if scenario == "missing-peer":
        peer_records = []
    elif scenario.startswith("live-"):
        peer_records = _live_peer_records(launches, scenario)
    else:
        peer_records = _live_peer_records(launches, "live-peer")
    if scenario == "unresolved-terminal":
        terminals = [record for record in terminals if record.get("uuid") != "terminal-3"]
    no_spawn = captured()
    no_spawn_dispatch = copy.deepcopy(no_spawn["no_spawn_agent_tool_use"])
    no_spawn_result = copy.deepcopy(no_spawn["no_spawn_error_result"])
    no_spawn_dispatch["session_id"] = "canary-managed-session"
    no_spawn_result["session_id"] = "canary-managed-session"
    terminal_records = [] if terminals_inline else terminals
    if scenario == "live-peer-after-terminal":
        managed_records = [*launch_and_terminal, *terminal_records, *peer_records]
    elif scenario == "live-peer-ack-after-terminal":
        managed_records = [
            *launch_and_terminal,
            peer_records[0],
            *terminal_records,
            peer_records[1],
        ]
    else:
        managed_records = [*launch_and_terminal, *peer_records, *terminal_records]
    result = [
        {
            "type": "system",
            "subtype": "init",
            "claude_code_version": "2.1.247",
            "session_id": "canary-managed-session",
        },
        *managed_records,
        no_spawn_dispatch,
        no_spawn_result,
        {
            "type": "result",
            "subtype": "success",
            "result": "CANARY_COMPLETE DEPENDENCY-42",
        },
    ]
    return result


def fake_claude(path: Path) -> Path:
    path.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "if '--version' in sys.argv[1:]:\n"
        " print('2.1.247 (Claude Code)'); raise SystemExit(0)\n"
        "audit=pathlib.Path(os.environ['FAKE_CLAUDE_AUDIT'])\n"
        "with audit.open('a') as handle:\n"
        " import json; handle.write(json.dumps({'config_dir':os.environ.get('CLAUDE_CONFIG_DIR'),'args':sys.argv[1:]})+'\\n')\n"
        "counter=pathlib.Path(os.environ['FAKE_CLAUDE_COUNTER'])\n"
        "count=int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(count+1))\n"
        "key='FAKE_UNMANAGED_STREAM' if count == 0 else 'FAKE_MANAGED_STREAM'\n"
        "sys.stdout.write(pathlib.Path(os.environ[key]).read_text())\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def run_canary(tmp_path: Path, scenario: str = "ok", *, drift: bool = False):
    assert CANARY.is_file(), "delegation canary is not implemented"
    unmanaged = tmp_path / "unmanaged.jsonl"
    managed = tmp_path / "managed.jsonl"
    write_jsonl(unmanaged, unmanaged_stream())
    write_jsonl(managed, managed_stream(scenario))
    candidate = PLUGIN
    if drift:
        candidate = tmp_path / "candidate"
        shutil.copytree(PLUGIN, candidate)
        hooks = candidate / "hooks" / "hooks.json"
        hooks.write_text(hooks.read_text() + "\n", encoding="utf-8")
    counter = tmp_path / "claude-counter"
    scratch = tmp_path / "scratch"
    result = subprocess.run(
        [
            sys.executable,
            str(CANARY),
            "--claude-bin",
            str(fake_claude(tmp_path / "claude")),
            "--source-root",
            str(ROOT),
            "--candidate-root",
            str(candidate),
            "--scratch-root",
            str(scratch),
            "--expected-version",
            "2.1.247",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "FAKE_CLAUDE_COUNTER": str(counter),
            "FAKE_CLAUDE_AUDIT": str(tmp_path / "claude-audit.jsonl"),
            "FAKE_UNMANAGED_STREAM": str(unmanaged),
            "FAKE_MANAGED_STREAM": str(managed),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    output = None
    for line in reversed(result.stdout.splitlines()):
        try:
            output = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    return result, output


def test_isolated_canary_proves_unmanaged_and_managed_public_outcomes(tmp_path) -> None:
    result, output = run_canary(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert output["status"] == "pass"
    assert output["host_version"] == "2.1.247"
    assert output["unmanaged"]["first_attempt"] is True
    assert output["unmanaged"]["escapement_state_created"] is False
    assert output["managed"]["distinct_native_children"] == 3
    assert output["managed"]["overlap_proven"] is True
    assert output["managed"]["peer_dependency_proven"] is True
    assert output["managed"]["terminal_count"] == 3
    assert output["managed"]["abort_count"] == 1
    assert output["managed"]["completion_decision"] == [
        "allow",
        "delegated_outcome_complete",
    ]


def test_canary_accepts_acknowledged_child_to_child_dependency(tmp_path) -> None:
    result, output = run_canary(tmp_path, "live-peer")

    assert result.returncode == 0, result.stdout + result.stderr
    assert output["managed"]["peer_dependency_proven"] is True


def test_result_release_rejects_cross_swapped_terminal_evidence(tmp_path) -> None:
    lifecycle = load_lifecycle_module()
    records = managed_stream()
    session_id = "canary-managed-session"
    scratch = tmp_path / "scratch"
    thread = scratch / "harness" / "threads" / session_id
    lifecycle.write_mode(thread, session_id, tmp_path)
    ledger_path = thread / "executions.json"
    api = lifecycle.load_api(PLUGIN)
    lifecycle.register_dispatches(records, ledger_path, api["hook"])
    transcript = scratch / "managed-stream.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(transcript, records)
    lifecycle.apply_transcript(transcript, ledger_path, session_id, api)

    def cross_swap(ledger: dict) -> dict:
        terminal_ids = [
            item["terminal_event_id"]
            for item in ledger["executions"]
            if item["state"] == "terminal"
        ]
        for index, execution in enumerate(
            item for item in ledger["executions"] if item["state"] == "terminal"
        ):
            execution["terminal_event_id"] = terminal_ids[(index + 1) % len(terminal_ids)]
        return ledger

    api["store"].mutate_atomic(ledger_path, cross_swap)

    with pytest.raises(lifecycle.CanaryFailure, match="managed_completion_unresolved"):
        lifecycle.apply_results(ledger_path, session_id, records, api)


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("sequential", "children_do_not_overlap"),
        ("native-sequential", "children_do_not_overlap"),
        ("duplicate-ids", "native_child_identity_unresolved"),
        ("missing-peer", "peer_dependency_unproven"),
        ("live-wrong-sender", "peer_dependency_unproven"),
        ("live-wrong-recipient-name", "peer_dependency_unproven"),
        ("live-wrong-recipient-id", "peer_dependency_unproven"),
        ("live-wrong-nonce", "peer_dependency_unproven"),
        ("live-peer-unacknowledged", "peer_dependency_unproven"),
        ("live-peer-after-terminal", "peer_dependency_unproven"),
        ("live-peer-ack-after-terminal", "peer_dependency_unproven"),
        ("unresolved-terminal", "managed_completion_unresolved"),
    ],
)
def test_canary_rejects_fragile_managed_workflows(tmp_path, scenario, reason) -> None:
    result, output = run_canary(tmp_path, scenario)

    assert result.returncode != 0
    assert output == {"status": "fail", "reason": reason}


def test_canary_rejects_candidate_source_or_hook_drift(tmp_path) -> None:
    result, output = run_canary(tmp_path, drift=True)

    assert result.returncode != 0
    assert output == {"status": "fail", "reason": "installed_surface_drift"}


def test_canary_isolates_settings_without_hiding_host_authentication(tmp_path) -> None:
    result, _output = run_canary(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = [
        json.loads(line)
        for line in (tmp_path / "claude-audit.jsonl").read_text().splitlines()
    ]
    assert len(calls) == 2
    for call in calls:
        assert call["config_dir"] is None
        assert "--no-session-persistence" in call["args"]
        assert "--tools" not in call["args"]
        setting_sources = call["args"][call["args"].index("--setting-sources") + 1]
        assert setting_sources == ""
        settings = call["args"][call["args"].index("--settings") + 1]
        assert Path(settings).is_relative_to(tmp_path / "scratch" / "config")
        mcp = call["args"][call["args"].index("--mcp-config") + 1]
        assert json.loads(mcp) == {"mcpServers": {}}
