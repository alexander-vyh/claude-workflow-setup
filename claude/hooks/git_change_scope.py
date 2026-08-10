#!/usr/bin/env python3
"""Repository-neutral Git scope for landing-time policy checks.

The remote default symref and commit DAG select an immutable feature baseline.
Git's index plus safe worktree overlays form the final candidate tree. Consumers
choose their own failure policy: this module reports unresolved committed scope
while preserving independently observable local changes.
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class LandingTarget:
    """Verified remote-default ref and the immutable commit it selected."""

    ref: str
    oid: str


@dataclass(frozen=True)
class NetTreeChange:
    """One semantic baseline-to-final-candidate path change."""

    baseline_path: bytes | None
    candidate_path: bytes | None
    candidate_from_worktree: bool = False

    @property
    def filepath(self) -> str:
        """User-facing path, preserving raw bytes with surrogateescape."""
        raw_path = self.candidate_path or self.baseline_path or b""
        return os.fsdecode(raw_path)


@dataclass(frozen=True)
class NetTreeScope:
    """Semantic changes between a branch baseline and final local candidate."""

    changes: tuple[NetTreeChange, ...]
    baseline: str | None
    landing_ref: str | None
    committed_scope_error: str | None = None

    @property
    def files(self) -> tuple[str, ...]:
        """Compatibility view containing each semantic change exactly once."""
        return tuple(change.filepath for change in self.changes)


def _run_git_bytes(
    repo_root: Path,
    args: list[str | bytes],
    *,
    timeout: int = 10,
) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            [
                b"git",
                *(os.fsencode(arg) if isinstance(arg, str) else arg for arg in args),
            ],
            cwd=os.fsencode(repo_root),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _successful_output(
    repo_root: Path,
    args: list[str | bytes],
    *,
    timeout: int = 10,
) -> bytes | None:
    result = _run_git_bytes(repo_root, args, timeout=timeout)
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def git_lines(repo_root: Path, args: list[str]) -> list[str] | None:
    """Compatibility helper returning surrogateescaped nonempty Git lines."""
    output = _successful_output(repo_root, args)
    if output is None:
        return None
    return [os.fsdecode(line.strip()) for line in output.splitlines() if line.strip()]


def remote_default_target(repo_root: Path) -> LandingTarget | None:
    """Resolve and verify ``origin/HEAD`` once, returning its immutable OID."""
    ref_output = _successful_output(
        repo_root,
        ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        timeout=5,
    )
    if ref_output is None:
        return None
    try:
        ref = ref_output.strip().decode("ascii")
    except UnicodeDecodeError:
        return None
    prefix = "refs/remotes/origin/"
    if not ref.startswith(prefix) or ref == f"{prefix}HEAD":
        return None

    oid_output = _successful_output(
        repo_root,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        timeout=5,
    )
    if oid_output is None:
        return None
    tokens = oid_output.split()
    if len(tokens) != 1:
        return None
    try:
        oid = tokens[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    if len(oid) not in {40, 64} or any(char not in "0123456789abcdef" for char in oid):
        return None
    return LandingTarget(ref=ref, oid=oid)


def remote_default_ref(repo_root: Path) -> str | None:
    """Compatibility view of the verified remote-default ref."""
    target = remote_default_target(repo_root)
    return target.ref if target is not None else None


def merge_base(repo_root: Path, landing_oid: str) -> str | None:
    """Resolve the branch point shared by an immutable landing OID and HEAD."""
    output = _successful_output(repo_root, ["merge-base", landing_oid, "HEAD"])
    if output is None:
        return None
    tokens = output.split()
    if len(tokens) != 1:
        return None
    try:
        return tokens[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _nul_paths(repo_root: Path, args: list[str]) -> set[bytes] | None:
    output = _successful_output(repo_root, args)
    if output is None:
        return None
    if output and not output.endswith(b"\0"):
        return None
    return {field for field in output.split(b"\0") if field}


def _parse_name_status(output: bytes) -> list[NetTreeChange] | None:
    if output and not output.endswith(b"\0"):
        return None
    fields = output.removesuffix(b"\0").split(b"\0") if output else []
    changes: list[NetTreeChange] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            return None
        kind = status[:1]
        path_count = 2 if kind in {b"R", b"C"} else 1
        if index + path_count > len(fields):
            return None
        paths = fields[index : index + path_count]
        index += path_count
        if any(not path for path in paths):
            return None
        if kind == b"R":
            changes.append(NetTreeChange(paths[0], paths[1]))
        elif kind == b"C":
            changes.append(NetTreeChange(None, paths[1]))
        elif kind == b"D":
            changes.append(NetTreeChange(paths[0], None))
        elif kind == b"A":
            changes.append(NetTreeChange(None, paths[0]))
        elif kind in {b"M", b"T", b"U"}:
            changes.append(NetTreeChange(paths[0], paths[0]))
        else:
            return None
    return changes


def _changes_from_baseline(
    repo_root: Path,
    baseline: str,
) -> tuple[NetTreeChange, ...] | None:
    output = _successful_output(
        repo_root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=all",
            "--name-status",
            "-z",
            "--find-renames",
            "-l0",
            baseline,
            "--",
        ],
    )
    if output is None:
        return None
    parsed = _parse_name_status(output)
    if parsed is None:
        return None

    unstaged = _nul_paths(
        repo_root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=all",
            "--name-only",
            "-z",
            "--no-renames",
            "--",
        ],
    )
    untracked = _nul_paths(
        repo_root,
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
    )
    if unstaged is None or untracked is None:
        return None

    changes = [
        replace(
            change,
            candidate_path=(
                change.candidate_path
                if change.candidate_path is not None
                else change.baseline_path
                if change.baseline_path in unstaged
                else None
            ),
            candidate_from_worktree=(
                change.candidate_path in unstaged
                or change.candidate_path in untracked
                or (change.candidate_path is None and change.baseline_path in unstaged)
            ),
        )
        for change in parsed
    ]

    for path in untracked:
        match = next(
            (
                index
                for index, change in enumerate(changes)
                if change.candidate_path == path
                or (change.candidate_path is None and change.baseline_path == path)
            ),
            None,
        )
        if match is None:
            changes.append(NetTreeChange(None, path, candidate_from_worktree=True))
        else:
            changes[match] = replace(
                changes[match],
                candidate_path=path,
                candidate_from_worktree=True,
            )

    return tuple(
        sorted(
            changes,
            key=lambda change: change.candidate_path or change.baseline_path or b"",
        )
    )


def _fallback_scope(
    repo_root: Path,
    landing_ref: str | None,
    error: str | None = None,
) -> NetTreeScope:
    changes = _changes_from_baseline(repo_root, "HEAD")
    if changes is None:
        untracked = _nul_paths(
            repo_root,
            ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        )
        if untracked is None:
            changes = ()
            error = error or "could not compute local Git change scope"
        else:
            changes = tuple(
                NetTreeChange(None, path, candidate_from_worktree=True)
                for path in sorted(untracked)
            )
    return NetTreeScope(changes, "HEAD", landing_ref, error)


def net_tree_scope(repo_root: Path) -> NetTreeScope:
    """Compare the branch base with the final local candidate tree once."""
    target = remote_default_target(repo_root)
    if target is None:
        return _fallback_scope(repo_root, None)

    baseline = merge_base(repo_root, target.oid)
    if baseline is None:
        return _fallback_scope(
            repo_root,
            target.ref,
            f"could not compute committed change scope from {target.ref}",
        )

    changes = _changes_from_baseline(repo_root, baseline)
    if changes is None:
        return _fallback_scope(
            repo_root,
            target.ref,
            f"could not compute committed change scope from {target.ref}",
        )
    return NetTreeScope(changes, baseline, target.ref)


def _tree_blob(repo_root: Path, revision: str, relative: bytes) -> str:
    listing = _successful_output(
        repo_root,
        ["ls-tree", "-z", revision, "--", relative],
    )
    if listing is None or not listing.endswith(b"\0"):
        return ""
    records = [record for record in listing.split(b"\0") if record]
    if len(records) != 1 or b"\t" not in records[0]:
        return ""
    metadata, listed_path = records[0].split(b"\t", 1)
    fields = metadata.split()
    if listed_path != relative or len(fields) != 3:
        return ""
    mode, object_type, oid = fields
    if mode not in {b"100644", b"100755"} or object_type != b"blob":
        return ""
    content = _successful_output(repo_root, ["cat-file", "blob", oid])
    return content.decode("utf-8", errors="replace") if content is not None else ""


def _index_blob(repo_root: Path, relative: bytes) -> str:
    listing = _successful_output(
        repo_root,
        ["ls-files", "--stage", "-z", "--", relative],
    )
    if listing is None or not listing.endswith(b"\0"):
        return ""
    records = [record for record in listing.split(b"\0") if record]
    if len(records) != 1 or b"\t" not in records[0]:
        return ""
    metadata, listed_path = records[0].split(b"\t", 1)
    fields = metadata.split()
    if listed_path != relative or len(fields) != 3:
        return ""
    mode, oid, stage = fields
    if mode not in {b"100644", b"100755"} or stage != b"0":
        return ""
    content = _successful_output(repo_root, ["cat-file", "blob", oid])
    return content.decode("utf-8", errors="replace") if content is not None else ""


def _safe_worktree_file(repo_root: Path, relative: bytes) -> str:
    parts = relative.split(b"/")
    if not parts or any(part in {b"", b".", b".."} for part in parts):
        return ""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        return ""
    common_flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    opened: list[int] = []
    try:
        current_fd = os.open(repo_root, common_flags | directory)
        opened.append(current_fd)
        for component in parts[:-1]:
            current_fd = os.open(
                component,
                common_flags | directory,
                dir_fd=current_fd,
            )
            opened.append(current_fd)
        leaf_fd = os.open(
            parts[-1],
            common_flags | getattr(os, "O_NONBLOCK", 0),
            dir_fd=current_fd,
        )
        opened.append(leaf_fd)
        if not stat.S_ISREG(os.fstat(leaf_fd).st_mode):
            return ""
        chunks: list[bytes] = []
        while chunk := os.read(leaf_fd, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")
    except OSError:
        return ""
    finally:
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass


def revision_file(repo_root: Path, revision: str | None, relative: str | bytes) -> str:
    """Read a regular blob from a Git revision; other entry types are empty."""
    if revision is None:
        return ""
    return _tree_blob(repo_root, revision, os.fsencode(relative))


def worktree_file(repo_root: Path, relative: str | bytes) -> str:
    """Safely read a regular worktree file without following any symlink."""
    return _safe_worktree_file(repo_root, os.fsencode(relative))


def change_sources(
    repo_root: Path,
    scope: NetTreeScope,
    change: NetTreeChange,
) -> tuple[str, str]:
    """Return baseline and final candidate source for one semantic change."""
    old_source = (
        revision_file(repo_root, scope.baseline, change.baseline_path)
        if change.baseline_path is not None
        else ""
    )
    if change.candidate_path is None:
        return old_source, ""
    if change.candidate_from_worktree:
        return old_source, _safe_worktree_file(repo_root, change.candidate_path)
    return old_source, _index_blob(repo_root, change.candidate_path)
