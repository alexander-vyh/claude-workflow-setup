#!/usr/bin/env python3
"""Behavioral contract for the installer fixture's offline Claude host."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tests" / "helpers" / "fake_delegation_canary_claude.py"
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


def run_helper(
    home: Path, *args: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        env={**os.environ, "HOME": str(home), **(extra_env or {})},
        capture_output=True,
        text=True,
        timeout=10,
    )


def settings_fixture(home: Path) -> Path:
    path = home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"model": "opus[1m]", "enabledPlugins": {PLUGIN_ID: False}},
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("args", "enabled"),
    [
        (("plugin", "update", PLUGIN_ID), True),
        (("plugin", "disable", PLUGIN_ID), False),
    ],
)
def test_fake_host_accepts_only_supported_plugin_operations(
    tmp_path, args, enabled
) -> None:
    settings = settings_fixture(tmp_path)

    result = run_helper(tmp_path, *args)

    assert result.returncode == 0, result.stderr
    observed = json.loads(settings.read_text(encoding="utf-8"))
    assert observed["enabledPlugins"][PLUGIN_ID] is enabled


@pytest.mark.parametrize(
    "args",
    [
        ("plugin",),
        ("plugin", "updat", PLUGIN_ID),
        ("plugin", "update"),
        ("plugin", "update", "other@marketplace"),
        ("plugin", "update", PLUGIN_ID, "surplus"),
        ("plugin", PLUGIN_ID, "update"),
        ("plugin", "uninstall", PLUGIN_ID),
    ],
)
def test_fake_host_rejects_unknown_or_malformed_plugin_operations(
    tmp_path, args
) -> None:
    settings = settings_fixture(tmp_path)
    before = settings.read_bytes()

    result = run_helper(tmp_path, *args)

    assert result.returncode != 0
    assert settings.read_bytes() == before


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("plugn", "update", PLUGIN_ID),
        ("doctor",),
        ("--verison",),
        ("--version", "surplus"),
        ("--assert-audit",),
        ("--assert-audit", ""),
        ("--plugin-dir", "/candidate", "--session-id", "session"),
    ],
)
def test_fake_host_rejects_every_unknown_top_level_invocation(tmp_path, args) -> None:
    settings = settings_fixture(tmp_path)
    before = settings.read_bytes()

    result = run_helper(tmp_path, *args)

    assert result.returncode != 0
    assert settings.read_bytes() == before


def exact_host_args(candidate: Path, settings: Path, session_id: str) -> tuple[str, ...]:
    return (
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
        str(settings),
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--plugin-dir",
        str(candidate),
        "--session-id",
        session_id,
        "unmanaged probe",
    )


def test_fake_host_executes_the_supplied_candidate_hook(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    hook = candidate / "harness" / "bin" / "delegation_hook.py"
    schemas = candidate / "harness" / "schemas"
    hook.parent.mkdir(parents=True)
    schemas.mkdir(parents=True)
    sentinel = tmp_path / "candidate-hook.jsonl"
    expected_hook_output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "candidate-specific-denial",
        }
    }
    expected_stdout = json.dumps(expected_hook_output) + "\n"
    hook.write_text(
        "import json, os, pathlib, sys\n"
        "payload=json.load(sys.stdin)\n"
        "with pathlib.Path(os.environ['CANDIDATE_HOOK_SENTINEL']).open('a') as out:\n"
        " out.write(json.dumps(payload)+'\\n')\n"
        f"print({expected_stdout.rstrip()!r})\n",
        encoding="utf-8",
    )
    claude = tmp_path / ".claude"
    harness = claude / "harness"
    backup = tmp_path / "backup"
    (claude / "plugins").mkdir(parents=True)
    harness.mkdir()
    backup.mkdir()
    (backup / "state.json").write_text("{}\n", encoding="utf-8")
    (claude / ".plugin-update-transaction.json").write_text(
        json.dumps({"backup": str(backup)}), encoding="utf-8"
    )
    (claude / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {"plugins": {PLUGIN_ID: [{"scope": "user", "installPath": str(candidate)}]}}
        ),
        encoding="utf-8",
    )
    settings = settings_fixture(tmp_path)
    observed_settings = json.loads(settings.read_text(encoding="utf-8"))
    observed_settings["enabledPlugins"][PLUGIN_ID] = True
    settings.write_text(json.dumps(observed_settings), encoding="utf-8")
    (harness / "bin").symlink_to(hook.parent)
    (harness / "schemas").symlink_to(schemas)
    (harness / "continuation-supervisor-installed.json").write_text("{}\n")
    plist = tmp_path / "Library" / "LaunchAgents"
    plist.mkdir(parents=True)
    (plist / "com.escapement.continuation-supervisor.plist").write_text("plist\n")
    (tmp_path / "launchctl.loaded").write_text(
        "com.escapement.continuation-supervisor\n", encoding="utf-8"
    )
    host_harness = tmp_path / "host-harness"
    host_harness.mkdir()
    session_id = "sentinel-unmanaged-session"

    result = run_helper(
        tmp_path,
        *exact_host_args(candidate, settings, session_id),
        extra_env={
            "HARNESS_ROOT": str(host_harness),
            "CANDIDATE_HOOK_SENTINEL": str(sentinel),
        },
    )

    assert result.returncode == 0, result.stderr
    payloads = [json.loads(line) for line in sentinel.read_text().splitlines()]
    assert len(payloads) == 1
    assert payloads[0]["hook_event_name"] == "PreToolUse"
    assert payloads[0]["tool_name"] == "Agent"
    assert payloads[0]["session_id"] == session_id
    assert payloads[0]["tool_input"]["run_in_background"] is True
    assert payloads[0]["tool_use_id"]
    responses = [
        record
        for record in (json.loads(line) for line in result.stdout.splitlines())
        if record.get("type") == "system"
        and record.get("subtype") == "hook_response"
    ]
    assert len(responses) == 1
    assert responses[0]["stdout"] == expected_stdout
    assert responses[0]["output"] == expected_stdout
    assert json.loads(responses[0]["stdout"]) == expected_hook_output
    sentinel_before = sentinel.read_bytes()
    settings_before = settings.read_bytes()

    invalid = run_helper(
        tmp_path,
        *exact_host_args(candidate, settings, session_id),
        "surplus",
        extra_env={
            "HARNESS_ROOT": str(host_harness),
            "CANDIDATE_HOOK_SENTINEL": str(sentinel),
        },
    )

    assert invalid.returncode != 0
    assert sentinel.read_bytes() == sentinel_before
    assert settings.read_bytes() == settings_before


def test_fake_host_rejects_reordered_complete_host_invocation(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    settings = settings_fixture(tmp_path)
    args = list(exact_host_args(candidate, settings, "reordered-session"))
    args[0], args[1] = args[1], args[0]
    before = settings.read_bytes()

    result = run_helper(tmp_path, *args)

    assert result.returncode != 0
    assert settings.read_bytes() == before


def audit_record(phase: str) -> dict:
    return {"phase": phase, **dict.fromkeys(AUDIT_CHECKS, True)}


def write_audit(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_audit_accepts_exact_nonempty_candidate_evidence(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    write_audit(audit, [audit_record("unmanaged"), audit_record("managed")])

    assert run_helper(tmp_path, "--assert-audit", str(audit)).returncode == 0


@pytest.mark.parametrize("mutation", ["phase-only", "missing", "extra", "wrong-type"])
def test_audit_rejects_incomplete_or_ambiguous_evidence(tmp_path, mutation) -> None:
    audit = tmp_path / "audit.jsonl"
    records = [audit_record("unmanaged"), audit_record("managed")]
    if mutation == "phase-only":
        records = [{"phase": "unmanaged"}, {"phase": "managed"}]
    elif mutation == "missing":
        records[0].pop("journal_armed")
    elif mutation == "extra":
        records[1]["surplus"] = True
    else:
        records[0]["registry_live"] = 1
    write_audit(audit, records)

    assert run_helper(tmp_path, "--assert-audit", str(audit)).returncode != 0
