#!/usr/bin/env python3
"""Repository-neutral Git scope for landing-time policy checks.

The remote default symref and commit DAG are the authority for where a feature
branch began.  Consumers choose their own failure policy: this module reports
an unresolved committed range while preserving independently observable local
changes.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NetTreeScope:
    """Files changed between a branch baseline and its final local candidate."""

    files: tuple[str, ...]
    baseline: str | None
    landing_ref: str | None
    committed_scope_error: str | None = None


def _run_git(
    repo_root: Path,
    args: list[str],
    *,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_lines(repo_root: Path, args: list[str]) -> list[str] | None:
    """Return nonempty Git output lines, or ``None`` when Git is unresolved."""
    result = _run_git(repo_root, args)
    if result is None or result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def remote_default_ref(repo_root: Path) -> str | None:
    """Return a verified ``origin/HEAD`` target without guessing a branch name."""
    result = _run_git(
        repo_root,
        ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        timeout=5,
    )
    if result is None or result.returncode != 0:
        return None

    ref = result.stdout.strip()
    prefix = "refs/remotes/origin/"
    if not ref.startswith(prefix) or ref == f"{prefix}HEAD":
        return None

    resolved = _run_git(
        repo_root,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        timeout=5,
    )
    if resolved is None or resolved.returncode != 0 or not resolved.stdout.strip():
        return None
    return ref


def merge_base(repo_root: Path, landing_ref: str) -> str | None:
    """Resolve the branch point shared by the landing ref and current HEAD."""
    result = _run_git(repo_root, ["merge-base", landing_ref, "HEAD"])
    if result is None or result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def _local_change_files(repo_root: Path) -> set[str]:
    files: set[str] = set()
    for args in (
        ["diff", "--name-only"],
        ["diff", "--cached", "--name-only"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        lines = git_lines(repo_root, args)
        if lines is not None:
            files.update(lines)
    return files


def net_tree_scope(repo_root: Path) -> NetTreeScope:
    """Compare the branch base with the final local tree exactly once per path.

    A valid landing ref selects a merge base, not the moving landing tip.  Git's
    commit-to-worktree diff includes committed, staged, and unstaged tracked
    changes; untracked files are added separately.  With no trustworthy landing
    ref (or no merge base), callers retain HEAD-to-worktree behavior.
    """
    local_files = _local_change_files(repo_root)
    landing_ref = remote_default_ref(repo_root)
    if landing_ref is None:
        return NetTreeScope(tuple(sorted(local_files)), "HEAD", None)

    baseline = merge_base(repo_root, landing_ref)
    if baseline is None:
        return NetTreeScope(
            tuple(sorted(local_files)),
            "HEAD",
            landing_ref,
            f"could not compute committed change scope from {landing_ref}",
        )

    # --no-renames makes old and new paths explicit and independent of local
    # diff.renameLimit/configuration.  The policy consumers then evaluate the
    # deletion and addition against the same semantic baseline.
    net_files = git_lines(repo_root, ["diff", "--no-renames", "--name-only", baseline])
    if net_files is None:
        return NetTreeScope(
            tuple(sorted(local_files)),
            "HEAD",
            landing_ref,
            f"could not compute committed change scope from {landing_ref}",
        )

    net_files_set = set(net_files)
    untracked = git_lines(repo_root, ["ls-files", "--others", "--exclude-standard"])
    if untracked is not None:
        net_files_set.update(untracked)
    return NetTreeScope(tuple(sorted(net_files_set)), baseline, landing_ref)


def revision_file(repo_root: Path, revision: str | None, relative: str) -> str:
    """Read a file from a Git revision, returning empty for new/unknown paths."""
    if revision is None:
        return ""
    result = _run_git(repo_root, ["show", f"{revision}:{relative}"])
    if result is None or result.returncode != 0:
        return ""
    return result.stdout


def worktree_file(repo_root: Path, relative: str) -> str:
    """Read the final local candidate content, returning empty for deletion."""
    path = repo_root / relative
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
