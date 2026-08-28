"""Authenticated GitHub landing proof for cleanup decisions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import quote

from escapement_worktree_git import OBJECT_ID_RE, RepositoryContext, WorktreeError, git, run


@dataclass(frozen=True)
class LandingProof:
    repository: str
    repository_id: int
    default_branch: str
    default_sha: str
    merge_result_sha: str


@dataclass(frozen=True)
class SemanticConflict(Exception):
    reason: str


def _json_api(*args: str) -> object:
    result = run(("gh", "api", "--hostname", "github.com", *args), check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
        raise WorktreeError(f"GitHub API inspection failed: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise WorktreeError(f"GitHub API returned malformed JSON: {error}") from error


def _graphql(query: str, **variables: str) -> dict[str, object]:
    args = ["graphql", "-f", f"query={query}"]
    for name, value in variables.items():
        args.extend(("-F", f"{name}={value}"))
    value = _json_api(*args)
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise WorktreeError("GitHub GraphQL response is malformed")
    repository = value["data"].get("repository")
    if not isinstance(repository, dict):
        raise WorktreeError("GitHub GraphQL repository is missing")
    return repository


def _repository(expected: str) -> tuple[int, str, str]:
    value = _json_api(f"repos/{expected}")
    if not isinstance(value, dict):
        raise WorktreeError("GitHub repository response is malformed")
    repository = value.get("full_name") or value.get("nameWithOwner")
    repository_id = value.get("id") or value.get("databaseId")
    default_branch = value.get("default_branch")
    nested_default = value.get("defaultBranchRef")
    default_sha = None
    if isinstance(nested_default, dict):
        default_branch = nested_default.get("name") or default_branch
        target = nested_default.get("target")
        if isinstance(target, dict):
            default_sha = target.get("oid")
    if default_sha is None and isinstance(default_branch, str):
        commit = _json_api(f"repos/{expected}/commits/{quote(default_branch, safe='')}")
        if isinstance(commit, dict):
            default_sha = commit.get("sha")
            nested = commit.get("defaultBranchRef")
            if default_sha is None and isinstance(nested, dict):
                target = nested.get("target")
                if isinstance(target, dict):
                    default_sha = target.get("oid")
    if repository != expected:
        raise SemanticConflict("authenticated-repository-mismatch")
    if not isinstance(repository_id, int) or not isinstance(default_branch, str):
        raise WorktreeError("GitHub repository identity is incomplete")
    if not isinstance(default_sha, str) or not OBJECT_ID_RE.fullmatch(default_sha):
        raise WorktreeError("GitHub live default SHA is missing")
    return repository_id, default_branch, default_sha.lower()


def _pull(expected: str, candidate_sha: str) -> dict[str, object]:
    owner, name = expected.split("/", 1)
    query = """
query($owner:String!,$name:String!,$oid:String!,$after:String) {
  repository(owner:$owner,name:$name) {
    object(expression:$oid) { ... on Commit {
      oid
      associatedPullRequests(first:100,after:$after) {
        nodes { number state merged mergedAt baseRefName headRefName headRefOid
          mergeCommit { oid }
          headRepository { id databaseId nameWithOwner }
        }
        pageInfo { hasNextPage endCursor }
      }
    } }
  }
}
"""
    pulls: list[dict[str, object]] = []
    cursor: str | None = None
    for _page in range(100):
        variables = {"owner": owner, "name": name, "oid": candidate_sha}
        if cursor is not None:
            variables["after"] = cursor
        repository = _graphql(query, **variables)
        commit = repository.get("object")
        if not isinstance(commit, dict) or commit.get("oid") != candidate_sha:
            raise WorktreeError("GitHub exact candidate commit is missing")
        connection = commit.get("associatedPullRequests")
        if not isinstance(connection, dict) or not isinstance(connection.get("nodes"), list):
            raise WorktreeError("GitHub associated pull response is malformed")
        for pull in connection["nodes"]:
            if not isinstance(pull, dict):
                raise WorktreeError("GitHub associated pull record is malformed")
            pulls.append(pull)
        page_info = connection.get("pageInfo")
        if not isinstance(page_info, dict):
            raise WorktreeError("GitHub associated pull pagination is missing")
        if page_info.get("hasNextPage") is not True:
            break
        cursor = page_info.get("endCursor")
        if not isinstance(cursor, str) or not cursor:
            raise WorktreeError("GitHub associated pull pagination cursor is missing")
    else:
        raise WorktreeError("GitHub associated pull pagination exceeded its bound")
    exact = [pull for pull in pulls if ((pull.get("head") or {}).get("sha") if isinstance(pull.get("head"), dict) else pull.get("headRefOid")) == candidate_sha]
    if not exact:
        raise SemanticConflict("exact-head-not-merged")
    merged = [pull for pull in exact if pull.get("merged") is True and pull.get("state") in {"MERGED", "closed"}]
    if not merged:
        raise SemanticConflict("pull-request-not-merged")
    if len(merged) != 1:
        raise WorktreeError("GitHub exact-head pull evidence is ambiguous")
    return merged[0]


def _field(pull: dict[str, object], rest: str, graphql: str) -> object:
    nested = pull.get(rest)
    if isinstance(nested, dict):
        return nested.get("ref" if rest in {"base", "head"} else "sha")
    return pull.get(graphql)


def _validate_pull(
    pull: dict[str, object],
    *,
    expected: str,
    repository_id: int,
    default_branch: str,
    branch: str,
) -> str:
    base = _field(pull, "base", "baseRefName")
    if base != default_branch:
        raise SemanticConflict("pull-request-base-is-not-live-default")
    head_name = _field(pull, "head", "headRefName")
    if head_name != branch:
        raise SemanticConflict("pull-request-head-branch-mismatch")
    head_repository = pull.get("headRepository")
    if not isinstance(head_repository, dict):
        raise WorktreeError("pull head repository identity is incomplete")
    head_repository_id = head_repository.get("databaseId", head_repository.get("id"))
    if head_repository_id != repository_id or head_repository.get("nameWithOwner") != expected:
        raise SemanticConflict("pull-head-repository-mismatch")
    merge_commit = pull.get("mergeCommit")
    merge_sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else pull.get("merge_commit_sha")
    if not isinstance(merge_sha, str) or not OBJECT_ID_RE.fullmatch(merge_sha):
        raise WorktreeError("pull merge result SHA is missing")
    return merge_sha.lower()


def _open_branch_reuse(expected: str, branch: str) -> bool:
    owner, name = expected.split("/", 1)
    query = """
query($owner:String!,$name:String!,$after:String) {
  repository(owner:$owner,name:$name) {
    pullRequests(first:100,after:$after,states:OPEN) {
      nodes { number state headRefName headRefOid
        headRepository { id databaseId nameWithOwner }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
    cursor: str | None = None
    for _page in range(100):
        variables = {"owner": owner, "name": name}
        if cursor is not None:
            variables["after"] = cursor
        repository = _graphql(query, **variables)
        connection = repository.get("pullRequests")
        if not isinstance(connection, dict) or not isinstance(connection.get("nodes"), list):
            raise WorktreeError("GitHub open pull response is malformed")
        for pull in connection["nodes"]:
            if not isinstance(pull, dict):
                raise WorktreeError("GitHub open pull record is malformed")
            if pull.get("headRefName") == branch and pull.get("state") in {"OPEN", "open"}:
                return True
        page_info = connection.get("pageInfo")
        if not isinstance(page_info, dict):
            raise WorktreeError("GitHub open pull pagination is missing")
        if page_info.get("hasNextPage") is not True:
            return False
        cursor = page_info.get("endCursor")
        if not isinstance(cursor, str) or not cursor:
            raise WorktreeError("GitHub open pull pagination cursor is missing")
    raise WorktreeError("GitHub open pull pagination exceeded its bound")


def _reachable(expected: str, merge_sha: str, default_sha: str) -> bool:
    value = _json_api(
        f"repos/{expected}/compare/{quote(merge_sha, safe='')}...{quote(default_sha, safe='')}"
    )
    if not isinstance(value, dict):
        raise WorktreeError("GitHub compare response is malformed")
    base = value.get("base_commit")
    if not isinstance(base, dict) or base.get("sha") != merge_sha:
        raise WorktreeError("GitHub compare response did not bind the merge result")
    status = value.get("status")
    if status not in {"ahead", "identical"}:
        return False

    merge_base = value.get("merge_base_commit")
    if not isinstance(merge_base, dict) or merge_base.get("sha") != merge_sha:
        raise WorktreeError("GitHub compare response did not bind the merge base")
    if "head_commit" in value:
        raise WorktreeError("GitHub compare response has an unexpected head")

    commits = value.get("commits")
    if not isinstance(commits, list):
        raise WorktreeError("GitHub compare response did not bind the default head")
    if any(
        not isinstance(commit, dict)
        or not isinstance(commit.get("sha"), str)
        or not OBJECT_ID_RE.fullmatch(commit["sha"])
        for commit in commits
    ):
        raise WorktreeError("GitHub compare response contained a malformed commit")

    ahead_by = value.get("ahead_by")
    behind_by = value.get("behind_by")
    total_commits = value.get("total_commits")
    if status == "identical":
        if (
            merge_sha != default_sha
            or commits
            or type(ahead_by) is not int
            or ahead_by != 0
            or type(behind_by) is not int
            or behind_by != 0
            or type(total_commits) is not int
            or total_commits != 0
        ):
            raise WorktreeError("GitHub compare response did not bind the default head")
        return True

    if merge_sha == default_sha:
        raise WorktreeError("GitHub compare response contradicted identical endpoints")
    if (
        type(ahead_by) is not int
        or ahead_by <= 0
        or type(behind_by) is not int
        or behind_by != 0
        or type(total_commits) is not int
        or total_commits != ahead_by
        or len(commits) != min(total_commits, 250)
    ):
        raise WorktreeError("GitHub ahead response has inconsistent counters")
    head = commits[-1] if commits else None
    if not isinstance(head, dict) or head["sha"] != default_sha:
        raise WorktreeError("GitHub compare response did not bind the default head")
    return True


def fetch_live_default(ctx: RepositoryContext, branch: str, expected_sha: str) -> bool:
    git(
        ctx,
        "fetch",
        "--no-tags",
        "origin",
        f"refs/heads/{branch}",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    actual = git(ctx, "rev-parse", "--verify", "FETCH_HEAD^{commit}").stdout.strip()
    return actual == expected_sha


def landing_proof(ctx: RepositoryContext, expected: str, candidate_sha: str, branch_ref: str) -> LandingProof:
    branch = branch_ref.removeprefix("refs/heads/")
    # ``gh api`` itself is an authenticated boundary; this explicit status probe
    # prevents credential-free transports from being mistaken for that boundary.
    auth = run(
        ("gh", "auth", "status", "--hostname", "github.com", "--active"),
        check=False,
    )
    if auth.returncode:
        raise WorktreeError("GitHub authentication is unavailable")
    for attempt in range(2):
        repository_id, default_branch, default_sha = _repository(expected)
        pull = _pull(expected, candidate_sha)
        merge_sha = _validate_pull(
            pull,
            expected=expected,
            repository_id=repository_id,
            default_branch=default_branch,
            branch=branch,
        )
        if _open_branch_reuse(expected, branch):
            raise SemanticConflict("open-pull-request-reuses-branch")
        if not _reachable(expected, merge_sha, default_sha):
            raise SemanticConflict("merge-result-not-reachable-from-live-default")
        if not fetch_live_default(ctx, default_branch, default_sha):
            if attempt:
                break
            continue
        final_id, final_branch, final_sha = _repository(expected)
        if (final_id, final_branch, final_sha) == (repository_id, default_branch, default_sha):
            return LandingProof(expected, repository_id, default_branch, default_sha, merge_sha)
        if attempt:
            break
    raise WorktreeError("GitHub live default changed during both inspection attempts")
