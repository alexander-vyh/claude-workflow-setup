"""Integration checks for the authoritative Codex updater."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


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
    (plugin_root / "skills" / "beads-execution").mkdir(parents=True)
    (plugin_root / "hooks").mkdir()
    global_skill.parent.mkdir(parents=True)
    sibling.parent.mkdir(parents=True)
    safe = b"safe explicit-execution skill\n"
    (plugin_root / "skills" / "beads-execution" / "SKILL.md").write_bytes(safe)
    (plugin_root / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"SessionStart": [{"matcher": "", "hooks": []}]}}),
        encoding="utf-8",
    )
    global_skill.write_bytes(safe)
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
    assert list(global_skill.parent.glob("SKILL.md.backup-*")) == []
    assert sibling.read_bytes() == b"user-owned sibling\n"
    commands = command_log.read_text(encoding="utf-8")
    assert "plugin remove escapement@escapement" in commands
    assert "plugin add escapement@escapement" in commands
    assert "plugin marketplace upgrade" not in commands
    assert "already current" in result.stdout
    assert "Codex plugin refreshed" in result.stdout
