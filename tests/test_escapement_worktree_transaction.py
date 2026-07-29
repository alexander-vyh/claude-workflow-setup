"""End-to-end transaction and rollback controls for Escapement worktrees."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from worktree_fixtures import git, make_remote_scenario, rev, run_cli


def _fake_bd(tmp_path: Path) -> Path:
    fake = tmp_path / "bd-bin"
    fake.mkdir()
    executable = fake / "bd"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

if sys.argv[1:] != ["context", "--json"]:
    raise SystemExit(19)
cwd = os.getcwd()
replacement = os.environ.get("REPLACE_TARGET")
if replacement and cwd == replacement:
    subprocess.run(
        [
            os.environ["REAL_GIT"],
            "-C",
            os.environ["PRIMARY_CWD"],
            "worktree",
            "remove",
            "--force",
            replacement,
        ],
        check=True,
    )
    Path(replacement).mkdir()
    (Path(replacement) / "preserve.txt").write_text("unrelated residue\\n", encoding="utf-8")
if os.environ.get("MOVE_BRANCH") and cwd == os.environ.get("TARGET_CWD"):
    subprocess.run([os.environ["REAL_GIT"], "-C", os.environ["PRIMARY_CWD"], "update-ref", os.environ["MOVE_BRANCH"], os.environ["MOVE_SHA"]], check=True)
identity = os.environ["BD_IDENTITY"]
if os.environ.get("MISMATCH_CWD") == cwd:
    identity = "mismatched-identity"
print(json.dumps({"project_id": identity, "database": identity, "beads_dir": identity, "repo_root": identity}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return fake


def _beads_env(
    tmp_path: Path,
    scenario,
    *,
    mismatch: Path | None = None,
    move: tuple[str, str, Path] | None = None,
    replace: Path | None = None,
) -> dict[str, str]:
    (scenario.primary / ".beads").mkdir(exist_ok=True)
    env = {
        "PATH": f"{_fake_bd(tmp_path)}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": shutil.which("git") or "git",
        "BD_IDENTITY": "same-live-tracker",
    }
    if mismatch is not None:
        env["MISMATCH_CWD"] = str(mismatch)
    if move is not None:
        branch, sha, target = move
        env.update(
            {
                "MOVE_BRANCH": f"refs/heads/{branch}",
                "MOVE_SHA": sha,
                "TARGET_CWD": str(target),
                "PRIMARY_CWD": str(scenario.primary),
            }
        )
    if replace is not None:
        env["REPLACE_TARGET"] = str(replace)
    return env


def test_success_reports_repo_branch_sha_source_kind_and_beads_status(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "beads-ok"
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "beads-ok",
        "--branch",
        "feature/beads-ok",
        env=_beads_env(tmp_path, scenario),
    )
    assert result.returncode == 0, result.stderr
    for expected in (
        str(scenario.primary),
        "feature/beads-ok",
        scenario.remote_head_sha,
        "remote-default",
        "beads",
    ):
        assert expected in result.stdout
    assert rev(target) == scenario.remote_head_sha


def test_created_common_directory_matches_requested_repository(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "common",
        "--branch",
        "feature/common",
    )
    target = scenario.primary / ".worktrees" / "common"
    assert result.returncode == 0, result.stderr
    assert (
        git(
            target, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
        == git(
            scenario.primary, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
    )


def test_beads_context_identity_matches_primary(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "same-context"
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "same-context",
        "--branch",
        "feature/same-context",
        env=_beads_env(tmp_path, scenario),
    )
    assert result.returncode == 0, result.stderr
    assert target.exists()
    assert "beads" in result.stdout.lower()


def test_mismatched_beads_context_rolls_back_target_and_branch(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "mismatch"
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "mismatch",
        "--branch",
        "feature/mismatch",
        env=_beads_env(tmp_path, scenario, mismatch=target),
    )
    assert result.returncode != 0
    assert not target.exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/mismatch",
            check=False,
        ).returncode
        != 0
    )


def test_branch_moved_during_failure_survives_guarded_rollback(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    moved = tmp_path / "moved-commit"
    moved.mkdir()
    git(tmp_path, "init", str(moved))
    (moved / "moved.txt").write_text("not transaction source\n", encoding="utf-8")
    git(moved, "add", "moved.txt")
    git(moved, "commit", "-m", "moved independently")
    moved_sha = rev(moved)
    git(scenario.primary, "fetch", str(moved), moved_sha)
    target = scenario.primary / ".worktrees" / "moved"
    env = _beads_env(
        tmp_path, scenario, mismatch=target, move=("feature/moved", moved_sha, target)
    )
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "moved",
        "--branch",
        "feature/moved",
        env=env,
    )
    assert result.returncode != 0
    assert rev(scenario.primary, "refs/heads/feature/moved") == moved_sha
    assert "refused to delete moved branch" in result.stderr


def test_displaced_target_survives_guarded_rollback(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "displaced"
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "displaced",
        "--branch",
        "feature/displaced",
        env=_beads_env(tmp_path, scenario, mismatch=target, replace=target),
    )

    sentinel = target / "preserve.txt"
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "unrelated residue\n"
    assert str(target) in result.stderr
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/displaced",
            check=False,
        ).returncode
        != 0
    )


def _git_proxy(tmp_path: Path, mode: str) -> dict[str, str]:
    proxy_dir = tmp_path / f"git-{mode}"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        "#!/usr/bin/env python3\nimport os, subprocess, sys\nargs=sys.argv[1:]\n"
        "if os.environ['PROXY_MODE'] == 'cleanup' and 'worktree' in args and 'remove' in args: raise SystemExit(43)\n"
        "r=subprocess.run([os.environ['REAL_GIT'], *args])\n"
        "if os.environ['PROXY_MODE'] == 'partial' and 'worktree' in args and 'add' in args: raise SystemExit(42)\nraise SystemExit(r.returncode)\n",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    return {
        "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": shutil.which("git") or "git",
        "PROXY_MODE": mode,
    }


def test_partial_git_creation_failure_is_inspected_and_cleaned(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "partial",
        "--branch",
        "feature/partial",
        env=_git_proxy(tmp_path, "partial"),
    )
    assert result.returncode != 0
    assert not (scenario.primary / ".worktrees" / "partial").exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/partial",
            check=False,
        ).returncode
        != 0
    )


def test_cleanup_failure_reports_exact_residue(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "cleanup-fails"
    git_env = _git_proxy(tmp_path, "cleanup")
    beads_env = _beads_env(tmp_path, scenario, mismatch=target)
    env = {**git_env, **beads_env}
    env["PATH"] = (
        f"{git_env['PATH'].split(os.pathsep, 1)[0]}{os.pathsep}{beads_env['PATH']}"
    )
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "cleanup-fails",
        "--branch",
        "feature/cleanup-fails",
        env=env,
    )
    assert result.returncode != 0
    assert str(target) in result.stderr
    assert target.exists()
