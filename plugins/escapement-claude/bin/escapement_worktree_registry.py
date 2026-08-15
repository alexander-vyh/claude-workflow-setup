"""Trusted lifecycle selector records for Escapement worktrees."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from escapement_worktree_git import OBJECT_ID_RE, WorktreeError

LIFECYCLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class LifecycleEntry:
    lifecycle_id: str
    repository: Path
    common_directory: Path
    origin: str
    worktree: Path
    branch_ref: str
    source_sha: str
    raw: bytes


def validate_lifecycle_id(lifecycle_id: str) -> str:
    if (
        not isinstance(lifecycle_id, str)
        or not LIFECYCLE_ID_RE.fullmatch(lifecycle_id)
        or lifecycle_id in {".", ".."}
    ):
        raise WorktreeError(f"invalid or unsafe lifecycle identity: {lifecycle_id!r}")
    return lifecycle_id


def registry_root() -> Path:
    home = Path(
        os.environ.get(
            "CONTINUATION_HARNESS_HOME",
            Path.home() / ".claude" / "harness",
        )
    ).expanduser()
    return home / "worktrees"


def ensure_registry() -> Path:
    """Create the one private lifecycle registry, or validate the existing one."""
    root = registry_root()
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(root.parent, 0o700)
    os.chmod(root, 0o700)
    return _trusted_directory(root)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_lifecycle(lifecycle_id: str, value: dict[str, object]) -> Path:
    root = ensure_registry()
    validate_lifecycle_id(lifecycle_id)
    path = root / f"{lifecycle_id}.json"
    if path.is_symlink():
        raise WorktreeError("lifecycle entry is a symlink")
    payload = (json.dumps(value, sort_keys=True) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{lifecycle_id}.", dir=root)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        _sync_directory(root)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise WorktreeError(f"lifecycle receipt could not be persisted: {error}") from error
    return path


def lifecycle_exists(lifecycle_id: str) -> bool:
    root = ensure_registry()
    validate_lifecycle_id(lifecycle_id)
    path = root / f"{lifecycle_id}.json"
    if path.is_symlink():
        raise WorktreeError("lifecycle entry is a symlink")
    return path.exists()


def delete_lifecycle(lifecycle_id: str) -> None:
    validate_lifecycle_id(lifecycle_id)
    path = _trusted_directory(registry_root()) / f"{lifecycle_id}.json"
    try:
        path.unlink()
        _sync_directory(path.parent)
    except OSError as error:
        raise WorktreeError(f"lifecycle receipt could not be removed: {error}") from error


@contextmanager
def lifecycle_lock(lifecycle_id: str) -> Iterator[None]:
    root = ensure_registry()
    validate_lifecycle_id(lifecycle_id)
    lock_path = root / f".{lifecycle_id}.lock"
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise WorktreeError("lifecycle lock ownership or permissions are untrusted")
        stream = os.fdopen(descriptor, "a+", encoding="utf-8")
        descriptor = None
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise WorktreeError(f"lifecycle lock is unavailable: {error}") from error
    except WorktreeError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    with stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def _trusted_directory(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise WorktreeError(f"lifecycle registry is unavailable: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise WorktreeError("lifecycle registry is not a trusted directory")
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise WorktreeError("lifecycle registry ownership or permissions are untrusted")
    return path.resolve(strict=True)


def _entry_bytes(root: Path, lifecycle_id: str) -> bytes:
    validate_lifecycle_id(lifecycle_id)
    trusted_root = _trusted_directory(root)
    path = trusted_root / f"{lifecycle_id}.json"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise WorktreeError(f"lifecycle entry is unavailable or untrusted: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise WorktreeError("lifecycle entry is not a regular file")
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise WorktreeError("lifecycle entry ownership or permissions are untrusted")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise WorktreeError("lifecycle entry is too large")
        return raw
    finally:
        os.close(descriptor)


def load_lifecycle(lifecycle_id: str, *, expected_raw: bytes | None = None) -> LifecycleEntry:
    raw = _entry_bytes(registry_root(), lifecycle_id)
    if expected_raw is not None and raw != expected_raw:
        raise WorktreeError("lifecycle entry changed during inspection")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorktreeError(f"lifecycle entry is malformed: {error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise WorktreeError("lifecycle entry has an unsupported schema")
    required = (
        "lifecycle_id",
        "repository",
        "common_directory",
        "origin",
        "worktree",
        "branch_ref",
        "source_sha",
    )
    if any(not isinstance(value.get(field), str) or not value[field] for field in required):
        raise WorktreeError("lifecycle entry is missing required string fields")
    if value["lifecycle_id"] != lifecycle_id:
        raise WorktreeError("lifecycle entry identity does not match its selector")
    repository = Path(value["repository"])
    common = Path(value["common_directory"])
    worktree = Path(value["worktree"])
    if not all(path.is_absolute() for path in (repository, common, worktree)):
        raise WorktreeError("lifecycle entry paths must be absolute")
    branch_ref = value["branch_ref"]
    if not branch_ref.startswith("refs/heads/") or not branch_ref.removeprefix("refs/heads/"):
        raise WorktreeError("lifecycle entry branch ref is invalid")
    if not OBJECT_ID_RE.fullmatch(value["source_sha"]):
        raise WorktreeError("lifecycle entry source SHA is invalid")
    return LifecycleEntry(
        lifecycle_id=lifecycle_id,
        repository=repository,
        common_directory=common,
        origin=value["origin"],
        worktree=worktree,
        branch_ref=branch_ref,
        source_sha=value["source_sha"].lower(),
        raw=raw,
    )
