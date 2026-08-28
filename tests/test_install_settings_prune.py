"""Installer oracle for migrating legacy lifecycle registrations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "INSTALL.sh"
PLUGIN_SOURCE = ROOT / "plugins" / "escapement-claude"

SHELL_LAUNCHCTL = r"""() {
  state="$HOME/launchctl.loaded"
  label="com.escapement.continuation-supervisor"
  touch "$state"
  printf '%s\n' "$*" >> "$HOME/launchctl.log"
  if [[ "${1:-}" == print ]]; then
    grep -Fxq "$label" "$state" && return 0
    return 113
  fi
  if [[ "${1:-}" == bootout ]]; then
    grep -Fxq "$label" "$state" || return 3
    grep -Fvx "$label" "$state" > "$state.next" || true
    mv -f "$state.next" "$state"
    return 0
  fi
  if [[ "${1:-}" == bootstrap ]]; then
    grep -Fxq "$label" "$state" && return 72
    printf '%s\n' "$label" >> "$state"
  fi
  return 0
}"""


def _commands(settings: dict) -> list[str]:
    return [
        hook.get("command", "")
        for groups in settings.get("hooks", {}).values()
        for group in groups
        for hook in group.get("hooks", [])
    ]


def _stub_claude_cli(home: Path) -> Path:
    stub_bin = home / "stub-bin"
    stub_bin.mkdir(parents=True)
    claude = stub_bin / "claude"
    claude.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    claude.chmod(0o755)
    python = stub_bin / "python3"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "if any(value.endswith('delegation-canary.py') for value in sys.argv[1:]):\n"
        " source=pathlib.Path(sys.argv[sys.argv.index('--source-root')+1])/'plugins'/'escapement-claude'\n"
        " candidate=pathlib.Path(sys.argv[sys.argv.index('--candidate-root')+1])\n"
        " files=lambda root:{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}\n"
        " if files(source)!=files(candidate): raise SystemExit(9)\n"
        " audit=pathlib.Path(os.environ['HOME'])/'canary-audit.json'\n"
        " audit.write_text(json.dumps({'source':str(source),'candidate':str(candidate)}))\n"
        " print(json.dumps({'status':'pass','managed':{'distinct_native_children':3,'overlap_proven':True,'peer_dependency_proven':True,'terminal_count':3,'abort_count':1,'completion_decision':['allow','delegated_outcome_complete']},'unmanaged':{'first_attempt':True,'escapement_state_created':False}}))\n"
        " raise SystemExit(0)\n"
        "os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    uname = stub_bin / "uname"
    uname.write_text(
        "#!/usr/bin/env bash\nprintf 'Darwin\\n'\n",
        encoding="utf-8",
    )
    uname.chmod(0o755)
    launchctl = stub_bin / "launchctl"
    launchctl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$HOME/launchctl.log\"\n"
        'state="$HOME/launchctl.loaded"\n'
        'label="com.escapement.continuation-supervisor"\n'
        'touch "$state"\n'
        'if [[ "${1:-}" == print ]]; then grep -Fxq "$label" "$state" && exit 0; exit 113; fi\n'
        'if [[ "${1:-}" == bootout ]]; then grep -Fxq "$label" "$state" || exit 3; grep -Fvx "$label" "$state" > "$state.next" || true; mv -f "$state.next" "$state"; exit 0; fi\n'
        'if [[ "${1:-}" == bootstrap ]]; then grep -Fxq "$label" "$state" && exit 72; printf "%s\\n" "$label" >> "$state"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)
    return stub_bin


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
    return plugin_root, _stub_claude_cli(home)


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
    env["BASH_FUNC_launchctl%%"] = SHELL_LAUNCHCTL
    result = subprocess.run(
        ["bash", str(INSTALLER), "--dev"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    audit = json.loads((home / "canary-audit.json").read_text(encoding="utf-8"))
    assert Path(audit["source"]) == PLUGIN_SOURCE
    assert Path(audit["candidate"]) == plugin_root
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

    stub_bin = _stub_claude_cli(home)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    env["BASH_FUNC_launchctl%%"] = SHELL_LAUNCHCTL
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

    stub_bin = _stub_claude_cli(home)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    env["BASH_FUNC_launchctl%%"] = SHELL_LAUNCHCTL
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

    stub_bin = _stub_claude_cli(home)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    env["BASH_FUNC_launchctl%%"] = SHELL_LAUNCHCTL
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
