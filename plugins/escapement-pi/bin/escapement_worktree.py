#!/usr/bin/env python3
"""Create repository-owned Git worktrees as a verified transaction."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from escapement_worktree_bootstrap import (
    resolve_bootstrap_contract,
    run_bootstrap,
)
from escapement_worktree_cleanup import SemanticConflict, github_repository
from escapement_worktree_git import (
    CreationIdentity,
    RepositoryContext,
    ResolvedSource,
    WorktreeError,
    capture_creation_identity,
    git,
    registered_branch_owners,
    repository_transaction_lock,
    resolve_default_source,
    resolve_explicit_source,
    resolve_repository,
    run,
    target_owned_by_creation,
)
from escapement_worktree_finish import finish_lifecycle
from escapement_worktree_registry import (
    ensure_registry,
    lifecycle_exists,
    lifecycle_lock,
    write_lifecycle,
)
from escapement_worktree_root import (
    RootSyncResult,
    sync_primary_checkout,
    synchronize_resolved_default,
)

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BEADS_IDENTITY_FIELDS = ("project_id", "database", "beads_dir", "repo_root")


@dataclass(frozen=True)
class WorktreeRequest:
    repo: Path
    name: str
    branch: str
    source: str | None


@dataclass(frozen=True)
class CreationResult:
    repo: Path
    target: Path
    branch: str
    source_sha: str
    source_kind: Literal["remote-default", "explicit"]
    beads_verified: bool
    root_sync_status: str
    root_sync_reason: str


def validate_request(ctx: RepositoryContext, request: WorktreeRequest) -> Path:
    if not SAFE_NAME_RE.fullmatch(request.name) or request.name in {".", ".."}:
        raise WorktreeError(f"unsafe worktree name: {request.name!r}")

    worktrees_dir = ctx.primary / ".worktrees"
    if worktrees_dir.is_symlink():
        raise WorktreeError(
            f"worktree directory must not be a symlink: {worktrees_dir}"
        )
    if worktrees_dir.exists() and not worktrees_dir.is_dir():
        raise WorktreeError(f"worktree directory is not a directory: {worktrees_dir}")

    target = worktrees_dir / request.name
    if target.exists() or target.is_symlink():
        raise WorktreeError(f"worktree target already exists: {target}")

    branch_check = git(ctx, "check-ref-format", "--branch", request.branch, check=False)
    if branch_check.returncode or branch_check.stdout.strip() != request.branch:
        raise WorktreeError(f"invalid branch name: {request.branch!r}")
    branch_ref = f"refs/heads/{request.branch}"
    branch_presence = git(
        ctx,
        "show-ref",
        "--verify",
        "--quiet",
        branch_ref,
        check=False,
    )
    if branch_presence.returncode == 0:
        raise WorktreeError(f"branch already exists: {request.branch}")
    if branch_presence.returncode != 1:
        detail = branch_presence.stderr.strip() or branch_presence.stdout.strip()
        detail = detail or f"exit status {branch_presence.returncode}"
        raise WorktreeError(f"failed to inspect branch {branch_ref}: {detail}")

    relative_target = target.relative_to(ctx.primary)
    ignored = git(
        ctx,
        "check-ignore",
        "--quiet",
        "--no-index",
        str(relative_target),
        check=False,
    )
    if ignored.returncode != 0:
        raise WorktreeError(f"worktree target is not safely ignored: {target}")
    return target


def _primary_for_path(path: Path) -> Path:
    common_dir = Path(
        git(
            path,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    ).resolve()
    return common_dir.parent


def beads_context(path: Path) -> dict[str, str] | None:
    primary = _primary_for_path(path)
    if not (primary / ".beads").is_dir():
        return None
    result = run(("bd", "context", "--json"), cwd=path)
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise WorktreeError(f"bd returned malformed context JSON: {error}") from error
    if not isinstance(raw, dict):
        raise WorktreeError("bd context must be a JSON object")
    context: dict[str, str] = {}
    for field in BEADS_IDENTITY_FIELDS:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise WorktreeError(f"bd context is missing non-empty {field!r}")
        context[field] = value
    return context


def _worktree_records(porcelain: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for block in porcelain.strip().split("\n\n"):
        if not block:
            continue
        record: dict[str, str] = {}
        for line in block.splitlines():
            key, separator, value = line.partition(" ")
            record[key] = value if separator else ""
        records.append(record)
    return records


def verify_created_worktree(
    ctx: RepositoryContext,
    request: WorktreeRequest,
    target: Path,
    source: ResolvedSource,
    root_beads: dict[str, str] | None,
) -> bool:
    target_sha = git(target, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    if target_sha != source.sha:
        raise WorktreeError(
            f"created worktree HEAD mismatch: expected {source.sha}, found {target_sha}"
        )
    branch = git(target, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if branch != request.branch:
        raise WorktreeError(
            f"created worktree branch mismatch: expected {request.branch}, found {branch}"
        )
    common_dir = Path(
        git(
            target,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    ).resolve()
    if common_dir != ctx.common_dir:
        raise WorktreeError(
            f"created worktree belongs to unexpected common directory: {common_dir}"
        )

    try:
        expected_target = target.resolve(strict=True)
    except OSError as error:
        raise WorktreeError(
            f"failed to resolve created worktree target {target}: {error}"
        ) from error
    expected_branch = f"refs/heads/{request.branch}"
    records = _worktree_records(git(ctx, "worktree", "list", "--porcelain").stdout)
    if not any(
        record.get("worktree") == str(expected_target)
        and record.get("HEAD") == source.sha
        and record.get("branch") == expected_branch
        for record in records
    ):
        raise WorktreeError(f"created worktree is absent from Git registry: {target}")

    worktrees_dir = ctx.primary / ".worktrees"
    if worktrees_dir.is_symlink() or not worktrees_dir.is_dir():
        raise WorktreeError(
            f"worktree directory changed during creation: {worktrees_dir}"
        )
    try:
        expected_parent = worktrees_dir.resolve(strict=True)
    except OSError as error:
        raise WorktreeError(
            f"failed to resolve worktree target parent {worktrees_dir}: {error}"
        ) from error
    if target.is_symlink() or expected_target.parent != expected_parent:
        raise WorktreeError(f"created target escaped worktree directory: {target}")
    ignored = git(
        ctx,
        "check-ignore",
        "--quiet",
        "--no-index",
        str(target.relative_to(ctx.primary)),
        check=False,
    )
    if ignored.returncode != 0:
        raise WorktreeError(f"created target is no longer safely ignored: {target}")

    if root_beads is None:
        return False
    target_beads = beads_context(target)
    if target_beads is None or target_beads != root_beads:
        raise WorktreeError("created worktree Beads identity does not match primary")
    return True


def rollback_created_artifacts(
    ctx: RepositoryContext,
    target: Path,
    branch: str,
    expected_sha: str,
    creation_identity: CreationIdentity | None = None,
) -> list[str]:
    residue: list[str] = []
    if target.exists() or target.is_symlink():
        try:
            owned, reason = target_owned_by_creation(
                ctx,
                target,
                branch,
                expected_sha,
                creation_identity,
            )
        except WorktreeError as error:
            owned, reason = False, str(error)
        if owned:
            removed = git(
                ctx,
                "worktree",
                "remove",
                "--force",
                str(target),
                check=False,
            )
            if removed.returncode:
                detail = removed.stderr.strip() or removed.stdout.strip()
                residue.append(f"failed to remove worktree {target}: {detail}")
        else:
            residue.append(f"preserved unowned target {target}: {reason}")

    branch_ref = f"refs/heads/{branch}"
    try:
        current = git(
            ctx,
            "rev-parse",
            "--verify",
            f"{branch_ref}^{{commit}}",
            check=False,
        )
        if current.returncode != 0:
            presence = git(
                ctx,
                "show-ref",
                "--verify",
                "--quiet",
                branch_ref,
                check=False,
            )
            if presence.returncode == 1:
                return residue
            detail = current.stderr.strip() or current.stdout.strip()
            detail = detail or f"rev-parse exited {current.returncode}"
            if presence.returncode not in {0, 1}:
                presence_detail = presence.stderr.strip() or presence.stdout.strip()
                presence_detail = presence_detail or (
                    f"show-ref exited {presence.returncode}"
                )
                detail = f"{detail}; absence check failed: {presence_detail}"
            residue.append(f"failed to inspect branch {branch_ref}: {detail}")
            return residue
        current_sha = current.stdout.strip()
        if current_sha != expected_sha:
            residue.append(
                "refused to delete moved branch "
                f"{branch_ref}: expected {expected_sha}, found {current_sha}"
            )
        else:
            owners = registered_branch_owners(ctx, branch_ref)
            if owners:
                residue.append(
                    f"refused to delete registered branch {branch_ref}: "
                    f"owned by worktree {owners[0]!r}"
                )
                return residue
            deleted = git(
                ctx,
                "update-ref",
                "-d",
                branch_ref,
                expected_sha,
                check=False,
            )
            if deleted.returncode:
                detail = deleted.stderr.strip() or deleted.stdout.strip()
                residue.append(f"failed to delete branch {branch_ref}: {detail}")
    except WorktreeError as error:
        residue.append(f"failed to inspect branch {branch_ref}: {error}")
    return residue


def create_worktree(request: WorktreeRequest) -> CreationResult:
    ctx = resolve_repository(request.repo)
    ensure_registry()
    with lifecycle_lock(request.name), repository_transaction_lock(ctx):
        if lifecycle_exists(request.name):
            raise WorktreeError(f"lifecycle receipt already exists: {request.name}")
        target = validate_request(ctx, request)
        source = (
            resolve_explicit_source(ctx, request.source)
            if request.source is not None
            else resolve_default_source(ctx)
        )
        root_sync = (
            synchronize_resolved_default(ctx, source)
            if source.kind == "remote-default"
            else RootSyncResult(
                repo=ctx.primary,
                branch="",
                previous_sha="",
                target_sha="",
                status="ineligible",
                reason="explicit-source",
            )
        )
        bootstrap = resolve_bootstrap_contract(ctx, source)
        root_beads = beads_context(ctx.primary)
        branch_created = False
        creation_identity: CreationIdentity | None = None
        try:
            git(
                ctx,
                "update-ref",
                f"refs/heads/{request.branch}",
                source.sha,
                "",
            )
            branch_created = True
            git(
                ctx,
                "worktree",
                "add",
                str(target),
                request.branch,
            )
            creation_identity = capture_creation_identity(target)
            beads_verified = verify_created_worktree(
                ctx, request, target, source, root_beads
            )
            if bootstrap is not None:
                run_bootstrap(bootstrap, target)
                beads_verified = verify_created_worktree(
                    ctx, request, target, source, root_beads
                )
            origin_url = git(ctx, "config", "--get", "remote.origin.url").stdout.strip()
            try:
                origin = f"github.com/{github_repository(origin_url)}"
            except SemanticConflict:
                origin = origin_url
            write_lifecycle(
                request.name,
                {
                    "schema_version": 1,
                    "lifecycle_id": request.name,
                    "repository": str(ctx.primary),
                    "common_directory": str(ctx.common_dir),
                    "origin": origin,
                    "worktree": str(target.resolve(strict=True)),
                    "branch_ref": f"refs/heads/{request.branch}",
                    "source_sha": source.sha,
                    "phase": "created",
                    "finish_requested": False,
                    "approved_head_sha": None,
                    "last_reason": None,
                },
            )
        except (WorktreeError, OSError) as error:
            failure = (
                error
                if isinstance(error, WorktreeError)
                else WorktreeError(f"worktree operation failed: {error}")
            )
            receipt_retained = lifecycle_exists(request.name)
            if receipt_retained:
                raise WorktreeError(
                    f"{failure}; durable lifecycle receipt retained with worktree "
                    "for recovery"
                ) from None
            residue = (
                rollback_created_artifacts(
                    ctx,
                    target,
                    request.branch,
                    source.sha,
                    creation_identity,
                )
                if branch_created
                else []
            )
            if residue:
                raise WorktreeError(
                    f"{failure}; rollback residue: {'; '.join(residue)}"
                ) from None
            raise failure from None
        finally:
            if creation_identity is not None:
                os.close(creation_identity.descriptor)
        return CreationResult(
            repo=ctx.primary,
            target=target,
            branch=request.branch,
            source_sha=source.sha,
            source_kind=source.kind,
            beads_verified=beads_verified,
            root_sync_status=root_sync.status,
            root_sync_reason=root_sync.reason,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="escapement-worktree")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--repo", type=Path, required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--branch", required=True)
    create.add_argument("--source")
    finish = commands.add_parser("finish")
    finish.add_argument("--lifecycle-id", required=True)
    sync_root = commands.add_parser("sync-root")
    sync_root.add_argument("--repo", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "sync-root":
            root_result = sync_primary_checkout(args.repo)
            print(json.dumps(root_result.as_dict(), sort_keys=True))
            return 1 if root_result.status == "ineligible" else 0
        if args.command == "finish":
            print(json.dumps(finish_lifecycle(args.lifecycle_id), sort_keys=True))
            return 0
        result = create_worktree(
            WorktreeRequest(
                repo=args.repo,
                name=args.name,
                branch=args.branch,
                source=args.source,
            )
        )
    except WorktreeError as error:
        print(f"escapement-worktree: {error}", file=sys.stderr)
        return 1
    output = {
        "beads_status": ("verified" if result.beads_verified else "not applicable"),
        "beads_verified": result.beads_verified,
        "branch": result.branch,
        "repo": str(result.repo),
        "source": result.source_sha,
        "source_kind": result.source_kind,
        "source_sha": result.source_sha,
        "target": str(result.target),
        "root_sync_status": result.root_sync_status,
        "root_sync_reason": result.root_sync_reason,
    }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
