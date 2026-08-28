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


def run_helper(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=10,
    )


def settings_fixture(home: Path) -> Path:
    path = home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
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
