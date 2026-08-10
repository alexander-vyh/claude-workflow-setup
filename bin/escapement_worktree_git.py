"""Git subprocess and source-resolution primitives for worktree creation."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

OBJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


@dataclass(frozen=True)
class RepositoryContext:
    primary: Path
    common_dir: Path


@dataclass(frozen=True)
class ResolvedSource:
    sha: str
    kind: Literal["remote-default", "explicit"]
    display_ref: str


class WorktreeError(RuntimeError):
    """A safe, user-facing worktree transaction failure."""


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            env=None if env is None else dict(env),
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorktreeError(f"{command[0]} failed: {error}") from error
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise WorktreeError(f"{command[0]} failed: {detail}")
    return result


def git(
    ctx_or_repo: RepositoryContext | Path,
    *args: str,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    repo = (
        ctx_or_repo.primary
        if isinstance(ctx_or_repo, RepositoryContext)
        else ctx_or_repo
    )
    return run(("git", "-C", str(repo), *args), env=env, check=check)


def _nul_worktree_records(porcelain: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for block in porcelain.split("\0\0"):
        if not block:
            continue
        record: dict[str, str] = {}
        for field in block.split("\0"):
            if not field:
                continue
            key, separator, value = field.partition(" ")
            record[key] = value if separator else ""
        records.append(record)
    return records


def registered_branch_owners(
    ctx: RepositoryContext, branch_ref: str
) -> list[str]:
    """Return paths that Git's NUL-delimited registry assigns to a branch."""
    records = _nul_worktree_records(
        git(ctx, "worktree", "list", "--porcelain", "-z").stdout
    )
    owners: list[str] = []
    for record in records:
        if record.get("branch") != branch_ref:
            continue
        owner = record.get("worktree")
        if not owner:
            raise WorktreeError(
                f"Git worktree registry omitted the owner path for {branch_ref}"
            )
        owners.append(owner)
    return owners


def resolve_repository(path: Path) -> RepositoryContext:
    requested = path.expanduser().resolve()
    top_level = Path(
        git(requested, "rev-parse", "--show-toplevel").stdout.strip()
    ).resolve()
    common_dir = Path(
        git(
            requested,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    ).resolve()
    primary_git_dir = top_level / ".git"
    if primary_git_dir.is_symlink() or not primary_git_dir.is_dir():
        raise WorktreeError(f"repository is not a primary checkout: {top_level}")
    if common_dir != primary_git_dir.resolve():
        raise WorktreeError(
            f"repository common directory does not belong to primary: {top_level}"
        )
    return RepositoryContext(primary=top_level, common_dir=common_dir)


def _discover_remote_head(ctx: RepositoryContext) -> tuple[str, str]:
    remote = git(
        ctx,
        "ls-remote",
        "--symref",
        "origin",
        "HEAD",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    refs: list[str] = []
    shas: list[str] = []
    lines = [line for line in remote.stdout.splitlines() if line]
    for line in lines:
        value, separator, name = line.partition("\t")
        if not separator or name != "HEAD":
            raise WorktreeError("origin HEAD advertisement was malformed")
        if value.startswith("ref: "):
            ref = value.removeprefix("ref: ")
            if not ref.startswith("refs/heads/") or not ref.removeprefix(
                "refs/heads/"
            ):
                raise WorktreeError("origin HEAD advertisement was malformed")
            refs.append(ref)
        elif OBJECT_ID_RE.fullmatch(value):
            shas.append(value.lower())
        else:
            raise WorktreeError("origin HEAD advertisement was malformed")
    if len(lines) != 2 or len(refs) != 1 or len(shas) != 1:
        raise WorktreeError("origin HEAD did not advertise exactly one branch and SHA")
    return refs[0], shas[0]


def _fetch_advertised(
    ctx: RepositoryContext, remote_ref: str
) -> tuple[str, str]:
    branch_name = remote_ref.removeprefix("refs/heads/")
    tracking_ref = f"refs/remotes/origin/{branch_name}"
    git(
        ctx,
        "fetch",
        "--no-tags",
        "--prune",
        "origin",
        f"+{remote_ref}:{tracking_ref}",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    fetched_sha = git(
        ctx, "rev-parse", "--verify", f"{tracking_ref}^{{commit}}"
    ).stdout.strip()
    return fetched_sha, tracking_ref


def resolve_default_source(ctx: RepositoryContext) -> ResolvedSource:
    for attempt in range(2):
        remote_ref, advertised_sha = _discover_remote_head(ctx)
        fetched_sha, tracking_ref = _fetch_advertised(ctx, remote_ref)
        if fetched_sha == advertised_sha:
            return ResolvedSource(
                sha=fetched_sha,
                kind="remote-default",
                display_ref=tracking_ref,
            )
        if attempt:
            break
    raise WorktreeError("origin HEAD changed during both source resolution attempts")


def resolve_explicit_source(
    ctx: RepositoryContext, source: str
) -> ResolvedSource:
    sha = git(
        ctx,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{source}^{{commit}}",
    ).stdout.strip()
    return ResolvedSource(sha=sha, kind="explicit", display_ref=source)
