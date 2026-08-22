"""Git subprocess and source-resolution primitives for worktree creation."""

from __future__ import annotations

import os
import fcntl
import re
import subprocess
from collections.abc import Mapping, Sequence
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field
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


@dataclass(frozen=True)
class CreationIdentity:
    """Controller-held identity of Git's administrative worktree instance."""

    admin_dir: Path
    device: int
    inode: int
    descriptor: int = dataclass_field(compare=False, repr=False)


@contextmanager
def repository_transaction_lock(ctx: RepositoryContext) -> Iterator[None]:
    """Serialize every worktree lifecycle transition for one Git common dir."""
    lock_path = ctx.common_dir / "escapement-worktree.lock"
    try:
        lock_file = lock_path.open("a+", encoding="utf-8")
    except OSError as error:
        raise WorktreeError(
            f"cannot open repository transaction lock: {error}"
        ) from error
    with lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except OSError as error:
            raise WorktreeError(
                f"cannot acquire repository transaction lock: {error}"
            ) from error
        yield


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


def _admin_metadata(target: Path) -> tuple[Path, int, int]:
    admin_dir = Path(
        git(
            target,
            "rev-parse",
            "--path-format=absolute",
            "--git-dir",
        ).stdout.strip()
    ).resolve(strict=True)
    metadata = admin_dir.stat()
    return admin_dir, metadata.st_dev, metadata.st_ino


def capture_creation_identity(target: Path) -> CreationIdentity:
    admin_dir, device, inode = _admin_metadata(target)
    return CreationIdentity(
        admin_dir=admin_dir,
        device=device,
        inode=inode,
        descriptor=os.open(admin_dir, os.O_RDONLY),
    )


def target_owned_by_creation(
    ctx: RepositoryContext,
    target: Path,
    branch: str,
    expected_sha: str,
    creation_identity: CreationIdentity | None,
) -> tuple[bool, str]:
    if target.is_symlink():
        return False, "target is a symlink"
    top = git(target, "rev-parse", "--show-toplevel", check=False)
    common = git(
        target,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
        check=False,
    )
    symbolic = git(target, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    head = git(target, "rev-parse", "--verify", "HEAD^{commit}", check=False)
    if top.returncode or common.returncode or symbolic.returncode or head.returncode:
        return False, "target is not an inspectable Git worktree"
    if creation_identity is not None:
        try:
            admin_dir, device, inode = _admin_metadata(target)
        except (OSError, WorktreeError):
            return False, "Git administrative instance cannot be identified"
        if (
            admin_dir != creation_identity.admin_dir
            or device != creation_identity.device
            or inode != creation_identity.inode
        ):
            return False, "Git administrative creation instance was replaced"
    try:
        target_path = target.resolve(strict=True)
        top_path = Path(top.stdout.strip()).resolve(strict=True)
        common_path = Path(common.stdout.strip()).resolve(strict=True)
    except OSError:
        return False, "target Git paths cannot be resolved"
    if top_path != target_path:
        return False, "target resolves to a different repository root"
    if common_path != ctx.common_dir:
        return False, "target belongs to a different common directory"
    if symbolic.stdout.strip() != branch:
        return False, "target branch does not match the transaction"
    if head.stdout.strip() != expected_sha:
        return False, "target HEAD does not match the transaction source"
    return True, ""


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


def worktree_records(ctx: RepositoryContext) -> list[dict[str, str]]:
    """Return Git's NUL-delimited registry as exact field dictionaries."""
    return _nul_worktree_records(
        git(ctx, "worktree", "list", "--porcelain", "-z").stdout
    )


def registered_branch_owners(ctx: RepositoryContext, branch_ref: str) -> list[str]:
    """Return paths that Git's NUL-delimited registry assigns to a branch."""
    records = worktree_records(ctx)
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
            if not ref.startswith("refs/heads/") or not ref.removeprefix("refs/heads/"):
                raise WorktreeError("origin HEAD advertisement was malformed")
            refs.append(ref)
        elif OBJECT_ID_RE.fullmatch(value):
            shas.append(value.lower())
        else:
            raise WorktreeError("origin HEAD advertisement was malformed")
    if len(lines) != 2 or len(refs) != 1 or len(shas) != 1:
        raise WorktreeError("origin HEAD did not advertise exactly one branch and SHA")
    return refs[0], shas[0]


def _fetch_advertised(ctx: RepositoryContext, remote_ref: str) -> tuple[str, str]:
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


def resolve_explicit_source(ctx: RepositoryContext, source: str) -> ResolvedSource:
    sha = git(
        ctx,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{source}^{{commit}}",
    ).stdout.strip()
    return ResolvedSource(sha=sha, kind="explicit", display_ref=source)
