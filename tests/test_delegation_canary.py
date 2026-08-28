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


def init_record(session_id: str) -> dict:
    return {
        "type": "system",
        "subtype": "init",
        "claude_code_version": "2.1.247",
        "session_id": session_id,
        "plugins": [
            {
                "name": "escapement",
                "path": str(PLUGIN.resolve()),
                "source": "escapement@inline",
            }
        ],
    }


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
        init_record(session_id),
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
                                    "message": (
                                        "Message queued for delivery to canary-child-2 "
                                        "at its next tool round."
                                    ),
                                    "pin": {
                                        "id": launches[pin_index][1]["task_id"],
                                        "name": f"canary-child-{pin_index + 1}",
                                        "ref": "fixture-ref",
                                    },
                                }
                            ),
                        }
                    ],
                }
            ],
        },
    }
    result_item = response["message"]["content"][0]
    text_item = result_item["content"][0]
    acknowledgement = json.loads(text_item["text"])
    if scenario == "live-peer-error-true":
        result_item["is_error"] = True
    elif scenario == "live-peer-multiple-content":
        result_item["content"].append({"type": "text", "text": "ambiguous"})
    elif scenario == "live-peer-non-text-content":
        text_item["type"] = "json"
    elif scenario == "live-peer-malformed-json":
        text_item["text"] = "{not-json"
    elif scenario == "live-peer-extra-envelope":
        text_item["status"] = "success"
    elif scenario == "live-peer-conflicting-status":
        acknowledgement["status"] = "failed"
        text_item["text"] = json.dumps(acknowledgement)
    elif scenario == "live-peer-conflicting-identity":
        acknowledgement["pin"]["agentId"] = launches[2][1]["task_id"]
        text_item["text"] = json.dumps(acknowledgement)
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
        init_record("canary-managed-session"),
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
        "import json, os, pathlib, subprocess, sys, uuid\n"
        "if '--version' in sys.argv[1:]:\n"
        " print('2.1.247 (Claude Code)'); raise SystemExit(0)\n"
        "audit=pathlib.Path(os.environ['FAKE_CLAUDE_AUDIT'])\n"
        "with audit.open('a') as handle:\n"
        " handle.write(json.dumps({'config_dir':os.environ.get('CLAUDE_CONFIG_DIR'),'args':sys.argv[1:]})+'\\n')\n"
        "counter=pathlib.Path(os.environ['FAKE_CLAUDE_COUNTER'])\n"
        "count=int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(count+1))\n"
        "key='FAKE_UNMANAGED_STREAM' if count == 0 else 'FAKE_MANAGED_STREAM'\n"
        "records=[json.loads(line) for line in pathlib.Path(os.environ[key]).read_text().splitlines()]\n"
        "session_id=sys.argv[sys.argv.index('--session-id')+1]\n"
        "for record in records:\n"
        " if isinstance(record.get('session_id'),str): record['session_id']=session_id\n"
        "plugin_dir=sys.argv[sys.argv.index('--plugin-dir')+1]\n"
        "if not pathlib.Path(plugin_dir).is_absolute():\n"
        " for record in records:\n"
        "  if record.get('type')=='system' and record.get('subtype')=='init':\n"
        "   record['plugins']=[]; record['plugin_errors']=[{'type':'path-not-found','path':plugin_dir}]\n"
        "if os.environ.get('FAKE_INVOKE_AGENT_HOOKS') == '1':\n"
        " observed=[]\n"
        " for record in records:\n"
        "  observed.append(record)\n"
        "  content=record.get('message',{}).get('content',[])\n"
        "  item=content[0] if len(content)==1 and isinstance(content[0],dict) else {}\n"
        "  if item.get('type')!='tool_use' or item.get('name')!='Agent': continue\n"
        "  payload={'hook_event_name':'PreToolUse','tool_name':'Agent','session_id':record.get('session_id'),'tool_use_id':item.get('id'),'tool_input':item.get('input')}\n"
        "  hook=subprocess.run([sys.executable,'-B',str(pathlib.Path(plugin_dir)/'harness/bin/delegation_hook.py')],input=json.dumps(payload),capture_output=True,text=True,env=os.environ)\n"
        "  if os.environ.get('FAKE_EMIT_HOOK_RESPONSES') == '1': observed.append({'type':'system','subtype':'hook_response','hook_id':str(uuid.uuid4()),'hook_name':'PreToolUse:Agent','hook_event':'PreToolUse','output':hook.stdout,'stdout':hook.stdout,'stderr':hook.stderr,'exit_code':hook.returncode,'outcome':'success' if hook.returncode==0 else 'error','session_id':record.get('session_id')})\n"
        " records=observed\n"
        "sys.stdout.write(''.join(json.dumps(record)+'\\n' for record in records))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def mutate_init(records: list[dict], scenario: str, other_path: Path) -> None:
    init = next(record for record in records if record.get("subtype") == "init")
    plugin = init["plugins"][0]
    if scenario == "empty-plugins":
        init["plugins"] = []
    elif scenario == "plugin-errors":
        init["plugin_errors"] = [{"type": "path-not-found"}]
    elif scenario == "empty-plugin-errors":
        init["plugin_errors"] = []
    elif scenario == "null-plugin-errors":
        init["plugin_errors"] = None
    elif scenario == "wrong-name":
        plugin["name"] = "not-escapement"
    elif scenario == "wrong-path":
        plugin["path"] = str(other_path / "missing")
    elif scenario == "wrong-source":
        plugin["source"] = "escapement@marketplace"
    elif scenario == "later-wrong-path":
        later = copy.deepcopy(init)
        later["plugins"][0]["path"] = str(other_path)
        records.insert(1, later)


def run_canary(
    tmp_path: Path,
    scenario: str = "ok",
    *,
    drift: bool = False,
    init_scenario: str | None = None,
    init_target: str = "managed",
    relative_paths: bool = False,
    claude_path_mode: str = "absolute",
    invoke_agent_hooks: bool = True,
    emit_hook_responses: bool = True,
):
    assert CANARY.is_file(), "delegation canary is not implemented"
    unmanaged = tmp_path / "unmanaged.jsonl"
    managed = tmp_path / "managed.jsonl"
    unmanaged_records = unmanaged_stream()
    managed_records = managed_stream(scenario)
    if init_scenario:
        other_path = tmp_path / "other-plugin"
        if init_scenario == "later-wrong-path":
            shutil.copytree(PLUGIN, other_path)
        target = unmanaged_records if init_target == "unmanaged" else managed_records
        mutate_init(target, init_scenario, other_path)
    write_jsonl(unmanaged, unmanaged_records)
    write_jsonl(managed, managed_records)
    candidate = PLUGIN
    if drift:
        candidate = tmp_path / "candidate"
        shutil.copytree(PLUGIN, candidate)
        hooks = candidate / "hooks" / "hooks.json"
        hooks.write_text(hooks.read_text() + "\n", encoding="utf-8")
    counter = tmp_path / "claude-counter"
    scratch = tmp_path / "scratch"
    source_arg: Path | str = ROOT
    candidate_arg: Path | str = candidate
    scratch_arg: Path | str = scratch
    if relative_paths:
        source_arg = "."
        candidate_arg = "plugins/escapement-claude"
        scratch_arg = os.path.relpath(scratch, ROOT)
    claude_path = fake_claude(tmp_path / "claude")
    child_path = os.environ.get("PATH", "")
    claude_arg = str(claude_path)
    if claude_path_mode == "explicit-relative":
        claude_arg = os.path.relpath(claude_path, ROOT)
    elif claude_path_mode == "name-relative-path":
        claude_arg = "claude"
        child_path = f"{os.path.relpath(tmp_path, ROOT)}:{child_path}"
    result = subprocess.run(
        [
            sys.executable,
            str(CANARY),
            "--claude-bin",
            claude_arg,
            "--source-root",
            str(source_arg),
            "--candidate-root",
            str(candidate_arg),
            "--scratch-root",
            str(scratch_arg),
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
            "FAKE_INVOKE_AGENT_HOOKS": "1" if invoke_agent_hooks else "0",
            "FAKE_EMIT_HOOK_RESPONSES": "1" if emit_hook_responses else "0",
            "PATH": child_path,
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


def test_canary_rejects_valid_transcript_without_automatic_hook_ledger(tmp_path) -> None:
    result, output = run_canary(tmp_path, invoke_agent_hooks=False)

    assert result.returncode != 0
    assert output == {"status": "fail", "reason": "managed_completion_unresolved"}


def test_canary_rejects_hook_ledger_without_structured_host_response(tmp_path) -> None:
    result, output = run_canary(tmp_path, emit_hook_responses=False)

    assert result.returncode != 0
    assert output == {"status": "fail", "reason": "managed_completion_unresolved"}


def test_canary_has_no_post_hoc_dispatch_registration_authority() -> None:
    lifecycle = (ROOT / "scripts" / "delegation_canary_lifecycle.py").read_text()

    assert "register_dispatches" not in lifecycle


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
    import delegation_hook

    for _index, record, item in lifecycle.dispatches(records):
        assert delegation_hook.pre_tool(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
                "session_id": record["session_id"],
                "tool_use_id": item["id"],
                "tool_input": item["input"],
            },
            None,
            ledger_path,
        )["reason"] == "dispatch_registered"
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
        ("live-peer-error-true", "peer_dependency_unproven"),
        ("live-peer-multiple-content", "peer_dependency_unproven"),
        ("live-peer-non-text-content", "peer_dependency_unproven"),
        ("live-peer-malformed-json", "peer_dependency_unproven"),
        ("live-peer-extra-envelope", "peer_dependency_unproven"),
        ("live-peer-conflicting-status", "peer_dependency_unproven"),
        ("live-peer-conflicting-identity", "peer_dependency_unproven"),
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


@pytest.mark.parametrize(
    ("target", "scenario"),
    [
        ("unmanaged", "empty-plugins"),
        ("managed", "empty-plugins"),
        ("unmanaged", "plugin-errors"),
        ("managed", "plugin-errors"),
        ("unmanaged", "empty-plugin-errors"),
        ("managed", "null-plugin-errors"),
        ("managed", "wrong-name"),
        ("managed", "wrong-path"),
        ("unmanaged", "wrong-source"),
        ("managed", "later-wrong-path"),
    ],
)
def test_canary_rejects_unloaded_or_wrong_candidate_plugin(
    tmp_path, target, scenario
) -> None:
    result, output = run_canary(
        tmp_path, init_scenario=scenario, init_target=target
    )

    assert result.returncode != 0
    assert output == {"status": "fail", "reason": "host_capability_unresolved"}


def test_canary_canonicalizes_relative_roots_before_changing_child_cwd(tmp_path) -> None:
    result, output = run_canary(tmp_path, relative_paths=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert output["status"] == "pass"
    calls = [
        json.loads(line)
        for line in (tmp_path / "claude-audit.jsonl").read_text().splitlines()
    ]
    for call in calls:
        plugin_dir = call["args"][call["args"].index("--plugin-dir") + 1]
        settings = call["args"][call["args"].index("--settings") + 1]
        assert plugin_dir == str(PLUGIN.resolve())
        assert Path(settings).is_absolute()


@pytest.mark.parametrize("mode", ["explicit-relative", "name-relative-path"])
def test_canary_resolves_claude_before_changing_child_cwd(tmp_path, mode) -> None:
    result, output = run_canary(tmp_path, claude_path_mode=mode)

    assert result.returncode == 0, result.stdout + result.stderr
    assert output["status"] == "pass"


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
