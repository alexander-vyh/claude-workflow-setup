"""Lock-held, read-only eligibility decision for lifecycle cleanup."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

from escapement_worktree_activity import active_reason
from escapement_worktree_github import SemanticConflict, landing_proof
from escapement_worktree_git import (
    RepositoryContext,
    WorktreeError,
    git,
    repository_transaction_lock,
    resolve_repository,
    worktree_records,
)
from escapement_worktree_registry import LifecycleEntry, load_lifecycle, registry_root

_HARNESS_BIN = Path(__file__).resolve().parents[1] / "harness" / "bin"
if str(_HARNESS_BIN) not in sys.path:
    sys.path.insert(0, str(_HARNESS_BIN))
import repo_outcome  # type: ignore  # noqa: E402

T = TypeVar("T")


def _preserve(base: dict[str, Any], reason: str, *, degraded: bool = False) -> dict[str, Any]:
    return {
        **base,
        "disposition": "preserve",
        "health": "degraded" if degraded else "healthy",
        "reason": reason,
    }


def github_repository(url: str) -> str:
    value = url.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    elif value.startswith("ssh://git@github.com/"):
        path = value.removeprefix("ssh://git@github.com/")
    else:
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            raise SemanticConflict("origin-is-not-github")
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if len(path.split("/")) != 2 or any(not part for part in path.split("/")):
        raise SemanticConflict("origin-repository-identity-invalid")
    return path


def _registration(ctx: RepositoryContext, entry: LifecycleEntry) -> dict[str, str]:
    matches = []
    for record in worktree_records(ctx):
        raw_path = record.get("worktree")
        if not raw_path:
            continue
        try:
            registered = Path(raw_path).resolve(strict=True)
        except OSError:
            continue
        if registered == entry.worktree.resolve(strict=True):
            matches.append(record)
    if len(matches) != 1:
        raise SemanticConflict("worktree-registration-mismatch")
    return matches[0]


def _local_identity(
    ctx: RepositoryContext, entry: LifecycleEntry
) -> tuple[dict[str, Any], str, dict[str, str]]:
    if entry.repository.resolve(strict=True) != ctx.primary:
        raise SemanticConflict("repository-identity-mismatch")
    if entry.common_directory.resolve(strict=True) != ctx.common_dir:
        raise SemanticConflict("common-directory-mismatch")
    if entry.worktree.is_symlink() or not entry.worktree.is_dir():
        raise SemanticConflict("worktree-identity-mismatch")
    owned_root = ctx.primary / ".worktrees"
    if owned_root.is_symlink() or not owned_root.is_dir():
        raise SemanticConflict("worktree-identity-mismatch")
    if (
        entry.worktree.resolve(strict=True).parent != owned_root.resolve(strict=True)
        or entry.worktree.name != entry.lifecycle_id
    ):
        raise SemanticConflict("worktree-identity-mismatch")
    record = _registration(ctx, entry)
    # Read the configured identity rather than ``remote get-url``: Git applies
    # url.*.insteadOf rewriting to the latter, which is transport routing, not
    # repository identity.
    origin_url = git(ctx, "config", "--get", "remote.origin.url").stdout.strip()
    repository = github_repository(origin_url)
    if entry.origin != f"github.com/{repository}":
        raise SemanticConflict("origin-identity-mismatch")
    base = {
        "branch_ref": entry.branch_ref,
        "candidate_sha": entry.source_sha,
        "common_directory": str(ctx.common_dir),
        "lifecycle_id": entry.lifecycle_id,
        "repository": repository,
        "worktree": str(entry.worktree.resolve(strict=True)),
    }
    return base, repository, record


def _ref_and_worktree_state(
    ctx: RepositoryContext, entry: LifecycleEntry, record: dict[str, str]
) -> tuple[str | None, str]:
    branch = git(
        ctx,
        "rev-parse",
        "--verify",
        f"{entry.branch_ref}^{{commit}}",
        check=False,
    )
    if branch.returncode:
        return "branch-tip-moved", entry.source_sha
    branch_sha = branch.stdout.strip()
    head = git(
        entry.worktree, "rev-parse", "--verify", "HEAD^{commit}", check=False
    )
    if head.returncode:
        return "worktree-head-mismatch", branch_sha
    head_sha = head.stdout.strip()
    if branch_sha != head_sha:
        reason = (
            "branch-tip-moved"
            if head_sha == entry.source_sha and branch_sha != entry.source_sha
            else "worktree-head-mismatch"
        )
        return reason, branch_sha
    top = git(entry.worktree, "rev-parse", "--show-toplevel", check=False)
    common = git(
        entry.worktree,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
        check=False,
    )
    if top.returncode or common.returncode:
        raise WorktreeError("candidate Git identity cannot be inspected")
    if Path(top.stdout.strip()).resolve(strict=True) != entry.worktree.resolve(strict=True):
        return "worktree-identity-mismatch", branch_sha
    if Path(common.stdout.strip()).resolve(strict=True) != ctx.common_dir:
        return "common-directory-mismatch", branch_sha
    if record.get("branch") != entry.branch_ref or record.get("HEAD") != branch_sha:
        return "worktree-registration-mismatch", branch_sha
    if "locked" in record:
        return "worktree-locked", branch_sha
    return None, branch_sha


def _status_reason(repo: Path) -> str | None:
    status = git(
        repo,
        "status",
        "--porcelain=v2",
        "-z",
        "--ignored=matching",
        "--untracked-files=all",
        "--ignore-submodules=none",
    ).stdout
    for record in (item for item in status.split("\0") if item):
        if record.startswith("! "):
            return "ignored-content"
        if record.startswith("? "):
            return "untracked-content"
        if record.startswith(("1 ", "2 ", "u ")):
            fields = record.split(" ", 2)
            xy = fields[1] if len(fields) > 1 else ""
            if len(xy) != 2:
                raise WorktreeError("Git status returned a malformed tracked record")
            if xy[0] != "." and xy[1] != ".":
                return "tracked-content"
            if xy[0] != ".":
                return "staged-content"
            if xy[1] != ".":
                return "unstaged-content"
    return None


def _submodule_reason(worktree: Path) -> str | None:
    result = git(worktree, "submodule", "status", "--recursive", check=False)
    if result.returncode:
        raise WorktreeError("initialized submodules cannot be inspected")
    for line in result.stdout.splitlines():
        if not line or line[0] == "-":
            continue
        parts = line[1:].split()
        if len(parts) < 2:
            raise WorktreeError("Git submodule status is malformed")
        submodule = (worktree / parts[1]).resolve(strict=True)
        if _status_reason(submodule) is not None:
            return "submodule-content"
    return None


def final_local_preserve_reason(
    ctx: RepositoryContext, entry: LifecycleEntry, expected_sha: str
) -> tuple[str | None, bool]:
    """Recheck volatile local inputs immediately before destructive work."""
    try:
        record = _registration(ctx, entry)
        reason, current_sha = _ref_and_worktree_state(ctx, entry, record)
        if reason or current_sha != expected_sha:
            return reason or "branch-tip-moved", False
        content = _submodule_reason(entry.worktree) or _status_reason(entry.worktree)
        if content:
            return content, False
        activity = active_reason(entry.worktree, registry_root().parent)
        if activity:
            return activity, False
        return None, False
    except (OSError, WorktreeError):
        return "local-inspection-failed", True


def _decision_under_lock(ctx: RepositoryContext, entry: LifecycleEntry) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch_ref": entry.branch_ref,
        "candidate_sha": entry.source_sha,
        "common_directory": str(ctx.common_dir),
        "lifecycle_id": entry.lifecycle_id,
        "repository": entry.origin.removeprefix("github.com/"),
        "worktree": str(entry.worktree),
    }
    try:
        base, repository, record = _local_identity(ctx, entry)
        local_reason, candidate_sha = _ref_and_worktree_state(ctx, entry, record)
        base["candidate_sha"] = candidate_sha
        if local_reason:
            return _preserve(base, local_reason)
        content_reason = _submodule_reason(entry.worktree) or _status_reason(entry.worktree)
        if content_reason:
            return _preserve(base, content_reason)
        try:
            liveness = active_reason(entry.worktree, registry_root().parent)
        except WorktreeError:
            return _preserve(base, "activity-inspection-failed", degraded=True)
        if liveness:
            return _preserve(base, liveness)
        try:
            proof = landing_proof(ctx, repository, candidate_sha, entry.branch_ref)
        except SemanticConflict as conflict:
            return _preserve(base, conflict.reason)
        except WorktreeError:
            return _preserve(base, "github-inspection-failed", degraded=True)
        if entry.branch_ref == f"refs/heads/{proof.default_branch}":
            return _preserve(base, "protected-or-default-ref")
        policy = repo_outcome.resolve_at_ref(ctx.primary, proof.default_sha)
        if policy.source == "default-absent":
            return _preserve(base, "missing-landing-policy")
        if policy.source == "default-invalid":
            return _preserve(base, "landing-policy-does-not-authorize-merge")
        if policy.source != "declared-ref":
            return _preserve(base, "landing-policy-inspection-failed", degraded=True)
        if not repo_outcome.authorizes_merged_outcome(policy):
            return _preserve(base, "landing-policy-does-not-authorize-merge")
        # Remote proof can take seconds. Re-read every volatile local input at
        # the mutation boundary; the cooperative lock alone cannot stop files,
        # processes, or ordinary Git commands outside Escapement.
        final_reason, degraded = final_local_preserve_reason(ctx, entry, candidate_sha)
        if final_reason:
            return _preserve(base, final_reason, degraded=degraded)
        return {
            **base,
            "default_branch": proof.default_branch,
            "default_sha": proof.default_sha,
            "disposition": "eligible",
            "health": "healthy",
            "merge_result_sha": proof.merge_result_sha,
            "reason": "exact-head-merged-to-live-default",
        }
    except SemanticConflict as conflict:
        return _preserve(base, conflict.reason)
    except (OSError, WorktreeError):
        return _preserve(base, "local-inspection-failed", degraded=True)


def with_safe_removal(lifecycle_id: str, continuation: Callable[[dict[str, Any]], T]) -> T | dict[str, Any]:
    """Inspect and, only if eligible, call ``continuation`` under the same lock."""
    selected = load_lifecycle(lifecycle_id)
    ctx = resolve_repository(selected.repository)
    with repository_transaction_lock(ctx):
        entry = load_lifecycle(lifecycle_id, expected_raw=selected.raw)
        decision = _decision_under_lock(ctx, entry)
        if decision["disposition"] == "eligible":
            return continuation(decision)
        return decision
