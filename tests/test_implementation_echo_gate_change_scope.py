"""Real-Git behavioral controls for the implementation-echo hook's scope.

The independent oracle here is the commit DAG plus Git's locally configured
remote-default symref.  These tests deliberately exercise ``main()`` rather
than mocking the changed-file collector, so a valid file list has to produce
the public PreToolUse allow/deny contract.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


HOOK = Path(__file__).resolve().parents[1] / "claude/hooks/implementation_echo_test_gate.py"
spec = importlib.util.spec_from_file_location("implementation_echo_test_gate_change_scope", HOOK)
gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)

OPAQUE = "0124p000000ABCDEF1"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def write(repo: Path, relative_path: str, text: str) -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)


def configure_author(repo: Path) -> None:
    git(repo, "config", "user.email", "echo-gate@example.test")
    git(repo, "config", "user.name", "Echo Gate Test")


def make_origin_clone(tmp_path: Path) -> tuple[Path, Path]:
    """Create a clone whose local remote default explicitly names ``trunk``."""
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True, capture_output=True)
    configure_author(repo)
    git(repo, "checkout", "-b", "trunk")
    write(repo, "README.md", "baseline\n")
    commit_all(repo, "baseline")
    git(repo, "push", "-u", "origin", "trunk")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    return origin, repo


def advance_trunk(origin: Path, tmp_path: Path, files: dict[str, str]) -> None:
    writer = tmp_path / "trunk-writer"
    subprocess.run(["git", "clone", str(origin), str(writer)], check=True, capture_output=True)
    configure_author(writer)
    git(writer, "checkout", "trunk")
    for path, text in files.items():
        write(writer, path, text)
    commit_all(writer, "advance trunk")
    git(writer, "push", "origin", "trunk")


def echo_pair(stem: str, token: str = OPAQUE) -> dict[str, str]:
    return {
        f"src/{stem}.py": f"TOKEN = '{token}'\n",
        f"tests/test_{stem}.py": f"def test_{stem}():\n    assert token == '{token}'\n",
    }


def write_files(repo: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        write(repo, path, text)


def hook_result(repo: Path) -> tuple[int, str, dict | None]:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(repo),
        "tool_input": {"command": "git commit -m echo"},
    }
    stdout = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        with patch("sys.stdout", stdout):
            code = gate.main()
    raw = stdout.getvalue().strip()
    return code, raw, json.loads(raw) if raw else None


def assert_allowed(repo: Path) -> None:
    code, raw, output = hook_result(repo)
    assert code == 0
    assert raw == ""
    assert output is None


def assert_denied_once(repo: Path) -> None:
    code, raw, output = hook_result(repo)
    assert code == 0
    assert len(raw.splitlines()) == 1
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def assert_scope_computation_denied(repo: Path) -> None:
    """A valid landing ref with uncomputable history fails closed once."""
    code, raw, output = hook_result(repo)
    assert code == 0
    assert len(raw.splitlines()) == 1
    assert output is not None
    decision = output["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "committed change scope" in decision["permissionDecisionReason"]
    assert "git merge-base" in decision["permissionDecisionReason"]


def test_rebased_feature_excludes_landing_only_echo_despite_stale_feature_ref(tmp_path: Path):
    """Landing-only commits after rebase cannot be attributed to the feature."""
    origin, repo = make_origin_clone(tmp_path)
    git(repo, "checkout", "-b", "feature")
    write_files(repo, {"src/feature.py": "def feature(): return 'ok'\n", "tests/test_feature.py": "def test_feature(): assert True\n"})
    commit_all(repo, "feature work")
    git(repo, "push", "-u", "origin", "feature")

    landing_files = echo_pair("landing")
    advance_trunk(origin, tmp_path, landing_files)
    git(repo, "fetch", "origin")
    git(repo, "rebase", "origin/trunk")

    assert set(gate.changed_files(repo)) == {"src/feature.py", "tests/test_feature.py"}
    assert_allowed(repo)


def test_feature_owned_committed_echo_denies_once(tmp_path: Path):
    """A feature commit remains visible and yields the public deny contract."""
    _origin, repo = make_origin_clone(tmp_path)
    git(repo, "checkout", "-b", "feature")
    write_files(repo, {"src/seed.py": "VALUE = 'safe'\n"})
    commit_all(repo, "feature seed")
    git(repo, "push", "-u", "origin", "feature")
    write_files(repo, echo_pair("feature"))
    commit_all(repo, "feature echo")

    assert set(gate.changed_files(repo)) == {
        "src/feature.py",
        "src/seed.py",
        "tests/test_feature.py",
    }
    assert_denied_once(repo)


def test_diverged_feature_uses_merge_base_and_excludes_trunk_only_files(tmp_path: Path):
    """A two-dot diff would report the trunk-only files as deletions here."""
    origin, repo = make_origin_clone(tmp_path)
    git(repo, "checkout", "-b", "feature")
    write_files(repo, echo_pair("feature"))
    commit_all(repo, "feature echo")
    advance_trunk(origin, tmp_path, echo_pair("trunk", "0124p000000ABCDEFA"))
    git(repo, "fetch", "origin")

    assert set(gate.changed_files(repo)) == {"src/feature.py", "tests/test_feature.py"}
    assert_denied_once(repo)


def test_missing_remote_default_omits_committed_history_instead_of_guessing_feature_upstream(tmp_path: Path):
    """A stale feature upstream and a local trunk are not landing authority."""
    _origin, repo = make_origin_clone(tmp_path)
    git(repo, "checkout", "-b", "feature")
    write_files(repo, {"src/seed.py": "VALUE = 'safe'\n"})
    commit_all(repo, "feature seed")
    git(repo, "push", "-u", "origin", "feature")
    write_files(repo, echo_pair("committed"))
    commit_all(repo, "committed echo")
    git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")

    assert gate.changed_files(repo) == []
    assert_allowed(repo)


@pytest.mark.parametrize(
    "invalid_target",
    ["refs/remotes/origin/dangling", "refs/heads/trunk"],
)
def test_invalid_remote_default_omits_commits_but_still_scans_uncommitted_echo(
    tmp_path: Path, invalid_target: str
):
    """Only a non-HEAD origin remote-tracking ref is landing authority."""
    _origin, repo = make_origin_clone(tmp_path)
    git(repo, "checkout", "-b", "feature")
    write_files(repo, echo_pair("committed"))
    commit_all(repo, "committed echo")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", invalid_target)
    working_files = echo_pair("working", "0124p000000ABCDEFA")
    write_files(repo, working_files)

    assert set(gate.changed_files(repo)) == set(working_files)
    assert_denied_once(repo)


@pytest.mark.parametrize("state", ["unstaged", "staged", "untracked"])
def test_missing_remote_default_still_scans_each_working_tree_state(tmp_path: Path, state: str):
    """Failing open for unknown committed scope never drops local changes."""
    _origin, repo = make_origin_clone(tmp_path)
    git(repo, "checkout", "-b", "feature")
    git(repo, "push", "-u", "origin", "feature")
    git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")

    if state == "untracked":
        files = echo_pair("untracked")
        expected = set(files)
        write_files(repo, files)
    else:
        files = echo_pair("working")
        write_files(repo, {path: text.replace(OPAQUE, "safe") for path, text in files.items()})
        commit_all(repo, "tracked safe pair")
        write_files(repo, files)
        if state == "staged":
            git(repo, "add", "src/working.py", "tests/test_working.py")
        expected = set(files)

    assert set(gate.changed_files(repo)) == expected
    assert_denied_once(repo)


def test_local_trunk_ahead_of_remote_default_scans_its_committed_echo(tmp_path: Path):
    """The remote default is a base, not a reason to discard local commits."""
    _origin, repo = make_origin_clone(tmp_path)
    write_files(repo, echo_pair("local_trunk"))
    commit_all(repo, "local trunk echo")

    assert set(gate.changed_files(repo)) == {"src/local_trunk.py", "tests/test_local_trunk.py"}
    assert_denied_once(repo)


def test_valid_remote_default_without_merge_base_denies_with_repairable_scope_reason(tmp_path: Path):
    """A valid landing ref cannot fail open merely because history is unrelated."""
    _origin, repo = make_origin_clone(tmp_path)
    git(repo, "checkout", "--orphan", "unrelated-feature")
    git(repo, "rm", "-rf", ".")
    write_files(repo, echo_pair("unrelated"))
    commit_all(repo, "unrelated feature echo")

    assert gate.remote_default_ref(repo) == "refs/remotes/origin/trunk"
    no_merge_base = subprocess.run(
        ["git", "merge-base", "refs/remotes/origin/trunk", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert no_merge_base.returncode == 1
    assert_scope_computation_denied(repo)
