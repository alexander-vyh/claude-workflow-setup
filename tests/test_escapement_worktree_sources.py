"""Source-resolution tests using a remote Git server, never a production mock."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from worktree_fixtures import git, make_remote_scenario, rev, run_cli


def _race_commits(scenario, tmp_path: Path) -> list[str]:
    writer = tmp_path / "race-writer"
    git(tmp_path, "clone", str(scenario.remote), str(writer))
    commits = []
    for value in ("race-one\n", "race-two\n"):
        (writer / "oracle.txt").write_text(value, encoding="utf-8")
        git(writer, "add", "oracle.txt")
        git(writer, "commit", "-m", value.strip())
        git(writer, "push", "origin", "trunk")
        commits.append(rev(writer))
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(scenario.remote),
            "update-ref",
            "refs/heads/trunk",
            scenario.remote_head_sha,
        ],
        check=True,
    )
    return commits


def _race_proxy(tmp_path: Path, scenario, shas: list[str]) -> dict[str, str]:
    proxy_dir = tmp_path / "proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

real_git = os.environ["REAL_GIT"]
args = sys.argv[1:]
if "update-ref" in args and "--stdin" in args:
    os.execv(real_git, [real_git, *args])
result = subprocess.run([real_git, *args], capture_output=True, check=False)
if "ls-remote" in args and args[-1] == "HEAD":
    counter_path = Path(os.environ["RACE_COUNTER"])
    count = int(counter_path.read_text() or "0") if counter_path.exists() else 0
    shas = json.loads(os.environ["RACE_SHAS"])
    if count < len(shas):
        subprocess.run(
            [
                real_git,
                "--git-dir",
                os.environ["RACE_REMOTE"],
                "update-ref",
                os.environ["RACE_REF"],
                shas[count],
            ],
            check=True,
        )
    counter_path.write_text(str(count + 1), encoding="utf-8")
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    return {
        "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": shutil.which("git") or "git",
        "RACE_COUNTER": str(tmp_path / "race-count"),
        "RACE_SHAS": json.dumps(shas),
        "RACE_REMOTE": str(scenario.remote),
        "RACE_REF": scenario.remote_default_ref,
    }


def test_default_uses_fetched_remote_head_not_stale_primary_head(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "fresh",
        "--branch",
        "feature/fresh",
    )
    created = scenario.primary / ".worktrees" / "fresh"
    assert result.returncode == 0, result.stderr
    assert rev(created) == scenario.remote_head_sha
    assert rev(created) != scenario.stale_primary_sha
    assert (created / "oracle.txt").read_text(encoding="utf-8") == "remote-default\n"


def test_repo_argument_overrides_the_invocation_repository(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    target_root.mkdir()
    target = make_remote_scenario(target_root)
    caller_root = tmp_path / "caller"
    caller_root.mkdir()
    caller = make_remote_scenario(caller_root, default_branch="caller-default")

    result = run_cli(
        caller.primary,
        "create",
        "--repo",
        str(target.primary),
        "--name",
        "targeted",
        "--branch",
        "feature/targeted",
    )

    created = target.primary / ".worktrees" / "targeted"
    assert result.returncode == 0, result.stderr
    assert rev(created) == target.remote_head_sha
    assert (
        git(
            created, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
        == git(
            target.primary, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
    )
    assert not (caller.primary / ".worktrees" / "targeted").exists()
    assert (
        git(
            caller.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/targeted",
            check=False,
        ).returncode
        != 0
    )


def test_default_branch_name_is_discovered_not_hardcoded_main(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path, default_branch="trunk")
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "trunk",
        "--branch",
        "feature/trunk",
    )
    assert result.returncode == 0, result.stderr
    assert rev(scenario.primary / ".worktrees" / "trunk") == scenario.remote_head_sha


def test_main_may_be_checked_out_in_another_worktree(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path, default_branch="main")
    occupied = tmp_path / "main-is-occupied"
    git(scenario.primary, "worktree", "add", str(occupied), "main")
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "independent",
        "--branch",
        "feature/independent",
    )
    assert result.returncode == 0, result.stderr
    assert (
        rev(scenario.primary / ".worktrees" / "independent") == scenario.remote_head_sha
    )
    assert rev(occupied) == scenario.stale_primary_sha


def test_explicit_source_uses_exact_local_commit_without_remote_refresh(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    proxy_dir = tmp_path / "no-network"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        '#!/bin/sh\ncase "$*" in *ls-remote*|*fetch*) exit 71;; esac\nexec "$REAL_GIT" "$@"\n',
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "explicit",
        "--branch",
        "feature/explicit",
        "--source",
        scenario.stale_primary_sha,
        env={
            "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
        },
    )
    assert result.returncode == 0, result.stderr
    assert (
        rev(scenario.primary / ".worktrees" / "explicit") == scenario.stale_primary_sha
    )
    assert "explicit" in result.stdout.lower()


def test_missing_origin_fails_without_creating_target_or_branch(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    git(scenario.primary, "remote", "remove", "origin")
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "missing-origin",
        "--branch",
        "feature/missing-origin",
    )
    assert result.returncode != 0
    assert not (scenario.primary / ".worktrees" / "missing-origin").exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/missing-origin",
            check=False,
        ).returncode
        != 0
    )


def test_missing_remote_head_fails_closed(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    git(scenario.remote, "symbolic-ref", "HEAD", "refs/heads/no-such-default")
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "no-head",
        "--branch",
        "feature/no-head",
    )
    assert result.returncode != 0
    assert not (scenario.primary / ".worktrees" / "no-head").exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/no-head",
            check=False,
        ).returncode
        != 0
    )


def test_remote_head_race_retries_once_then_uses_matching_sha(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    race_one, _ = _race_commits(scenario, tmp_path)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "race",
        "--branch",
        "feature/race",
        env=_race_proxy(tmp_path, scenario, [race_one]),
    )
    assert result.returncode == 0, result.stderr
    assert rev(scenario.primary / ".worktrees" / "race") == race_one


def test_second_remote_head_race_fails_without_residue(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    race_one, race_two = _race_commits(scenario, tmp_path)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "race-fails",
        "--branch",
        "feature/race-fails",
        env=_race_proxy(tmp_path, scenario, [race_one, race_two]),
    )
    assert result.returncode != 0
    assert not (scenario.primary / ".worktrees" / "race-fails").exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/race-fails",
            check=False,
        ).returncode
        != 0
    )
