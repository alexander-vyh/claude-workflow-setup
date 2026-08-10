"""Public-hook oracle for committed feature-branch test weakening.

The commit DAG and origin/HEAD symref are the independent scope authority.  The
tests invoke hook scripts as subprocesses rather than echoing a change collector.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
LANDING_HOOKS = (
    ROOT / "claude" / "hooks" / "oracle_downgrade_warning_gate.py",
    ROOT
    / "plugins"
    / "escapement"
    / "claude"
    / "hooks"
    / "oracle_downgrade_warning_gate.py",
    ROOT
    / "plugins"
    / "escapement-claude"
    / "hooks"
    / "oracle_downgrade_warning_gate.py",
)
STOP_HOOKS = (
    ROOT / "claude" / "hooks" / "oracle_downgrade_stop.py",
    ROOT / "plugins" / "escapement-claude" / "hooks" / "oracle_downgrade_stop.py",
)

STRONG = (
    "def test_total():\n    assert compute() == 42\n    assert category() == 'active'\n"
)
WEAK = "def test_total():\n    assert compute()\n"
DUPLICATE_STRONG = (
    "def test_total():\n    assert compute() == 42\n    assert compute() == 42\n"
)
SINGLE_STRONG = "def test_total():\n    assert compute() == 42\n"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)


def landing_repo(tmp_path: Path, baseline_test: str) -> Path:
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "clone", str(origin), str(repo)], check=True, capture_output=True
    )
    git(repo, "config", "user.email", "oracle@example.test")
    git(repo, "config", "user.name", "Oracle Test")
    git(repo, "checkout", "-b", "trunk")
    write(repo, "tests/test_total.py", baseline_test)
    write(repo, "README.md", "baseline\n")
    commit(repo, "landing baseline")
    git(repo, "push", "-u", "origin", "trunk")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    return repo


def feature_repo(tmp_path: Path, baseline_test: str) -> Path:
    repo = landing_repo(tmp_path, baseline_test)
    git(repo, "checkout", "-b", "feature/oracle-change")
    return repo


def run_hook(hook: Path, repo: Path, event: str) -> subprocess.CompletedProcess[str]:
    if event == "Stop":
        payload = {"hook_event_name": "Stop", "cwd": str(repo)}
    else:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": str(repo),
            "tool_input": {"command": "gh pr create --title oracle-change"},
        }
    return subprocess.run(
        [sys.executable, str(hook)],
        cwd=repo,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("hook", LANDING_HOOKS)
def test_clean_committed_weakening_is_asked_at_landing(
    tmp_path: Path, hook: Path
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    write(repo, "tests/test_total.py", WEAK)
    commit(repo, "weaken oracle")
    write(repo, "README.md", "unrelated second feature commit\n")
    commit(repo, "follow-up docs")

    result = run_hook(hook, repo, "PreToolUse")

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    decision = output["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "tests/test_total.py" in decision["permissionDecisionReason"]


@pytest.mark.parametrize("hook", STOP_HOOKS)
def test_clean_committed_weakening_is_advised_at_stop(
    tmp_path: Path, hook: Path
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    write(repo, "tests/test_total.py", WEAK)
    commit(repo, "weaken oracle")
    write(repo, "README.md", "unrelated second feature commit\n")
    commit(repo, "follow-up docs")

    result = run_hook(hook, repo, "Stop")

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "tests/test_total.py" in output["systemMessage"]


@pytest.mark.parametrize(
    ("hook", "event"),
    tuple((hook, "PreToolUse") for hook in LANDING_HOOKS)
    + tuple((hook, "Stop") for hook in STOP_HOOKS),
)
def test_committed_strengthening_is_silent(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, WEAK)
    write(repo, "tests/test_total.py", STRONG)
    commit(repo, "strengthen oracle")

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    ("hook", "event"),
    tuple((hook, "PreToolUse") for hook in LANDING_HOOKS)
    + tuple((hook, "Stop") for hook in STOP_HOOKS),
)
def test_test_weakening_before_feature_branch_is_out_of_scope(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, WEAK)
    write(repo, "README.md", "feature-only docs\n")
    commit(repo, "feature docs")

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    ("hook", "event"),
    tuple((hook, "PreToolUse") for hook in LANDING_HOOKS)
    + tuple((hook, "Stop") for hook in STOP_HOOKS),
)
def test_uncommitted_restore_is_evaluated_as_net_landing_tree(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    write(repo, "tests/test_total.py", WEAK)
    commit(repo, "temporary weakening")
    write(repo, "tests/test_total.py", STRONG)

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    ("hook", "event"),
    tuple((hook, "PreToolUse") for hook in LANDING_HOOKS)
    + tuple((hook, "Stop") for hook in STOP_HOOKS),
)
def test_landing_tip_changes_after_fork_do_not_leak_into_feature_scope(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, WEAK)
    git(repo, "checkout", "trunk")
    write(repo, "tests/test_total.py", STRONG)
    commit(repo, "strengthen landing branch after feature fork")
    git(repo, "push", "origin", "trunk")
    git(repo, "checkout", "feature/oracle-change")
    write(repo, "README.md", "feature remains on fork behavior\n")
    commit(repo, "feature docs")

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    ("hook", "event"),
    tuple((hook, "PreToolUse") for hook in LANDING_HOOKS)
    + tuple((hook, "Stop") for hook in STOP_HOOKS),
)
def test_weakening_committed_before_feature_branch_is_out_of_scope(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = landing_repo(tmp_path, STRONG)
    write(repo, "tests/test_total.py", WEAK)
    commit(repo, "weaken on landing branch before feature")
    git(repo, "push", "origin", "trunk")
    git(repo, "checkout", "-b", "feature/oracle-change")
    write(repo, "README.md", "feature-only docs\n")
    commit(repo, "feature docs")

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    ("hook", "event"),
    tuple((hook, "PreToolUse") for hook in LANDING_HOOKS)
    + tuple((hook, "Stop") for hook in STOP_HOOKS),
)
def test_duplicate_assertion_removal_is_committed_noop(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, DUPLICATE_STRONG)
    write(repo, "tests/test_total.py", SINGLE_STRONG)
    commit(repo, "remove duplicate assertion")

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("hook", LANDING_HOOKS)
def test_resolved_landing_ref_keeps_uncommitted_weakening_at_landing(
    tmp_path: Path, hook: Path
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    write(repo, "README.md", "feature docs before test edit\n")
    commit(repo, "feature docs")
    write(repo, "tests/test_total.py", WEAK)

    result = run_hook(hook, repo, "PreToolUse")

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert (
        "tests/test_total.py"
        in output["hookSpecificOutput"]["permissionDecisionReason"]
    )


@pytest.mark.parametrize("hook", STOP_HOOKS)
def test_resolved_landing_ref_keeps_uncommitted_weakening_at_stop(
    tmp_path: Path, hook: Path
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    write(repo, "README.md", "feature docs before test edit\n")
    commit(repo, "feature docs")
    write(repo, "tests/test_total.py", WEAK)

    result = run_hook(hook, repo, "Stop")

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "tests/test_total.py" in output["systemMessage"]
