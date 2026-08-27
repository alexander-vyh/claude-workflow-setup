"""One receipt-backed, replayable local worktree finish transaction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from escapement_worktree_cleanup import final_local_preserve_reason, with_safe_removal
from escapement_worktree_git import (
    RepositoryContext,
    WorktreeError,
    git,
    repository_transaction_lock,
    resolve_default_source,
    resolve_repository,
    worktree_records,
)
from escapement_worktree_root import (
    synchronize_resolved_default,
    unresolved_root_sync,
)
from escapement_worktree_registry import (
    LifecycleEntry,
    delete_lifecycle,
    lifecycle_lock,
    load_lifecycle,
    write_lifecycle,
)


def _value(entry: LifecycleEntry) -> dict[str, Any]:
    value = json.loads(entry.raw)
    if not isinstance(value, dict):
        raise WorktreeError("lifecycle receipt is malformed")
    return value


def _store(entry: LifecycleEntry, **changes: object) -> LifecycleEntry:
    value = _value(entry)
    value.update(changes)
    write_lifecycle(entry.lifecycle_id, value)
    return load_lifecycle(entry.lifecycle_id)


def _registered(ctx: RepositoryContext, worktree: Path) -> bool:
    target = worktree.resolve(strict=False)
    return any(
        record.get("worktree")
        and Path(record["worktree"]).resolve(strict=False) == target
        for record in worktree_records(ctx)
    )


def _symbolic_ref_target(ctx: RepositoryContext, ref: str) -> str | None:
    result = git(ctx, "symbolic-ref", "--quiet", ref, check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1:
        return None
    raise WorktreeError("approved local ref cannot be inspected")


def _raw_ref_exists(ctx: RepositoryContext, ref: str) -> bool:
    result = git(ctx, "for-each-ref", "--format=%(refname)", ref, check=False)
    if result.returncode:
        raise WorktreeError("approved local ref cannot be inspected")
    return ref in result.stdout.splitlines()


def _pending(entry: LifecycleEntry, reason: str) -> dict[str, str]:
    _store(
        entry,
        phase="requested",
        finish_requested=True,
        approved_head_sha=None,
        last_reason=reason,
    )
    return {"lifecycle_id": entry.lifecycle_id, "reason": reason, "status": "pending"}


def _completed_with_root_sync(
    ctx: RepositoryContext, lifecycle_id: str
) -> dict[str, str]:
    try:
        root_sync = synchronize_resolved_default(ctx, resolve_default_source(ctx))
    except (OSError, WorktreeError):
        root_sync = unresolved_root_sync(ctx, "remote-resolution-failed")
    return {
        "lifecycle_id": lifecycle_id,
        "reason": "removed",
        "root_sync_reason": root_sync.reason,
        "root_sync_status": root_sync.status,
        "status": "completed",
    }


def _resume_after_removal(
    ctx: RepositoryContext, entry: LifecycleEntry, approved_sha: str
) -> dict[str, str]:
    if entry.worktree.exists() or _registered(ctx, entry.worktree):
        raise WorktreeError("removed worktree postcondition is unresolved")
    entry = _store(entry, phase="worktree_removed")
    if _symbolic_ref_target(ctx, entry.branch_ref) is not None:
        _store(
            entry,
            phase="worktree_removed",
            finish_requested=True,
            approved_head_sha=approved_sha,
            last_reason="branch-ref-symbolic",
        )
        return {
            "lifecycle_id": entry.lifecycle_id,
            "reason": "branch-ref-symbolic",
            "status": "pending",
        }
    current = git(
        ctx,
        "rev-parse",
        "--verify",
        f"{entry.branch_ref}^{{commit}}",
        check=False,
    )
    if current.returncode == 0:
        observed = current.stdout.strip()
        if observed != approved_sha:
            _store(
                entry,
                phase="worktree_removed",
                finish_requested=True,
                approved_head_sha=approved_sha,
                last_reason="branch-tip-moved",
            )
            return {
                "lifecycle_id": entry.lifecycle_id,
                "reason": "branch-tip-moved",
                "status": "pending",
            }
        deleted = git(
            ctx,
            "update-ref",
            "--no-deref",
            "-d",
            entry.branch_ref,
            approved_sha,
            check=False,
        )
        if deleted.returncode:
            raise WorktreeError("approved local ref could not be deleted")
    elif _raw_ref_exists(ctx, entry.branch_ref):
        raise WorktreeError("approved local ref cannot be inspected")
    if _raw_ref_exists(ctx, entry.branch_ref):
        raise WorktreeError("approved local ref still exists")
    entry = _store(entry, phase="ref_deleted")
    if entry.worktree.exists() or _registered(ctx, entry.worktree):
        raise WorktreeError("final worktree absence could not be verified")
    delete_lifecycle(entry.lifecycle_id)
    return _completed_with_root_sync(ctx, entry.lifecycle_id)


def _remove(entry: LifecycleEntry, decision: dict[str, Any]) -> dict[str, str]:
    ctx = resolve_repository(entry.repository)
    approved_sha = str(decision["candidate_sha"])
    entry = _store(
        entry,
        phase="approved",
        finish_requested=True,
        approved_head_sha=approved_sha,
        last_reason=None,
    )
    reason, _degraded = final_local_preserve_reason(ctx, entry, approved_sha)
    if reason:
        return _pending(entry, reason)
    removed = git(ctx, "worktree", "remove", str(entry.worktree), check=False)
    if removed.returncode:
        if not entry.worktree.exists() and not _registered(ctx, entry.worktree):
            return _resume_after_removal(ctx, entry, approved_sha)
        _store(entry, phase="approved", last_reason="worktree-removal-refused")
        return {
            "lifecycle_id": entry.lifecycle_id,
            "reason": "worktree-removal-refused",
            "status": "pending",
        }
    return _resume_after_removal(ctx, entry, approved_sha)


def finish_lifecycle(lifecycle_id: str) -> dict[str, str]:
    with lifecycle_lock(lifecycle_id):
        entry = load_lifecycle(lifecycle_id)
        value = _value(entry)
        phase = value.get("phase", "created")
        approved = value.get("approved_head_sha")
        if phase in {"approved", "worktree_removed", "ref_deleted"} and isinstance(approved, str):
            ctx = resolve_repository(entry.repository)
            if not entry.worktree.exists() and not _registered(ctx, entry.worktree):
                with repository_transaction_lock(ctx):
                    return _resume_after_removal(ctx, entry, approved)
        entry = _store(
            entry,
            phase="requested",
            finish_requested=True,
            last_reason=value.get("last_reason"),
        )
        result = with_safe_removal(entry.lifecycle_id, lambda decision: _remove(entry, decision))
        if isinstance(result, dict) and result.get("disposition") == "preserve":
            return _pending(entry, str(result["reason"]))
        if not isinstance(result, dict) or result.get("status") not in {"pending", "completed"}:
            raise WorktreeError("finish transaction returned an invalid result")
        return result
