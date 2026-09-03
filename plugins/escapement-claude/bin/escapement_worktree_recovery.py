"""Receipt-backed rollback and recovery for interrupted worktree creation."""

from __future__ import annotations

import json
import os
from pathlib import Path

from escapement_worktree_git import (
    CreationIdentity,
    RepositoryContext,
    WorktreeError,
    capture_creation_identity,
    git,
    repository_transaction_lock,
    resolve_repository,
    target_owned_by_creation,
)
from escapement_worktree_rollback_fs import (
    path_identity,
    receipt_identity,
    remove_exact_rollback_claim,
    validate_admin_path,
)
from escapement_worktree_rollback_ref import (
    ROLLBACK_REF_CLAIMED,
    ROLLBACK_REF_DETACHED,
    ROLLBACK_REF_RESTORING,
    ROLLBACK_REF_REMOVED,
    delete_owned_branch,
)
from escapement_worktree_registry import (
    CREATION_PHASES,
    LifecycleEntryMissing,
    delete_lifecycle,
    lifecycle_lock,
    load_lifecycle,
    write_lifecycle,
)

ROLLBACK_CLAIMED = "rollback_claimed"
ROLLBACK_WORKTREE_REMOVED = "rollback_worktree_removed"


def _rollback_path(ctx: RepositoryContext, token: str) -> Path:
    return ctx.primary / ".worktrees" / f".escapement-rollback-{token}"


def _restore_unowned_claim(claimed: Path, target: Path) -> str:
    if target.exists() or target.is_symlink():
        return f"preserved unowned claimed worktree {claimed}; target is occupied"
    restored = git(
        claimed,
        "worktree",
        "move",
        str(claimed),
        str(target),
        check=False,
    )
    if restored.returncode:
        detail = restored.stderr.strip() or restored.stdout.strip()
        return f"preserved unowned claimed worktree {claimed}: {detail}"
    return f"preserved unowned target {target}: creation token does not match"


def _claim_worktree(
    ctx: RepositoryContext,
    lifecycle_id: str,
    target: Path,
    branch: str,
    expected_sha: str,
    token: str | None,
    receipt: dict[str, object],
    creation_identity: CreationIdentity | None,
) -> tuple[Path | None, Path | None, list[str]]:
    claimed = _rollback_path(ctx, str(receipt["creation_token"]))
    phase = receipt.get("phase")
    if phase in {
        ROLLBACK_WORKTREE_REMOVED,
        ROLLBACK_REF_CLAIMED,
        ROLLBACK_REF_DETACHED,
        ROLLBACK_REF_RESTORING,
        ROLLBACK_REF_REMOVED,
    }:
        return None, None, []

    if phase == ROLLBACK_CLAIMED:
        recorded = receipt.get("rollback_worktree")
        if recorded != str(claimed):
            return None, None, [
                "rollback claim path does not match the receipt token"
            ]
        recorded_admin = receipt.get("rollback_admin")
        if not isinstance(recorded_admin, str):
            return None, None, ["rollback claim has no administrative identity"]
        try:
            admin_dir = validate_admin_path(ctx, Path(recorded_admin))
        except WorktreeError as error:
            return None, None, [str(error)]
        if not claimed.exists() and not claimed.is_symlink():
            return None, admin_dir, []
    elif claimed.exists() or claimed.is_symlink():
        if token is None:
            return None, None, [f"preserved unowned claimed worktree {claimed}"]
    elif target.exists() or target.is_symlink():
        if token is None and creation_identity is None:
            return None, None, [
                f"preserved unowned target {target}: "
                "exact creation instance is unavailable"
            ]
        try:
            owned, reason = target_owned_by_creation(
                ctx,
                target,
                branch,
                expected_sha,
                creation_identity,
                token,
            )
        except WorktreeError as error:
            owned, reason = False, str(error)
        if not owned:
            return None, None, [f"preserved unowned target {target}: {reason}"]
        moved = git(
            ctx,
            "worktree",
            "move",
            str(target),
            str(claimed),
            check=False,
        )
        if moved.returncode:
            detail = moved.stderr.strip() or moved.stdout.strip()
            return None, None, [f"failed to claim worktree {target}: {detail}"]
    else:
        return None, None, []

    try:
        owned, reason = target_owned_by_creation(
            ctx,
            claimed,
            branch,
            expected_sha,
            creation_identity,
            token,
        )
    except WorktreeError as error:
        owned, reason = False, str(error)
    if not owned:
        return None, None, [_restore_unowned_claim(claimed, target), reason]

    try:
        observed_identity = capture_creation_identity(claimed)
    except WorktreeError as error:
        return None, None, [_restore_unowned_claim(claimed, target), str(error)]
    try:
        admin_dir = validate_admin_path(ctx, observed_identity.admin_dir)
        worktree_device, worktree_inode = path_identity(claimed)
    except (OSError, WorktreeError) as error:
        return None, None, [_restore_unowned_claim(claimed, target), str(error)]
    finally:
        os.close(observed_identity.descriptor)

    receipt.update(
        phase=ROLLBACK_CLAIMED,
        rollback_worktree=str(claimed),
        rollback_worktree_device=worktree_device,
        rollback_worktree_inode=worktree_inode,
        rollback_admin=str(admin_dir),
        rollback_admin_device=observed_identity.device,
        rollback_admin_inode=observed_identity.inode,
        last_reason=None,
    )
    write_lifecycle(lifecycle_id, receipt)
    return claimed, admin_dir, []


def _remove_claimed_worktree(
    ctx: RepositoryContext,
    lifecycle_id: str,
    claimed: Path | None,
    admin_dir: Path | None,
    receipt: dict[str, object],
) -> list[str]:
    if claimed is None and receipt.get("phase") != ROLLBACK_CLAIMED:
        receipt.update(phase=ROLLBACK_WORKTREE_REMOVED, last_reason=None)
        write_lifecycle(lifecycle_id, receipt)
        return []
    token = str(receipt["creation_token"])
    worktree_identity = receipt_identity(receipt, "rollback_worktree")
    admin_identity = receipt_identity(receipt, "rollback_admin")
    if worktree_identity is None or admin_identity is None or admin_dir is None:
        return ["rollback claim is missing an exact filesystem identity"]
    residue = remove_exact_rollback_claim(
        ctx,
        Path(str(receipt["rollback_worktree"])),
        admin_dir,
        token,
        worktree_identity,
        admin_identity,
    )
    if residue:
        return residue
    receipt.update(phase=ROLLBACK_WORKTREE_REMOVED, last_reason=None)
    write_lifecycle(lifecycle_id, receipt)
    return []


def recovery_reason(residue: list[str]) -> str:
    combined = "; ".join(residue)
    if "moved branch" in combined or "HEAD does not match" in combined:
        return "branch-tip-moved"
    if "creation" in combined and (
        "token" in combined or "instance" in combined or "claim" in combined
    ):
        return "creation-instance-mismatch"
    return "rollback-residue"


def abort_creation_locked(
    ctx: RepositoryContext,
    lifecycle_id: str,
    target: Path,
    branch: str,
    source_sha: str,
    receipt: dict[str, object],
    creation_identity: CreationIdentity | None,
    *,
    branch_created: bool = True,
    recovery: bool = False,
) -> list[str]:
    token = receipt.get("creation_token")
    ownership_token = (
        token
        if isinstance(token, str) and (recovery or creation_identity is not None)
        else None
    )
    phase = receipt.get("phase")
    rollback_authorized = (
        phase
        in {
            ROLLBACK_CLAIMED,
            ROLLBACK_WORKTREE_REMOVED,
            ROLLBACK_REF_CLAIMED,
            ROLLBACK_REF_DETACHED,
            ROLLBACK_REF_RESTORING,
            ROLLBACK_REF_REMOVED,
        }
        or (
            phase == "allocating"
            and receipt.get("branch_allocation_state") in {"prepared", "committed"}
        )
        or (branch_created and not recovery)
    )
    try:
        claimed, admin_dir, residue = _claim_worktree(
            ctx,
            lifecycle_id,
            target,
            branch,
            source_sha,
            ownership_token,
            receipt,
            creation_identity,
        )
        if not residue and phase not in {
            ROLLBACK_WORKTREE_REMOVED,
            ROLLBACK_REF_CLAIMED,
            ROLLBACK_REF_DETACHED,
            ROLLBACK_REF_RESTORING,
            ROLLBACK_REF_REMOVED,
        }:
            rollback_authorized = rollback_authorized or (
                claimed is not None or phase == ROLLBACK_CLAIMED
            )
            if rollback_authorized:
                residue = _remove_claimed_worktree(
                    ctx, lifecycle_id, claimed, admin_dir, receipt
                )
        if branch_created:
            branch_cleanup_safe = not residue or (
                not recovery and creation_identity is not None
                and receipt.get("phase") not in {
                    ROLLBACK_CLAIMED,
                    ROLLBACK_WORKTREE_REMOVED,
                    ROLLBACK_REF_CLAIMED,
                    ROLLBACK_REF_DETACHED,
                    ROLLBACK_REF_RESTORING,
                    ROLLBACK_REF_REMOVED,
                }
            )
            rollback_authorized = branch_cleanup_safe and (
                rollback_authorized
                or receipt.get("phase")
                in {
                    ROLLBACK_WORKTREE_REMOVED,
                    ROLLBACK_REF_CLAIMED,
                    ROLLBACK_REF_DETACHED,
                    ROLLBACK_REF_RESTORING,
                    ROLLBACK_REF_REMOVED,
                }
            )
            residue.extend(delete_owned_branch(
                ctx,
                lifecycle_id,
                branch,
                source_sha,
                str(receipt["creation_token"]),
                receipt,
                authorized=rollback_authorized,
            ))
    except (OSError, WorktreeError) as error:
        residue = [str(error)]

    if not residue:
        try:
            delete_lifecycle(lifecycle_id)
        except WorktreeError as error:
            residue.append(str(error))
    if residue:
        reason = recovery_reason(residue)
        receipt["last_reason"] = reason
        if receipt.get("phase") not in {
            ROLLBACK_CLAIMED,
            ROLLBACK_WORKTREE_REMOVED,
            ROLLBACK_REF_CLAIMED,
            ROLLBACK_REF_DETACHED,
            ROLLBACK_REF_RESTORING,
            ROLLBACK_REF_REMOVED,
        }:
            receipt["phase"] = "bootstrap_failed"
        try:
            write_lifecycle(lifecycle_id, receipt)
        except WorktreeError as error:
            residue.append(str(error))
    return residue


def creation_error(
    error: WorktreeError | OSError, residue: list[str]
) -> WorktreeError:
    failure = (
        error
        if isinstance(error, WorktreeError)
        else WorktreeError(f"worktree operation failed: {error}")
    )
    if residue:
        return WorktreeError(f"{failure}; rollback residue: {'; '.join(residue)}")
    return failure


def recover_lifecycle(lifecycle_id: str) -> dict[str, str]:
    with lifecycle_lock(lifecycle_id, blocking=False) as acquired:
        if not acquired:
            return {
                "lifecycle_id": lifecycle_id,
                "reason": "bootstrap-active",
                "status": "pending",
            }
        try:
            entry = load_lifecycle(lifecycle_id)
        except LifecycleEntryMissing:
            return {
                "lifecycle_id": lifecycle_id,
                "reason": "removed",
                "status": "completed",
            }
        if entry.phase not in CREATION_PHASES:
            return {
                "lifecycle_id": lifecycle_id,
                "reason": "creation-complete",
                "status": "pending",
            }
        ctx = resolve_repository(entry.repository)
        if ctx.common_dir != entry.common_directory:
            raise WorktreeError("lifecycle repository common directory changed")
        receipt = json.loads(entry.raw)
        with repository_transaction_lock(ctx):
            current = load_lifecycle(lifecycle_id, expected_raw=entry.raw)
            if current.phase not in CREATION_PHASES:
                return {
                    "lifecycle_id": lifecycle_id,
                    "reason": "creation-complete",
                    "status": "pending",
                }
            residue = abort_creation_locked(
                ctx,
                lifecycle_id,
                entry.worktree,
                entry.branch_ref.removeprefix("refs/heads/"),
                entry.source_sha,
                receipt,
                None,
                recovery=True,
            )
        if residue:
            return {
                "lifecycle_id": lifecycle_id,
                "reason": recovery_reason(residue),
                "status": "pending",
            }
        return {
            "lifecycle_id": lifecycle_id,
            "reason": "rolled-back",
            "status": "completed",
        }
