"""Behavioral oracle for Escapement-managed primary-checkout synchronization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_fixtures import (
    git,
    make_remote_scenario,
    rev,
    run_cli,
    snapshot_primary,
)


def _default_primary(scenario) -> None:
    branch = scenario.remote_default_ref.removeprefix("refs/heads/")
    git(scenario.primary, "switch", branch)


def _result_json(result) -> dict[str, object]:
    assert result.stdout.strip(), result.stderr
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def test_sync_root_fast_forwards_branch_index_and_files_to_exact_remote_head(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    _default_primary(scenario)
    before = rev(scenario.primary)
    assert before == scenario.stale_primary_sha
    assert (scenario.primary / "oracle.txt").read_text() == "stale-primary\n"

    result = run_cli(
        scenario.primary, "sync-root", "--repo", str(scenario.primary)
    )

    assert result.returncode == 0, result.stderr
    assert _result_json(result) == {
        "branch": "trunk",
        "previous_sha": before,
        "reason": "fast-forwarded",
        "repo": str(scenario.primary),
        "status": "synchronized",
        "target_sha": scenario.remote_head_sha,
    }
    assert rev(scenario.primary) == scenario.remote_head_sha
    assert git(scenario.primary, "symbolic-ref", "--short", "HEAD").stdout.strip() == "trunk"
    assert git(scenario.primary, "status", "--porcelain").stdout == ""
    assert (scenario.primary / "oracle.txt").read_text() == "remote-default\n"


def test_sync_root_reports_current_exact_remote_head_without_mutation(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    _default_primary(scenario)
    first = run_cli(scenario.primary, "sync-root", "--repo", str(scenario.primary))
    assert first.returncode == 0, first.stderr
    before = snapshot_primary(scenario.primary)

    result = run_cli(scenario.primary, "sync-root", "--repo", str(scenario.primary))

    assert result.returncode == 0, result.stderr
    output = _result_json(result)
    assert output["status"] == "up_to_date"
    assert output["previous_sha"] == scenario.remote_head_sha
    assert output["target_sha"] == scenario.remote_head_sha
    assert snapshot_primary(scenario.primary) == before


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_sync_root_preserves_dirty_primary_exactly(
    tmp_path: Path, dirty_kind: str
) -> None:
    scenario = make_remote_scenario(tmp_path)
    _default_primary(scenario)
    if dirty_kind == "tracked":
        (scenario.primary / "oracle.txt").write_text("user edit\n")
    else:
        (scenario.primary / "untracked.txt").write_text("user artifact\n")
    before = snapshot_primary(scenario.primary)

    result = run_cli(scenario.primary, "sync-root", "--repo", str(scenario.primary))

    assert result.returncode != 0
    output = _result_json(result)
    assert output["status"] == "ineligible"
    assert output["reason"] == "primary-dirty"
    assert snapshot_primary(scenario.primary) == before


def test_sync_root_preserves_divergent_primary_commit_and_files(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    _default_primary(scenario)
    (scenario.primary / "local.txt").write_text("local history\n")
    git(scenario.primary, "add", "local.txt")
    git(scenario.primary, "commit", "-m", "diverge primary")
    before = snapshot_primary(scenario.primary)

    result = run_cli(scenario.primary, "sync-root", "--repo", str(scenario.primary))

    assert result.returncode != 0
    output = _result_json(result)
    assert output["status"] == "ineligible"
    assert output["reason"] == "primary-diverged"
    assert snapshot_primary(scenario.primary) == before


@pytest.mark.parametrize("head_kind", ["different-branch", "detached"])
def test_sync_root_preserves_non_default_head(tmp_path: Path, head_kind: str) -> None:
    scenario = make_remote_scenario(tmp_path)
    if head_kind == "detached":
        git(scenario.primary, "switch", "--detach", scenario.stale_primary_sha)
    before = snapshot_primary(scenario.primary)

    result = run_cli(scenario.primary, "sync-root", "--repo", str(scenario.primary))

    assert result.returncode != 0
    output = _result_json(result)
    assert output["status"] == "ineligible"
    expected = "primary-detached" if head_kind == "detached" else "primary-not-default"
    assert output["reason"] == expected
    assert snapshot_primary(scenario.primary) == before


def test_sync_root_rejects_bare_repository_without_moving_refs(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    before = rev(scenario.remote, "trunk")

    result = run_cli(scenario.remote, "sync-root", "--repo", str(scenario.remote))

    assert result.returncode != 0
    assert "primary checkout" in result.stderr
    assert rev(scenario.remote, "trunk") == before


def test_dirty_root_does_not_block_fresh_default_source_worktree(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    _default_primary(scenario)
    (scenario.primary / "untracked.txt").write_text("preserve me\n")
    before = snapshot_primary(scenario.primary)

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "dirty-root",
        "--branch",
        "feature/dirty-root",
    )

    assert result.returncode == 0, result.stderr
    output = _result_json(result)
    assert output["source_sha"] == scenario.remote_head_sha
    assert output["root_sync_status"] == "ineligible"
    assert output["root_sync_reason"] == "primary-dirty"
    assert rev(scenario.primary / ".worktrees" / "dirty-root") == scenario.remote_head_sha
    assert snapshot_primary(scenario.primary) == before


def test_explicit_task_source_does_not_authorize_root_synchronization(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    _default_primary(scenario)
    before = snapshot_primary(scenario.primary)

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "explicit-source",
        "--branch",
        "feature/explicit-source",
        "--source",
        scenario.stale_primary_sha,
    )

    assert result.returncode == 0, result.stderr
    output = _result_json(result)
    assert output["root_sync_status"] == "ineligible"
    assert output["root_sync_reason"] == "explicit-source"
    assert rev(scenario.primary / ".worktrees" / "explicit-source") == scenario.stale_primary_sha
    assert snapshot_primary(scenario.primary) == before


def test_root_lifecycle_source_forbids_destructive_or_ref_only_shortcuts() -> None:
    source = (Path(__file__).resolve().parents[1] / "bin" / "escapement_worktree_root.py")
    assert source.is_file(), "managed root lifecycle module is missing"
    text = source.read_text(encoding="utf-8")
    for forbidden in ("reset", "stash", "clean", "checkout", "switch", "update-ref"):
        assert f'"{forbidden}"' not in text
