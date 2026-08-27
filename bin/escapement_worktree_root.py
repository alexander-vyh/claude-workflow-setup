"""Safe primary-checkout synchronization for Escapement repositories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from escapement_worktree_git import (
    RepositoryContext,
    ResolvedSource,
    WorktreeError,
    git,
    repository_transaction_lock,
    resolve_default_source,
    resolve_repository,
)

RootSyncStatus = Literal["synchronized", "up_to_date", "ineligible", "unresolved"]


@dataclass(frozen=True)
class RootSyncResult:
    repo: Path
    branch: str
    previous_sha: str
    target_sha: str
    status: RootSyncStatus
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "branch": self.branch,
            "previous_sha": self.previous_sha,
            "reason": self.reason,
            "repo": str(self.repo),
            "status": self.status,
            "target_sha": self.target_sha,
        }


def _ineligible(
    ctx: RepositoryContext,
    source: ResolvedSource,
    previous_sha: str,
    branch: str,
    reason: str,
) -> RootSyncResult:
    return RootSyncResult(
        repo=ctx.primary,
        branch=branch,
        previous_sha=previous_sha,
        target_sha=source.sha,
        status="ineligible",
        reason=reason,
    )


def synchronize_resolved_default(
    ctx: RepositoryContext, source: ResolvedSource
) -> RootSyncResult:
    """Advance one eligible primary checkout to an already verified remote SHA."""
    if source.kind != "remote-default" or not source.display_ref.startswith(
        "refs/remotes/origin/"
    ):
        raise WorktreeError("root synchronization requires the resolved remote default")
    default_branch = source.display_ref.removeprefix("refs/remotes/origin/")
    previous_sha = git(ctx, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    symbolic = git(ctx, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if symbolic.returncode == 1:
        return _ineligible(
            ctx, source, previous_sha, "", "primary-detached"
        )
    if symbolic.returncode:
        raise WorktreeError("primary checkout branch cannot be inspected")
    branch = symbolic.stdout.strip()
    if branch != default_branch:
        return _ineligible(
            ctx, source, previous_sha, branch, "primary-not-default"
        )
    status = git(
        ctx, "status", "--porcelain=v1", "--untracked-files=all"
    ).stdout
    if status:
        return _ineligible(ctx, source, previous_sha, branch, "primary-dirty")
    if previous_sha == source.sha:
        return RootSyncResult(
            repo=ctx.primary,
            branch=branch,
            previous_sha=previous_sha,
            target_sha=source.sha,
            status="up_to_date",
            reason="already-current",
        )
    ancestor = git(
        ctx, "merge-base", "--is-ancestor", previous_sha, source.sha, check=False
    )
    if ancestor.returncode == 1:
        return _ineligible(
            ctx, source, previous_sha, branch, "primary-diverged"
        )
    if ancestor.returncode:
        raise WorktreeError("primary checkout ancestry cannot be verified")

    git(ctx, "merge", "--ff-only", source.sha)
    observed_branch = git(
        ctx, "symbolic-ref", "--quiet", "--short", "HEAD"
    ).stdout.strip()
    observed_sha = git(ctx, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    observed_status = git(
        ctx, "status", "--porcelain=v1", "--untracked-files=all"
    ).stdout
    if (
        observed_branch != default_branch
        or observed_sha != source.sha
        or observed_status
    ):
        raise WorktreeError("primary checkout fast-forward postcondition failed")
    return RootSyncResult(
        repo=ctx.primary,
        branch=observed_branch,
        previous_sha=previous_sha,
        target_sha=source.sha,
        status="synchronized",
        reason="fast-forwarded",
    )


def sync_primary_checkout(repo: Path) -> RootSyncResult:
    """Resolve and synchronize a public primary-checkout request."""
    requested = repo.expanduser().resolve()
    bare = git(
        requested, "rev-parse", "--is-bare-repository", check=False
    )
    if bare.returncode == 0 and bare.stdout.strip() == "true":
        raise WorktreeError(f"repository is not a primary checkout: {requested}")
    ctx = resolve_repository(requested)
    with repository_transaction_lock(ctx):
        source = resolve_default_source(ctx)
        return synchronize_resolved_default(ctx, source)


def unresolved_root_sync(ctx: RepositoryContext, reason: str) -> RootSyncResult:
    """Represent a best-effort lifecycle attempt whose remote could not be resolved."""
    head = git(ctx, "rev-parse", "--verify", "HEAD^{commit}", check=False)
    symbolic = git(ctx, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return RootSyncResult(
        repo=ctx.primary,
        branch=symbolic.stdout.strip() if symbolic.returncode == 0 else "",
        previous_sha=head.stdout.strip() if head.returncode == 0 else "",
        target_sha="",
        status="unresolved",
        reason=reason,
    )
