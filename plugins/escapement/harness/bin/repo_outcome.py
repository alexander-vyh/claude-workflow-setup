#!/usr/bin/env python3
"""Reader for `.escapement/repo.json` — the per-project options manifest.

Resolves a repo's declared *intended outcome* and *auto-merge authorization*
(repo-outcome-authorization). This is the durable artifact the base Claude Code
system prompt defers to when it says "confirm before hard-to-reverse / outward-facing
actions — unless durably authorized." A committed declaration IS that authorization.

Fail-safe by construction: absent, malformed, or invalid declarations resolve to the
CONSERVATIVE default (intended_outcome=pr-opened, auto_merge_on_green=False). An
unconfigured repo therefore behaves exactly as today, and a missing or broken file is
NEVER treated as authorization to merge/deploy live (design anti-metric #2).

`.escapement/repo.json` schema (all keys optional; unknown keys ignored):
    {
      "intended_outcome": "committed" | "pr-opened" | "merged" | "merged-and-deployed",
      "auto_merge_on_green": true | false,
      "deploy": { ... informational, surfaced in the agent's report ... },
      "confirm_class": [ "db-migration", ... ]   # reserved metadata; not yet enforced
    }
"""
from __future__ import annotations

import json
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Optional

# Ordered ladder: how far "done" reaches. Index encodes the ordering.
INTENDED_OUTCOME_LADDER = ("committed", "pr-opened", "merged", "merged-and-deployed")

# Auto-merge is only meaningful at or above this rung.
_MERGE_MIN_INDEX = INTENDED_OUTCOME_LADDER.index("merged")

_DEFAULT_OUTCOME = "pr-opened"


@dataclass
class RepoOutcome:
    """Resolved per-project outcome authorization.

    `source` records provenance so callers can distinguish a real declaration from a
    fallback: "declared" | "declared-default-branch" | "default-absent" |
    "default-malformed" | "default-invalid".
    `warning` is set (non-None) only when we fell back from a present-but-broken file,
    so the caller can surface it instead of silently swallowing a misconfiguration.
    """

    intended_outcome: str
    auto_merge_on_green: bool
    deploy: Optional[dict]
    confirm_class: list
    source: str
    warning: Optional[str] = None


def _default(source: str, warning: Optional[str]) -> RepoOutcome:
    return RepoOutcome(
        intended_outcome=_DEFAULT_OUTCOME,
        auto_merge_on_green=False,
        deploy=None,
        confirm_class=[],
        source=source,
        warning=warning,
    )


def declaration_path(repo_root) -> pathlib.Path:
    return pathlib.Path(repo_root) / ".escapement" / "repo.json"


def _git(repo_root, *args: str) -> Optional[subprocess.CompletedProcess[str]]:
    """Run a bounded, argument-safe Git read; failure is never authorization."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            text=True,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _default_branch_declaration(repo_root) -> tuple[bool, Optional[str]]:
    """Read the committed policy from the repository's remote default branch.

    A linked worktree's checked-out policy can be intentionally stale while the
    repository's merged policy has changed. ``origin/HEAD`` is Git's durable
    default-branch identity and therefore wins over branch-local filesystem
    copies. ``False`` means no default branch is known locally, preserving the
    standalone/local-file behavior below. ``True, None`` is a known branch with
    no readable declaration and must fail closed rather than falling back to a
    potentially self-authorizing sibling worktree.
    """
    default_ref = _git(repo_root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if default_ref is None or default_ref.returncode != 0:
        return False, None

    ref = default_ref.stdout.strip()
    if not ref:
        return False, None

    declaration = _git(repo_root, "show", f"{ref}:.escapement/repo.json")
    if declaration is None or declaration.returncode != 0:
        return True, None
    return True, declaration.stdout


def _resolve_text(raw_text: Optional[str], *, declared_source: str) -> RepoOutcome:
    if raw_text is None:
        return _default("default-absent", None)

    try:
        raw = json.loads(raw_text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _default(
            "default-malformed",
            f".escapement/repo.json is unparseable ({exc}); using conservative "
            f"default (no auto-merge).",
        )

    if not isinstance(raw, dict):
        return _default(
            "default-malformed",
            ".escapement/repo.json must be a JSON object; using conservative default "
            "(no auto-merge).",
        )

    outcome = raw.get("intended_outcome", _DEFAULT_OUTCOME)
    if outcome not in INTENDED_OUTCOME_LADDER:
        return _default(
            "default-invalid",
            f".escapement/repo.json has invalid intended_outcome {outcome!r} "
            f"(expected one of {list(INTENDED_OUTCOME_LADDER)}); using conservative "
            f"default (no auto-merge).",
        )

    # auto_merge_on_green: only a real boolean True counts; anything else is False.
    auto_merge = raw.get("auto_merge_on_green", False)
    if not isinstance(auto_merge, bool):
        auto_merge = False

    deploy = raw.get("deploy")
    if deploy is not None and not isinstance(deploy, dict):
        deploy = None

    confirm_class = raw.get("confirm_class", [])
    if not isinstance(confirm_class, list):
        confirm_class = []

    return RepoOutcome(
        intended_outcome=outcome,
        auto_merge_on_green=auto_merge,
        deploy=deploy,
        confirm_class=confirm_class,
        source=declared_source,
        warning=None,
    )


def resolve(repo_root) -> RepoOutcome:
    """Resolve the repo's outcome authorization, fail-safe on every error path."""
    has_default_branch, default_declaration = _default_branch_declaration(repo_root)
    if has_default_branch:
        return _resolve_text(default_declaration, declared_source="declared-default-branch")

    path = declaration_path(repo_root)
    if not path.exists():
        return _default("default-absent", None)

    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _default(
            "default-malformed",
            f".escapement/repo.json is unparseable ({exc}); using conservative "
            f"default (no auto-merge).",
        )
    return _resolve_text(raw_text, declared_source="declared")


def _outcome_index(outcome: str) -> int:
    try:
        return INTENDED_OUTCOME_LADDER.index(outcome)
    except ValueError:
        return INTENDED_OUTCOME_LADDER.index(_DEFAULT_OUTCOME)


def authorizes_auto_merge(outcome: RepoOutcome) -> bool:
    """The load-bearing predicate: may an agent merge a GREEN PR without asking?

    Requires BOTH the explicit flag AND an intended outcome at or above 'merged'.
    The consistency guard (flag true but outcome only pr-opened) does NOT authorize —
    you cannot auto-merge in a repo that declared it only wants a PR opened.
    """
    return bool(outcome.auto_merge_on_green) and _outcome_index(
        outcome.intended_outcome
    ) >= _MERGE_MIN_INDEX
