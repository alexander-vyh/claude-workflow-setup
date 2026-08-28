#!/usr/bin/env python3
"""Oracle for the updater's one rollback-capable pre-commit canary."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from test_plugin_update_supervisor_transaction import (
    PLUGIN,
    _cutover_fixture,
    _native_agent_probe,
    _run,
    _write_executable,
)


def install_audit(tmp_path: Path, fake_bin: Path) -> tuple[dict, Path, Path]:
    trace = tmp_path / "trace.jsonl"
    snapshot = tmp_path / "snapshot.json"
    real_python = sys.executable
    _write_executable(
        fake_bin / "python3",
        f"#!{real_python}\n"
        + r'''
import json, os, pathlib, sys
args=sys.argv[1:]
trace=pathlib.Path(os.environ["CUTOVER_TRACE"])
def record(value):
    with trace.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value)+"\n")
script=next((value for value in args if value.endswith(".py")), None)
if script and script.endswith("plugin-update-transaction.py"):
    index=args.index(script)
    action=args[index+1] if index+1 < len(args) else "missing"
    if action in {"begin","commit","rollback","recover"}:
        record(["transaction",action])
if script and script.endswith("delegation-canary.py"):
    record(["canary"])
    home=pathlib.Path(os.environ["HOME"])
    claude=home/".claude"
    journal=claude/".plugin-update-transaction.json"
    registry=json.loads((claude/"plugins/installed_plugins.json").read_text())
    selected=pathlib.Path(registry["plugins"]["escapement@escapement"][0]["installPath"])
    pointer=json.loads(journal.read_text()) if journal.exists() else None
    state=(json.loads((pathlib.Path(pointer["backup"])/"state.json").read_text()) if pointer else None)
    source=pathlib.Path(os.environ["SOURCE_PLUGIN"])
    files=lambda root: {str(path.relative_to(root)):path.read_bytes() for path in root.rglob("*") if path.is_file()}
    settings=json.loads((claude/"settings.json").read_text())
    marker=claude/"harness/continuation-supervisor-installed.json"
    plist=home/"Library/LaunchAgents/com.escapement.continuation-supervisor.plist"
    loaded=home/"launchctl.loaded"
    observed={
      "journal_exists":journal.is_file(),
      "commit_guard_exists":(claude/".plugin-update-transaction.json.commit-guard").exists(),
      "backup_state_exists":state is not None,
      "backup_registry_mentions_old":bool(state and any(item.get("kind")=="registry" and "/old" in pathlib.Path(item["backup"]).read_text() for item in state["files"])),
      "selected_cache":str(selected),
      "bin_wrapper":os.readlink(claude/"harness/bin"),
      "schemas_wrapper":os.readlink(claude/"harness/schemas"),
      "plugin_enabled":settings["enabledPlugins"]["escapement@escapement"],
      "supervisor_marker_live":marker.is_file(),
      "supervisor_plist_live":plist.is_file(),
      "supervisor_loaded":loaded.exists() and "com.escapement.continuation-supervisor" in loaded.read_text().splitlines(),
      "installed_surface_parity":files(source)==files(selected),
    }
    pathlib.Path(os.environ["CUTOVER_SNAPSHOT"]).write_text(json.dumps(observed))
    raise SystemExit(int(os.environ.get("CUTOVER_CANARY_EXIT","0")))
os.execv(os.environ["REAL_PYTHON"],[os.environ["REAL_PYTHON"],*args])
''',
    )
    real_diff = shutil.which("diff", path="/usr/bin:/bin")
    assert real_diff
    _write_executable(
        fake_bin / "diff",
        "#!/bin/bash\n"
        "printf '[\"installed-parity\"]\\n' >> \"$CUTOVER_TRACE\"\n"
        "exec \"$REAL_DIFF\" \"$@\"\n",
    )
    return (
        {
            "REAL_PYTHON": real_python,
            "REAL_DIFF": real_diff,
            "CUTOVER_TRACE": str(trace),
            "CUTOVER_SNAPSHOT": str(snapshot),
            "SOURCE_PLUGIN": str(PLUGIN),
        },
        trace,
        snapshot,
    )


def authority_snapshot(home: Path, new_cache: Path) -> dict:
    claude = home / ".claude"
    marker = claude / "harness" / "continuation-supervisor-installed.json"
    plist = home / "Library/LaunchAgents/com.escapement.continuation-supervisor.plist"
    loaded = home / "launchctl.loaded"
    return {
        "settings": (claude / "settings.json").read_bytes(),
        "registry": (claude / "plugins/installed_plugins.json").read_bytes(),
        "bin": os.readlink(claude / "harness/bin"),
        "schemas": os.readlink(claude / "harness/schemas"),
        "modes": {
            str(path.relative_to(new_cache)): path.stat().st_mode & 0o777
            for path in new_cache.rglob("*")
            if path.is_file()
        },
        "marker": marker.read_bytes() if marker.exists() else None,
        "plist": plist.read_bytes() if plist.exists() else None,
        "loaded": loaded.read_bytes() if loaded.exists() else None,
    }


def events(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def position(observed: list[list[str]], expected: list[str]) -> int:
    return observed.index(expected)


def test_updater_delegates_candidate_verification_to_focused_helper():
    updater = (PLUGIN.parents[1] / "scripts/plugin-update.sh").read_text()
    helper = PLUGIN.parents[1] / "scripts/plugin-update-canary-gate.sh"

    assert helper.is_file()
    assert helper.stat().st_mode & 0o111
    assert "plugin-update-canary-gate.sh" in updater
    assert "delegation-canary.py" not in updater
    assert "diff -qr" not in updater
    helper_text = helper.read_text()
    assert "delegation-canary.py" in helper_text
    assert "diff -qr" in helper_text


def test_single_transaction_commits_only_after_parity_and_live_canary(tmp_path):
    home, old_cache, new_cache, fake_bin, env = _cutover_fixture(tmp_path)
    audit, trace, snapshot_path = install_audit(tmp_path, fake_bin)

    result = _run({**env, **audit, "CUTOVER_CANARY_EXIT": "0"})

    assert result.returncode == 0, result.stdout + result.stderr
    observed = events(trace)
    assert observed.count(["transaction", "begin"]) == 1
    assert observed.count(["canary"]) == 1
    assert observed.count(["transaction", "commit"]) == 1
    assert ["transaction", "rollback"] not in observed
    assert (
        position(observed, ["transaction", "begin"])
        < position(observed, ["installed-parity"])
        < position(observed, ["canary"])
        < position(observed, ["transaction", "commit"])
    )
    assert json.loads(snapshot_path.read_text()) == {
        "journal_exists": True,
        "commit_guard_exists": False,
        "backup_state_exists": True,
        "backup_registry_mentions_old": True,
        "selected_cache": str(new_cache),
        "bin_wrapper": str(new_cache / "harness/bin"),
        "schemas_wrapper": str(new_cache / "harness/schemas"),
        "plugin_enabled": False,
        "supervisor_marker_live": True,
        "supervisor_plist_live": True,
        "supervisor_loaded": True,
        "installed_surface_parity": True,
    }
    assert not (home / ".claude/.plugin-update-transaction.json").exists()


def test_canary_failure_uses_original_rollback_and_is_byte_exact(tmp_path):
    home, old_cache, new_cache, fake_bin, env = _cutover_fixture(tmp_path)
    before = authority_snapshot(home, new_cache)
    audit, trace, snapshot_path = install_audit(tmp_path, fake_bin)

    failed = _run({**env, **audit, "CUTOVER_CANARY_EXIT": "73"})

    assert failed.returncode != 0
    observed = events(trace)
    assert observed.count(["transaction", "begin"]) == 1
    assert observed.count(["canary"]) == 1
    assert observed.count(["transaction", "rollback"]) == 1
    assert ["transaction", "commit"] not in observed
    assert (
        position(observed, ["transaction", "begin"])
        < position(observed, ["installed-parity"])
        < position(observed, ["canary"])
        < position(observed, ["transaction", "rollback"])
    )
    assert json.loads(snapshot_path.read_text())["journal_exists"] is True
    assert authority_snapshot(home, new_cache) == before
    assert not (home / ".claude/.plugin-update-transaction.json").exists()
    assert not list((home / ".claude").glob(".cutover-backup-*"))
    native = _native_agent_probe(fake_bin, env)
    dispatch = native[1]["message"]["content"][0]
    started = native[2]
    terminal = native[3]
    assert dispatch["name"] == "Agent"
    assert dispatch["input"]["run_in_background"] is True
    assert dispatch["id"] == started["tool_use_id"] == terminal["tool_use_id"]
    assert started["task_id"] == terminal["task_id"]
    assert terminal["status"] == "completed"
    assert native[4]["result"] == "NATIVE_AGENT_OK"

    retry = _run(
        {
            **env,
            **audit,
            "CUTOVER_CANARY_EXIT": "0",
            "CLAUDE_UPDATE_FAIL": "1",
        }
    )
    assert retry.returncode != 0
    assert authority_snapshot(home, new_cache) == before
