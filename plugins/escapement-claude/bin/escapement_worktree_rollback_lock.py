"""Durable, lifecycle-owned lock files for rollback mutations."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from escapement_worktree_git import WorktreeError
from escapement_worktree_registry import write_lifecycle


@dataclass(frozen=True)
class AnchoredPath:
    """A display path whose leaf operations are pinned to an open parent."""

    path: Path
    parent_descriptor: int
    name: str


FileLocation = Path | AnchoredPath


def pin_leaf(root: Path, relative: Path) -> AnchoredPath:
    """Open each parent without following links and retain the leaf's parent."""
    descriptor = os.open(
        root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        current = root
        for part in relative.parts[:-1]:
            child = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise WorktreeError(
                    f"rollback parent is not a directory: {current / part}"
                )
            os.close(descriptor)
            descriptor = child
            current /= part
        return AnchoredPath(root / relative, descriptor, relative.name)
    except BaseException:
        os.close(descriptor)
        raise


def _display(path: FileLocation) -> Path:
    return path.path if isinstance(path, AnchoredPath) else path


def _exists(path: FileLocation) -> bool:
    try:
        if isinstance(path, AnchoredPath):
            os.stat(path.name, dir_fd=path.parent_descriptor, follow_symlinks=False)
        else:
            path.lstat()
    except FileNotFoundError:
        return False
    return True


def _lstat(path: FileLocation) -> os.stat_result:
    if isinstance(path, AnchoredPath):
        return os.stat(
            path.name,
            dir_fd=path.parent_descriptor,
            follow_symlinks=False,
        )
    return path.lstat()


def location_exists(path: FileLocation) -> bool:
    return _exists(path)


def location_lstat(path: FileLocation) -> os.stat_result:
    return _lstat(path)


def regular_file_identity_and_content(
    path: FileLocation,
) -> tuple[tuple[int, int], bytes]:
    descriptor, identity, content = _open_regular(path)
    os.close(descriptor)
    return identity, content


def _sync_parent(path: FileLocation) -> None:
    if isinstance(path, AnchoredPath):
        os.fsync(path.parent_descriptor)
    else:
        sync_directory(path.parent)


def _rename_to_claim(source: FileLocation, claim: Path) -> None:
    if isinstance(source, AnchoredPath):
        os.rename(source.name, claim, src_dir_fd=source.parent_descriptor)
    else:
        os.rename(source, claim)


def _link_to_location(source: Path, destination: FileLocation) -> None:
    if isinstance(destination, AnchoredPath):
        os.link(
            source,
            destination.name,
            dst_dir_fd=destination.parent_descriptor,
            follow_symlinks=False,
        )
    else:
        os.link(source, destination, follow_symlinks=False)


def sync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_regular(path: FileLocation) -> tuple[int, tuple[int, int], bytes]:
    display = _display(path)
    target: str | Path = path.name if isinstance(path, AnchoredPath) else path
    descriptor = os.open(
        target,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=(path.parent_descriptor if isinstance(path, AnchoredPath) else None),
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise WorktreeError(f"rollback lock is not a regular file: {display}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(129)
        return descriptor, (metadata.st_dev, metadata.st_ino), content
    except BaseException:
        os.close(descriptor)
        raise


def recorded_identity(
    receipt: dict[str, object], prefix: str
) -> tuple[int, int] | None:
    device = receipt.get(f"{prefix}_device")
    inode = receipt.get(f"{prefix}_inode")
    if isinstance(device, int) and isinstance(inode, int):
        return device, inode
    return None


def _lock_matches(
    path: FileLocation,
    token: str,
    expected_identity: tuple[int, int] | None,
) -> bool:
    try:
        descriptor, identity, content = _open_regular(path)
    except (OSError, WorktreeError):
        return False
    try:
        metadata = os.fstat(descriptor)
        return (
            metadata.st_uid == os.getuid()
            and metadata.st_mode & 0o077 == 0
            and content == f"{token}\n".encode()
            and (expected_identity is None or identity == expected_identity)
        )
    finally:
        os.close(descriptor)


def private_lock_path(common_dir: Path, token: str, name: str) -> Path:
    root = common_dir / "escapement-worktree-locks"
    root.mkdir(mode=0o700, exist_ok=True)
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise WorktreeError(f"rollback lock directory is untrusted: {root}")
    return root / f"{token}-{name}"


def journal_lock_intent(
    lifecycle_id: str,
    receipt: dict[str, object],
    prefix: str,
    lock_path: FileLocation,
    temporary_path: Path,
    *,
    phase: str = "rollback_ref_claimed",
) -> None:
    receipt.update(
        phase=phase,
        **{
            prefix: str(_display(lock_path)),
            f"{prefix}_temporary": str(temporary_path),
        },
        last_reason=None,
    )
    write_lifecycle(lifecycle_id, receipt)


def _remove_private_temporary(
    path: Path,
    token: str,
    expected_identity: tuple[int, int] | None,
) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if not _lock_matches(path, token, expected_identity):
        raise WorktreeError(f"preserved unowned rollback lock temporary {path}")
    path.unlink()
    sync_directory(path.parent)


def detach_exact_file(
    source: FileLocation,
    claim: Path,
    expected_identity: tuple[int, int],
) -> bool:
    """Atomically move a public file name, then prove which inode moved."""
    display = _display(source)
    if claim.exists() or claim.is_symlink():
        if _exists(source):
            raise WorktreeError(f"rollback file claim is occupied: {claim}")
        descriptor, identity, _content = _open_regular(claim)
        os.close(descriptor)
        if identity != expected_identity:
            raise WorktreeError(f"rollback file claim identity changed: {claim}")
        return True
    if not _exists(source):
        return False
    descriptor, identity, _content = _open_regular(source)
    try:
        if identity != expected_identity:
            raise WorktreeError(f"rollback file identity changed: {display}")
        _rename_to_claim(source, claim)
        moved = claim.lstat()
        if (moved.st_dev, moved.st_ino) != expected_identity:
            raise WorktreeError(f"rollback detached a replacement file: {display}")
        _sync_parent(source)
        sync_directory(claim.parent)
        return True
    finally:
        os.close(descriptor)


def remove_detached_file(claim: Path, expected_identity: tuple[int, int]) -> None:
    if not claim.exists() and not claim.is_symlink():
        return
    descriptor, identity, _content = _open_regular(claim)
    os.close(descriptor)
    if identity != expected_identity:
        raise WorktreeError(f"rollback detached file identity changed: {claim}")
    claim.unlink()
    sync_directory(claim.parent)


def restore_detached_file(
    claim: Path,
    destination: FileLocation,
    expected_identity: tuple[int, int],
) -> None:
    descriptor, identity, _content = _open_regular(claim)
    os.close(descriptor)
    if identity != expected_identity:
        raise WorktreeError(f"rollback restore identity changed: {claim}")
    display = _display(destination)
    if _exists(destination):
        destination_descriptor, destination_identity, _content = _open_regular(
            destination
        )
        os.close(destination_descriptor)
        if destination_identity != expected_identity:
            raise WorktreeError(
                f"cannot restore occupied rollback path: {display}"
            )
    else:
        try:
            _link_to_location(claim, destination)
        except FileExistsError as error:
            raise WorktreeError(
                f"cannot restore occupied rollback path: {display}"
            ) from error
    restored = _lstat(destination)
    if (restored.st_dev, restored.st_ino) != expected_identity:
        raise WorktreeError(f"rollback restored a different file: {display}")
    _sync_parent(destination)
    remove_detached_file(claim, expected_identity)


def adopt_stale_lock(
    lock_path: FileLocation,
    temporary_path: Path,
    token: str,
    receipt: dict[str, object],
    prefix: str,
    label: str,
) -> list[str]:
    display = _display(lock_path)
    expected = recorded_identity(receipt, prefix)
    published_claim = Path(f"{temporary_path}.published")
    release_claim = Path(f"{temporary_path}.release")
    if release_claim.exists() or release_claim.is_symlink():
        if _exists(lock_path):
            return [f"preserved replacement {label} {display}"]
        if expected is None:
            return [f"preserved unjournaled {label} release {release_claim}"]
        try:
            remove_detached_file(release_claim, expected)
        except (OSError, WorktreeError) as error:
            return [f"failed to replay release of {label} {display}: {error}"]
    if published_claim.exists() or published_claim.is_symlink():
        if _exists(lock_path):
            return [f"preserved replacement {label} {display}"]
        if expected is None:
            return [f"preserved unjournaled {label} adoption {published_claim}"]
        try:
            remove_detached_file(published_claim, expected)
        except (OSError, WorktreeError) as error:
            return [f"failed to replay adoption of {label} {display}: {error}"]
    if _exists(lock_path):
        if receipt.get(prefix) != str(display) or not _lock_matches(
            lock_path, token, expected
        ):
            return [f"preserved unowned {label} {display}"]
        if expected is None:
            return [f"preserved unjournaled {label} {display}"]
        try:
            detach_exact_file(lock_path, published_claim, expected)
            remove_detached_file(published_claim, expected)
        except (OSError, WorktreeError) as error:
            return [f"failed to release owned {label} {display}: {error}"]
    try:
        _remove_private_temporary(temporary_path, token, expected)
    except (OSError, WorktreeError) as error:
        return [str(error)]
    return []


@dataclass
class HeldPathLock:
    path: FileLocation
    private_path: Path
    descriptor: int
    identity: tuple[int, int]
    owned: bool = True

    def release(self) -> None:
        if not self.owned:
            return
        release_claim = Path(f"{self.private_path}.release")
        detach_exact_file(self.path, release_claim, self.identity)
        remove_detached_file(release_claim, self.identity)
        self.owned = False

    def close(self) -> None:
        os.close(self.descriptor)


def acquire_journaled_lock(
    lifecycle_id: str,
    receipt: dict[str, object],
    prefix: str,
    lock_path: FileLocation,
    temporary_path: Path,
    token: str,
    label: str,
) -> tuple[HeldPathLock | None, list[str]]:
    display = _display(lock_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    linked = False
    try:
        _remove_private_temporary(temporary_path, token, None)
        descriptor = os.open(temporary_path, flags, 0o600)
        os.write(descriptor, f"{token}\n".encode())
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
        receipt.update(
            **{
                f"{prefix}_device": identity[0],
                f"{prefix}_inode": identity[1],
            }
        )
        write_lifecycle(lifecycle_id, receipt)
        _link_to_location(temporary_path, lock_path)
        linked = True
        os.fsync(descriptor)
        _sync_parent(lock_path)
        temporary_path.unlink()
        sync_directory(temporary_path.parent)
        return HeldPathLock(lock_path, temporary_path, descriptor, identity), []
    except FileExistsError:
        detail = f"{label} is already held: {display}"
    except (OSError, WorktreeError) as error:
        detail = f"failed to acquire {label} {display}: {error}"
    if descriptor is not None:
        os.close(descriptor)
    if not linked:
        try:
            _remove_private_temporary(
                temporary_path, token, recorded_identity(receipt, prefix)
            )
        except (OSError, WorktreeError):
            pass
    return None, [detail]
