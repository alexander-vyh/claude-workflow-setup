"""Integration checks for the authoritative Codex updater."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from legacy_codex_skill_fixture import historical_legacy_skill_bytes


ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts" / "codex-plugin-update.sh"


def test_updater_refreshes_plugin_migrates_legacy_and_preserves_siblings(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    fake_codex = fake_bin / "codex"
    command_log = tmp_path / "codex.log"
    plugin_source = tmp_path / "marketplace-source"
    plugin_root = codex_home / "plugins" / "cache" / "escapement" / "escapement" / "1.0.0"
    harness = tmp_path / "shared-harness"
    global_skill = tmp_path / ".agents" / "skills" / "beads-execution" / "SKILL.md"
    global_hooks = codex_home / "hooks.json"
    sibling = tmp_path / ".agents" / "skills" / "user-skill" / "notes.txt"
    shutil.copytree(ROOT / "plugins" / "escapement", plugin_root)
    shutil.copytree(ROOT / "plugins" / "escapement", plugin_source)
    prior_runtime = tmp_path / "prior-runtime"
    shutil.copytree(ROOT / "plugins" / "escapement" / "harness", prior_runtime)
    (prior_runtime / "bin" / "verify").write_text("#!/bin/sh\nexit 0\n")
    (prior_runtime / "bin" / "init_contract.py").write_text("# claude-only runtime\n")
    runtime_hooks = prior_runtime.parent / "hooks"
    runtime_hooks.mkdir()
    shutil.copy2(
        ROOT / "claude" / "hooks" / "_local_judge_client.py",
        runtime_hooks / "_local_judge_client.py",
    )
    for executable in prior_runtime.joinpath("bin").glob("*.py"):
        executable.chmod(executable.stat().st_mode | 0o111)
    harness.mkdir()
    harness.joinpath("bin").symlink_to(prior_runtime / "bin", target_is_directory=True)
    harness.joinpath("schemas").symlink_to(
        prior_runtime / "schemas", target_is_directory=True
    )
    global_skill.parent.mkdir(parents=True)
    global_hooks.parent.mkdir(parents=True, exist_ok=True)
    sibling.parent.mkdir(parents=True)
    safe = (
        ROOT / "plugins" / "escapement" / "skills" / "beads-execution" / "SKILL.md"
    ).read_bytes()
    legacy = historical_legacy_skill_bytes()
    global_skill.write_bytes(legacy)
    sibling.write_bytes(b"user-owned sibling\n")
    for directory, name in (
        (codex_home / "hooks", "test_oracle_brief_gate.py"),
        (codex_home / "hooks", "implementation_echo_test_gate.py"),
        (codex_home / "hooks", "oracle_downgrade_warning_gate.py"),
        (home / ".claude" / "hooks", "beads_worktree_guard.py"),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "claude" / "hooks" / name, directory / name)
    legacy_hooks = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "command": f"python3 {codex_home}/hooks/test_oracle_brief_gate.py",
                            "statusMessage": "Checking Test Oracle Brief gate",
                            "timeout": 30,
                            "type": "command",
                        },
                        {
                            "command": f"python3 {codex_home}/hooks/implementation_echo_test_gate.py",
                            "statusMessage": "Checking implementation-echo tests",
                            "timeout": 30,
                            "type": "command",
                        },
                        {
                            "command": f"python3 {codex_home}/hooks/oracle_downgrade_warning_gate.py",
                            "statusMessage": "Checking oracle downgrade warnings",
                            "timeout": 30,
                            "type": "command",
                        },
                        {
                            "command": (
                                "python3 /repo/.git/codex-hooks/sifi_pr_policy.py"
                            ),
                            "statusMessage": "preserve Sifi",
                        },
                        {
                            "command": f"python3 {codex_home}/hooks/pr_create_guard.py",
                            "statusMessage": "preserve PR guard",
                        },
                        {
                            "command": f"python3 {home}/.claude/hooks/beads_worktree_guard.py",
                            "statusMessage": "Checking bd worktree location (.worktrees/)",
                            "timeout": 10,
                            "type": "command",
                        },
                    ],
                }
            ]
        }
    }
    original_hooks = json.dumps(legacy_hooks, indent=2) + "\n"
    global_hooks.write_text(original_hooks, encoding="utf-8")
    fake_codex.write_text(
        """#!/bin/bash
set -eu
echo "$*" >> "$FAKE_CODEX_LOG"
if [[ "$*" == "plugin marketplace list --json" ]]; then
  printf '{"marketplaces":[{"name":"escapement","marketplaceSource":{"sourceType":"local"}}]}'
elif [[ "$*" == "plugin list --marketplace escapement --json" ]]; then
  printf '{"installed":[{"pluginId":"escapement@escapement","name":"escapement","marketplaceName":"escapement","version":"1.0.0","source":{"source":"local","path":"%s"}}]}' "$FAKE_PLUGIN_ROOT"
fi
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    (fake_bin / "uname").write_text("#!/bin/sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
    (fake_bin / "launchctl").write_text(
        "#!/bin/sh\n"
        "[ \"${1:-}\" != print ] || exit 113\n"
        "[ \"${1:-}\" != bootout ] || exit 3\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "uname").chmod(0o755)
    (fake_bin / "launchctl").chmod(0o755)
    env = os.environ | {
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CODEX_BIN": str(fake_codex),
        "FAKE_CODEX_LOG": str(command_log),
        "FAKE_PLUGIN_ROOT": str(plugin_source),
        "ESCAPEMENT_GLOBAL_BEADS_SKILL": str(global_skill),
        "CONTINUATION_HARNESS_HOME": str(harness),
    }

    result = subprocess.run(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    second_result = subprocess.run(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert second_result.returncode == 0, second_result.stderr
    assert global_skill.read_bytes() == safe
    backups = list(global_skill.parent.glob("SKILL.md.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == legacy
    assert sibling.read_bytes() == b"user-owned sibling\n"
    [surviving_group] = json.loads(global_hooks.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert [hook["statusMessage"] for hook in surviving_group["hooks"]] == [
        "preserve Sifi",
        "preserve PR guard",
    ]
    hook_backups = list(codex_home.glob("hooks.json.backup-*"))
    assert len(hook_backups) == 1
    assert hook_backups[0].read_text(encoding="utf-8") == original_hooks
    commands = command_log.read_text(encoding="utf-8")
    assert "plugin remove escapement@escapement" in commands
    assert "plugin add escapement@escapement" in commands
    assert "plugin marketplace upgrade" not in commands
    assert "replaced recognized legacy skill" in result.stdout
    assert "Codex plugin refreshed" in result.stdout
    assert harness.joinpath("bin").resolve() == prior_runtime / "bin"
    assert harness.joinpath("schemas").resolve() == prior_runtime / "schemas"
    assert harness.joinpath("bin", "verify").is_file()
    assert harness.joinpath("bin", "init_contract.py").is_file()
    codex_runtime = codex_home / "escapement-harness"
    assert codex_runtime.joinpath("bin").resolve() == plugin_root / "harness" / "bin"
    assert codex_runtime.joinpath("schemas").resolve() == plugin_root / "harness" / "schemas"
    with (home / "Library" / "LaunchAgents" / "com.escapement.continuation-supervisor.plist").open("rb") as stream:
        plist = plistlib.load(stream)
    assert plist["ProgramArguments"] == [str(harness / "bin" / "wakeup_waker.py"), "--fire"]

    # An executable, runnable pre-watchdog Claude waker is not preserved as the
    # supervisor target; the fresh Codex runtime supplies the required capability.
    (prior_runtime / "bin" / "wakeup_waker.py").write_text(
        "#!/bin/sh\n[ \"${1:-}\" = --help ] && exit 0\nexit 2\n"
    )
    (prior_runtime / "bin" / "wakeup_waker.py").chmod(0o755)
    repaired = subprocess.run(
        ["bash", str(UPDATER)], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False,
    )
    assert repaired.returncode == 0, repaired.stderr
    with (home / "Library" / "LaunchAgents" / "com.escapement.continuation-supervisor.plist").open("rb") as stream:
        repaired_plist = plistlib.load(stream)
    assert repaired_plist["ProgramArguments"] == [
        str(codex_runtime / "bin" / "wakeup_waker.py"), "--fire",
    ]
    assert harness.joinpath("bin").resolve() == prior_runtime / "bin"


@pytest.mark.parametrize(
    ("relative_path", "stale_content"),
    (
        ("skills/beads-execution/SKILL.md", b"stale broad skill\n"),
        (
            "hooks/hooks.json",
            b'{"hooks":{"SessionStart":[{"matcher":"","hooks":[]}]}}\n',
        ),
        (
            ".codex-plugin/plugin.json",
            b'{"name":"escapement","version":"stale-test-fixture"}\n',
        ),
        ("bin/escapement_worktree_registry.py", b"stale registry runtime\n"),
        ("bin/escapement_worktree_root.py", b"stale root runtime\n"),
    ),
)
def test_updater_rejects_stale_installed_surface(
    tmp_path: Path,
    relative_path: str,
    stale_content: bytes,
) -> None:
    fake_codex = tmp_path / "codex"
    codex_home = tmp_path / "codex-home"
    plugin_source = tmp_path / "marketplace-source"
    plugin_root = codex_home / "plugins" / "cache" / "escapement" / "escapement" / "1.0.0"
    global_skill = tmp_path / "global" / "SKILL.md"
    shutil.copytree(ROOT / "plugins" / "escapement", plugin_root)
    shutil.copytree(ROOT / "plugins" / "escapement", plugin_source)
    (plugin_root / relative_path).write_bytes(stale_content)
    global_skill.parent.mkdir()
    legacy = historical_legacy_skill_bytes()
    global_skill.write_bytes(legacy)
    fake_codex.write_text(
        """#!/bin/bash
set -eu
if [[ "$*" == "plugin marketplace list --json" ]]; then
  printf '{"marketplaces":[{"name":"escapement","marketplaceSource":{"sourceType":"local"}}]}'
elif [[ "$*" == "plugin list --marketplace escapement --json" ]]; then
  printf '{"installed":[{"pluginId":"escapement@escapement","name":"escapement","marketplaceName":"escapement","version":"1.0.0","source":{"source":"local","path":"%s"}}]}' "$FAKE_PLUGIN_ROOT"
fi
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    result = subprocess.run(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env=os.environ
        | {
            "CODEX_BIN": str(fake_codex),
            "CODEX_HOME": str(codex_home),
            "FAKE_PLUGIN_ROOT": str(plugin_source),
            "ESCAPEMENT_GLOBAL_BEADS_SKILL": str(global_skill),
            "CONTINUATION_HARNESS_HOME": str(tmp_path / "harness"),
            "ESCAPEMENT_SKIP_SUPERVISOR_INSTALL": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "installed plugin surface is stale" in result.stderr
    assert global_skill.read_bytes() == legacy
    assert list(global_skill.parent.glob("SKILL.md.backup-*")) == []
    assert not (tmp_path / "harness").exists()
