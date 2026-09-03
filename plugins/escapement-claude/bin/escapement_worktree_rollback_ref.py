"""Crash-replayable deletion of one transaction-owned loose branch ref."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from pathlib import Path

from escapement_worktree_git import (
    RepositoryContext,
    WorktreeError,
    git,
    registered_branch_owners,
)
from escapement_worktree_registry import write_lifecycle
from escapement_worktree_rollback_lock import (
    AnchoredPath,
    FileLocation,
    HeldPathLock,
    acquire_journaled_lock,
    adopt_stale_lock,
    detach_exact_file,
    journal_lock_intent,
    location_exists,
    location_lstat,
    pin_leaf,
    private_lock_path,
    recorded_identity,
    remove_detached_file,
    restore_detached_file,
)

ROLLBACK_REF_CLAIMED = "rollback_ref_claimed"
ROLLBACK_REF_DETACHED = "rollback_ref_detached"
ROLLBACK_REF_RESTORING = "rollback_ref_restoring"
ROLLBACK_REF_REMOVED = "rollback_ref_removed"
ROLLBACK_WORKTREE_REMOVED = "rollback_worktree_removed"


def _branch_paths(ctx: RepositoryContext, branch: str) -> tuple[Path, Path]:
    root = (ctx.common_dir / "refs" / "heads").resolve(strict=True)
    relative = Path(branch)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise WorktreeError(f"branch ref is unsafe: {branch!r}")
    ref_path = root / relative
    current = ref_path.parent
    while current != root:
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise WorktreeError(
                    f"branch ref parent is untrusted: {current}"
                )
        current = current.parent
    if root not in ref_path.parents:
        raise WorktreeError(f"branch ref escaped the heads directory: {ref_path}")
    return ref_path, Path(f"{ref_path}.lock")


def _open_regular(path: FileLocation) -> tuple[int, tuple[int, int], bytes]:
    display = path.path if isinstance(path, AnchoredPath) else path
    target: str | Path = path.name if isinstance(path, AnchoredPath) else path
    descriptor = os.open(
        target,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=(path.parent_descriptor if isinstance(path, AnchoredPath) else None),
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise WorktreeError(f"rollback ref is not a regular file: {display}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(129)
        return descriptor, (metadata.st_dev, metadata.st_ino), content
    except BaseException:
        os.close(descriptor)
        raise


def _branch_presence(ctx: RepositoryContext, branch_ref: str) -> int:
    result = git(
        ctx,
        "show-ref",
        "--verify",
        "--quiet",
        branch_ref,
        check=False,
    )
    return result.returncode


def _remove_recorded_reflog(
    ctx: RepositoryContext,
    token: str,
    receipt: dict[str, object],
    location: FileLocation | None,
) -> list[str]:
    raw_path = receipt.get("rollback_reflog")
    identity = recorded_identity(receipt, "rollback_reflog")
    if raw_path is None and identity is None:
        return []
    if not isinstance(raw_path, str) or identity is None:
        return ["rollback ref claim has incomplete reflog identity"]
    path: FileLocation = location if location is not None else Path(raw_path)
    claim = private_lock_path(ctx.common_dir, token, "reflog")
    recorded_claim = receipt.get("rollback_reflog_claim")
    if recorded_claim not in {None, str(claim)}:
        return ["rollback reflog claim path does not match the receipt"]
    try:
        receipt["rollback_reflog_claim"] = str(claim)
        detach_exact_file(path, claim, identity)
        remove_detached_file(claim, identity)
    except (OSError, WorktreeError) as error:
        return [f"failed to remove rollback reflog {raw_path}: {error}"]
    return []


def _packed_ref_present(ctx: RepositoryContext, branch_ref: str) -> bool:
    path = ctx.common_dir / "packed-refs"
    if not path.exists() and not path.is_symlink():
        return False
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise WorktreeError(f"packed refs is not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(64 * 1024 * 1024 + 1)
        if len(content) > 64 * 1024 * 1024:
            raise WorktreeError("packed refs is too large to verify safely")
    finally:
        os.close(descriptor)
    suffix = f" {branch_ref}\n".encode()
    return any(
        line.endswith(suffix)
        for line in content.splitlines(keepends=True)
        if line and not line.startswith((b"#", b"^"))
    )


def _restore_for_owners(
    claim: Path,
    ref_path: Path,
    ref_identity: tuple[int, int],
    expected_sha: str,
    lifecycle_id: str,
    receipt: dict[str, object],
    branch_ref: str,
    owners: list[str],
) -> list[str]:
    receipt.update(phase=ROLLBACK_REF_RESTORING, last_reason=None)
    write_lifecycle(lifecycle_id, receipt)
    restore_detached_file(claim, ref_path, ref_identity)
    descriptor, _restored_identity, content = _open_regular(ref_path)
    os.close(descriptor)
    if content != f"{expected_sha}\n".encode():
        raise WorktreeError(f"restored branch content changed: {branch_ref}")
    receipt.update(phase=ROLLBACK_WORKTREE_REMOVED, last_reason=None)
    write_lifecycle(lifecycle_id, receipt)
    return [
        f"refused to delete registered branch {branch_ref}: "
        f"owned by worktree {owners[0]!r}"
    ]


def delete_owned_branch(
    ctx: RepositoryContext,
    lifecycle_id: str,
    branch: str,
    expected_sha: str,
    token: str,
    receipt: dict[str, object],
    *,
    authorized: bool,
) -> list[str]:
    branch_ref = f"refs/heads/{branch}"
    try:
        ref_display, lock_display = _branch_paths(ctx, branch)
    except (OSError, WorktreeError) as error:
        return [str(error)]
    relative_ref = Path("refs") / "heads" / branch
    try:
        ref_path = pin_leaf(ctx.common_dir, relative_ref)
    except (OSError, WorktreeError) as error:
        if (
            receipt.get("branch_allocated") is False
            and receipt.get("branch_allocation_state") == "unstarted"
            and _branch_presence(ctx, branch_ref) == 1
        ):
            return []
        return [f"failed to pin branch parent {ref_display.parent}: {error}"]
    lock_path = AnchoredPath(
        lock_display,
        ref_path.parent_descriptor,
        f"{ref_path.name}.lock",
    )
    reflog_display = ctx.common_dir / "logs" / relative_ref
    reflog_location: AnchoredPath | None = None
    with ExitStack() as resources:
        resources.callback(os.close, ref_path.parent_descriptor)
        recorded_reflog = receipt.get("rollback_reflog")
        created_reflog = receipt.get("branch_reflog")
        should_pin_reflog = (
            recorded_reflog == str(reflog_display)
            or (
                receipt.get("branch_reflog_present") is True
                and created_reflog == str(reflog_display)
            )
            or reflog_display.exists()
            or reflog_display.is_symlink()
        )
        if should_pin_reflog:
            try:
                reflog_location = pin_leaf(
                    ctx.common_dir,
                    Path("logs") / relative_ref,
                )
            except (OSError, WorktreeError) as error:
                return [
                    f"failed to pin reflog parent {reflog_display.parent}: {error}"
                ]
            resources.callback(os.close, reflog_location.parent_descriptor)
        return _delete_owned_branch_anchored(
            ctx,
            lifecycle_id,
            branch,
            branch_ref,
            expected_sha,
            token,
            receipt,
            ref_path,
            lock_path,
            reflog_location,
            authorized=authorized,
        )


def _delete_owned_branch_anchored(
    ctx: RepositoryContext,
    lifecycle_id: str,
    branch: str,
    branch_ref: str,
    expected_sha: str,
    token: str,
    receipt: dict[str, object],
    ref_path: AnchoredPath,
    lock_path: AnchoredPath,
    reflog_location: AnchoredPath | None,
    *,
    authorized: bool,
) -> list[str]:

    packed_lock_path = ctx.common_dir / "packed-refs.lock"
    ref_temporary = private_lock_path(ctx.common_dir, token, "ref")
    packed_temporary = private_lock_path(ctx.common_dir, token, "packed")
    ref_claim = private_lock_path(ctx.common_dir, token, "branch")
    phase = receipt.get("phase")
    if phase in {
        ROLLBACK_REF_CLAIMED,
        ROLLBACK_REF_DETACHED,
        ROLLBACK_REF_RESTORING,
        ROLLBACK_REF_REMOVED,
    }:
        if receipt.get("rollback_ref") != str(ref_path.path):
            return ["rollback branch path does not match the receipt"]
        residue = adopt_stale_lock(
            lock_path,
            ref_temporary,
            token,
            receipt,
            "rollback_ref_lock",
            "branch lock",
        )
        if residue:
            return residue
        residue = adopt_stale_lock(
            packed_lock_path,
            packed_temporary,
            token,
            receipt,
            "rollback_packed_lock",
            "packed refs lock",
        )
        if residue:
            return residue
    if phase == ROLLBACK_REF_REMOVED:
        return []
    ref_present = location_exists(ref_path)
    if not ref_present:
        presence = _branch_presence(ctx, branch_ref)
        if presence == 1 and receipt.get("branch_allocated") is False:
            if receipt.get("branch_allocation_state") == "unstarted":
                return []
    created_ref_identity = recorded_identity(receipt, "branch_ref")
    if ref_present:
        try:
            descriptor, current_identity, content = _open_regular(ref_path)
        except (OSError, WorktreeError) as error:
            return [f"failed to inspect branch {branch_ref}: {error}"]
        try:
            if created_ref_identity is None:
                return [
                    f"refused to delete branch {branch_ref}: "
                    "no exact creation ref identity was recorded"
                ]
            if content != f"{expected_sha}\n".encode():
                observed = content.decode(errors="replace").strip()
                return [
                    f"refused to delete moved branch {branch_ref}: "
                    f"expected {expected_sha}, found {observed}"
                ]
            if current_identity != created_ref_identity:
                return [f"refused to delete replaced branch {branch_ref}"]
        finally:
            os.close(descriptor)
    reflog_present = (
        reflog_location is not None and location_exists(reflog_location)
    )
    if reflog_present:
        created_reflog_identity = recorded_identity(receipt, "branch_reflog")
        if receipt.get("branch_reflog_present") is not True:
            return ["refused to delete a reflog not present during creation"]
        if created_reflog_identity is None:
            return ["refused to delete a reflog with no creation identity"]
        try:
            log_descriptor, current_reflog_identity, _content = _open_regular(
                reflog_location
            )
        except (OSError, WorktreeError) as error:
            return [f"failed to inspect branch reflog: {error}"]
        os.close(log_descriptor)
        if current_reflog_identity != created_reflog_identity:
            return ["refused to delete a replaced branch reflog"]
    if not authorized:
        return [
            f"refused to delete branch {branch_ref}: "
            "no creation token-bound rollback claim exists"
        ]
    receipt.update(
        rollback_ref=str(ref_path.path),
        rollback_packed_ref=str(ctx.common_dir / "packed-refs"),
    )
    lock_phase = (
        phase
        if phase in {ROLLBACK_REF_DETACHED, ROLLBACK_REF_RESTORING}
        else ROLLBACK_REF_CLAIMED
    )
    journal_lock_intent(
        lifecycle_id,
        receipt,
        "rollback_ref_lock",
        lock_path,
        ref_temporary,
        phase=lock_phase,
    )
    journal_lock_intent(
        lifecycle_id,
        receipt,
        "rollback_packed_lock",
        packed_lock_path,
        packed_temporary,
        phase=lock_phase,
    )
    packed_lock, residue = acquire_journaled_lock(
        lifecycle_id,
        receipt,
        "rollback_packed_lock",
        packed_lock_path,
        packed_temporary,
        token,
        "packed refs lock",
    )
    if residue or packed_lock is None:
        return residue
    ref_lock: HeldPathLock | None = None
    ref_unlinked = False
    try:
        ref_lock, residue = acquire_journaled_lock(
            lifecycle_id,
            receipt,
            "rollback_ref_lock",
            lock_path,
            ref_temporary,
            token,
            "branch ref lock",
        )
        if residue or ref_lock is None:
            return residue
        if _packed_ref_present(ctx, branch_ref):
            return [
                f"refused to delete packed branch {branch_ref}: "
                "a packed representation exists"
            ]

        if not location_exists(ref_path):
            if (
                receipt.get("branch_allocation_state") in {"prepared", "committed"}
                and not ref_claim.exists()
                and not ref_claim.is_symlink()
                and receipt.get("phase") != ROLLBACK_REF_DETACHED
            ):
                return [
                    f"refused to accept missing branch {branch_ref}: "
                    "the exact detached ref claim is unavailable"
                ]
            if _branch_presence(ctx, branch_ref) != 1:
                return [
                    f"refused to delete non-loose branch {branch_ref}: "
                    "the loose ref is absent but Git still resolves the branch"
                ]
            owners = registered_branch_owners(ctx, branch_ref)
            if owners:
                ref_identity = recorded_identity(receipt, "rollback_ref")
                if ref_identity is None or not ref_claim.exists():
                    return [
                        f"refused to restore registered branch {branch_ref}: "
                        "the exact detached ref is unavailable"
                    ]
                return _restore_for_owners(
                    ref_claim,
                    ref_path,
                    ref_identity,
                    expected_sha,
                    lifecycle_id,
                    receipt,
                    branch_ref,
                    owners,
                )
            if ref_claim.exists() or ref_claim.is_symlink():
                ref_identity = recorded_identity(receipt, "rollback_ref")
                if ref_identity is None:
                    return ["rollback ref claim has no exact identity"]
                receipt.update(phase=ROLLBACK_REF_DETACHED, last_reason=None)
                write_lifecycle(lifecycle_id, receipt)
                try:
                    remove_detached_file(ref_claim, ref_identity)
                except (OSError, WorktreeError) as error:
                    return [f"failed to remove detached branch ref: {error}"]
                ref_unlinked = True
            residue = _remove_recorded_reflog(
                ctx, token, receipt, reflog_location
            )
            if residue:
                return residue
        else:
            descriptor, ref_identity, content = _open_regular(ref_path)
            try:
                recorded = recorded_identity(receipt, "rollback_ref")
                if content != f"{expected_sha}\n".encode():
                    observed = content.decode(errors="replace").strip()
                    return [
                        f"refused to delete moved branch {branch_ref}: "
                        f"expected {expected_sha}, found {observed}"
                    ]
                if recorded is not None and ref_identity != recorded:
                    return [f"refused to delete replaced branch {branch_ref}"]
                receipt.update(
                    rollback_ref_device=ref_identity[0],
                    rollback_ref_inode=ref_identity[1],
                    rollback_ref_claim=str(ref_claim),
                )
                if reflog_location is not None and location_exists(
                    reflog_location
                ):
                    log_descriptor, log_identity, _log_content = _open_regular(
                        reflog_location
                    )
                    os.close(log_descriptor)
                    created_log_identity = recorded_identity(
                        receipt, "branch_reflog"
                    )
                    if log_identity != created_log_identity:
                        return ["refused to delete a replaced branch reflog"]
                    receipt.update(
                        rollback_reflog=str(reflog_location.path),
                        rollback_reflog_device=log_identity[0],
                        rollback_reflog_inode=log_identity[1],
                        rollback_reflog_claim=str(
                            private_lock_path(ctx.common_dir, token, "reflog")
                        ),
                    )
                write_lifecycle(lifecycle_id, receipt)
                owners = registered_branch_owners(ctx, branch_ref)
                if owners:
                    if (
                        receipt.get("phase") == ROLLBACK_REF_RESTORING
                        and ref_claim.exists()
                    ):
                        return _restore_for_owners(
                            ref_claim,
                            ref_path,
                            ref_identity,
                            expected_sha,
                            lifecycle_id,
                            receipt,
                            branch_ref,
                            owners,
                        )
                    return [
                        f"refused to delete registered branch {branch_ref}: "
                        f"owned by worktree {owners[0]!r}"
                    ]
                current = location_lstat(ref_path)
                if (current.st_dev, current.st_ino) != ref_identity:
                    return [f"refused to delete replaced branch {branch_ref}"]
                detach_exact_file(ref_path, ref_claim, ref_identity)
                ref_unlinked = True
            finally:
                os.close(descriptor)
            owners = registered_branch_owners(ctx, branch_ref)
            if owners:
                residue = _restore_for_owners(
                    ref_claim,
                    ref_path,
                    ref_identity,
                    expected_sha,
                    lifecycle_id,
                    receipt,
                    branch_ref,
                    owners,
                )
                ref_unlinked = False
                return residue
            try:
                receipt.update(phase=ROLLBACK_REF_DETACHED, last_reason=None)
                write_lifecycle(lifecycle_id, receipt)
                remove_detached_file(ref_claim, ref_identity)
            except (OSError, WorktreeError) as error:
                return [f"failed to remove detached branch ref: {error}"]
            residue = _remove_recorded_reflog(
                ctx, token, receipt, reflog_location
            )
            if residue:
                return residue

        if _branch_presence(ctx, branch_ref) != 1:
            return [
                f"failed to verify deletion of branch {branch_ref}: "
                "Git still resolves the ref"
            ]

        receipt.update(phase=ROLLBACK_REF_REMOVED, last_reason=None)
        write_lifecycle(lifecycle_id, receipt)
        ref_lock.release()
        packed_lock.release()
        return []
    except (OSError, WorktreeError) as error:
        return [f"failed to delete owned branch {branch_ref}: {error}"]
    finally:
        if ref_lock is not None:
            if ref_lock.owned and not ref_unlinked:
                try:
                    ref_lock.release()
                except OSError:
                    pass
            ref_lock.close()
        if packed_lock.owned and not ref_unlinked:
            try:
                packed_lock.release()
            except OSError:
                pass
        packed_lock.close()
