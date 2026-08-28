#!/usr/bin/env python3
"""Offline Claude host for installer tests that exercises the real canary seam."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "tests" / "test_delegation_canary.py"
VERSION = "2.1.248"
PLUGIN_ID = "escapement@escapement"
AUDIT_CHECKS = {
    "journal_armed",
    "registry_live",
    "bin_wrapper_live",
    "schemas_wrapper_live",
    "plugin_setting_restored",
    "supervisor_marker_live",
    "supervisor_plist_live",
    "supervisor_loaded",
}


def _contract():
    spec = importlib.util.spec_from_file_location("installer_canary_contract", CONTRACT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("delegation canary contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin_command(args: list[str]) -> int:
    supported = {
        ("plugin", "update", PLUGIN_ID): True,
        ("plugin", "disable", PLUGIN_ID): False,
    }
    enabled = supported.get(tuple(args))
    if enabled is None:
        return 2
    settings_path = Path.home() / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings.setdefault("enabledPlugins", {})[PLUGIN_ID] = enabled
    if enabled:
        settings.pop("model", None)
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return 0


def _candidate_audit(candidate: Path, phase: str) -> None:
    claude = Path.home() / ".claude"
    journal = claude / ".plugin-update-transaction.json"
    pointer = json.loads(journal.read_text(encoding="utf-8")) if journal.is_file() else None
    backup = Path(pointer["backup"]) if isinstance(pointer, dict) else None
    registry = json.loads(
        (claude / "plugins" / "installed_plugins.json").read_text(encoding="utf-8")
    )
    selected = [
        Path(item["installPath"]).resolve()
        for item in registry["plugins"]["escapement@escapement"]
        if item.get("scope") == "user"
    ]
    settings = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    loaded = Path.home() / "launchctl.loaded"
    observed = {
        "phase": phase,
        "journal_armed": bool(
            backup
            and journal.is_file()
            and (backup / "state.json").is_file()
            and not (Path(str(journal) + ".commit-guard")).exists()
        ),
        "registry_live": selected == [candidate],
        "bin_wrapper_live": (claude / "harness" / "bin").resolve()
        == (candidate / "harness" / "bin").resolve(),
        "schemas_wrapper_live": (claude / "harness" / "schemas").resolve()
        == (candidate / "harness" / "schemas").resolve(),
        "plugin_setting_restored": settings["enabledPlugins"][
            "escapement@escapement"
        ]
        is True,
        "supervisor_marker_live": (
            claude / "harness" / "continuation-supervisor-installed.json"
        ).is_file(),
        "supervisor_plist_live": (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / "com.escapement.continuation-supervisor.plist"
        ).is_file(),
        "supervisor_loaded": loaded.is_file()
        and "com.escapement.continuation-supervisor"
        in loaded.read_text(encoding="utf-8").splitlines(),
    }
    audit = Path.home() / "canary-audit.jsonl"
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observed, sort_keys=True) + "\n")
    if not all(value for key, value in observed.items() if key != "phase"):
        raise SystemExit(72)


def _assert_audit(path: Path) -> int:
    records = (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if path.is_file()
        else []
    )
    expected_keys = AUDIT_CHECKS | {"phase"}
    if [record.get("phase") for record in records] != ["unmanaged", "managed"]:
        return 1
    if any(set(record) != expected_keys for record in records):
        return 1
    return int(any(record[key] is not True for record in records for key in AUDIT_CHECKS))


def _is_host_invocation(args: list[str]) -> bool:
    return (
        len(args) == 22
        and args[:13]
        == [
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--forward-subagent-text",
            "--include-hook-events",
            "--no-session-persistence",
            "--permission-mode",
            "bypassPermissions",
            "--dangerously-skip-permissions",
            "--setting-sources",
            "",
            "--settings",
        ]
        and bool(args[13])
        and args[14:18]
        == [
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--plugin-dir",
        ]
        and bool(args[18])
        and args[19] == "--session-id"
        and bool(args[20])
        and bool(args[21])
    )


def _host_stream(args: list[str]) -> int:
    candidate = Path(args[args.index("--plugin-dir") + 1]).resolve()
    session_id = args[args.index("--session-id") + 1]
    harness = Path(os.environ["HARNESS_ROOT"])
    managed = (harness / "threads" / session_id / "session_mode.json").is_file()
    _candidate_audit(candidate, "managed" if managed else "unmanaged")
    contract = _contract()
    records = contract.managed_stream() if managed else contract.unmanaged_stream()
    for record in records:
        if isinstance(record.get("session_id"), str):
            record["session_id"] = session_id
        if record.get("type") == "system" and record.get("subtype") == "init":
            record["claude_code_version"] = VERSION
            record["plugins"] = [
                {
                    "name": "escapement",
                    "path": str(candidate),
                    "source": "escapement@inline",
                }
            ]
            record.pop("plugin_errors", None)
    observed = []
    dispatch_inputs = {}
    for record in records:
        observed.append(record)
        content = record.get("message", {}).get("content", [])
        item = content[0] if len(content) == 1 and isinstance(content[0], dict) else {}
        if item.get("type") != "tool_use" or item.get("name") != "Agent":
            continue
        dispatch_inputs[item.get("id")] = item.get("input")
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "session_id": session_id,
            "tool_use_id": item.get("id"),
            "tool_input": item.get("input"),
        }
        hook = subprocess.run(
            [sys.executable, "-B", str(candidate / "harness/bin/delegation_hook.py")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=os.environ,
            timeout=10,
        )
        observed.append(
            {
                "type": "system",
                "subtype": "hook_response",
                "hook_id": str(uuid.uuid4()),
                "hook_name": "PreToolUse:Agent",
                "hook_event": "PreToolUse",
                "output": hook.stdout,
                "stdout": hook.stdout,
                "stderr": hook.stderr,
                "exit_code": hook.returncode,
                "outcome": "success" if hook.returncode == 0 else "error",
                "session_id": session_id,
            }
        )
    if managed:
        completed = []
        for record in observed:
            content = record.get("message", {}).get("content", [])
            item = (
                content[0]
                if len(content) == 1 and isinstance(content[0], dict)
                else {}
            )
            result = record.get("tool_use_result")
            tool_id = item.get("tool_use_id")
            tool_input = dispatch_inputs.get(tool_id)
            if (
                not isinstance(result, dict)
                or result.get("status") != "async_launched"
                or not isinstance(tool_input, dict)
            ):
                completed.append(record)
                continue
            hook = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(candidate / "harness/bin/delegation_hook.py"),
                ],
                input=json.dumps(
                    {
                        "hook_event_name": "PostToolUse",
                        "tool_name": "Agent",
                        "session_id": session_id,
                        "tool_use_id": tool_id,
                        "tool_input": tool_input,
                        "tool_response": {
                            "status": result.get("status"),
                            "isAsync": result.get("isAsync"),
                            "agentId": result.get("agentId"),
                        },
                    }
                ),
                capture_output=True,
                text=True,
                env=os.environ,
                timeout=10,
            )
            completed.append(
                {
                    "type": "system",
                    "subtype": "hook_response",
                    "hook_id": str(uuid.uuid4()),
                    "hook_name": "PostToolUse:Agent",
                    "hook_event": "PostToolUse",
                    "output": hook.stdout,
                    "stdout": hook.stdout,
                    "stderr": hook.stderr,
                    "exit_code": hook.returncode,
                    "outcome": "success" if hook.returncode == 0 else "error",
                    "session_id": session_id,
                }
            )
            completed.append(record)
        observed = completed
    sys.stdout.write("".join(json.dumps(record) + "\n" for record in observed))
    return 0


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--assert-audit" and args[1]:
        return _assert_audit(Path(args[1]))
    if args == ["--version"]:
        print(f"{VERSION} (Claude Code)")
        return 0
    if args and args[0] == "plugin":
        return _plugin_command(args)
    if _is_host_invocation(args):
        return _host_stream(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
