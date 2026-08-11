"""Focused transaction-safety controls for repository-declared bootstrap."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from test_escapement_worktree_bootstrap import (
    _assert_no_transaction,
    _commit_contract,
    _run_public_cli,
)
from worktree_fixtures import make_remote_scenario, rev, run_cli

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_WORKTREE_CLI = ROOT / "bin" / "escapement-worktree"


def test_failed_bootstrap_preserves_same_sha_replacement_instance(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    name = "same-sha-replacement"
    branch = "feature/same-sha-replacement"
    target = scenario.primary / ".worktrees" / name
    marker = target / "replacement-marker.txt"
    identities = tmp_path / "admin-identities.json"
    code = (
        "import json, subprocess; from pathlib import Path; "
        f"repo={str(scenario.primary)!r}; target=Path({str(target)!r}); branch={branch!r}; "
        "admin=lambda: Path((target/'.git').read_text().split(': ',1)[1].strip()); "
        "old=admin().stat(); "
        "subprocess.run(['git','-C',repo,'worktree','remove','--force',str(target)],check=True); "
        "subprocess.run(['git','-C',repo,'worktree','add',str(target),branch],check=True); "
        "new=admin().stat(); "
        f"Path({str(identities)!r}).write_text(json.dumps([[old.st_dev,old.st_ino],[new.st_dev,new.st_ino]])); "
        f"Path({str(marker)!r}).write_text('replacement'); "
        "raise SystemExit(71)"
    )
    source_sha = _commit_contract(
        scenario.primary,
        argv=[sys.executable, "-c", code],
    )

    result = _run_public_cli(
        PUBLIC_WORKTREE_CLI,
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        name,
        "--branch",
        branch,
        "--source",
        source_sha,
    )

    assert result.returncode != 0
    old_identity, new_identity = json.loads(identities.read_text(encoding="utf-8"))
    assert old_identity != new_identity
    assert marker.read_text(encoding="utf-8") == "replacement"
    assert rev(target) == source_sha
    assert rev(scenario.primary, f"refs/heads/{branch}") == source_sha


def test_successful_bootstrap_is_reverified_before_create_returns(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    name = "post-bootstrap-reverify"
    branch = "feature/post-bootstrap-reverify"
    target = scenario.primary / ".worktrees" / name
    source_sha = _commit_contract(
        scenario.primary,
        argv=["git", "reset", "--hard", scenario.stale_primary_sha],
    )

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        name,
        "--branch",
        branch,
        "--source",
        source_sha,
    )

    assert result.returncode != 0
    assert "head mismatch" in result.stderr.lower()
    assert rev(target) == scenario.stale_primary_sha
    assert rev(scenario.primary, f"refs/heads/{branch}") == scenario.stale_primary_sha


def test_timeout_contains_descendant_that_escapes_the_bootstrap_session(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    name = "escaped-session"
    branch = "feature/escaped-session"
    target = scenario.primary / ".worktrees" / name
    spawned = tmp_path / "escaped-child-spawned"
    late_marker = target / "late-write.txt"
    child_code = (
        "import time; from pathlib import Path; time.sleep(1.2); "
        f"target=Path({str(target)!r}); target.mkdir(parents=True,exist_ok=True); "
        f"Path({str(late_marker)!r}).write_text('escaped')"
    )
    parent_code = (
        "import subprocess, sys, time; from pathlib import Path; "
        f"subprocess.Popen([{sys.executable!r},'-c',{child_code!r}],start_new_session=True,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        f"Path({str(spawned)!r}).write_text('spawned'); time.sleep(10)"
    )
    source_sha = _commit_contract(
        scenario.primary,
        argv=[sys.executable, "-c", parent_code],
        timeout_seconds=0.4,
    )

    result = _run_public_cli(
        PUBLIC_WORKTREE_CLI,
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        name,
        "--branch",
        branch,
        "--source",
        source_sha,
    )

    assert result.returncode != 0
    assert spawned.read_text(encoding="utf-8") == "spawned"
    time.sleep(1.4)
    assert not target.exists()
    assert not late_marker.exists()
    _assert_no_transaction(scenario.primary, name, branch)
