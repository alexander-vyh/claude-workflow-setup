"""Behavioral boundary for repository-declared worktree provisioning.

The fixtures are intentionally repository-agnostic.  Their bootstrap commands
write externally observable sentinels; no test imports or mocks the transaction
implementation.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from worktree_fixtures import git, make_remote_scenario, rev, run_cli


ROOT = Path(__file__).resolve().parents[1]


def escaped_raw_bytes(raw: bytes) -> str:
    """Render the contract's reversible fixed-width byte diagnostic."""
    return "".join(f"\\x{byte:02x}" for byte in raw)


PUBLIC_WORKTREE_CLIS = (
    pytest.param(ROOT / "bin" / "escapement-worktree", id="canonical"),
    pytest.param(
        ROOT / "plugins" / "escapement" / "bin" / "escapement-worktree",
        id="codex-plugin",
    ),
    pytest.param(
        ROOT / "plugins" / "escapement-claude" / "bin" / "escapement-worktree",
        id="claude-plugin",
    ),
)


@pytest.fixture(params=PUBLIC_WORKTREE_CLIS)
def public_worktree_cli(request: pytest.FixtureRequest) -> Path:
    cli = request.param
    assert cli.is_file()
    assert os.access(cli, os.X_OK)
    return cli


def _run_public_cli(
    cli: Path,
    primary: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(cli), *args],
        cwd=primary,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


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
    "argv",
    [
        ["bad\0executable"],
        [sys.executable, "bad\0argument"],
    ],
    ids=["executable", "later-argument"],
)
def test_embedded_nul_fails_before_git_creation_without_traceback(
    tmp_path: Path, argv: list[str]
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source_sha = _commit_contract(scenario.primary, argv=argv)
    name = "nul-argv"
    branch = "feature/nul-argv"

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
    assert "traceback" not in result.stderr.lower()
    _assert_no_transaction(scenario.primary, name, branch)


@pytest.mark.parametrize(
    ("argv_builder", "timeout", "expected_fragment"),
    [
        (
            lambda _marker: ["definitely-not-a-real-bootstrap-executable"],
            5,
            "executable",
        ),
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
        f"worktree-bootstrap:\n    @touch {marker}\n",
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
    assert any(
        word in result.stderr.lower()
        for word in ("signal", "terminated", str(signal.SIGTERM))
    )
    _assert_no_transaction(scenario.primary, name, branch)


def test_timeout_kills_delayed_descendant_before_rollback(
    tmp_path: Path, public_worktree_cli: Path
) -> None:
    scenario = make_remote_scenario(tmp_path)
    name = "delayed-descendant"
    branch = "feature/delayed-descendant"
    target = scenario.primary / ".worktrees" / name
    late_marker = target / "late-descendant.txt"
    spawned_marker = tmp_path / "descendant-spawned.txt"
    child_code = (
        "import time; from pathlib import Path; "
        "time.sleep(1.5); "
        f"target=Path({str(target)!r}); target.mkdir(parents=True, exist_ok=True); "
        f"Path({str(late_marker)!r}).write_text('late descendant survived')"
    )
    parent_code = (
        "import subprocess, sys, time; from pathlib import Path; "
        f"subprocess.Popen([{sys.executable!r}, '-c', {child_code!r}]); "
        f"Path({str(spawned_marker)!r}).write_text('spawned'); "
        "print('timeout stdout tail', flush=True); "
        "print('timeout stderr tail', file=sys.stderr, flush=True); "
        "time.sleep(10)"
    )
    source_sha = _commit_contract(
        scenario.primary,
        argv=[sys.executable, "-c", parent_code],
        timeout_seconds=0.5,
    )

    result = _run_public_cli(
        public_worktree_cli,
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
    assert spawned_marker.read_text(encoding="utf-8") == "spawned"
    assert escaped_raw_bytes(b"timeout stdout tail") in result.stderr
    assert escaped_raw_bytes(b"timeout stderr tail") in result.stderr
    immediate_target_exists = target.exists()
    immediate_branch_exists = (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        == 0
    )
    time.sleep(1.7)
    assert not immediate_target_exists
    assert not immediate_branch_exists
    assert not target.exists()
    assert not late_marker.exists()
    _assert_no_transaction(scenario.primary, name, branch)


def test_failed_bootstrap_preserves_valid_replacement_worktree_identity(
    tmp_path: Path, public_worktree_cli: Path
) -> None:
    scenario = make_remote_scenario(tmp_path)
    name = "registered-replacement"
    branch = "feature/registered-replacement"
    target = scenario.primary / ".worktrees" / name
    marker = target / "replacement-marker.txt"
    code = (
        "import subprocess; from pathlib import Path; "
        f"repo={str(scenario.primary)!r}; target={str(target)!r}; branch={branch!r}; "
        "subprocess.run(['git','-C',repo,'worktree','remove','--force',target],check=True); "
        f"subprocess.run(['git','-C',repo,'update-ref',f'refs/heads/{{branch}}',{scenario.stale_primary_sha!r}],check=True); "
        "subprocess.run(['git','-C',repo,'worktree','add',target,branch],check=True); "
        f"Path({str(marker)!r}).write_text('valid foreign replacement'); "
        "raise SystemExit(47)"
    )
    source_sha = _commit_contract(
        scenario.primary,
        argv=[sys.executable, "-c", code],
    )

    result = _run_public_cli(
        public_worktree_cli,
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
    assert marker.read_text(encoding="utf-8") == "valid foreign replacement"
    assert rev(target) == scenario.stale_primary_sha
    assert rev(scenario.primary, f"refs/heads/{branch}") == scenario.stale_primary_sha
    listing = git(scenario.primary, "worktree", "list", "--porcelain").stdout
    assert (
        f"worktree {target.resolve()}\n"
        f"HEAD {scenario.stale_primary_sha}\n"
        f"branch refs/heads/{branch}"
    ) in listing


def test_failed_bootstrap_preserves_foreign_same_branch_same_sha_worktree(
    tmp_path: Path, public_worktree_cli: Path
) -> None:
    scenario = make_remote_scenario(tmp_path)
    name = "foreign-branch-owner"
    branch = "feature/foreign-branch-owner"
    target = scenario.primary / ".worktrees" / name
    foreign_target = tmp_path / "foreign\nregistered-worktree"
    marker = foreign_target / "foreign-owner-marker.txt"
    code = (
        "import subprocess; from pathlib import Path; "
        f"repo={str(scenario.primary)!r}; target={str(target)!r}; "
        f"foreign={str(foreign_target)!r}; branch={branch!r}; "
        "subprocess.run(['git','-C',repo,'worktree','remove','--force',target],check=True); "
        "subprocess.run(['git','-C',repo,'worktree','add',foreign,branch],check=True); "
        f"Path({str(marker)!r}).write_text('foreign worktree owns branch'); "
        "raise SystemExit(61)"
    )
    source_sha = _commit_contract(
        scenario.primary,
        argv=[sys.executable, "-c", code],
    )

    result = _run_public_cli(
        public_worktree_cli,
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
    assert not target.exists()
    assert marker.read_text(encoding="utf-8") == "foreign worktree owns branch"
    assert rev(foreign_target) == source_sha
    assert rev(scenario.primary, f"refs/heads/{branch}") == source_sha
    listing = git(scenario.primary, "worktree", "list", "--porcelain", "-z").stdout
    records = [record.split("\0") for record in listing.split("\0\0") if record]
    assert any(
        f"worktree {foreign_target.resolve()}" in record
        and f"HEAD {source_sha}" in record
        and f"branch refs/heads/{branch}" in record
        for record in records
    )


def test_noisy_failure_reports_bounded_output_tails(
    tmp_path: Path, public_worktree_cli: Path
) -> None:
    scenario = make_remote_scenario(tmp_path)
    code = (
        "import sys; "
        "sys.stdout.write('o' * 250000 + ' useful stdout tail\\n'); "
        "sys.stderr.write('e' * 250000 + '\\x1b[31m useful stderr tail\\n'); "
        "raise SystemExit(53)"
    )
    source_sha = _commit_contract(
        scenario.primary,
        argv=[sys.executable, "-c", code],
    )
    name = "noisy-failure"
    branch = "feature/noisy-failure"

    result = _run_public_cli(
        public_worktree_cli,
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
    assert result.stdout == ""
    assert "exit status 53" in result.stderr
    assert "\x1b" not in result.stderr
    failure = result.stderr.removesuffix("\n")
    stderr_section, stdout_tail = failure.rsplit("; stdout tail: ", maxsplit=1)
    _prefix, stderr_tail = stderr_section.rsplit("; stderr tail: ", maxsplit=1)
    stdout_suffix = b" useful stdout tail\n"
    stderr_suffix = b"\x1b[31m useful stderr tail\n"
    expected_stdout = escaped_raw_bytes(
        b"o" * (8192 - len(stdout_suffix)) + stdout_suffix
    )
    expected_stderr = escaped_raw_bytes(
        b"e" * (8192 - len(stderr_suffix)) + stderr_suffix
    )
    assert stdout_tail == expected_stdout
    assert stderr_tail == expected_stderr
    _assert_no_transaction(scenario.primary, name, branch)
