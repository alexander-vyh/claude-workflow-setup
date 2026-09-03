"""Identity-safe filesystem disposal for claimed worktree rollbacks."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import sys
from pathlib import Path

from escapement_worktree_git import (
    RepositoryContext,
    WorktreeError,
    admin_token_matches,
)


def path_identity(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise WorktreeError(f"rollback identity is not a directory: {path}")
    return metadata.st_dev, metadata.st_ino


def receipt_identity(
    receipt: dict[str, object], prefix: str
) -> tuple[int, int] | None:
    device = receipt.get(f"{prefix}_device")
    inode = receipt.get(f"{prefix}_inode")
    if isinstance(device, int) and isinstance(inode, int):
        return device, inode
    return None


def validate_admin_path(ctx: RepositoryContext, admin_dir: Path) -> Path:
    registry = ctx.common_dir / "worktrees"
    try:
        trusted_registry = registry.resolve(strict=True)
        trusted_parent = admin_dir.parent.resolve(strict=True)
    except OSError as error:
        raise WorktreeError(
            f"Git administrative rollback path is unavailable: {error}"
        ) from error
    if (
        not admin_dir.is_absolute()
        or trusted_parent != trusted_registry
        or admin_dir.name in {"", ".", ".."}
    ):
        raise WorktreeError(
            f"Git administrative rollback path is outside the registry: {admin_dir}"
        )
    return admin_dir


def _trusted_private_directory(path: Path) -> Path:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise WorktreeError(f"rollback disposal directory is untrusted: {path}")
    return path


def _disposal_directory(ctx: RepositoryContext, token: str) -> Path:
    root = ctx.common_dir / "escapement-worktree-rollbacks"
    root.mkdir(mode=0o700, exist_ok=True)
    _trusted_private_directory(root)
    disposal = root / token
    disposal.mkdir(mode=0o700, exist_ok=True)
    return _trusted_private_directory(disposal)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _exclusive_rename(
    source_parent: int,
    source_name: str,
    destination_parent: int,
    destination_name: str,
) -> None:
    """Atomically rename without replacing a destination on Linux or macOS."""
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        operation = library.renameatx_np
        operation.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        arguments = (
            source_parent,
            os.fsencode(source_name),
            destination_parent,
            os.fsencode(destination_name),
            0x00000004,  # RENAME_EXCL
        )
    else:
        try:
            operation = library.renameat2
        except AttributeError as error:
            raise WorktreeError(
                "atomic no-overwrite rename is unavailable on this platform"
            ) from error
        operation.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        arguments = (
            source_parent,
            os.fsencode(source_name),
            destination_parent,
            os.fsencode(destination_name),
            1,  # RENAME_NOREPLACE
        )
    operation.restype = ctypes.c_int
    if operation(*arguments) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, os.strerror(error_number))
    raise OSError(error_number, os.strerror(error_number))


def _open_parent(path: Path) -> int:
    return os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )


def _detach_exact_directory(
    source: Path,
    destination: Path,
    expected: tuple[int, int],
) -> bool:
    """Atomically detach one exact directory, or resume an earlier detach."""
    source_parent = _open_parent(source.parent)
    destination_parent = _open_parent(destination.parent)
    try:
        try:
            destination_metadata = os.stat(
                destination.name,
                dir_fd=destination_parent,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            destination_metadata = None
        if destination_metadata is not None:
            try:
                source_metadata = os.stat(
                    source.name,
                    dir_fd=source_parent,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                source_metadata = None
            else:
                raise WorktreeError(
                    "rollback disposal destination is already occupied: "
                    f"{destination}"
                )
            destination_identity = (
                destination_metadata.st_dev,
                destination_metadata.st_ino,
            )
            if destination_identity != expected:
                if source_metadata is not None:
                    raise WorktreeError(
                        f"rollback disposal identity changed: {destination}"
                    )
                try:
                    _exclusive_rename(
                        destination_parent,
                        destination.name,
                        source_parent,
                        source.name,
                    )
                except FileExistsError as error:
                    raise WorktreeError(
                        f"rollback displaced a replacement at {source}; "
                        f"preserved it at {destination} because the public "
                        "path is now occupied"
                    ) from error
                os.fsync(source_parent)
                os.fsync(destination_parent)
                raise WorktreeError(
                    "rollback restored a replacement stranded by an "
                    f"interrupted detach: {source}"
                )
            if not stat.S_ISDIR(destination_metadata.st_mode):
                raise WorktreeError(
                    f"rollback disposal identity changed: {destination}"
                )
            return True
        try:
            descriptor = os.open(
                source.name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=source_parent,
            )
        except FileNotFoundError:
            return False
        try:
            metadata = os.fstat(descriptor)
            observed = (metadata.st_dev, metadata.st_ino)
            if not stat.S_ISDIR(metadata.st_mode) or observed != expected:
                raise WorktreeError(f"rollback source identity changed: {source}")
            os.rename(
                source.name,
                destination.name,
                src_dir_fd=source_parent,
                dst_dir_fd=destination_parent,
            )
        finally:
            os.close(descriptor)
        moved = os.stat(
            destination.name,
            dir_fd=destination_parent,
            follow_symlinks=False,
        )
        moved_identity = (moved.st_dev, moved.st_ino)
        if moved_identity != observed:
            try:
                _exclusive_rename(
                    destination_parent,
                    destination.name,
                    source_parent,
                    source.name,
                )
            except FileExistsError as error:
                raise WorktreeError(
                    f"rollback displaced a replacement at {source}; "
                    f"preserved it at {destination} because the public path "
                    "is now occupied"
                ) from error
            os.fsync(source_parent)
            os.fsync(destination_parent)
            raise WorktreeError(
                f"rollback source identity changed during detach and was restored: "
                f"{source}"
            )
        os.fsync(source_parent)
        os.fsync(destination_parent)
        return True
    finally:
        os.close(destination_parent)
        os.close(source_parent)


def remove_exact_rollback_claim(
    ctx: RepositoryContext,
    claimed: Path,
    admin_dir: Path,
    token: str,
    worktree_identity: tuple[int, int],
    admin_identity: tuple[int, int],
) -> list[str]:
    """Delete only token-bound identities detached into a private directory."""
    disposal = _disposal_directory(ctx, token)
    detached_worktree = disposal / "worktree"
    detached_admin = disposal / "admin"
    try:
        worktree_present = _detach_exact_directory(
            claimed,
            detached_worktree,
            worktree_identity,
        )
        admin_present = _detach_exact_directory(
            admin_dir,
            detached_admin,
            admin_identity,
        )
        if admin_present and not admin_token_matches(detached_admin, token):
            return [
                f"preserved detached administrative directory {detached_admin}: "
                "creation token does not match"
            ]
        if worktree_present:
            shutil.rmtree(detached_worktree)
        if admin_present:
            shutil.rmtree(detached_admin)
        _sync_directory(disposal)
        disposal.rmdir()
        _sync_directory(disposal.parent)
    except (OSError, WorktreeError) as error:
        return [f"failed to remove exact rollback claim {claimed}: {error}"]
    return []
