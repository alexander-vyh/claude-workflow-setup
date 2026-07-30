"""External Git fixtures and CLI driver for the worktree transaction oracle.

Nothing here imports the transaction implementation: the remote, its symbolic
HEAD, and Git's own object database are the oracle for every CLI assertion.
"""

from __future__ import annotations

import os
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class GitScenario:
    primary: Path
    remote: Path
    remote_default_ref: str
    remote_head_sha: str
    stale_primary_sha: str


@dataclass(frozen=True)
class PrimarySnapshot:
    head: str
    branch: str
    status: str
    cached_diff: str
    worktree_diff: str
    files: tuple[tuple[str, str], ...]


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Oracle",
            "GIT_AUTHOR_EMAIL": "oracle@example.test",
            "GIT_COMMITTER_NAME": "Oracle",
            "GIT_COMMITTER_EMAIL": "oracle@example.test",
        },
    )
    if check and result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result


def rev(cwd: Path, ref: str = "HEAD") -> str:
    return git(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def snapshot_primary(repo: Path) -> PrimarySnapshot:
    """Capture Git-observable user state that the transaction must not alter."""
    return PrimarySnapshot(
        head=rev(repo),
        branch=git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip(),
        status=git(repo, "status", "--porcelain=v2", "--untracked-files=all").stdout,
        cached_diff=git(repo, "diff", "--cached", "--binary").stdout,
        worktree_diff=git(repo, "diff", "--binary").stdout,
        files=tuple(
            sorted(
                (
                    str(path.relative_to(repo)),
                    hashlib.sha256(
                        path.readlink().as_posix().encode()
                        if path.is_symlink()
                        else path.read_bytes()
                    ).hexdigest(),
                )
                for path in repo.rglob("*")
                if ".git" not in path.parts
                and path.relative_to(repo).parts[0] != ".worktrees"
                and (path.is_file() or path.is_symlink())
            )
        ),
    )


def make_remote_scenario(
    tmp_path: Path, *, default_branch: str = "trunk"
) -> GitScenario:
    remote = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    primary = tmp_path / "primary"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "--initial-branch", default_branch, str(seed))
    (seed / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    (seed / "oracle.txt").write_text("stale-primary\n", encoding="utf-8")
    git(seed, "add", ".gitignore", "oracle.txt")
    git(seed, "commit", "-m", "old primary fixture")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", default_branch)
    git(remote, "symbolic-ref", "HEAD", f"refs/heads/{default_branch}")
    git(tmp_path, "clone", str(remote), str(primary))
    stale_primary_sha = rev(primary)
    git(primary, "switch", "-c", "root-main")
    (seed / "oracle.txt").write_text("remote-default\n", encoding="utf-8")
    git(seed, "add", "oracle.txt")
    git(seed, "commit", "-m", "advance remote default fixture")
    git(seed, "push", "origin", default_branch)
    remote_head_sha = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote),
            "rev-parse",
            "--verify",
            f"{default_branch}^{{commit}}",
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    return GitScenario(
        primary=primary,
        remote=remote,
        remote_default_ref=f"refs/heads/{default_branch}",
        remote_head_sha=remote_head_sha,
        stale_primary_sha=stale_primary_sha,
    )


_REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = _REPO_ROOT / "bin" / "escapement-worktree"


def run_cli(
    primary: Path, *args: str, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the public command without making absent-command tests error."""
    command_env = {**os.environ, **(env or {})}
    if not CLI.is_file():
        return subprocess.CompletedProcess(
            [str(CLI), *args], 127, "", f"required CLI is absent: {CLI}\n"
        )
    return subprocess.run(
        [str(CLI), *args],
        cwd=primary,
        text=True,
        capture_output=True,
        check=False,
        env=command_env,
    )
