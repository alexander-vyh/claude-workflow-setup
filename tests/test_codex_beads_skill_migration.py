"""Behavioral tests for conservative legacy Codex skill migration."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATOR = ROOT / "scripts" / "migrate_codex_beads_skill.py"


def _installed_legacy_candidate() -> Path | None:
    skill_dir = Path.home() / ".agents" / "skills" / "beads-execution"
    for candidate in sorted(skill_dir.glob("SKILL.md*")):
        try:
            content = candidate.read_bytes()
        except OSError:
            continue
        if b"description: Use when the user mentions beads, bead, bd" in content:
            return candidate
    return None


INSTALLED_LEGACY = _installed_legacy_candidate()


def _run(source: Path, target: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MIGRATOR), str(source), str(target), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def test_recognized_legacy_skill_is_replaced_and_backed_up(tmp_path: Path) -> None:
    source = tmp_path / "safe.md"
    target = tmp_path / "SKILL.md"
    source.write_text("safe explicit-execution skill\n", encoding="utf-8")
    legacy = b"recognized historical Escapement skill\n"
    target.write_bytes(legacy)
    legacy_hash = hashlib.sha256(legacy).hexdigest()

    result = _run(source, target, "--legacy-sha256", legacy_hash)

    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == source.read_bytes()
    backups = list(tmp_path.glob("SKILL.md.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == legacy
    assert "replaced recognized legacy skill" in result.stdout


@pytest.mark.skipif(
    INSTALLED_LEGACY is None,
    reason="the historical user-global skill is not installed on this machine",
)
def test_actual_historical_legacy_skill_is_recognized_without_hash_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "safe.md"
    target = tmp_path / "SKILL.md"
    source.write_text("safe explicit-execution skill\n", encoding="utf-8")
    assert INSTALLED_LEGACY is not None
    legacy = INSTALLED_LEGACY.read_bytes()
    target.write_bytes(legacy)

    result = _run(source, target)

    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == source.read_bytes()
    backups = list(tmp_path.glob("SKILL.md.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == legacy


@pytest.mark.skipif(
    INSTALLED_LEGACY is None,
    reason="the historical user-global skill is not installed on this machine",
)
def test_customized_historical_skill_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "safe.md"
    target = tmp_path / "SKILL.md"
    source.write_text("safe explicit-execution skill\n", encoding="utf-8")
    assert INSTALLED_LEGACY is not None
    customized = INSTALLED_LEGACY.read_bytes() + b"\nUser directive: preserve this policy.\n"
    target.write_bytes(customized)

    result = _run(source, target)

    assert result.returncode == 2
    assert target.read_bytes() == customized
    assert list(tmp_path.glob("SKILL.md.backup-*")) == []
    assert "unrecognized" in result.stderr.lower()


def test_unknown_user_skill_is_preserved_byte_for_byte(tmp_path: Path) -> None:
    source = tmp_path / "safe.md"
    target = tmp_path / "SKILL.md"
    source.write_text("safe explicit-execution skill\n", encoding="utf-8")
    user_content = b"user-authored skill with custom instructions\n"
    target.write_bytes(user_content)

    result = _run(source, target, "--legacy-sha256", "0" * 64)

    assert result.returncode == 2
    assert target.read_bytes() == user_content
    assert list(tmp_path.glob("SKILL.md.backup-*")) == []
    assert "unrecognized" in result.stderr.lower()


def test_migration_preserves_unrelated_skill_tree_metadata(tmp_path: Path) -> None:
    skills = tmp_path / ".agents" / "skills"
    target = skills / "beads-execution" / "SKILL.md"
    source = tmp_path / "safe.md"
    sibling_file = skills / "user-skill" / "nested" / "notes.txt"
    sibling_link = skills / "user-skill" / "current"
    target.parent.mkdir(parents=True)
    sibling_file.parent.mkdir(parents=True)
    source.write_text("safe explicit-execution skill\n", encoding="utf-8")
    legacy = b"recognized historical Escapement skill\n"
    target.write_bytes(legacy)
    sibling_file.write_bytes(b"\x00user-owned bytes\n")
    sibling_file.chmod(0o640)
    sibling_link.symlink_to("nested/notes.txt")
    before = {
        "bytes": sibling_file.read_bytes(),
        "mode": sibling_file.stat().st_mode & 0o777,
        "link": os.readlink(sibling_link),
        "paths": sorted(path.relative_to(skills).as_posix() for path in skills.rglob("*")),
    }

    result = _run(
        source,
        target,
        "--legacy-sha256",
        hashlib.sha256(legacy).hexdigest(),
    )

    assert result.returncode == 0, result.stderr
    assert sibling_file.read_bytes() == before["bytes"]
    assert sibling_file.stat().st_mode & 0o777 == before["mode"]
    assert sibling_link.is_symlink()
    assert os.readlink(sibling_link) == before["link"]
    after_paths = sorted(
        path.relative_to(skills).as_posix()
        for path in skills.rglob("*")
        if ".backup-" not in path.name
    )
    assert after_paths == before["paths"]


def test_current_safe_skill_is_an_idempotent_noop(tmp_path: Path) -> None:
    source = tmp_path / "safe.md"
    target = tmp_path / "SKILL.md"
    content = b"safe explicit-execution skill\n"
    source.write_bytes(content)
    target.write_bytes(content)

    result = _run(source, target)

    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == content
    assert list(tmp_path.glob("SKILL.md.backup-*")) == []
    assert "already current" in result.stdout


def test_missing_global_skill_is_not_created(tmp_path: Path) -> None:
    source = tmp_path / "safe.md"
    target = tmp_path / "missing" / "SKILL.md"
    source.write_text("safe explicit-execution skill\n", encoding="utf-8")

    result = _run(source, target)

    assert result.returncode == 0, result.stderr
    assert not target.exists()
    assert "no legacy global skill" in result.stdout
