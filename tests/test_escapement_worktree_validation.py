"""Boundary tests for the public Escapement worktree transaction."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from worktree_fixtures import (
    git,
    make_remote_scenario,
    rev,
    run_cli,
    snapshot_primary,
)


def _create_branch(repo: Path, branch: str) -> str:
    git(repo, "branch", branch, "HEAD")
    return rev(repo, f"refs/heads/{branch}")


def test_rejects_non_primary_repo_path(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    linked = tmp_path / "already-linked"
    git(
        scenario.primary,
        "worktree",
        "add",
        "-b",
        "feature/existing",
        str(linked),
        "HEAD",
    )
    result = run_cli(
        linked,
        "create",
        "--repo",
        str(linked),
        "--name",
        "bad",
        "--branch",
        "feature/bad",
    )
    assert result.returncode != 0
    assert not (linked / ".worktrees" / "bad").exists()
    assert (
        git(
            linked,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/bad",
            check=False,
        ).returncode
        != 0
    )
    assert not (scenario.primary / ".worktrees" / "bad").exists()


def test_rejects_existing_target_without_modifying_it(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "taken"
    target.mkdir(parents=True)
    sentinel = target / "keep.txt"
    sentinel.write_text("do not touch\n", encoding="utf-8")
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "taken",
        "--branch",
        "feature/taken",
    )
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "do not touch\n"
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/taken",
            check=False,
        ).returncode
        != 0
    )


def test_rejects_symlinked_worktrees_directory(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    worktrees = scenario.primary / ".worktrees"
    worktrees.symlink_to(external, target_is_directory=True)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "link",
        "--branch",
        "feature/link",
    )
    assert result.returncode != 0
    assert not (external / "link").exists()


def test_rejects_dangling_target_symlink_without_branch_residue(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    worktrees = scenario.primary / ".worktrees"
    worktrees.mkdir()
    target = worktrees / "dangling"
    target.symlink_to(tmp_path / "does-not-exist")
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "dangling",
        "--branch",
        "feature/dangling",
    )
    assert result.returncode != 0
    assert target.is_symlink()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/dangling",
            check=False,
        ).returncode
        != 0
    )


def test_rejects_nonignored_target_and_leaves_no_branch(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    (scenario.primary / ".gitignore").write_text("", encoding="utf-8")
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "unsafe",
        "--branch",
        "feature/unsafe",
    )
    assert result.returncode != 0
    assert not (scenario.primary / ".worktrees" / "unsafe").exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/unsafe",
            check=False,
        ).returncode
        != 0
    )


def test_rejects_invalid_branch_name(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "invalid",
        "--branch",
        "feature bad",
    )
    assert result.returncode != 0
    assert not (scenario.primary / ".worktrees" / "invalid").exists()


@pytest.mark.parametrize("name", ["", ".", "..", "../escape", "nested/name"])
def test_rejects_unsafe_worktree_name_without_escape_or_branch(
    tmp_path: Path, name: str
) -> None:
    scenario = make_remote_scenario(tmp_path)
    branch = "feature/unsafe-name"
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        name,
        "--branch",
        branch,
    )
    assert result.returncode != 0
    assert not (tmp_path / "escape").exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        != 0
    )


def test_check_ignore_inspection_error_fails_closed(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    proxy_dir = tmp_path / "git-proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        '#!/bin/sh\ncase "$*" in *check-ignore*) exit 74;; esac\nexec "$REAL_GIT" "$@"\n',
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "inspection-error",
        "--branch",
        "feature/inspection-error",
        env={
            "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
        },
    )
    assert result.returncode != 0
    assert not (scenario.primary / ".worktrees" / "inspection-error").exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/inspection-error",
            check=False,
        ).returncode
        != 0
    )


def test_rejects_preexisting_branch_without_moving_it(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    before = _create_branch(scenario.primary, "feature/existing")
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "existing",
        "--branch",
        "feature/existing",
    )
    assert result.returncode != 0
    assert rev(scenario.primary, "refs/heads/feature/existing") == before
    assert not (scenario.primary / ".worktrees" / "existing").exists()


def test_plain_git_repository_does_not_require_beads(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    (scenario.primary / "staged-user-state.txt").write_text(
        "staged\n", encoding="utf-8"
    )
    git(scenario.primary, "add", "staged-user-state.txt")
    (scenario.primary / "unstaged-user-state.txt").write_text(
        "unstaged\n", encoding="utf-8"
    )
    before = snapshot_primary(scenario.primary)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "plain",
        "--branch",
        "feature/plain",
    )
    created = scenario.primary / ".worktrees" / "plain"
    assert result.returncode == 0, result.stderr
    assert rev(created) == scenario.remote_head_sha
    assert (
        "beads" in result.stdout.lower() and "not applicable" in result.stdout.lower()
    )
    assert snapshot_primary(scenario.primary) == before


def test_failed_validation_never_switches_or_resets_dirty_primary(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    (scenario.primary / "keep-me.txt").write_text(
        "dirty user state\n", encoding="utf-8"
    )
    before = snapshot_primary(scenario.primary)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "invalid-primary",
        "--branch",
        "feature invalid",
    )
    assert result.returncode != 0
    assert snapshot_primary(scenario.primary) == before
