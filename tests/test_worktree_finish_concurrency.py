"""Behavioral oracle for concurrent lifecycle finish callers."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import escapement_worktree_finish as finish_module  # noqa: E402
from escapement_worktree_git import WorktreeError  # noqa: E402


def _write_receipt(harness: Path, lifecycle_id: str = "race-7") -> Path:
    registry = harness / "worktrees"
    registry.mkdir(parents=True, mode=0o700)
    receipt = registry / f"{lifecycle_id}.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "lifecycle_id": lifecycle_id,
                "repository": "/fixture/repository",
                "common_directory": "/fixture/repository/.git",
                "origin": "https://github.com/acme/widget.git",
                "worktree": f"/fixture/repository/.worktrees/{lifecycle_id}",
                "branch_ref": f"refs/heads/feature/{lifecycle_id}",
                "source_sha": "1" * 40,
                "phase": "created",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    return receipt


def test_finish_accepts_completion_by_lock_holder_while_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = tmp_path / "harness"
    receipt = _write_receipt(harness)
    monkeypatch.setenv("CONTINUATION_HARNESS_HOME", str(harness))

    @contextmanager
    def completing_lock(lifecycle_id: str) -> Iterator[None]:
        assert lifecycle_id == "race-7"
        receipt.unlink()
        yield

    monkeypatch.setattr(finish_module, "lifecycle_lock", completing_lock)

    result = finish_module.finish_lifecycle("race-7")

    assert result == {
        "lifecycle_id": "race-7",
        "reason": "removed",
        "status": "completed",
    }
    assert not receipt.exists()


def test_finish_rejects_lifecycle_missing_before_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = tmp_path / "harness"
    (harness / "worktrees").mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("CONTINUATION_HARNESS_HOME", str(harness))

    with pytest.raises(WorktreeError, match="unavailable"):
        finish_module.finish_lifecycle("race-7")


def test_finish_rejects_untrusted_receipt_before_entering_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = tmp_path / "harness"
    receipt = _write_receipt(harness)
    receipt.chmod(0o644)
    monkeypatch.setenv("CONTINUATION_HARNESS_HOME", str(harness))
    lock_entered = False

    @contextmanager
    def destructive_lock(_lifecycle_id: str) -> Iterator[None]:
        nonlocal lock_entered
        lock_entered = True
        receipt.unlink()
        yield

    monkeypatch.setattr(finish_module, "lifecycle_lock", destructive_lock)

    with pytest.raises(WorktreeError, match="ownership or permissions are untrusted"):
        finish_module.finish_lifecycle("race-7")

    assert not lock_entered
    assert receipt.exists()


def test_finish_rejects_receipt_made_untrusted_while_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = tmp_path / "harness"
    receipt = _write_receipt(harness)
    monkeypatch.setenv("CONTINUATION_HARNESS_HOME", str(harness))

    @contextmanager
    def replacing_lock(lifecycle_id: str) -> Iterator[None]:
        assert lifecycle_id == "race-7"
        receipt.chmod(0o644)
        yield

    monkeypatch.setattr(finish_module, "lifecycle_lock", replacing_lock)

    with pytest.raises(WorktreeError, match="ownership or permissions are untrusted"):
        finish_module.finish_lifecycle("race-7")

    assert receipt.exists()


def test_finish_rejects_broken_receipt_symlink_installed_while_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = tmp_path / "harness"
    receipt = _write_receipt(harness)
    monkeypatch.setenv("CONTINUATION_HARNESS_HOME", str(harness))

    @contextmanager
    def replacing_lock(lifecycle_id: str) -> Iterator[None]:
        assert lifecycle_id == "race-7"
        receipt.unlink()
        receipt.symlink_to(tmp_path / "missing-receipt-target")
        yield

    monkeypatch.setattr(finish_module, "lifecycle_lock", replacing_lock)

    with pytest.raises(WorktreeError, match="lifecycle entry is unavailable or untrusted"):
        finish_module.finish_lifecycle("race-7")

    assert receipt.is_symlink()


def test_finish_rejects_trusted_replacement_receipt_installed_while_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = tmp_path / "harness"
    receipt = _write_receipt(harness)
    monkeypatch.setenv("CONTINUATION_HARNESS_HOME", str(harness))
    replacement_raw = b""

    @contextmanager
    def replacing_lock(lifecycle_id: str) -> Iterator[None]:
        nonlocal replacement_raw
        assert lifecycle_id == "race-7"
        replacement = json.loads(receipt.read_text(encoding="utf-8"))
        replacement["source_sha"] = "2" * 40
        receipt.write_text(json.dumps(replacement) + "\n", encoding="utf-8")
        replacement_raw = receipt.read_bytes()
        yield

    monkeypatch.setattr(finish_module, "lifecycle_lock", replacing_lock)

    with pytest.raises(WorktreeError, match="changed during inspection"):
        finish_module.finish_lifecycle("race-7")

    assert receipt.read_bytes() == replacement_raw
