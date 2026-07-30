#!/usr/bin/env python3
"""Inject Escapement-owned workflow context at lifecycle boundaries."""

from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from _worktree_cli import bundled_cli_prefix

_LIFECYCLE_EVENTS = {"SessionStart", "PreCompact"}
_SUPPORTED_OUTCOMES = ("committed", "pr-opened", "merged", "merged-and-deployed")
_LANDING_PATHS = {
    "committed": "Use a feature branch and finish with a verified local commit.",
    "pr-opened": "Use a feature branch, push it, and open a pull request.",
    "merged": (
        "Use a feature branch, push it, open a pull request, and carry it through "
        "merge under the existing Escapement authorization gates."
    ),
    "merged-and-deployed": (
        "Use a feature branch, push it, open a pull request, carry it through "
        "merge, and verify deployment under the existing Escapement authorization "
        "gates."
    ),
}


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _event_name(payload: dict) -> str:
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    return event if isinstance(event, str) else ""


def _repo_root(payload: dict) -> Path:
    cwd = payload.get("cwd")
    start = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return start
    root = result.stdout.strip()
    return Path(root) if result.returncode == 0 and root else start


def _resolver_path() -> Path | None:
    hook_path = Path(__file__).resolve()
    candidates = (
        hook_path.parents[2] / "harness" / "bin" / "repo_outcome.py",
        hook_path.parents[1] / "harness" / "bin" / "repo_outcome.py",
    )
    return next((path for path in candidates if path.is_file()), None)


def _resolve_outcome(repo_root: Path):
    resolver_path = _resolver_path()
    if resolver_path is None:
        return SimpleNamespace(
            intended_outcome="pr-opened",
            auto_merge_on_green=False,
            source="default-resolver-unavailable",
            warning=(
                "Escapement outcome resolver is unavailable; using conservative "
                "default."
            ),
        )

    spec = importlib.util.spec_from_file_location(
        "_escapement_repo_outcome",
        resolver_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Escapement outcome resolver: {resolver_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.resolve(repo_root)


def _worktree_context(repo_root: Path) -> str:
    prefix = bundled_cli_prefix(Path(__file__))
    if prefix is None:
        return (
            "Worktree creation is unavailable: broken Escapement installation; "
            "the bundled escapement-worktree CLI "
            "is missing. Repair or reinstall Escapement before creating a worktree."
        )
    command = (
        f"{shlex.join(prefix)} create --repo {shlex.quote(str(repo_root))} "
        "--name <task> --branch <branch>"
    )
    return (
        "Escapement owns Git worktree creation policy. Use the session-specific "
        f"`escapement-worktree create` transaction: `{command}`. Beads remains "
        "task state and is checked after creation when present."
    )


def _additional_context(outcome, repo_root: Path) -> str:
    auto_merge = str(outcome.auto_merge_on_green).lower()
    lines = [
        "Escapement owns workflow policy. Beads is the task-state system only.",
        (
            "Git, pull-request, merge, deployment, completion, memory, and "
            "agent-behavior policy come from Escapement and "
            ".escapement/repo.json."
        ),
        (
            "Use Beads only for work state: bd ready; bd show <id>; "
            "bd update <id> --claim; and bd close <id>."
        ),
        _worktree_context(repo_root),
        (
            f"Resolved landing policy: intended_outcome={outcome.intended_outcome}; "
            f"auto_merge_on_green={auto_merge}; source={outcome.source}."
        ),
        f"Required landing path: {_LANDING_PATHS[outcome.intended_outcome]}",
    ]

    if outcome.source.startswith("default-"):
        lines.append(
            (
                "No valid declared policy is available; offer the user the "
                f"supported outcomes: {', '.join(_SUPPORTED_OUTCOMES)}. Until "
                "selected, pr-opened remains the default."
            )
        )

    if outcome.warning:
        lines.append(f"Policy warning: {outcome.warning}")
    return " ".join(lines)


def main() -> int:
    payload = _read_payload()
    event = _event_name(payload)
    if event not in _LIFECYCLE_EVENTS:
        return 0

    repo_root = _repo_root(payload)
    try:
        outcome = _resolve_outcome(repo_root)
    except Exception:  # noqa: BLE001 - hook boundary must fail closed on version skew
        outcome = SimpleNamespace(
            intended_outcome="pr-opened",
            auto_merge_on_green=False,
            source="default-resolver-error",
            warning=(
                "Escapement outcome resolver failed; using conservative default."
            ),
        )

    print(
        json.dumps(
            {
                "systemMessage": "Escapement workflow policy is active.",
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": _additional_context(outcome, repo_root),
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
