"""Behavioral oracle for Escapement-owned lifecycle context."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


HOOK = Path(__file__).resolve().parents[1] / "escapement_session_context.py"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "harness" / "bin"))

import repo_outcome  # noqa: E402

TRACKER_COMMANDS = (
    "bd ready",
    "bd show <id>",
    "bd update <id> --claim",
    "bd close <id>",
    "bd worktree create",
)
BEADS_POLICY_PHRASES = ("bd prime", "stealth mode", "no git operations")


def _run(
    cwd: Path,
    payload: dict | None = None,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=cwd,
        input="" if payload is None else json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _run_raw(cwd: Path, raw_payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=cwd,
        input=raw_payload,
        capture_output=True,
        text=True,
        check=False,
    )


def _context(result: subprocess.CompletedProcess[str]) -> tuple[str, dict]:
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "lifecycle hook emitted no context"
    output = json.loads(result.stdout)
    return output["hookSpecificOutput"]["additionalContext"], output


def _write_policy(root: Path, raw: object) -> None:
    policy_dir = root / ".escapement"
    policy_dir.mkdir(exist_ok=True)
    text = raw if isinstance(raw, str) else json.dumps(raw)
    (policy_dir / "repo.json").write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_stale_policy_worktree(tmp_path: Path) -> Path:
    primary = tmp_path / "primary"
    sibling = tmp_path / "sibling"
    origin = tmp_path / "origin.git"
    primary.mkdir()
    _git(primary, "init", "--initial-branch=main")
    _git(primary, "config", "user.email", "test@example.com")
    _git(primary, "config", "user.name", "Test User")
    _write_policy(
        primary,
        {"intended_outcome": "merged", "auto_merge_on_green": True},
    )
    _git(primary, "add", ".")
    _git(primary, "commit", "-m", "primary policy")
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(primary, "remote", "add", "origin", str(origin))
    _git(primary, "push", "-u", "origin", "main")
    _git(primary, "fetch", "origin")
    _git(
        primary,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/main",
    )
    _git(primary, "worktree", "add", "-b", "stale-policy", str(sibling))
    _write_policy(
        sibling,
        {"intended_outcome": "pr-opened", "auto_merge_on_green": False},
    )
    _git(sibling, "add", ".escapement/repo.json")
    _git(sibling, "commit", "-m", "stale branch policy")
    return sibling


def test_missing_policy_defaults_to_branch_push_pr_and_offers_configuration(
    tmp_path: Path,
) -> None:
    context, output = _context(
        _run(tmp_path, {"hook_event_name": "SessionStart", "cwd": str(tmp_path)})
    )

    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "feature branch" in context
    assert "push" in context
    assert "pull request" in context
    assert "pr-opened" in context
    assert "committed" in context
    assert "merged" in context
    assert "merged-and-deployed" in context
    assert "offer" in context.lower()
    assert all(command in context for command in TRACKER_COMMANDS)
    assert all(phrase not in context.lower() for phrase in BEADS_POLICY_PHRASES)


def test_hostile_beads_global_config_cannot_change_landing_context(tmp_path: Path) -> None:
    config_home = tmp_path / "hostile-config"
    beads_config = config_home / "bd"
    beads_config.mkdir(parents=True)
    (beads_config / "config.yaml").write_text("no-git-ops: true\n", encoding="utf-8")

    context, _ = _context(
        _run(
            tmp_path,
            {"hook_event_name": "SessionStart", "cwd": str(tmp_path)},
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )
    )

    assert "feature branch" in context
    assert "push" in context
    assert "pull request" in context
    assert all(phrase not in context.lower() for phrase in BEADS_POLICY_PHRASES)


def test_configured_outcome_is_reported_without_reimplementing_authorization(
    tmp_path: Path,
) -> None:
    _write_policy(
        tmp_path,
        {
            "intended_outcome": "merged-and-deployed",
            "auto_merge_on_green": True,
        },
    )

    context, _ = _context(
        _run(tmp_path, {"hook_event_name": "SessionStart", "cwd": str(tmp_path)})
    )

    assert "merged-and-deployed" in context
    assert "auto_merge_on_green=true" in context
    assert "existing Escapement authorization gates" in context
    assert "offer the user" not in context.lower()


def test_nested_working_directory_uses_the_repository_root_policy(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    nested = repo / "nested" / "deeper"
    nested.mkdir(parents=True)
    _git(repo, "init", "--initial-branch=main")
    _write_policy(
        repo,
        {
            "intended_outcome": "merged-and-deployed",
            "auto_merge_on_green": True,
        },
    )

    context, _ = _context(
        _run(nested, {"hook_event_name": "SessionStart", "cwd": str(nested)})
    )

    assert "intended_outcome=merged-and-deployed" in context
    assert "auto_merge_on_green=true" in context
    assert "source=declared" in context


def test_malformed_policy_fails_closed_to_pr_opened(tmp_path: Path) -> None:
    _write_policy(tmp_path, "{not-json")

    context, _ = _context(
        _run(tmp_path, {"hook_event_name": "SessionStart", "cwd": str(tmp_path)})
    )

    assert "pr-opened" in context
    assert "auto_merge_on_green=false" in context
    assert "malformed" in context.lower()
    assert "offer" in context.lower()


@pytest.mark.parametrize(
    "raw_policy",
    (
        {"intended_outcome": "not-a-real-outcome", "auto_merge_on_green": True},
        ["not", "an", "object"],
        {"intended_outcome": "merged", "auto_merge_on_green": "true"},
    ),
)
def test_context_matches_the_existing_outcome_resolver(
    tmp_path: Path,
    raw_policy: object,
) -> None:
    _write_policy(tmp_path, raw_policy)
    resolved = repo_outcome.resolve(tmp_path)

    context, _ = _context(
        _run(tmp_path, {"hook_event_name": "SessionStart", "cwd": str(tmp_path)})
    )

    assert f"intended_outcome={resolved.intended_outcome}" in context
    assert (
        f"auto_merge_on_green={str(resolved.auto_merge_on_green).lower()}" in context
    )
    assert f"source={resolved.source}" in context


def test_default_branch_policy_wins_over_stale_worktree_policy(tmp_path: Path) -> None:
    sibling = _init_stale_policy_worktree(tmp_path)
    nested = sibling / "nested" / "deeper"
    nested.mkdir(parents=True)
    resolved = repo_outcome.resolve(sibling)

    context, _ = _context(
        _run(nested, {"hook_event_name": "SessionStart", "cwd": str(nested)})
    )

    assert resolved.source == "declared-default-branch"
    assert "intended_outcome=merged" in context
    assert "auto_merge_on_green=true" in context
    assert "source=declared-default-branch" in context


def test_precompact_reinjects_the_same_authoritative_contract(tmp_path: Path) -> None:
    context, output = _context(
        _run(tmp_path, {"hookEventName": "PreCompact", "cwd": str(tmp_path)})
    )

    assert output["hookSpecificOutput"]["hookEventName"] == "PreCompact"
    assert "Escapement owns workflow policy" in context
    assert "pull request" in context
    assert all(command in context for command in TRACKER_COMMANDS)


def test_non_lifecycle_event_is_silent(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        {"hook_event_name": "PreToolUse", "cwd": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("raw_payload", ("", "{}", "{not-json", '{"hook_event_name": 7}'))
def test_missing_or_malformed_event_is_silent(
    tmp_path: Path,
    raw_payload: str,
) -> None:
    result = _run_raw(tmp_path, raw_payload)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_broken_packaged_resolver_fails_closed_with_context(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    hook_dir = plugin / "claude" / "hooks"
    resolver_dir = plugin / "harness" / "bin"
    hook_dir.mkdir(parents=True)
    resolver_dir.mkdir(parents=True)
    packaged_hook = hook_dir / HOOK.name
    shutil.copyfile(HOOK, packaged_hook)
    (resolver_dir / "repo_outcome.py").write_text(
        "this is not valid Python !!!\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(packaged_hook)],
        cwd=tmp_path,
        input=json.dumps(
            {"hook_event_name": "SessionStart", "cwd": str(tmp_path)}
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    context, _ = _context(result)

    assert "intended_outcome=pr-opened" in context
    assert "auto_merge_on_green=false" in context
    assert "source=default-resolver-error" in context
    assert "resolver failed" in context.lower()
