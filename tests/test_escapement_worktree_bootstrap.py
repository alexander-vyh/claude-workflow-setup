"""Behavioral boundary for repository-declared worktree provisioning.

The fixtures are intentionally repository-agnostic.  Their bootstrap commands
write externally observable sentinels; no test imports or mocks the transaction
implementation.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import pytest

from worktree_fixtures import git, make_remote_scenario, rev, run_cli


def _commit_contract(
    repo: Path,
    *,
    argv: object,
    timeout_seconds: object = 5,
    extra_bootstrap: dict[str, object] | None = None,
) -> str:
    bootstrap = {
        "argv": argv,
        "timeout_seconds": timeout_seconds,
        **(extra_bootstrap or {}),
    }
    config = {
        "intended_outcome": "merged",
        "worktree": {"bootstrap": bootstrap},
    }
    config_path = repo / ".escapement" / "repo.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    git(repo, "add", ".escapement/repo.json")
    git(repo, "commit", "-m", "declare portable worktree bootstrap")
    return rev(repo)


def _assert_no_transaction(repo: Path, name: str, branch: str) -> None:
    assert not (repo / ".worktrees" / name).exists()
    assert (
        git(
            repo,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        != 0
    )


def test_arbitrary_repository_bootstrap_runs_in_verified_target_at_source(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    sentinel = tmp_path / "portable-bootstrap.json"
    script = scenario.primary / "tools" / "bootstrap_probe.py"
    script.parent.mkdir()
    script.write_text(
        """from __future__ import annotations
import json
import subprocess
from pathlib import Path

sentinel = Path(%s)
payload = {
    "cwd": str(Path.cwd()),
    "head": subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip(),
    "marker": "portable-repository-command",
}
sentinel.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
"""
        % repr(str(sentinel)),
        encoding="utf-8",
    )
    git(scenario.primary, "add", "tools/bootstrap_probe.py")
    _commit_contract(
        scenario.primary,
        argv=[sys.executable, "tools/bootstrap_probe.py"],
    )
    source_sha = rev(scenario.primary)
    name = "portable"
    branch = "feature/portable-bootstrap"
    target = scenario.primary / ".worktrees" / name

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

    assert result.returncode == 0, result.stderr
    observed = json.loads(sentinel.read_text(encoding="utf-8"))
    assert observed == {
        "cwd": str(target),
        "head": source_sha,
        "marker": "portable-repository-command",
    }
    assert rev(target) == source_sha


@pytest.mark.parametrize(
    ("argv", "timeout"),
    [
        (None, 5),
        ([], 5),
        ([""], 5),
        (["tool", 7], 5),
        ("tool --flag", 5),
        (["tool"], None),
        (["tool"], True),
        (["tool"], 0),
        (["tool"], -1),
        (["tool"], "5"),
    ],
)
def test_malformed_bootstrap_contract_fails_before_git_creation(
    tmp_path: Path,
    argv: object,
    timeout: object,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source_sha = _commit_contract(
        scenario.primary,
        argv=argv,
        timeout_seconds=timeout,
    )
    name = "malformed"
    branch = "feature/malformed-bootstrap"

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
    _assert_no_transaction(scenario.primary, name, branch)


@pytest.mark.parametrize(
    ("argv_builder", "timeout", "expected_fragment"),
    [
        (lambda _marker: ["definitely-not-a-real-bootstrap-executable"], 5, "executable"),
        (
            lambda marker: [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran'); raise SystemExit(23)",
            ],
            5,
            "23",
        ),
        (
            lambda marker: [
                sys.executable,
                "-c",
                f"from pathlib import Path; import time; Path({str(marker)!r}).write_text('started'); time.sleep(2)",
            ],
            0.05,
            "timed out",
        ),
    ],
    ids=["missing-executable", "nonzero", "timeout"],
)
def test_configured_bootstrap_failure_rolls_back_owned_worktree_and_branch(
    tmp_path: Path,
    argv_builder,
    timeout: float,
    expected_fragment: str,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    marker = tmp_path / "bootstrap-attempted"
    source_sha = _commit_contract(
        scenario.primary,
        argv=argv_builder(marker),
        timeout_seconds=timeout,
    )
    name = "bootstrap-fails"
    branch = "feature/bootstrap-fails"

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
    assert expected_fragment in result.stderr.lower()
    _assert_no_transaction(scenario.primary, name, branch)


def test_shell_metacharacters_are_literal_argv_not_executed(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    escaped = tmp_path / "must-not-be-created"
    source_sha = _commit_contract(
        scenario.primary,
        argv=[f"missing-command;touch {escaped}"],
        timeout_seconds=5,
    )
    name = "no-shell"
    branch = "feature/no-shell-bootstrap"

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
    assert not escaped.exists()
    _assert_no_transaction(scenario.primary, name, branch)


def test_bootstrap_contract_is_read_from_resolved_source_not_dirty_primary(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source_marker = tmp_path / "source-contract-ran"
    dirty_marker = tmp_path / "dirty-primary-contract-ran"
    source_sha = _commit_contract(
        scenario.primary,
        argv=[
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(source_marker)!r}).write_text('source')",
        ],
    )
    dirty_config = {
        "worktree": {
            "bootstrap": {
                "argv": [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(dirty_marker)!r}).write_text('dirty')",
                ],
                "timeout_seconds": 5,
            }
        }
    }
    (scenario.primary / ".escapement" / "repo.json").write_text(
        json.dumps(dirty_config), encoding="utf-8"
    )

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "source-policy",
        "--branch",
        "feature/source-policy",
        "--source",
        source_sha,
    )

    assert result.returncode == 0, result.stderr
    assert source_marker.read_text(encoding="utf-8") == "source"
    assert not dirty_marker.exists()


def test_repository_without_contract_does_not_run_named_convention(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    marker = tmp_path / "convention-must-not-run"
    conventional = scenario.primary / "worktree-bootstrap"
    conventional.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    conventional.chmod(0o755)
    git(scenario.primary, "add", "worktree-bootstrap")
    git(scenario.primary, "commit", "-m", "add unconfigured conventional script")
    source_sha = rev(scenario.primary)

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "no-convention",
        "--branch",
        "feature/no-convention",
        "--source",
        source_sha,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_repository_without_contract_does_not_auto_detect_justfile_recipe(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    marker = tmp_path / "justfile-must-not-run"
    (scenario.primary / "Justfile").write_text(
        "worktree-bootstrap:\n"
        f"    @touch {marker}\n",
        encoding="utf-8",
    )
    git(scenario.primary, "add", "Justfile")
    git(scenario.primary, "commit", "-m", "add unconfigured Justfile recipe")
    source_sha = rev(scenario.primary)

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "no-justfile-convention",
        "--branch",
        "feature/no-justfile-convention",
        "--source",
        source_sha,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_bootstrap_waits_for_target_beads_identity_verification(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    bootstrap_marker = tmp_path / "bootstrap-must-not-run"
    beads_target_marker = tmp_path / "target-beads-checked"
    source_sha = _commit_contract(
        scenario.primary,
        argv=[
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(bootstrap_marker)!r}).write_text('ran')",
        ],
    )
    (scenario.primary / ".beads").mkdir()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        """#!%s
import json
import os
from pathlib import Path

target = os.environ["EXPECTED_TARGET"]
identity = "primary"
if os.getcwd() == target:
    Path(os.environ["TARGET_BEADS_MARKER"]).write_text("checked")
    identity = "mismatch"
print(json.dumps({
    "project_id": identity,
    "database": identity,
    "beads_dir": identity,
    "repo_root": identity,
}))
"""
        % sys.executable,
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)
    name = "beads-first"
    target = scenario.primary / ".worktrees" / name
    branch = "feature/beads-first"

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
        env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "EXPECTED_TARGET": str(target),
            "TARGET_BEADS_MARKER": str(beads_target_marker),
        },
    )

    assert result.returncode != 0
    assert beads_target_marker.read_text(encoding="utf-8") == "checked"
    assert not bootstrap_marker.exists()
    _assert_no_transaction(scenario.primary, name, branch)


def test_failed_bootstrap_preserves_branch_moved_by_bootstrap(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    branch = "feature/bootstrap-moved-branch"
    source_sha = _commit_contract(
        scenario.primary,
        argv=[
            sys.executable,
            "-c",
            (
                "import subprocess; "
                f"subprocess.run(['git','update-ref','refs/heads/{branch}',"
                f"'{scenario.stale_primary_sha}'],check=True); "
                "raise SystemExit(31)"
            ),
        ],
    )

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        "moved-branch",
        "--branch",
        branch,
        "--source",
        source_sha,
    )

    assert result.returncode != 0
    assert rev(scenario.primary, f"refs/heads/{branch}") == scenario.stale_primary_sha
    assert "refused to delete moved branch" in result.stderr


def test_failed_bootstrap_preserves_replacement_target(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    name = "replaced-target"
    branch = "feature/replaced-target"
    target = scenario.primary / ".worktrees" / name
    replacement_marker = target / "preserve.txt"
    code = (
        "import subprocess; from pathlib import Path; "
        f"subprocess.run(['git','-C',{str(scenario.primary)!r},'worktree','remove','--force',{str(target)!r}],check=True); "
        f"Path({str(target)!r}).mkdir(); "
        f"Path({str(replacement_marker)!r}).write_text('unrelated replacement'); "
        "raise SystemExit(37)"
    )
    source_sha = _commit_contract(
        scenario.primary,
        argv=[sys.executable, "-c", code],
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
    assert replacement_marker.read_text(encoding="utf-8") == "unrelated replacement"
    assert "preserved unowned target" in result.stderr


def test_unknown_bootstrap_fields_fail_closed(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    source_sha = _commit_contract(
        scenario.primary,
        argv=[sys.executable, "-c", "raise SystemExit(0)"],
        extra_bootstrap={"shell": True},
    )
    name = "unknown-contract"
    branch = "feature/unknown-contract"

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
    _assert_no_transaction(scenario.primary, name, branch)


def test_bootstrap_starts_without_implicitly_copied_test_cache(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    primary_cache = scenario.primary / ".testmondata"
    primary_cache.write_bytes(b"stale-primary-cache")
    target_cache_bytes = b"target-generated-cache"
    code = (
        "from pathlib import Path; "
        "cache=Path('.testmondata'); "
        "assert not cache.exists(), 'target must start without copied cache'; "
        f"cache.write_bytes({target_cache_bytes!r})"
    )
    source_sha = _commit_contract(
        scenario.primary,
        argv=[sys.executable, "-c", code],
    )
    name = "cache-owned"
    target = scenario.primary / ".worktrees" / name

    result = run_cli(
        scenario.primary,
        "create",
        "--repo",
        str(scenario.primary),
        "--name",
        name,
        "--branch",
        "feature/cache-owned",
        "--source",
        source_sha,
    )

    assert result.returncode == 0, result.stderr
    assert primary_cache.read_bytes() == b"stale-primary-cache"
    assert (target / ".testmondata").read_bytes() == target_cache_bytes


def test_bootstrap_terminated_by_signal_fails_and_rolls_back(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    source_sha = _commit_contract(
        scenario.primary,
        argv=[
            sys.executable,
            "-c",
            "import os, signal; os.kill(os.getpid(), signal.SIGTERM)",
        ],
    )
    name = "signaled"
    branch = "feature/signaled-bootstrap"

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
    assert any(word in result.stderr.lower() for word in ("signal", "terminated", str(signal.SIGTERM)))
    _assert_no_transaction(scenario.primary, name, branch)
