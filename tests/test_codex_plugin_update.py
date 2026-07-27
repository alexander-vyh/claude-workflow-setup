"""Integration checks for the authoritative Codex updater."""

from __future__ import annotations

import os
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
    fake_codex = fake_bin / "codex"
    command_log = tmp_path / "codex.log"
    plugin_root = tmp_path / "plugin"
    global_skill = tmp_path / ".agents" / "skills" / "beads-execution" / "SKILL.md"
    sibling = tmp_path / ".agents" / "skills" / "user-skill" / "notes.txt"
    shutil.copytree(ROOT / "plugins" / "escapement", plugin_root)
    global_skill.parent.mkdir(parents=True)
    sibling.parent.mkdir(parents=True)
    safe = (
        ROOT / "plugins" / "escapement" / "skills" / "beads-execution" / "SKILL.md"
    ).read_bytes()
    legacy = historical_legacy_skill_bytes()
    global_skill.write_bytes(legacy)
    sibling.write_bytes(b"user-owned sibling\n")
    fake_codex.write_text(
        """#!/bin/bash
set -eu
echo "$*" >> "$FAKE_CODEX_LOG"
if [[ "$*" == "plugin marketplace list --json" ]]; then
  printf '{"marketplaces":[{"name":"escapement","marketplaceSource":{"sourceType":"local"}}]}'
elif [[ "$*" == "plugin list --marketplace escapement --json" ]]; then
  printf '{"installed":[{"pluginId":"escapement@escapement","source":{"source":"local","path":"%s"}}]}' "$FAKE_PLUGIN_ROOT"
fi
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = os.environ | {
        "CODEX_BIN": str(fake_codex),
        "FAKE_CODEX_LOG": str(command_log),
        "FAKE_PLUGIN_ROOT": str(plugin_root),
        "ESCAPEMENT_GLOBAL_BEADS_SKILL": str(global_skill),
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
    assert global_skill.read_bytes() == safe
    backups = list(global_skill.parent.glob("SKILL.md.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == legacy
    assert sibling.read_bytes() == b"user-owned sibling\n"
    commands = command_log.read_text(encoding="utf-8")
    assert "plugin remove escapement@escapement" in commands
    assert "plugin add escapement@escapement" in commands
    assert "plugin marketplace upgrade" not in commands
    assert "replaced recognized legacy skill" in result.stdout
    assert "Codex plugin refreshed" in result.stdout


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
    ),
)
def test_updater_rejects_stale_installed_surface(
    tmp_path: Path,
    relative_path: str,
    stale_content: bytes,
) -> None:
    fake_codex = tmp_path / "codex"
    plugin_root = tmp_path / "plugin"
    global_skill = tmp_path / "global" / "SKILL.md"
    shutil.copytree(ROOT / "plugins" / "escapement", plugin_root)
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
  printf '{"installed":[{"pluginId":"escapement@escapement","source":{"source":"local","path":"%s"}}]}' "$FAKE_PLUGIN_ROOT"
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
            "FAKE_PLUGIN_ROOT": str(plugin_root),
            "ESCAPEMENT_GLOBAL_BEADS_SKILL": str(global_skill),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "installed plugin surface is stale" in result.stderr
    assert global_skill.read_bytes() == legacy
    assert list(global_skill.parent.glob("SKILL.md.backup-*")) == []
