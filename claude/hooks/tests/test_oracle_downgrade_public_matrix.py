"""Compact public matrix for final-tree oracle-downgrade behavior."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from git_change_scope import SOURCE_BYTE_LIMIT, change_sources, net_tree_scope
from oracle_downgrade_git_fixtures import (
    PUBLIC_HOOKS,
    STRONG,
    WEAK,
    advisory_message,
    commit,
    feature_repo,
    git,
    landing_repo,
    run_hook,
    write,
)


def _named_baseline(tmp_path: Path, filename: str, source: str) -> Path:
    repo = landing_repo(tmp_path, source)
    git(repo, "mv", "tests/test_total.py", filename)
    commit(repo, "place literal baseline")
    git(repo, "push", "origin", "trunk")
    git(repo, "checkout", "-b", "feature/oracle-change")
    return repo


def _apply_source(repo: Path, filename: str, source: str, state: str) -> None:
    write(repo, filename, source)
    if state == "committed":
        commit(repo, "change candidate oracle")
    elif state == "staged":
        git(repo, "add", "--all")


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
@pytest.mark.parametrize("state", ("committed", "staged", "unstaged"))
def test_literal_pathspec_name_warns_once(
    tmp_path: Path,
    hook: Path,
    event: str,
    state: str,
) -> None:
    filename = ":(literal)test_oracle_test.py"
    repo = _named_baseline(tmp_path, filename, STRONG)
    _apply_source(repo, filename, WEAK, state)

    message = advisory_message(run_hook(hook, repo, event), event)

    assert message.count(filename) == 1


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
@pytest.mark.parametrize("state", ("committed", "staged", "unstaged"))
@pytest.mark.parametrize(("source", "warns"), ((STRONG, True), (WEAK, False)))
def test_rename_out_of_discovery_uses_net_candidate_tree(
    tmp_path: Path,
    hook: Path,
    event: str,
    state: str,
    source: str,
    warns: bool,
) -> None:
    repo = feature_repo(tmp_path, source)
    source_path = "tests/test_total.py"
    destination = "support/oracle_check.py"
    (repo / destination).parent.mkdir(parents=True)
    git(repo, "mv", source_path, destination)
    if state == "committed":
        commit(repo, "move oracle out of discovery")
    elif state == "unstaged":
        git(repo, "reset", "HEAD", "--", source_path, destination)

    result = run_hook(hook, repo, event)

    if warns:
        message = advisory_message(result, event)
        assert message.count(destination) == 1
        assert source_path not in message
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_multiple_reasons_render_under_one_path(
    tmp_path: Path,
    hook: Path,
    event: str,
) -> None:
    baseline = (
        "def test_alpha():\n    assert category() == 'active'\n\n"
        "def test_beta():\n    assert response.status_code == 403\n"
    )
    candidate = (
        "import pytest\n\n"
        "@pytest.mark.skip(reason='disabled')\n"
        "def test_alpha():\n    assert category\n\n"
        "def test_beta():\n    assert response\n"
    )
    repo = feature_repo(tmp_path, baseline)
    write(repo, "tests/test_total.py", candidate)
    commit(repo, "weaken multiple oracle dimensions")

    message = advisory_message(run_hook(hook, repo, event), event)

    assert message.count("tests/test_total.py") == 1
    if event == "Stop":
        assert "test_alpha" in message
        assert "test_beta" in message
    else:
        assert "skip-or-xfail-added" in message
        assert "negative-control-removed" in message


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_large_ordinary_source_is_capped_and_still_warns(
    tmp_path: Path,
    hook: Path,
    event: str,
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    large_candidate = WEAK + "#" + ("x" * (2 * 1024 * 1024)) + "\n"
    write(repo, "tests/test_total.py", large_candidate)
    commit(repo, "weaken oracle in large ordinary source")
    scope = net_tree_scope(repo)
    change = next(change for change in scope.changes if change.filepath.endswith(".py"))
    _old_source, candidate_source = change_sources(repo, scope, change)

    message = advisory_message(run_hook(hook, repo, event), event)

    assert 0 < len(candidate_source.encode()) <= 1024 * 1024
    assert SOURCE_BYTE_LIMIT <= 1024 * 1024
    assert message.count("tests/test_total.py") == 1
