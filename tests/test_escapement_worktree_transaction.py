"""End-to-end transaction and rollback controls for Escapement worktrees."""
# file-complexity-waiver: one public transaction fixture matrix and its failure boundaries

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from worktree_fixtures import CLI, git, make_remote_scenario, rev, run_cli


def _success_record(output: str) -> dict[str, str]:
    """Accept a concise JSON record or documented labeled text, not loose words."""
    try:
        raw = json.loads(output)
    except json.JSONDecodeError:
        raw = {
            key: value.strip('"')
            for key, value in re.findall(r"([a-z_]+)=([^\s]+)", output)
        }
    aliases = {
        "repository": ("repository", "repo"),
        "branch": ("branch",),
        "source": ("source", "sha"),
        "source_kind": ("source_kind", "source-kind"),
        "beads": ("beads", "beads_status"),
    }
    return {
        expected: str(next(raw[name] for name in names if name in raw))
        for expected, names in aliases.items()
    }


def _worktree_stanzas(porcelain: str) -> list[dict[str, str]]:
    stanzas = []
    for block in porcelain.strip().split("\n\n"):
        if not block:
            continue
        stanza = {}
        for line in block.splitlines():
            key, separator, value = line.partition(" ")
            stanza[key] = value if separator else ""
        stanzas.append(stanza)
    return stanzas


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
mode = os.environ.get("BROKEN_BD_MODE") if cwd == os.environ.get("BROKEN_BD_CWD") else ""
if mode == "fail":
    raise SystemExit(88)
if mode == "malformed":
    print("{not-json")
    raise SystemExit(0)
identity = os.environ["BD_IDENTITY"]
if os.environ.get("MISMATCH_CWD") == cwd:
    identity = "mismatched-identity"
context = {"project_id": identity, "database": identity, "beads_dir": identity, "repo_root": identity}
if mode == "partial":
    context["database"] = "different-database"
if mode == "missing":
    context.pop("repo_root")
print(json.dumps(context))
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
    broken: tuple[str, Path] | None = None,
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
        env.update(
            {
                "REPLACE_TARGET": str(replace),
                "PRIMARY_CWD": str(scenario.primary),
            }
        )
    if broken is not None:
        mode, target = broken
        env.update({"BROKEN_BD_MODE": mode, "BROKEN_BD_CWD": str(target)})
    return env


def _beads_without_bd_env(tmp_path: Path, scenario) -> dict[str, str]:
    """Require Beads while exposing Git and Python but no `bd` executable."""
    (scenario.primary / ".beads").mkdir(exist_ok=True)
    available = tmp_path / "without-bd"
    available.mkdir()
    for executable in ("git", "python3"):
        resolved = shutil.which(executable)
        assert resolved, f"test host requires {executable}"
        (available / executable).symlink_to(resolved)
    assert shutil.which("bd", path=str(available)) is None
    return {"PATH": str(available)}


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
    record = _success_record(result.stdout)
    assert record == {
        "repository": str(scenario.primary),
        "branch": "feature/beads-ok",
        "source": scenario.remote_head_sha,
        "source_kind": "remote-default",
        "beads": "verified",
    }
    assert rev(target) == scenario.remote_head_sha
    assert (
        git(target, "symbolic-ref", "--short", "HEAD").stdout.strip()
        == "feature/beads-ok"
    )
    listing = git(scenario.primary, "worktree", "list", "--porcelain").stdout
    assert any(
        stanza.get("worktree") == str(target.resolve())
        and stanza.get("HEAD") == scenario.remote_head_sha
        and stanza.get("branch") == "refs/heads/feature/beads-ok"
        for stanza in _worktree_stanzas(listing)
    )


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


def test_beads_required_but_bd_absent_fails_closed(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "no-bd"
    branch = "feature/no-bd"
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "no-bd",
        "--branch",
        branch,
        env=_beads_without_bd_env(tmp_path, scenario),
    )
    assert result.returncode != 0
    assert not target.exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        != 0
    )


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


@pytest.mark.parametrize("mode", ["partial", "missing", "malformed", "fail"])
def test_required_beads_evidence_fails_closed_for_incomplete_or_broken_context(
    tmp_path: Path, mode: str
) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / f"broken-{mode}"
    branch = f"feature/broken-{mode}"
    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        f"broken-{mode}",
        "--branch",
        branch,
        env=_beads_env(tmp_path, scenario, broken=(mode, target)),
    )
    assert result.returncode != 0
    assert not target.exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
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


def _git_proxy(
    tmp_path: Path, mode: str, *, inspection_ref: str | None = None
) -> dict[str, str]:
    proxy_dir = tmp_path / f"git-{mode}"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
mode = os.environ["PROXY_MODE"]
if mode == "cleanup" and (
    ("worktree" in args and "remove" in args)
    or ("update-ref" in args and "-d" in args)
):
    raise SystemExit(43)
if mode == "external-branch":
    marker = Path(os.environ["EXTERNAL_BRANCH_MARKER"])
    if "update-ref" in args and not marker.exists():
        subprocess.run(
            [
                os.environ["REAL_GIT"],
                "-C",
                args[args.index("-C") + 1],
                "update-ref",
                os.environ["EXTERNAL_BRANCH_REF"],
                os.environ["EXTERNAL_BRANCH_SHA"],
            ],
            check=True,
        )
        marker.touch()
if (
    mode == "branch-inspection"
    and "rev-parse" in args
    and args[-1] == os.environ["INSPECTION_REF"]
):
    print("simulated rollback branch inspection failure", file=sys.stderr)
    raise SystemExit(74)
if mode == "add-fails" and "worktree" in args and "add" in args:
    raise SystemExit(42)
result = subprocess.run([os.environ["REAL_GIT"], *args])
if mode == "partial" and "worktree" in args and "add" in args:
    raise SystemExit(42)
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    env = {
        "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": shutil.which("git") or "git",
        "PROXY_MODE": mode,
    }
    if inspection_ref is not None:
        env["INSPECTION_REF"] = f"{inspection_ref}^{{commit}}"
    if mode == "external-branch":
        env["EXTERNAL_BRANCH_MARKER"] = str(tmp_path / "external-branch-created")
    return env


def _verification_displacement_env(
    tmp_path: Path,
    scenario,
    target: Path,
    replacement: Path,
) -> dict[str, str]:
    proxy_dir = tmp_path / "verification-displacement-git"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
if "update-ref" in args and "--stdin" in args:
    os.execv(os.environ["REAL_GIT"], [os.environ["REAL_GIT"], *args])
result = subprocess.run(
    [os.environ["REAL_GIT"], *args],
    text=True,
    capture_output=True,
)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
target = Path(os.environ["DISPLACE_TARGET"])
if (
    result.returncode == 0
    and args[:2] == ["-C", str(target)]
    and args[2:] == [
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    ]
):
    removal = subprocess.run(
        [
            os.environ["REAL_GIT"],
            "-C",
            os.environ["PRIMARY_CWD"],
            "worktree",
            "remove",
            "--force",
            str(target),
        ],
        text=True,
        capture_output=True,
    )
    if removal.returncode:
        sys.stderr.write(removal.stderr or removal.stdout)
        raise SystemExit(removal.returncode)
    target.symlink_to(Path(os.environ["REPLACEMENT_TARGET"]), target_is_directory=True)
    Path(os.environ["DISPLACEMENT_MARKER"]).touch()
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    return {
        "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": shutil.which("git") or "git",
        "PRIMARY_CWD": str(scenario.primary),
        "DISPLACE_TARGET": str(target),
        "REPLACEMENT_TARGET": str(replacement),
        "DISPLACEMENT_MARKER": str(tmp_path / "verification-displaced"),
    }


def test_partial_git_creation_failure_preserves_the_unbound_worktree(
    tmp_path: Path,
) -> None:
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
    target = scenario.primary / ".worktrees" / "partial"
    assert target.exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/partial",
            check=False,
        ).returncode
        == 0
    )
    receipt = json.loads(
        (scenario.primary.parent / "harness/worktrees/partial.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["phase"] == "bootstrap_failed"
    assert receipt["last_reason"] == "creation-instance-mismatch"


def test_worktree_add_failure_before_creation_cleans_the_owned_branch(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "add-fails"
    branch_ref = "refs/heads/feature/add-fails"

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "add-fails",
        "--branch",
        "feature/add-fails",
        env=_git_proxy(tmp_path, "add-fails"),
    )

    assert result.returncode != 0
    assert not target.exists()
    assert git(
        scenario.primary,
        "show-ref",
        "--verify",
        "--quiet",
        branch_ref,
        check=False,
    ).returncode == 1
    assert not (scenario.primary.parent / "harness/worktrees/add-fails.json").exists()


def test_same_sha_branch_created_by_another_writer_survives_failed_creation(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "external-winner"
    branch = "feature/external-winner"
    branch_ref = f"refs/heads/{branch}"
    env = _git_proxy(tmp_path, "external-branch")
    env.update(
        {
            "EXTERNAL_BRANCH_REF": branch_ref,
            "EXTERNAL_BRANCH_SHA": scenario.remote_head_sha,
        }
    )

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "external-winner",
        "--branch",
        branch,
        env=env,
    )

    assert result.returncode != 0
    assert Path(env["EXTERNAL_BRANCH_MARKER"]).exists()
    assert not target.exists()
    assert rev(scenario.primary, branch_ref) == scenario.remote_head_sha


def test_cleanup_failure_reports_exact_residue(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "cleanup-fails"
    beads_env = _beads_env(tmp_path, scenario, mismatch=target)
    fault_dir = tmp_path / "cleanup-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import shutil

real_rmtree = shutil.rmtree

def fail_rollback_cleanup(path, *args, **kwargs):
    if "escapement-worktree-rollbacks" in str(path):
        raise OSError("simulated exact rollback cleanup failure")
    return real_rmtree(path, *args, **kwargs)

shutil.rmtree = fail_rollback_cleanup
""",
        encoding="utf-8",
    )
    env = {
        **beads_env,
        "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
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
    assert "simulated exact rollback cleanup failure" in result.stderr
    assert ".escapement-rollback-" in result.stderr
    assert not target.exists()
    receipt = json.loads(
        (scenario.primary.parent / "harness/worktrees/cleanup-fails.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["phase"] == "rollback_claimed"
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    assert list((common_dir / "escapement-worktree-rollbacks").glob("*/worktree"))
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/feature/cleanup-fails",
            check=False,
        ).returncode
        == 0
    )


def test_verification_resolution_race_uses_public_error_and_guarded_cleanup(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "resolution-race"
    replacement = tmp_path / "unrelated-missing-replacement"
    branch = "feature/resolution-race"
    branch_ref = f"refs/heads/{branch}"
    env = _verification_displacement_env(tmp_path, scenario, target, replacement)

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "resolution-race",
        "--branch",
        branch,
        env=env,
    )

    assert Path(env["DISPLACEMENT_MARKER"]).exists()
    assert result.returncode != 0
    assert result.stderr.startswith("escapement-worktree: "), result.stderr
    assert "Traceback" not in result.stderr
    assert str(target) in result.stderr
    assert target.is_symlink()
    assert target.readlink() == replacement
    assert not replacement.exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            branch_ref,
            check=False,
        ).returncode
        != 0
    )


def test_branch_inspection_failure_reports_surviving_ref_residue(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    target = scenario.primary / ".worktrees" / "branch-inspection-residue"
    branch = "feature/branch-inspection-residue"
    branch_ref = f"refs/heads/{branch}"
    beads_env = _beads_env(tmp_path, scenario, mismatch=target)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    watched_ref = common_dir / branch_ref
    fault_dir = tmp_path / "branch-inspection-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import os

real_open = os.open

def fail_ref_inspection(path, flags, *args, **kwargs):
    candidate = os.fspath(path)
    watched = os.environ["WATCHED_REF"]
    if (
        (
            (os.path.isabs(candidate) and os.path.abspath(candidate) == watched)
            or (
                not os.path.isabs(candidate)
                and os.path.basename(candidate) == os.path.basename(watched)
            )
        )
        and flags & os.O_ACCMODE == os.O_RDONLY
    ):
        raise PermissionError("simulated rollback branch inspection failure")
    return real_open(path, flags, *args, **kwargs)

os.open = fail_ref_inspection
""",
        encoding="utf-8",
    )
    env = {
        **beads_env,
        "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "WATCHED_REF": str(watched_ref),
    }

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "branch-inspection-residue",
        "--branch",
        branch,
        env=env,
    )

    assert result.returncode != 0
    assert not target.exists()
    assert rev(scenario.primary, branch_ref) == scenario.remote_head_sha
    rollback_label = "rollback residue: "
    assert rollback_label in result.stderr
    rollback_residue = result.stderr.split(rollback_label, 1)[1]
    assert f"failed to inspect branch {branch_ref}:" in rollback_residue
    assert "simulated rollback branch inspection failure" in rollback_residue
    assert "Traceback" not in result.stderr


def test_repository_wide_transaction_lock_serializes_source_resolution(
    tmp_path: Path,
) -> None:
    """Two independent branches cannot enter source resolution concurrently."""
    assert CLI.is_file(), "the transaction executable is required for this probe"
    scenario = make_remote_scenario(tmp_path)
    proxy_dir = tmp_path / "locking-git"
    proxy_dir.mkdir()
    entered = tmp_path / "entered"
    overlap = tmp_path / "overlap"
    release = tmp_path / "release"
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
entered = Path(os.environ["LOCK_ENTERED"])
overlap = Path(os.environ["LOCK_OVERLAP"])
release = Path(os.environ["LOCK_RELEASE"])
if "ls-remote" in args and args[-1] == "HEAD":
    if entered.exists():
        overlap.write_text("concurrent source resolution\\n", encoding="utf-8")
    else:
        entered.write_text("first transaction entered\\n", encoding="utf-8")
        while not release.exists():
            time.sleep(0.01)
        entered.unlink()
result = subprocess.run([os.environ["REAL_GIT"], *args])
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
        "CONTINUATION_HARNESS_HOME": str(tmp_path / "harness"),
        "REAL_GIT": shutil.which("git") or "git",
        "LOCK_ENTERED": str(entered),
        "LOCK_OVERLAP": str(overlap),
        "LOCK_RELEASE": str(release),
    }
    command = [str(CLI), "create", "--repo", str(scenario.primary)]
    first = subprocess.Popen(
        [*command, "--name", "locked-one", "--branch", "feature/locked-one"],
        cwd=scenario.primary,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert entered.exists(), "first transaction never reached remote discovery"
    second = subprocess.Popen(
        [*command, "--name", "locked-two", "--branch", "feature/locked-two"],
        cwd=scenario.primary,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.2)
    assert not overlap.exists(), (
        "separate transaction reached source resolution before lock release"
    )
    release.touch()
    first_out, first_err = first.communicate(timeout=30)
    second_out, second_err = second.communicate(timeout=30)
    assert first.returncode == 0, first_err or first_out
    assert second.returncode == 0, second_err or second_out
    assert not overlap.exists()
