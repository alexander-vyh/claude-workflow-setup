"""Public-hook oracle for committed feature-branch test weakening.

The commit DAG and origin/HEAD symref are the independent scope authority.  The
tests invoke hook scripts as subprocesses rather than echoing a change collector.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from oracle_downgrade_git_fixtures import (
    DUPLICATE_STRONG,
    LANDING_HOOKS,
    PUBLIC_HOOKS,
    SINGLE_STRONG,
    STOP_HOOKS,
    STRONG,
    WEAK,
    advisory_message,
    commit,
    feature_repo,
    git,
    git_bytes,
    landing_repo,
    raw_non_utf8_repo,
    run_hook,
    write,
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


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_committed_byte_identical_rename_is_silent(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    old_path = "tests/test_total.py"
    new_path = "tests/test_renamed.py"
    git(repo, "mv", old_path, new_path)
    commit(repo, "rename test without changing oracle")
    git(repo, "config", "diff.renames", "false")
    git(repo, "config", "diff.renameLimit", "1")

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    "filename",
    (
        "tests/test_newline\ncase.py",
        "tests/test_tab\tcase.py",
        'tests/test_"quoted"_case.py',
    ),
)
@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_committed_weakening_at_git_valid_unusual_path_warns(
    tmp_path: Path,
    hook: Path,
    event: str,
    filename: str,
) -> None:
    repo = landing_repo(tmp_path, STRONG)
    git(repo, "mv", "tests/test_total.py", filename)
    commit(repo, "move baseline to unusual path")
    git(repo, "push", "origin", "trunk")
    git(repo, "checkout", "-b", "feature/oracle-change")
    write(repo, filename, WEAK)
    commit(repo, "weaken unusual-path oracle")

    message = advisory_message(run_hook(hook, repo, event), event)

    assert filename in message


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_raw_non_utf8_committed_weakening_warns_from_nul_git_record(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo, raw_path, baseline = raw_non_utf8_repo(tmp_path)
    records = git_bytes(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        baseline,
        "HEAD",
    )
    assert raw_path in records.split(b"\0")

    message = advisory_message(run_hook(hook, repo, event), event)

    assert os.fsdecode(raw_path) in message


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_resolved_landing_ref_keeps_staged_only_weakening(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    write(repo, "tests/test_total.py", WEAK)
    git(repo, "add", "tests/test_total.py")
    assert git(repo, "diff", "--name-only") == ""

    message = advisory_message(run_hook(hook, repo, event), event)

    assert message.count("tests/test_total.py") == 1


@pytest.mark.parametrize("landing_state", ("missing", "dangling"))
@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_unresolved_landing_ref_retains_local_weakening(
    tmp_path: Path,
    hook: Path,
    event: str,
    landing_state: str,
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    if landing_state == "missing":
        git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    else:
        git(
            repo,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/dangling",
        )
    write(repo, "tests/test_total.py", WEAK)

    message = advisory_message(run_hook(hook, repo, event), event)

    assert message.count("tests/test_total.py") == 1


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_resolved_landing_ref_keeps_untracked_only_weak_replacement(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    git(repo, "rm", "tests/test_total.py")
    commit(repo, "remove baseline test")
    write(repo, "tests/test_total.py", WEAK)
    assert git(repo, "ls-files", "--others", "--exclude-standard") == (
        "tests/test_total.py"
    )

    message = advisory_message(run_hook(hook, repo, event), event)

    assert message.count("tests/test_total.py") == 1


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_staged_deletion_with_untracked_stronger_replacement_is_net_silent(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    path = "tests/test_total.py"
    raw_path = path.encode()
    git(repo, "rm", path)
    stronger = STRONG + "    assert replacement_extra() == 11\n"
    write(repo, path, stronger)
    cached = git_bytes(repo, "diff", "--cached", "--name-only", "-z")
    untracked = git_bytes(repo, "ls-files", "--others", "--exclude-standard", "-z")
    assert raw_path in cached.split(b"\0")
    assert raw_path in untracked.split(b"\0")
    assert stronger != STRONG

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_mixed_candidate_states_warn_once_per_net_path(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = landing_repo(tmp_path, STRONG)
    paths = (
        "tests/test_total.py",
        "tests/test_unstaged.py",
        "tests/test_untracked.py",
    )
    write(repo, paths[1], STRONG)
    write(repo, paths[2], STRONG)
    commit(repo, "add mixed-state baselines")
    git(repo, "push", "origin", "trunk")
    git(repo, "checkout", "-b", "feature/oracle-change")
    git(repo, "rm", paths[2])
    commit(repo, "remove future untracked replacement")
    write(repo, paths[2], WEAK)
    write(repo, paths[0], WEAK)
    git(repo, "add", paths[0])
    write(repo, paths[1], WEAK)

    message = advisory_message(run_hook(hook, repo, event), event)

    for path in paths:
        assert message.count(path) == 1


def ref_moving_git_environment(
    tmp_path: Path,
    landing_oid: str,
) -> dict[str, str]:
    real_git = shutil.which("git")
    assert real_git is not None
    wrapper_dir = tmp_path / "git-wrapper"
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "git"
    wrapper.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys

args = sys.argv[1:]
result = subprocess.run([os.environ["REAL_GIT"], *args], capture_output=True)
resolved_tokens = result.stdout.replace(b"\\0", b" ").split()
if (
    result.returncode == 0
    and os.environ["LANDING_OID"].encode() in resolved_tokens
    and not os.path.exists(os.environ["REF_MOVE_MARKER"])
):
    subprocess.run(
        [
            os.environ["REAL_GIT"],
            "update-ref",
            "refs/remotes/origin/trunk",
            "refs/remotes/origin/alternate",
        ],
        check=True,
    )
    with open(os.environ["REF_MOVE_MARKER"], "wb") as marker:
        marker.write(b"moved after immutable target resolution\\n")
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    env = os.environ.copy()
    env["REAL_GIT"] = real_git
    env["LANDING_OID"] = landing_oid
    env["REF_MOVE_MARKER"] = str(tmp_path / "ref-moved.marker")
    env["PATH"] = f"{wrapper_dir}{os.pathsep}{env['PATH']}"
    return env


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_resolved_landing_oid_survives_later_target_ref_move(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    landing_oid = git(repo, "rev-parse", "refs/remotes/origin/trunk")
    write(repo, "tests/test_total.py", WEAK)
    commit(repo, "weaken oracle")
    git(repo, "update-ref", "refs/remotes/origin/alternate", "HEAD")
    env = ref_moving_git_environment(tmp_path, landing_oid)

    result = run_hook(hook, repo, event, env=env)
    assert Path(env["REF_MOVE_MARKER"]).read_text() == (
        "moved after immutable target resolution\n"
    )
    assert git(repo, "rev-parse", "refs/remotes/origin/trunk") == git(
        repo, "rev-parse", "refs/remotes/origin/alternate"
    ), "race fixture must move the verified target ref before scope evaluation"
    message = advisory_message(result, event)

    assert "tests/test_total.py" in message
