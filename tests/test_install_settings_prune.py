"""Installer oracle for migrating legacy lifecycle registrations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "INSTALL.sh"
PLUGIN_SOURCE = ROOT / "plugins" / "escapement-claude"


def _commands(settings: dict) -> list[str]:
    return [
        hook.get("command", "")
        for groups in settings.get("hooks", {}).values()
        for group in groups
        for hook in group.get("hooks", [])
    ]


def _complete_plugin_fixture(
    home: Path,
    version: str,
) -> tuple[Path, Path]:
    plugin_root = (
        home
        / ".claude"
        / "plugins"
        / "cache"
        / "escapement"
        / "escapement"
        / version
    )
    shutil.copytree(PLUGIN_SOURCE, plugin_root)
    stub_bin = home / "stub-bin"
    stub_bin.mkdir(parents=True)
    claude = stub_bin / "claude"
    claude.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    claude.chmod(0o755)
    return plugin_root, stub_bin


@pytest.mark.parametrize("version_seed", ("first-layout", "second-layout"))
def test_installer_finds_real_plugin_cache_shape_and_removes_legacy_prime(
    tmp_path: Path,
    version_seed: str,
) -> None:
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    version_dir = hashlib.sha256(
        f"{version_seed}:{tmp_path}".encode()
    ).hexdigest()[:12]
    plugin_root, stub_bin = _complete_plugin_fixture(home, version_dir)
    cached_hooks = plugin_root / "hooks"
    stale_hooks = (
        claude_dir
        / "plugins"
        / "cache"
        / "escapement"
        / "escapement"
        / "aaa-stale"
        / "hooks"
    )
    stale_hooks.mkdir(parents=True)
    (stale_hooks / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreCompact": [
                        {
                            "hooks": [
                                {
                                    "command": (
                                        'python3 -B "${CLAUDE_PLUGIN_ROOT}/hooks/'
                                        'unrelated_old_hook.py"'
                                    )
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    registry_path = claude_dir / "plugins" / "installed_plugins.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "escapement@escapement": [
                        {
                            "scope": "project",
                            "projectPath": str(tmp_path / "other-repo"),
                            "installPath": str(stale_hooks.parent),
                            "version": "stale-project",
                        },
                        {
                            "scope": "user",
                            "installPath": str(cached_hooks.parent),
                            "version": version_dir,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    settings_path = claude_dir / "settings.json"
    personal_command = "~/.claude/hooks/pre-compact-save.sh"
    personal_group = {
        "matcher": "manual",
        "custom": "preserve-me",
        "hooks": [
            {
                "type": "command",
                "command": personal_command,
                "timeout": 17,
                "statusMessage": "Saving personal context",
            }
        ],
    }
    settings_path.write_text(
        json.dumps(
            {
                "model": "opus",
                "permissions": {"allow": ["Read"]},
                "hooks": {
                    "PreCompact": [
                        personal_group,
                        {
                            "matcher": "",
                            "hooks": [{"type": "command", "command": "bd prime"}],
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(INSTALLER), "--dev"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "plugin not installed" not in result.stdout
    updated = json.loads(settings_path.read_text(encoding="utf-8"))
    assert updated["model"] == "opus"
    assert updated["permissions"] == {"allow": ["Read"]}
    assert _commands(updated) == [personal_command]
    assert updated["hooks"]["PreCompact"] == [personal_group]


def test_installer_without_plugin_inventory_preserves_settings(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    settings_path = claude_dir / "settings.json"
    original = {
        "model": "opus",
        "hooks": {
            "PreCompact": [
                {"hooks": [{"type": "command", "command": "bd prime"}]}
            ]
        },
    }
    original_bytes = json.dumps(original, separators=(",", ":")).encode()
    settings_path.write_bytes(original_bytes)

    env = os.environ.copy()
    env["HOME"] = str(home)
    result = subprocess.run(
        ["bash", str(INSTALLER), "--dev"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "no valid user-scope" in result.stderr
    assert settings_path.read_bytes() == original_bytes
    assert list(claude_dir.glob("settings.json.backup-*")) == []
    assert list(claude_dir.glob(".cutover-backup-*")) == []


@pytest.mark.parametrize("registry_case", ("unrelated-only", "project-only"))
def test_registry_without_user_plugin_preserves_settings(
    tmp_path: Path,
    registry_case: str,
) -> None:
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    plugins_dir = claude_dir / "plugins"
    plugins_dir.mkdir(parents=True)
    settings_path = claude_dir / "settings.json"
    original_bytes = (
        b'{"model":"opus","hooks":{"PreCompact":[{"hooks":'
        b'[{"command":"bd prime"}]}]}}'
    )
    settings_path.write_bytes(original_bytes)
    plugins: dict[str, object]
    if registry_case == "unrelated-only":
        plugins = {
            "other@marketplace": [
                {"scope": "user", "installPath": str(tmp_path / "other")}
            ]
        }
    else:
        plugins = {
            "escapement@escapement": [
                {
                    "scope": "project",
                    "projectPath": str(tmp_path / "repo"),
                    "installPath": str(tmp_path / "project-plugin"),
                }
            ]
        }
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": plugins}),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    result = subprocess.run(
        ["bash", str(INSTALLER), "--dev"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "no valid user-scope" in result.stderr
    assert settings_path.read_bytes() == original_bytes
    assert list(claude_dir.glob("settings.json.backup-*")) == []
    assert list(claude_dir.glob(".cutover-backup-*")) == []


@pytest.mark.parametrize(
    "registry_case",
    (
        "malformed-json",
        "wrong-entry-type",
        "missing-install-path",
        "missing-hooks-file",
    ),
)
def test_invalid_registered_plugin_fails_without_touching_settings(
    tmp_path: Path,
    registry_case: str,
) -> None:
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    plugins_dir = claude_dir / "plugins"
    plugins_dir.mkdir(parents=True)
    settings_path = claude_dir / "settings.json"
    original_bytes = (
        b'{"model":"opus","hooks":{"PreCompact":[{"hooks":'
        b'[{"command":"bd prime"}]}]}}'
    )
    settings_path.write_bytes(original_bytes)
    registry_path = plugins_dir / "installed_plugins.json"

    if registry_case == "malformed-json":
        registry_path.write_text("{not-json", encoding="utf-8")
    else:
        entry: object
        if registry_case == "wrong-entry-type":
            entry = {"scope": "user", "installPath": 42}
        elif registry_case == "missing-install-path":
            entry = {"scope": "user"}
        else:
            entry = {
                "scope": "user",
                "installPath": str(tmp_path / "missing-plugin"),
            }
        registry_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "plugins": {"escapement@escapement": [entry]},
                }
            ),
            encoding="utf-8",
        )

    env = os.environ.copy()
    env["HOME"] = str(home)
    result = subprocess.run(
        ["bash", str(INSTALLER), "--dev"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "FATAL:" in result.stderr
    assert settings_path.read_bytes() == original_bytes
    assert list(claude_dir.glob("settings.json.backup-*")) == []
    assert list(claude_dir.glob(".cutover-backup-*")) == []
