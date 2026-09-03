"""Behavioral oracle for the staged worktree creation transaction."""
# file-complexity-waiver: crash-window cases share one public-process fixture matrix

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_escapement_worktree_bootstrap import _commit_contract
from worktree_fixtures import git, make_remote_scenario, rev


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "escapement-worktree"


def _wait_for(path: Path, *, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), f"timed out waiting for {path}"


def _create_command(primary: Path, name: str, branch: str, source: str) -> list[str]:
    return [
        str(CLI),
        "create",
        "--repo",
        str(primary),
        "--name",
        name,
        "--branch",
        branch,
        "--source",
        source,
    ]


def _start_create(
    primary: Path,
    name: str,
    branch: str,
    source: str,
    harness: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        _create_command(primary, name, branch, source),
        cwd=primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            **(env or {}),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _blocking_source(primary: Path, tmp_path: Path) -> tuple[str, Path, Path]:
    release = tmp_path / "release-bootstrap"
    observations = tmp_path / "bootstrap-observations"
    code = (
        "import os,time; from pathlib import Path; "
        f"observations=Path({str(observations)!r}); observations.mkdir(exist_ok=True); "
        "name=Path.cwd().name; "
        "(observations/f'{name}.entered').write_text(str(os.getpid())); "
        f"release=Path({str(release)!r}); "
        "\nif name.startswith('slow'):\n"
        "    while not release.exists(): time.sleep(0.01)\n"
    )
    source = _commit_contract(
        primary,
        argv=[sys.executable, "-c", code],
        timeout_seconds=20,
    )
    return source, release, observations


def _receipt(harness: Path, name: str) -> tuple[Path, dict[str, object]]:
    path = harness / "worktrees" / f"{name}.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def _finish_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 0, stderr or stdout
    return stdout, stderr


def _kill_create_and_blocked_git(
    process: subprocess.Popen[str], blocked_git_pid: Path
) -> None:
    git_pid = int(blocked_git_pid.read_text(encoding="utf-8"))
    process.kill()
    process.wait(timeout=5)
    try:
        os.kill(git_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _crash_creation(process: subprocess.Popen[str], entered: Path) -> None:
    bootstrap_pid = int(entered.read_text(encoding="utf-8"))
    process.kill()
    process.wait(timeout=5)
    try:
        os.kill(bootstrap_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _recover(
    primary: Path,
    harness: Path,
    name: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            **(env or {}),
        },
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def _path_replacement_fault(
    tmp_path: Path,
    label: str,
    watched: Path,
    replacement_content: bytes,
    *,
    mode: int = 0o600,
) -> tuple[dict[str, str], Path]:
    """Replace a public name at its destructive operation, not before its check."""
    fault_dir = tmp_path / f"{label}-fault"
    fault_dir.mkdir()
    injected = tmp_path / f"{label}-injected"
    original_hold = tmp_path / f"{label}-original"
    (fault_dir / "sitecustomize.py").write_text(
        """import json
import os
import subprocess
from pathlib import Path

real_link = os.link
real_rename = os.rename
real_replace = os.replace
real_unlink = os.unlink

watched = os.path.abspath(os.environ["WATCHED_PATH"])
injected = Path(os.environ["REPLACEMENT_INJECTED"])
replacement = bytes.fromhex(os.environ["REPLACEMENT_CONTENT_HEX"])
mode = int(os.environ["REPLACEMENT_MODE"], 8)
watched_parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == watched
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(watched):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == watched_parent_identity

def write_replacement():
    path = Path(watched)
    path.write_bytes(replacement)
    path.chmod(mode)

def record(operation):
    injected.write_text(json.dumps({"operation": operation}))

def inject_owner(path, directory_fd=None):
    owner_watched = os.environ.get("OWNER_WATCHED_PATH")
    owner_marker = os.environ.get("OWNER_INJECTED")
    candidate = os.fspath(path)
    owner_matches = bool(owner_watched) and (
        (
            os.path.isabs(candidate)
            and os.path.abspath(candidate) == os.path.abspath(owner_watched)
        )
        or (
            not os.path.isabs(candidate)
            and directory_fd is not None
            and os.path.basename(candidate) == os.path.basename(owner_watched)
            and (
                os.fstat(directory_fd).st_dev,
                os.fstat(directory_fd).st_ino,
            ) == watched_parent_identity
        )
    )
    if (
        owner_watched
        and owner_marker
        and owner_matches
        and not Path(owner_marker).exists()
    ):
        result = subprocess.run(
            [
                os.environ["REAL_GIT"],
                "-C",
                os.environ["PRIMARY"],
                "worktree",
                "add",
                "--no-checkout",
                os.environ["OWNER_REPLACEMENT"],
                os.environ["OWNER_BRANCH"],
            ],
            capture_output=True,
            text=True,
        )
        Path(owner_marker).write_text(json.dumps({
            "returncode": result.returncode,
            "stderr": result.stderr,
        }))

def guarded_unlink(path, *args, **kwargs):
    inject_owner(path)
    if matches(path) and not injected.exists():
        real_rename(path, os.environ["ORIGINAL_HOLD"])
        write_replacement()
        record("unlink")
    return real_unlink(path, *args, **kwargs)

def guarded_link(source, destination, *args, **kwargs):
    source_matches = matches(source, kwargs.get("src_dir_fd"))
    if source_matches and not injected.exists():
        real_rename(source, os.environ["ORIGINAL_HOLD"])
        write_replacement()
        record("link")
    return real_link(source, destination, *args, **kwargs)

def guarded_rename(source, destination, *args, **kwargs):
    source_directory = kwargs.get("src_dir_fd")
    inject_owner(source, source_directory)
    result = real_rename(source, destination, *args, **kwargs)
    if matches(source, source_directory) and not injected.exists():
        write_replacement()
        record("rename")
    return result

def guarded_replace(source, destination, *args, **kwargs):
    source_directory = kwargs.get("src_dir_fd")
    inject_owner(source, source_directory)
    result = real_replace(source, destination, *args, **kwargs)
    if matches(source, source_directory) and not injected.exists():
        write_replacement()
        record("replace")
    return result

os.link = guarded_link
os.rename = guarded_rename
os.replace = guarded_replace
os.unlink = guarded_unlink
""",
        encoding="utf-8",
    )
    return (
        {
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_PATH": str(watched),
            "REPLACEMENT_CONTENT_HEX": replacement_content.hex(),
            "REPLACEMENT_MODE": f"{mode:o}",
            "REPLACEMENT_INJECTED": str(injected),
            "ORIGINAL_HOLD": str(original_hold),
            "WATCHED_PARENT_DEVICE": str(watched.parent.stat().st_dev),
            "WATCHED_PARENT_INODE": str(watched.parent.stat().st_ino),
        },
        injected,
    )


def _parent_substitution_fault(
    tmp_path: Path,
    label: str,
    public_parent: Path,
    leaf: str,
    external_parent: Path,
) -> tuple[dict[str, str], Path]:
    fault_dir = tmp_path / f"{label}-fault"
    fault_dir.mkdir()
    injected = tmp_path / f"{label}-injected"
    original_parent = tmp_path / f"{label}-original-parent"
    parent_identity = public_parent.stat()
    (fault_dir / "sitecustomize.py").write_text(
        """import json
import os
from pathlib import Path

real_rename = os.rename
public_parent = Path(os.environ["PUBLIC_PARENT"])
original_parent = Path(os.environ["ORIGINAL_PARENT"])
external_parent = Path(os.environ["EXTERNAL_PARENT"])
leaf = os.environ["WATCHED_LEAF"]
parent_identity = (
    int(os.environ["PARENT_DEVICE"]),
    int(os.environ["PARENT_INODE"]),
)
injected = Path(os.environ["PARENT_SUBSTITUTED"])

def watched_source(source, directory_fd):
    candidate = os.fspath(source)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == os.path.abspath(public_parent / leaf)
    if directory_fd is None or os.path.basename(candidate) != leaf:
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def guarded_rename(source, destination, *args, **kwargs):
    if (
        watched_source(source, kwargs.get("src_dir_fd"))
        and not injected.exists()
    ):
        real_rename(public_parent, original_parent)
        public_parent.symlink_to(external_parent, target_is_directory=True)
        injected.write_text(json.dumps({"operation": "rename"}))
    return real_rename(source, destination, *args, **kwargs)

os.rename = guarded_rename
""",
        encoding="utf-8",
    )
    return (
        {
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PUBLIC_PARENT": str(public_parent),
            "ORIGINAL_PARENT": str(original_parent),
            "EXTERNAL_PARENT": str(external_parent),
            "WATCHED_LEAF": leaf,
            "PARENT_DEVICE": str(parent_identity.st_dev),
            "PARENT_INODE": str(parent_identity.st_ino),
            "PARENT_SUBSTITUTED": str(injected),
        },
        injected,
    )


def test_bootstrap_does_not_hold_repository_lock(tmp_path: Path) -> None:
    """Regression: wrapping run_bootstrap in the repo lock blocks this marker."""
    scenario = make_remote_scenario(tmp_path)
    source, release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    slow = _start_create(
        scenario.primary,
        "slow-one",
        "feature/slow-one",
        source,
        harness,
    )
    second: subprocess.Popen[str] | None = None
    try:
        _wait_for(observations / "slow-one.entered")
        second = _start_create(
            scenario.primary,
            "fast-two",
            "feature/fast-two",
            source,
            harness,
        )
        _wait_for(observations / "fast-two.entered", timeout=3)
    finally:
        release.touch()
    slow_stdout, _slow_stderr = _finish_process(slow)
    assert second is not None
    second_stdout, _second_stderr = _finish_process(second)
    assert rev(scenario.primary / ".worktrees" / "slow-one") == source
    assert rev(scenario.primary / ".worktrees" / "fast-two") == source
    assert json.loads(slow_stdout)["target"] == str(
        scenario.primary / ".worktrees" / "slow-one"
    )
    assert json.loads(second_stdout)["target"] == str(
        scenario.primary / ".worktrees" / "fast-two"
    )
    _slow_path, slow_receipt = _receipt(harness, "slow-one")
    _fast_path, fast_receipt = _receipt(harness, "fast-two")
    assert slow_receipt["phase"] == "created"
    assert fast_receipt["phase"] == "created"
    assert slow_receipt["creation_token"] != fast_receipt["creation_token"]


def test_receipt_is_durable_while_bootstrap_is_pending(tmp_path: Path) -> None:
    """Regression: a receipt written only after bootstrap is absent here."""
    scenario = make_remote_scenario(tmp_path)
    source, release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    process = _start_create(
        scenario.primary,
        "slow-receipt",
        "feature/slow-receipt",
        source,
        harness,
    )
    try:
        _wait_for(observations / "slow-receipt.entered")
        _path, pending = _receipt(harness, "slow-receipt")
        assert pending["phase"] == "bootstrap_pending"
        assert isinstance(pending.get("creation_token"), str)
        assert pending["creation_token"]
    finally:
        release.touch()
    _finish_process(process)
    _path, ready = _receipt(harness, "slow-receipt")
    assert ready["phase"] == "created"


def test_post_bootstrap_reverification_holds_repository_lock(tmp_path: Path) -> None:
    """Another create cannot interleave with final identity verification."""
    scenario = make_remote_scenario(tmp_path)
    source, _unused_release, observations = _blocking_source(
        scenario.primary, tmp_path
    )
    harness = tmp_path / "harness"
    name = "verify-locked"
    target = scenario.primary / ".worktrees" / name
    entered = tmp_path / "post-bootstrap-verify-entered"
    release = tmp_path / "release-post-bootstrap-verify"
    count = tmp_path / "target-head-verification-count"
    proxy_dir = tmp_path / "git-proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
is_target_head = (
    os.environ["TARGET"] in args
    and "rev-parse" in args
    and "--verify" in args
    and "HEAD^{commit}" in args
)
if is_target_head:
    count_path = Path(os.environ["VERIFY_COUNT"])
    count = int(count_path.read_text()) + 1 if count_path.exists() else 1
    count_path.write_text(str(count))
    if count == 2:
        Path(os.environ["POST_VERIFY_ENTERED"]).touch()
        release = Path(os.environ["POST_VERIFY_RELEASE"])
        while not release.exists():
            time.sleep(0.01)
raise SystemExit(subprocess.run([os.environ["REAL_GIT"], *args]).returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    first = _start_create(
        scenario.primary,
        name,
        "feature/verify-locked",
        source,
        harness,
        env={
            "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "TARGET": str(target),
            "VERIFY_COUNT": str(count),
            "POST_VERIFY_ENTERED": str(entered),
            "POST_VERIFY_RELEASE": str(release),
        },
    )
    second: subprocess.Popen[str] | None = None
    try:
        _wait_for(entered)
        second = _start_create(
            scenario.primary,
            "lock-probe",
            "feature/lock-probe",
            source,
            harness,
        )
        probe_entered = observations / "lock-probe.entered"
        deadline = time.monotonic() + 1
        while not probe_entered.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not probe_entered.exists(), (
            "a second create entered bootstrap while final verification was active"
        )
    finally:
        release.touch()
    _finish_process(first)
    assert second is not None
    _finish_process(second)


def test_receipt_precedes_the_first_ref_mutation(tmp_path: Path) -> None:
    """Create must be journaled before its branch update can be observed."""
    scenario = make_remote_scenario(tmp_path)
    harness = tmp_path / "harness"
    name = "write-ahead"
    branch = "feature/write-ahead"
    branch_ref = f"refs/heads/{branch}"
    entered = tmp_path / "update-ref-entered"
    release = tmp_path / "release-update-ref"
    proxy_dir = tmp_path / "git-proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if "update-ref" in args:
    Path(os.environ["UPDATE_REF_ENTERED"]).touch()
    release = Path(os.environ["UPDATE_REF_RELEASE"])
    while not release.exists():
        time.sleep(0.01)
raise SystemExit(subprocess.run([os.environ["REAL_GIT"], *args]).returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    env = {
        "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": shutil.which("git") or "git",
        "UPDATE_REF_ENTERED": str(entered),
        "UPDATE_REF_RELEASE": str(release),
    }
    process = _start_create(
        scenario.primary,
        name,
        branch,
        scenario.stale_primary_sha,
        harness,
        env=env,
    )
    try:
        _wait_for(entered)
        _path, allocating = _receipt(harness, name)
        assert allocating["phase"] == "allocating"
        assert (
            git(
                scenario.primary,
                "show-ref",
                "--verify",
                "--quiet",
                branch_ref,
                check=False,
            ).returncode
            == 1
        )
    finally:
        release.touch()
    _finish_process(process)


def test_recovery_clears_an_abandoned_pre_mutation_receipt(tmp_path: Path) -> None:
    """A crash before update-ref leaves no user state and is safe to clear."""
    scenario = make_remote_scenario(tmp_path)
    harness = tmp_path / "harness"
    name = "pre-mutation-crash"
    branch = "feature/pre-mutation-crash"
    branch_ref = f"refs/heads/{branch}"
    entered = tmp_path / "blocked-update-ref-pid"
    release = tmp_path / "never-release-update-ref"
    proxy_dir = tmp_path / "git-proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if "update-ref" in args:
    Path(os.environ["BLOCKED_GIT_PID"]).write_text(str(os.getpid()))
    release = Path(os.environ["UPDATE_REF_RELEASE"])
    while not release.exists():
        time.sleep(0.01)
raise SystemExit(subprocess.run([os.environ["REAL_GIT"], *args]).returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    process = _start_create(
        scenario.primary,
        name,
        branch,
        scenario.stale_primary_sha,
        harness,
        env={
            "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "BLOCKED_GIT_PID": str(entered),
            "UPDATE_REF_RELEASE": str(release),
        },
    )
    _wait_for(entered)
    receipt_path, allocating = _receipt(harness, name)
    assert allocating["phase"] == "allocating"
    _kill_create_and_blocked_git(process, entered)

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "completed"
    assert not receipt_path.exists()
    assert not (scenario.primary / ".worktrees" / name).exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            branch_ref,
            check=False,
        ).returncode
        == 1
    )


def test_recovery_preserves_unbound_worktree_created_before_token_write(
    tmp_path: Path,
) -> None:
    """Crash after worktree add cannot prove ownership without the admin token."""
    scenario = make_remote_scenario(tmp_path)
    harness = tmp_path / "harness"
    name = "unbound-worktree"
    branch = "feature/unbound-worktree"
    target = scenario.primary / ".worktrees" / name
    entered = tmp_path / "blocked-worktree-add-pid"
    release = tmp_path / "never-release-worktree-add"
    proxy_dir = tmp_path / "git-proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
result = subprocess.run([os.environ["REAL_GIT"], *args])
if result.returncode == 0 and "worktree" in args and "add" in args:
    Path(os.environ["BLOCKED_GIT_PID"]).write_text(str(os.getpid()))
    release = Path(os.environ["WORKTREE_ADD_RELEASE"])
    while not release.exists():
        time.sleep(0.01)
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    process = _start_create(
        scenario.primary,
        name,
        branch,
        scenario.stale_primary_sha,
        harness,
        env={
            "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "BLOCKED_GIT_PID": str(entered),
            "WORKTREE_ADD_RELEASE": str(release),
        },
    )
    _wait_for(entered)
    receipt_path, allocating = _receipt(harness, name)
    assert allocating["phase"] == "allocating"
    assert target.is_dir()
    _kill_create_and_blocked_git(process, entered)

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    retained = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert retained["phase"] == "bootstrap_failed"
    assert retained["last_reason"]
    assert target.is_dir()
    assert rev(target) == scenario.stale_primary_sha
    assert rev(scenario.primary, f"refs/heads/{branch}") == scenario.stale_primary_sha


def test_recovery_returns_while_bootstrap_creator_is_active(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "slow-active"
    process = _start_create(
        scenario.primary,
        name,
        "feature/slow-active",
        source,
        harness,
    )
    try:
        _wait_for(observations / f"{name}.entered")
        recovered = _recover(scenario.primary, harness, name)
        assert recovered.returncode == 0, recovered.stderr
        assert json.loads(recovered.stdout) == {
            "lifecycle_id": name,
            "reason": "bootstrap-active",
            "status": "pending",
        }
        assert (scenario.primary / ".worktrees" / name).is_dir()
    finally:
        release.touch()
    _finish_process(process)


def test_abandoned_pending_creation_is_rolled_back_by_receipt(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "slow-abandoned"
    branch = "feature/slow-abandoned"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    assert pending["phase"] == "bootstrap_pending"
    _crash_creation(process, entered)

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout) == {
        "lifecycle_id": name,
        "reason": "rolled-back",
        "status": "completed",
    }
    assert not (scenario.primary / ".worktrees" / name).exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        == 1
    )
    assert not receipt_path.exists()


def test_finish_preserves_an_incomplete_creation_for_recovery(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "finish-incomplete"
    branch = "feature/finish-incomplete"
    target = scenario.primary / ".worktrees" / name
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)

    finished = subprocess.run(
        [str(CLI), "finish", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={**os.environ, "CONTINUATION_HARNESS_HOME": str(harness)},
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert json.loads(finished.stdout) == {
        "lifecycle_id": name,
        "reason": "bootstrap-incomplete",
        "status": "pending",
    }
    assert target.is_dir()
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["phase"] == (
        "bootstrap_pending"
    )
    recovered = _recover(scenario.primary, harness, name)
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "completed"


def test_recovery_preserves_same_sha_replacement_worktree(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "slow-replaced"
    branch = "feature/slow-replaced"
    target = scenario.primary / ".worktrees" / name
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    git(scenario.primary, "worktree", "remove", "--force", str(target))
    git(scenario.primary, "worktree", "add", str(target), branch)
    marker = target / "replacement.txt"
    marker.write_text("preserve replacement\n", encoding="utf-8")

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout) == {
        "lifecycle_id": name,
        "reason": "creation-instance-mismatch",
        "status": "pending",
    }
    retained = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert retained["phase"] == "bootstrap_failed"
    assert retained["last_reason"] == "creation-instance-mismatch"
    assert marker.read_text(encoding="utf-8") == "preserve replacement\n"
    assert rev(target) == source


def test_recovery_preserves_a_branch_moved_after_creation(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "slow-moved"
    branch = "feature/slow-moved"
    target = scenario.primary / ".worktrees" / name
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    git(target, "reset", "--hard", scenario.stale_primary_sha)

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout) == {
        "lifecycle_id": name,
        "reason": "branch-tip-moved",
        "status": "pending",
    }
    retained = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert retained["phase"] == "bootstrap_failed"
    assert retained["last_reason"] == "branch-tip-moved"
    assert rev(target) == scenario.stale_primary_sha
    assert rev(scenario.primary, f"refs/heads/{branch}") == scenario.stale_primary_sha


def test_recovery_never_invokes_git_worktree_remove(
    tmp_path: Path,
) -> None:
    """No claimed path is safe to pass to Git's path-based destructive remove."""
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "no-git-remove"
    branch = "feature/no-git-remove"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    _receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    forbidden = tmp_path / "git-worktree-remove-invoked"
    proxy_dir = tmp_path / "git-proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
if "worktree" in args and "remove" in args:
    Path(os.environ["FORBIDDEN"]).write_text(" ".join(args))
    raise SystemExit(91)
raise SystemExit(subprocess.run([os.environ["REAL_GIT"], *args]).returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)

    recovered = _recover(
        scenario.primary,
        harness,
        name,
        env={
            "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "FORBIDDEN": str(forbidden),
        },
    )

    assert recovered.returncode == 0, recovered.stderr
    assert not forbidden.exists(), forbidden.read_text() if forbidden.exists() else ""
    assert json.loads(recovered.stdout)["status"] == "completed"


@pytest.mark.parametrize(
    "replaced_kind",
    ["worktree", "admin"],
    ids=["claimed-worktree", "git-admin"],
)
@pytest.mark.parametrize(
    "interrupt_after_rename",
    [False, True],
    ids=["synchronous", "crash-replay"],
)
@pytest.mark.parametrize(
    "replacement_kind",
    ["directory", "regular", "symlink"],
)
def test_filesystem_rollback_restores_a_replacement_inserted_at_detach(
    tmp_path: Path,
    replaced_kind: str,
    interrupt_after_rename: bool,
    replacement_kind: str,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = f"filesystem-detach-{replaced_kind}"
    branch = f"feature/filesystem-detach-{replaced_kind}"
    target = scenario.primary / ".worktrees" / name
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    admin_dir = Path(
        git(
            target,
            "rev-parse",
            "--path-format=absolute",
            "--git-dir",
        ).stdout.strip()
    )
    _crash_creation(process, entered)
    claimed = (
        scenario.primary
        / ".worktrees"
        / f".escapement-rollback-{pending['creation_token']}"
    )
    watched = claimed if replaced_kind == "worktree" else admin_dir
    original_hold = watched.parent / f".{name}-original-hold"
    injected = tmp_path / f"{name}-injected"
    forward_renamed = tmp_path / f"{name}-forward-renamed"
    marker_name = "foreign-replacement.txt"
    fault_dir = tmp_path / f"{name}-fault"
    fault_dir.mkdir()
    parent_metadata = watched.parent.stat()
    (fault_dir / "sitecustomize.py").write_text(
        """import json
import os
import time
from pathlib import Path

real_rename = os.rename
watched = os.path.abspath(os.environ["WATCHED_DIRECTORY"])
parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == watched
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(watched):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def guarded_rename(source, destination, *args, **kwargs):
    injected_now = False
    if (
        matches(source, kwargs.get("src_dir_fd"))
        and not Path(os.environ["REPLACEMENT_INJECTED"]).exists()
    ):
        real_rename(
            source,
            os.environ["ORIGINAL_HOLD"],
            src_dir_fd=kwargs.get("src_dir_fd"),
        )
        replacement = Path(watched)
        replacement_kind = os.environ["REPLACEMENT_KIND"]
        if replacement_kind == "directory":
            replacement.mkdir()
            (replacement / os.environ["MARKER_NAME"]).write_text(
                "foreign replacement must survive\\n"
            )
        elif replacement_kind == "regular":
            replacement.write_text("foreign replacement must survive\\n")
        else:
            replacement.symlink_to("foreign-symlink-target")
        metadata = replacement.lstat()
        Path(os.environ["REPLACEMENT_INJECTED"]).write_text(json.dumps({
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "source_anchored": kwargs.get("src_dir_fd") is not None,
            "destination_anchored": kwargs.get("dst_dir_fd") is not None,
        }))
        injected_now = True
    result = real_rename(source, destination, *args, **kwargs)
    if injected_now and os.environ["BLOCK_AFTER_RENAME"] == "1":
        Path(os.environ["FORWARD_RENAMED"]).touch()
        while True:
            time.sleep(0.1)
    return result

os.rename = guarded_rename
""",
        encoding="utf-8",
    )

    fault_env = {
        "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "WATCHED_DIRECTORY": str(watched),
        "WATCHED_PARENT_DEVICE": str(parent_metadata.st_dev),
        "WATCHED_PARENT_INODE": str(parent_metadata.st_ino),
        "ORIGINAL_HOLD": str(original_hold),
        "REPLACEMENT_INJECTED": str(injected),
        "MARKER_NAME": marker_name,
        "REPLACEMENT_KIND": replacement_kind,
        "BLOCK_AFTER_RENAME": "1" if interrupt_after_rename else "0",
        "FORWARD_RENAMED": str(forward_renamed),
    }

    def assert_replacement_at(path: Path) -> None:
        if replacement_kind == "directory":
            assert (path / marker_name).read_text(encoding="utf-8") == (
                "foreign replacement must survive\n"
            )
        elif replacement_kind == "regular":
            assert path.read_text(encoding="utf-8") == (
                "foreign replacement must survive\n"
            )
        else:
            assert path.is_symlink()
            assert path.readlink() == Path("foreign-symlink-target")

    if interrupt_after_rename:
        recovery = subprocess.Popen(
            [str(CLI), "recover", "--lifecycle-id", name],
            cwd=scenario.primary,
            env={
                **os.environ,
                "CONTINUATION_HARNESS_HOME": str(harness),
                **fault_env,
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_for(forward_renamed)
        recovery.kill()
        recovery.wait(timeout=5)
        common_dir = Path(
            git(
                scenario.primary,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ).stdout.strip()
        )
        detached = (
            common_dir
            / "escapement-worktree-rollbacks"
            / str(pending["creation_token"])
            / ("worktree" if replaced_kind == "worktree" else "admin")
        )
        assert not watched.exists() and not watched.is_symlink()
        assert_replacement_at(detached)
        recovered = _recover(scenario.primary, harness, name)
    else:
        recovered = _recover(scenario.primary, harness, name, env=fault_env)

    assert injected.exists(), recovered.stderr or recovered.stdout
    replacement_identity = json.loads(injected.read_text(encoding="utf-8"))
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert replacement_identity["source_anchored"] is True
    assert replacement_identity["destination_anchored"] is True
    observed = watched.lstat()
    assert (observed.st_dev, observed.st_ino) == (
        replacement_identity["device"],
        replacement_identity["inode"],
    )
    assert_replacement_at(watched)
    assert original_hold.is_dir()
    assert receipt_path.exists()
    assert rev(scenario.primary, f"refs/heads/{branch}") == source


def test_recovery_preserves_replacement_installed_after_non_destructive_claim(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "replace-after-claim"
    branch = "feature/replace-after-claim"
    target = scenario.primary / ".worktrees" / name
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    _receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    replacement = target / "replacement.txt"
    installed = tmp_path / "replacement-installed"
    racer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import time; from pathlib import Path; "
                f"target=Path({str(target)!r}); "
                "deadline=time.monotonic()+5; "
                "\nwhile target.exists() and time.monotonic()<deadline:\n"
                " time.sleep(0.005)\n"
                "\nif target.exists(): raise SystemExit(97)\n"
                "target.mkdir(); "
                f"(target/'replacement.txt').write_text('preserve replacement\\n'); "
                f"Path({str(installed)!r}).touch()"
            ),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    recovered = _recover(scenario.primary, harness, name)
    racer_stdout, racer_stderr = racer.communicate(timeout=10)

    assert racer.returncode == 0, racer_stderr or racer_stdout
    assert installed.exists(), "replacement was not installed after the claim"
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "completed"
    assert replacement.read_text(encoding="utf-8") == "preserve replacement\n"


def test_no_bootstrap_create_rejects_replacement_during_verification(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    harness = tmp_path / "harness"
    name = "no-bootstrap-replaced"
    branch = "feature/no-bootstrap-replaced"
    target = scenario.primary / ".worktrees" / name
    marker = target / "replacement.txt"
    proxy_dir = tmp_path / "git-proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
result = subprocess.run([os.environ["REAL_GIT"], *args])
target = Path(os.environ["TARGET"])
marker = target / "replacement.txt"
if result.returncode == 0 and "check-ignore" in args and target.exists() and not marker.exists():
    subprocess.run([os.environ["REAL_GIT"], "-C", os.environ["PRIMARY"], "worktree", "remove", "--force", str(target)], check=True)
    subprocess.run([os.environ["REAL_GIT"], "-C", os.environ["PRIMARY"], "worktree", "add", str(target), os.environ["BRANCH"]], check=True)
    marker.write_text("preserve replacement\\n")
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)

    result = subprocess.run(
        _create_command(
            scenario.primary,
            name,
            branch,
            scenario.stale_primary_sha,
        ),
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "PRIMARY": str(scenario.primary),
            "TARGET": str(target),
            "BRANCH": branch,
        },
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "preserve replacement\n"
    _path, retained = _receipt(harness, name)
    assert retained["phase"] == "rollback_ref_claimed"
    assert retained["last_reason"] == "creation-instance-mismatch"


def test_recovery_replays_after_worktree_removal_before_branch_deletion(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "replay-after-removal"
    branch = "feature/replay-after-removal"
    target = scenario.primary / ".worktrees" / name
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    lock_path = Path(f"{common_dir / f'refs/heads/{branch}'}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("foreign lock\n", encoding="utf-8")

    blocked = _recover(scenario.primary, harness, name)

    assert blocked.returncode == 0, blocked.stderr
    assert json.loads(blocked.stdout)["status"] == "pending"
    assert not target.exists()
    assert rev(scenario.primary, f"refs/heads/{branch}") == source
    assert lock_path.read_text(encoding="utf-8") == "foreign lock\n"
    assert receipt_path.exists()
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["phase"] == (
        "rollback_ref_claimed"
    )

    lock_path.unlink()
    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout) == {
        "lifecycle_id": name,
        "reason": "rolled-back",
        "status": "completed",
    }
    assert not receipt_path.exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        == 1
    )


def test_recovery_does_not_accept_a_missing_ref_without_exact_claim(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "missing-ref-without-claim"
    branch = "feature/missing-ref-without-claim"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    lock_path = Path(f"{loose_ref}.lock")
    lock_path.write_text("foreign lock\n", encoding="utf-8")

    blocked = _recover(scenario.primary, harness, name)

    assert blocked.returncode == 0, blocked.stderr
    assert json.loads(blocked.stdout)["status"] == "pending"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["phase"] == (
        "rollback_ref_claimed"
    )
    lock_path.unlink()
    moved_ref = tmp_path / "moved-ref"
    os.rename(loose_ref, moved_ref)
    moved_identity = (moved_ref.stat().st_dev, moved_ref.stat().st_ino)

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "pending"
    assert (moved_ref.stat().st_dev, moved_ref.stat().st_ino) == moved_identity
    assert moved_ref.read_text(encoding="utf-8") == f"{source}\n"
    assert receipt_path.exists()


def test_recovery_preserves_loose_branch_changed_after_worktree_removal(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "branch-changed-after-removal"
    branch = "feature/branch-changed-after-removal"
    branch_ref = f"refs/heads/{branch}"
    target = scenario.primary / ".worktrees" / name
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    lock_path = Path(f"{common_dir / branch_ref}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("foreign lock\n", encoding="utf-8")

    first_recovery = _recover(scenario.primary, harness, name)

    assert first_recovery.returncode == 0, first_recovery.stderr
    assert json.loads(first_recovery.stdout)["status"] == "pending"
    assert not target.exists()
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["phase"] == (
        "rollback_ref_claimed"
    )
    lock_path.unlink()
    git(
        scenario.primary,
        "update-ref",
        branch_ref,
        scenario.stale_primary_sha,
        source,
    )

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "pending"
    assert rev(scenario.primary, branch_ref) == scenario.stale_primary_sha
    assert receipt_path.exists()


def test_recovery_replays_a_claim_moved_before_its_receipt_update(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "replay-after-claim"
    branch = "feature/replay-after-claim"
    target = scenario.primary / ".worktrees" / name
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    assert pending["phase"] == "bootstrap_pending"
    _crash_creation(process, entered)
    blocked = tmp_path / "blocked-move-pid"
    proxy_dir = tmp_path / "git-proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
result = subprocess.run([os.environ["REAL_GIT"], *args])
if result.returncode == 0 and "worktree" in args and "move" in args and not Path(os.environ["BLOCKED_GIT_PID"]).exists():
    Path(os.environ["BLOCKED_GIT_PID"]).write_text(str(os.getpid()))
    while True:
        time.sleep(0.1)
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    recovery = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "BLOCKED_GIT_PID": str(blocked),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for(blocked)
    _kill_create_and_blocked_git(recovery, blocked)
    assert not target.exists()
    retained = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert retained["phase"] == "bootstrap_pending"
    claimed = list((scenario.primary / ".worktrees").glob(".escapement-rollback-*"))
    assert len(claimed) == 1

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "completed"
    assert not receipt_path.exists()
    assert not claimed[0].exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        == 1
    )


def test_recovery_preserves_branch_when_target_disappeared_before_rollback_claim(
    tmp_path: Path,
) -> None:
    """A missing token target is not proof that recovery removed it."""
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "missing-before-claim"
    branch = "feature/missing-before-claim"
    target = scenario.primary / ".worktrees" / name
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    assert pending["phase"] == "bootstrap_pending"
    _crash_creation(process, entered)
    git(scenario.primary, "worktree", "remove", "--force", str(target))

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout) == {
        "lifecycle_id": name,
        "reason": "creation-instance-mismatch",
        "status": "pending",
    }
    retained = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert retained["phase"] == "bootstrap_failed"
    assert retained["last_reason"] == "creation-instance-mismatch"
    assert rev(scenario.primary, f"refs/heads/{branch}") == source


def test_recovery_never_dereferences_a_symbolic_branch_replacement(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "symbolic-ref-replacement"
    branch = "feature/symbolic-ref-replacement"
    branch_ref = f"refs/heads/{branch}"
    git(scenario.primary, "update-ref", "refs/heads/trunk", source)
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    git(
        scenario.primary,
        "symbolic-ref",
        branch_ref,
        "refs/heads/trunk",
    )

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert rev(scenario.primary, "refs/heads/trunk") == source
    assert (
        git(scenario.primary, "symbolic-ref", "--quiet", branch_ref).stdout.strip()
        == "refs/heads/trunk"
    )
    assert receipt_path.exists()


def test_branch_deletion_holds_loose_ref_lock_against_symbolic_writer(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "branch-lock-race"
    branch = "feature/branch-lock-race"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered_bootstrap = observations / f"{name}.entered"
    _wait_for(entered_bootstrap)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered_bootstrap)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    lock_path = Path(f"{loose_ref}.lock")
    entered_lock = tmp_path / "branch-lock-entered"
    release_lock = tmp_path / "release-branch-lock"
    fault_dir = tmp_path / "branch-lock-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import os
import time
from pathlib import Path

real_open = os.open
real_link = os.link
parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == os.environ["WATCHED_REF_LOCK"]
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(os.environ["WATCHED_REF_LOCK"]):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def hold_after_acquisition(path, directory_fd=None):
    if (
        matches(path, directory_fd)
        and not Path(os.environ["LOCK_ENTERED"]).exists()
    ):
        Path(os.environ["LOCK_ENTERED"]).touch()
        release = Path(os.environ["LOCK_RELEASE"])
        while not release.exists():
            time.sleep(0.01)

def guarded_open(path, flags, *args, **kwargs):
    descriptor = real_open(path, flags, *args, **kwargs)
    if (
        os.path.abspath(os.fspath(path)) == os.environ["WATCHED_REF_LOCK"]
        and flags & os.O_EXCL
    ):
        hold_after_acquisition(path, kwargs.get("dir_fd"))
    return descriptor

def guarded_link(source, destination, *args, **kwargs):
    result = real_link(source, destination, *args, **kwargs)
    hold_after_acquisition(destination, kwargs.get("dst_dir_fd"))
    return result

os.open = guarded_open
os.link = guarded_link
""",
        encoding="utf-8",
    )
    recovery = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_REF_LOCK": str(lock_path),
            "LOCK_ENTERED": str(entered_lock),
                "LOCK_RELEASE": str(release_lock),
                "WATCHED_PARENT_DEVICE": str(lock_path.parent.stat().st_dev),
                "WATCHED_PARENT_INODE": str(lock_path.parent.stat().st_ino),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not entered_lock.exists() and recovery.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert entered_lock.exists(), recovery.communicate(timeout=5)[1]
        raced = git(
            scenario.primary,
            "symbolic-ref",
            branch_ref,
            "refs/heads/trunk",
            check=False,
        )
        assert raced.returncode != 0
        assert loose_ref.read_text(encoding="utf-8").strip() == source
    finally:
        release_lock.touch()
    stdout, stderr = recovery.communicate(timeout=10)
    assert recovery.returncode == 0, stderr or stdout
    assert json.loads(stdout)["status"] == "completed"
    assert not receipt_path.exists()
    assert not loose_ref.exists()
    assert not lock_path.exists()


def test_branch_deletion_preserves_owner_added_after_ref_lock(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "branch-owner-race"
    branch = "feature/branch-owner-race"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered_bootstrap = observations / f"{name}.entered"
    _wait_for(entered_bootstrap)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered_bootstrap)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    lock_path = Path(f"{loose_ref}.lock")
    replacement = scenario.primary / ".worktrees" / "late-owner"
    injected = tmp_path / "owner-race-injected"
    fault_dir = tmp_path / "owner-race-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import json
import os
import subprocess
from pathlib import Path

real_rename = os.rename
parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == os.environ["WATCHED_LOOSE_REF"]
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(os.environ["WATCHED_LOOSE_REF"]):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def guarded_rename(source, destination, *args, **kwargs):
    if (
        matches(source, kwargs.get("src_dir_fd"))
        and not Path(os.environ["OWNER_INJECTED"]).exists()
    ):
        result = subprocess.run(
            [
                os.environ["REAL_GIT"],
                "-C",
                os.environ["PRIMARY"],
                "worktree",
                "add",
                "--no-checkout",
                os.environ["REPLACEMENT"],
                os.environ["BRANCH"],
            ],
            capture_output=True,
            text=True,
        )
        Path(os.environ["OWNER_INJECTED"]).write_text(json.dumps({
            "lock_held": Path(os.environ["WATCHED_REF_LOCK"]).exists(),
            "returncode": result.returncode,
            "stderr": result.stderr,
        }))
    return real_rename(source, destination, *args, **kwargs)

os.rename = guarded_rename
""",
        encoding="utf-8",
    )
    recovered = _recover(
        scenario.primary,
        harness,
        name,
        env={
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "REAL_GIT": shutil.which("git") or "git",
            "PRIMARY": str(scenario.primary),
            "REPLACEMENT": str(replacement),
            "BRANCH": branch,
            "WATCHED_LOOSE_REF": str(loose_ref),
            "WATCHED_REF_LOCK": str(lock_path),
            "OWNER_INJECTED": str(injected),
            "WATCHED_PARENT_DEVICE": str(loose_ref.parent.stat().st_dev),
            "WATCHED_PARENT_INODE": str(loose_ref.parent.stat().st_ino),
        },
    )

    injection = json.loads(injected.read_text(encoding="utf-8"))
    assert injection["lock_held"] is True
    assert injection["returncode"] == 0, injection["stderr"]
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert rev(scenario.primary, branch_ref) == source
    assert rev(replacement) == source
    registry = git(
        scenario.primary,
        "worktree",
        "list",
        "--porcelain",
    ).stdout
    assert f"worktree {replacement.resolve()}\n" in registry
    assert f"branch {branch_ref}\n" in registry
    assert receipt_path.exists()


def test_branch_deletion_preserves_ref_replaced_at_destructive_boundary(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "ref-path-replacement"
    branch = "feature/ref-path-replacement"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    replacement_content = f"{scenario.stale_primary_sha}\n".encode()
    fault_env, injected = _path_replacement_fault(
        tmp_path,
        "ref-path-replacement",
        loose_ref,
        replacement_content,
    )

    recovered = _recover(scenario.primary, harness, name, env=fault_env)

    assert json.loads(injected.read_text(encoding="utf-8"))["operation"] in {
        "rename",
        "replace",
        "unlink",
    }
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert loose_ref.read_bytes() == replacement_content
    assert rev(scenario.primary, branch_ref) == scenario.stale_primary_sha
    assert receipt_path.exists()


def test_recovery_preserves_same_sha_ref_replaced_before_recovery(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "pre-recovery-ref-replacement"
    branch = "feature/pre-recovery-ref-replacement"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    recorded_identity = (
        pending["branch_ref_device"],
        pending["branch_ref_inode"],
    )
    assert (loose_ref.stat().st_dev, loose_ref.stat().st_ino) == recorded_identity
    original_ref = tmp_path / "original-created-ref"
    os.rename(loose_ref, original_ref)
    loose_ref.write_text(f"{source}\n", encoding="utf-8")
    replacement_identity = (loose_ref.stat().st_dev, loose_ref.stat().st_ino)
    assert replacement_identity != recorded_identity

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert (loose_ref.stat().st_dev, loose_ref.stat().st_ino) == replacement_identity
    assert loose_ref.read_text(encoding="utf-8") == f"{source}\n"
    assert receipt_path.exists()


@pytest.mark.parametrize(
    "ref_state",
    ["exact", "moved", "replaced"],
    ids=["exact-prepared-ref", "moved-prepared-ref", "replaced-prepared-ref"],
)
def test_recovery_handles_commit_before_final_allocation_receipt(
    tmp_path: Path,
    ref_state: str,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, _observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "prepared-ref-window"
    branch = "feature/prepared-ref-window"
    branch_ref = f"refs/heads/{branch}"
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    committed = tmp_path / "prepared-ref-committed"
    fault_dir = tmp_path / "prepared-ref-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import os
import time
from pathlib import Path

real_open = os.open
watched_name = os.path.basename(os.environ["WATCHED_REF"])

def guarded_open(path, flags, *args, **kwargs):
    candidate = os.fspath(path)
    if (
        not os.path.isabs(candidate)
        and os.path.basename(candidate) == watched_name
        and Path(os.environ["WATCHED_REF"]).exists()
        and not Path(os.environ["WATCHED_REF"] + ".lock").exists()
        and not Path(os.environ["REF_COMMITTED"]).exists()
    ):
        Path(os.environ["REF_COMMITTED"]).touch()
        while True:
            time.sleep(0.1)
    return real_open(path, flags, *args, **kwargs)

os.open = guarded_open
""",
        encoding="utf-8",
    )
    process = _start_create(
        scenario.primary,
        name,
        branch,
        source,
        harness,
        env={
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_REF": str(loose_ref),
            "REF_COMMITTED": str(committed),
        },
    )
    _wait_for(committed)
    receipt_path, pending = _receipt(harness, name)
    process.kill()
    process.wait(timeout=5)
    assert pending["branch_allocation_state"] == "prepared"
    assert pending["branch_allocated"] is False
    assert (loose_ref.stat().st_dev, loose_ref.stat().st_ino) == (
        pending["branch_ref_device"],
        pending["branch_ref_inode"],
    )

    moved_ref = tmp_path / "prepared-ref-moved"
    replacement_identity: tuple[int, int] | None = None
    if ref_state == "moved":
        os.rename(loose_ref, moved_ref)
    elif ref_state == "replaced":
        os.rename(loose_ref, moved_ref)
        loose_ref.write_text(f"{source}\n", encoding="utf-8")
        replacement_identity = (
            loose_ref.stat().st_dev,
            loose_ref.stat().st_ino,
        )
    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    if ref_state == "moved":
        assert json.loads(recovered.stdout)["status"] == "pending"
        assert moved_ref.read_text(encoding="utf-8") == f"{source}\n"
        assert receipt_path.exists()
    elif ref_state == "replaced":
        assert json.loads(recovered.stdout)["status"] == "pending"
        assert replacement_identity is not None
        assert (loose_ref.stat().st_dev, loose_ref.stat().st_ino) == (
            replacement_identity
        )
        assert loose_ref.read_text(encoding="utf-8") == f"{source}\n"
        assert moved_ref.read_text(encoding="utf-8") == f"{source}\n"
        assert receipt_path.exists()
    else:
        assert json.loads(recovered.stdout)["status"] == "completed"
        assert not loose_ref.exists()
        assert not receipt_path.exists()


def test_branch_deletion_cannot_follow_a_replaced_ref_parent(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "ref-parent-replacement"
    branch = "feature/ref-parent-replacement"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    external_parent = tmp_path / "external-ref-parent"
    external_parent.mkdir()
    external_ref = external_parent / loose_ref.name
    external_ref.write_text(f"{source}\n", encoding="utf-8")
    external_identity = (external_ref.stat().st_dev, external_ref.stat().st_ino)
    fault_env, injected = _parent_substitution_fault(
        tmp_path,
        "ref-parent-replacement",
        loose_ref.parent,
        loose_ref.name,
        external_parent,
    )

    recovered = _recover(scenario.primary, harness, name, env=fault_env)

    assert injected.exists()
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert (external_ref.stat().st_dev, external_ref.stat().st_ino) == external_identity
    assert external_ref.read_text(encoding="utf-8") == f"{source}\n"
    assert receipt_path.exists()


def test_branch_deletion_preserves_lock_replaced_during_release(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "release-lock-replacement"
    branch = "feature/release-lock-replacement"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    lock_path = Path(f"{common_dir / branch_ref}.lock")
    replacement_content = b"foreign replacement lock\n"
    fault_env, injected = _path_replacement_fault(
        tmp_path,
        "release-lock-replacement",
        lock_path,
        replacement_content,
    )

    recovered = _recover(scenario.primary, harness, name, env=fault_env)

    assert json.loads(injected.read_text(encoding="utf-8"))["operation"] in {
        "rename",
        "replace",
        "unlink",
    }
    assert recovered.returncode == 0, recovered.stderr
    status = json.loads(recovered.stdout)["status"]
    assert status in {"pending", "completed"}
    assert lock_path.read_bytes() == replacement_content
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            branch_ref,
            check=False,
        ).returncode
        == 1
    )
    assert receipt_path.exists() is (status == "pending")


def test_late_owner_restore_preserves_a_replaced_lock_and_exact_ref(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "restore-lock-replacement"
    branch = "feature/restore-lock-replacement"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    lock_path = Path(f"{loose_ref}.lock")
    replacement_worktree = scenario.primary / ".worktrees" / "restore-late-owner"
    owner_injected = tmp_path / "restore-owner-injected"
    replacement_content = f"{scenario.stale_primary_sha}\n".encode()
    fault_env, lock_injected = _path_replacement_fault(
        tmp_path,
        "restore-lock-replacement",
        lock_path,
        replacement_content,
    )
    fault_env.update(
        {
            "REAL_GIT": shutil.which("git") or "git",
            "PRIMARY": str(scenario.primary),
            "OWNER_WATCHED_PATH": str(loose_ref),
            "OWNER_INJECTED": str(owner_injected),
            "OWNER_REPLACEMENT": str(replacement_worktree),
            "OWNER_BRANCH": branch,
        }
    )

    recovered = _recover(scenario.primary, harness, name, env=fault_env)

    owner_result = json.loads(owner_injected.read_text(encoding="utf-8"))
    assert owner_result["returncode"] == 0, owner_result["stderr"]
    assert json.loads(lock_injected.read_text(encoding="utf-8"))["operation"] in {
        "link",
        "rename",
        "replace",
    }
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert loose_ref.read_text(encoding="utf-8") == f"{source}\n"
    assert rev(scenario.primary, branch_ref) == source
    assert rev(replacement_worktree) == source
    registry = git(
        scenario.primary,
        "worktree",
        "list",
        "--porcelain",
    ).stdout
    expected_owner = (
        f"worktree {replacement_worktree.resolve()}\n"
        f"HEAD {source}\n"
        f"branch {branch_ref}\n"
    )
    assert expected_owner in registry
    assert lock_path.read_bytes() == replacement_content
    assert receipt_path.exists()


def test_late_owner_restore_does_not_overwrite_a_replacement_ref(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "restore-ref-replacement"
    branch = "feature/restore-ref-replacement"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    replacement_worktree = scenario.primary / ".worktrees" / "restore-ref-owner"
    owner_injected = tmp_path / "restore-ref-owner-injected"
    ref_injected = tmp_path / "restore-ref-replacement-injected"
    fault_dir = tmp_path / "restore-ref-replacement-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import json
import os
import subprocess
from pathlib import Path

real_link = os.link
real_rename = os.rename
watched = os.path.abspath(os.environ["WATCHED_LOOSE_REF"])
parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == watched
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(watched):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def guarded_rename(source, destination, *args, **kwargs):
    if (
        matches(source, kwargs.get("src_dir_fd"))
        and not Path(os.environ["OWNER_INJECTED"]).exists()
    ):
        result = subprocess.run(
            [
                os.environ["REAL_GIT"],
                "-C",
                os.environ["PRIMARY"],
                "worktree",
                "add",
                "--no-checkout",
                os.environ["OWNER_REPLACEMENT"],
                os.environ["OWNER_BRANCH"],
            ],
            capture_output=True,
            text=True,
        )
        Path(os.environ["OWNER_INJECTED"]).write_text(json.dumps({
            "returncode": result.returncode,
            "stderr": result.stderr,
        }))
    return real_rename(source, destination, *args, **kwargs)

def guarded_link(source, destination, *args, **kwargs):
    if (
        matches(destination, kwargs.get("dst_dir_fd"))
        and not Path(os.environ["REF_INJECTED"]).exists()
    ):
        replacement = Path(watched)
        replacement.write_text(os.environ["EXPECTED_REF"] + "\\n")
        metadata = replacement.stat()
        Path(os.environ["REF_INJECTED"]).write_text(json.dumps({
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
        }))
    return real_link(source, destination, *args, **kwargs)

os.link = guarded_link
os.rename = guarded_rename
""",
        encoding="utf-8",
    )

    recovered = _recover(
        scenario.primary,
        harness,
        name,
        env={
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "REAL_GIT": shutil.which("git") or "git",
            "PRIMARY": str(scenario.primary),
            "WATCHED_LOOSE_REF": str(loose_ref),
            "OWNER_INJECTED": str(owner_injected),
            "OWNER_REPLACEMENT": str(replacement_worktree),
            "OWNER_BRANCH": branch,
            "REF_INJECTED": str(ref_injected),
            "EXPECTED_REF": source,
            "WATCHED_PARENT_DEVICE": str(loose_ref.parent.stat().st_dev),
            "WATCHED_PARENT_INODE": str(loose_ref.parent.stat().st_ino),
        },
    )

    owner_result = json.loads(owner_injected.read_text(encoding="utf-8"))
    replacement_identity = json.loads(ref_injected.read_text(encoding="utf-8"))
    observed = loose_ref.stat()
    assert owner_result["returncode"] == 0, owner_result["stderr"]
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert (observed.st_dev, observed.st_ino) == (
        replacement_identity["device"],
        replacement_identity["inode"],
    )
    assert loose_ref.read_text(encoding="utf-8") == f"{source}\n"
    assert rev(replacement_worktree) == source
    assert receipt_path.exists()


def test_creation_suppresses_branch_reflog_and_clean_recovery_completes(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "reflog-path-replacement"
    branch = "feature/reflog-path-replacement"
    branch_ref = f"refs/heads/{branch}"
    allocation_invocation = tmp_path / "allocation-invocation.json"
    proxy_dir = tmp_path / "allocation-git-proxy"
    proxy_dir.mkdir()
    proxy = proxy_dir / "git"
    proxy.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if "update-ref" in args:
    Path(os.environ["ALLOCATION_INVOCATION"]).write_text(json.dumps(args))
os.execv(os.environ["REAL_GIT"], [os.environ["REAL_GIT"], *args])
""",
        encoding="utf-8",
    )
    proxy.chmod(0o755)
    process = _start_create(
        scenario.primary,
        name,
        branch,
        source,
        harness,
        env={
            "PATH": f"{proxy_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git") or "git",
            "ALLOCATION_INVOCATION": str(allocation_invocation),
        },
    )
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    reflog = common_dir / "logs" / branch_ref
    allocation_args = json.loads(allocation_invocation.read_text(encoding="utf-8"))
    assert "--stdin" in allocation_args
    assert "core.logAllRefUpdates=false" in allocation_args
    assert pending["branch_reflog_present"] is False
    assert not reflog.exists()

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "completed"
    assert not reflog.exists()
    assert not receipt_path.exists()


def test_recovery_preserves_an_unexpected_reflog_created_before_recovery(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "pre-recovery-reflog-replacement"
    branch = "feature/pre-recovery-reflog-replacement"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    reflog = common_dir / "logs" / branch_ref
    assert pending["branch_reflog_present"] is False
    assert not reflog.exists()
    replacement_content = b"unexpected reflog must survive\n"
    reflog.parent.mkdir(parents=True, exist_ok=True)
    reflog.write_bytes(replacement_content)
    replacement_identity = (reflog.stat().st_dev, reflog.stat().st_ino)

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert (reflog.stat().st_dev, reflog.stat().st_ino) == replacement_identity
    assert reflog.read_bytes() == replacement_content
    assert receipt_path.exists()


def test_branch_deletion_cannot_follow_a_symlinked_reflog_parent(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "reflog-parent-replacement"
    branch = "feature/reflog-parent-replacement"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    reflog = common_dir / "logs" / branch_ref
    assert pending["branch_reflog_present"] is False
    assert not reflog.exists()
    original_parent = tmp_path / "original-reflog-parent"
    reflog.parent.mkdir(parents=True, exist_ok=True)
    reflog.parent.rename(original_parent)
    external_parent = tmp_path / "external-reflog-parent"
    external_parent.mkdir()
    external_reflog = external_parent / reflog.name
    replacement_content = b"external reflog must survive\n"
    external_reflog.write_bytes(replacement_content)
    external_identity = (
        external_reflog.stat().st_dev,
        external_reflog.stat().st_ino,
    )
    reflog.parent.symlink_to(external_parent, target_is_directory=True)

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert (
        external_reflog.stat().st_dev,
        external_reflog.stat().st_ino,
    ) == external_identity
    assert external_reflog.read_bytes() == replacement_content
    assert receipt_path.exists()


@pytest.mark.parametrize(
    "replace_lock",
    [False, True],
    ids=["owned-lock", "same-content-replacement-lock"],
)
def test_branch_lock_is_replayable_if_killed_before_post_lock_receipt(
    tmp_path: Path,
    replace_lock: bool,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "branch-pre-journal-crash"
    branch = "feature/branch-pre-journal-crash"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered_bootstrap = observations / f"{name}.entered"
    _wait_for(entered_bootstrap)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered_bootstrap)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    lock_path = Path(f"{loose_ref}.lock")
    durable = tmp_path / "branch-lock-token-durable"
    fault_dir = tmp_path / "branch-pre-journal-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import os
import time
from pathlib import Path

real_fsync = os.fsync

def watched_fsync(descriptor):
    result = real_fsync(descriptor)
    lock = Path(os.environ["WATCHED_REF_LOCK"])
    if lock.exists():
        observed = os.fstat(descriptor)
        locked = lock.stat()
        if (
            (observed.st_dev, observed.st_ino) == (locked.st_dev, locked.st_ino)
            and not Path(os.environ["LOCK_DURABLE"]).exists()
        ):
            Path(os.environ["LOCK_DURABLE"]).touch()
            while True:
                time.sleep(0.1)
    return result

os.fsync = watched_fsync
""",
        encoding="utf-8",
    )
    recovery = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_REF_LOCK": str(lock_path),
            "LOCK_DURABLE": str(durable),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for(durable)
    recovery.kill()
    recovery.wait(timeout=5)
    assert lock_path.exists()
    assert receipt_path.exists()
    lock_content = lock_path.read_bytes()

    if replace_lock:
        original_identity = (lock_path.stat().st_dev, lock_path.stat().st_ino)
        original_lock = tmp_path / "original-branch-lock"
        os.rename(lock_path, original_lock)
        lock_path.write_bytes(lock_content)
        lock_path.chmod(0o600)
        replacement_identity = (lock_path.stat().st_dev, lock_path.stat().st_ino)
        assert replacement_identity != original_identity

        rejected = _recover(scenario.primary, harness, name)

        assert rejected.returncode == 0, rejected.stderr
        assert json.loads(rejected.stdout)["status"] == "pending"
        assert rev(scenario.primary, branch_ref) == source
        assert loose_ref.read_text(encoding="utf-8").strip() == source
        assert lock_path.read_bytes() == lock_content
        assert receipt_path.exists()
        return

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "completed"
    assert not receipt_path.exists()
    assert not loose_ref.exists()
    assert not lock_path.exists()


def test_stale_lock_adoption_preserves_replacement_at_unlink(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "stale-lock-unlink-race"
    branch = "feature/stale-lock-unlink-race"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    lock_path = Path(f"{loose_ref}.lock")
    before_detach = tmp_path / "stale-lock-before-ref-detach"
    stage_fault = tmp_path / "stale-lock-stage-fault"
    stage_fault.mkdir()
    (stage_fault / "sitecustomize.py").write_text(
        """import os
import time
from pathlib import Path

real_rename = os.rename
parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == os.environ["WATCHED_LOOSE_REF"]
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(os.environ["WATCHED_LOOSE_REF"]):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def blocked_rename(source, destination, *args, **kwargs):
    if (
        matches(source, kwargs.get("src_dir_fd"))
        and not Path(os.environ["BEFORE_REF_DETACH"]).exists()
    ):
        Path(os.environ["BEFORE_REF_DETACH"]).touch()
        while True:
            time.sleep(0.1)
    return real_rename(source, destination, *args, **kwargs)

os.rename = blocked_rename
""",
        encoding="utf-8",
    )
    interrupted = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{stage_fault}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_LOOSE_REF": str(loose_ref),
            "BEFORE_REF_DETACH": str(before_detach),
            "WATCHED_PARENT_DEVICE": str(loose_ref.parent.stat().st_dev),
            "WATCHED_PARENT_INODE": str(loose_ref.parent.stat().st_ino),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for(before_detach)
    interrupted.kill()
    interrupted.wait(timeout=5)
    assert loose_ref.read_text(encoding="utf-8").strip() == source
    assert lock_path.exists()
    retained = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert retained["phase"] == "rollback_ref_claimed"
    lock_content = lock_path.read_bytes()
    lock_mode = lock_path.stat().st_mode & 0o777
    fault_env, injected = _path_replacement_fault(
        tmp_path,
        "stale-lock-adoption",
        lock_path,
        lock_content,
        mode=lock_mode,
    )

    replayed = _recover(scenario.primary, harness, name, env=fault_env)

    assert json.loads(injected.read_text(encoding="utf-8"))["operation"] in {
        "rename",
        "replace",
        "unlink",
    }
    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "pending"
    assert lock_path.read_bytes() == lock_content
    assert loose_ref.read_text(encoding="utf-8").strip() == source
    assert rev(scenario.primary, branch_ref) == source
    assert receipt_path.exists()


@pytest.mark.parametrize(
    "replace_claim",
    [False, True],
    ids=["owned-private-claim", "replacement-private-claim"],
)
def test_stale_lock_adoption_replays_after_public_lock_detach(
    tmp_path: Path,
    replace_claim: bool,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "stale-lock-detach-replay"
    branch = "feature/stale-lock-detach-replay"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    lock_path = Path(f"{loose_ref}.lock")
    before_detach = tmp_path / "before-initial-ref-detach"
    initial_fault = tmp_path / "initial-ref-detach-fault"
    initial_fault.mkdir()
    (initial_fault / "sitecustomize.py").write_text(
        """import os
import time
from pathlib import Path

real_rename = os.rename
parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == os.environ["WATCHED_LOOSE_REF"]
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(os.environ["WATCHED_LOOSE_REF"]):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def blocked_rename(source, destination, *args, **kwargs):
    if (
        matches(source, kwargs.get("src_dir_fd"))
        and not Path(os.environ["BEFORE_REF_DETACH"]).exists()
    ):
        Path(os.environ["BEFORE_REF_DETACH"]).touch()
        while True:
            time.sleep(0.1)
    return real_rename(source, destination, *args, **kwargs)

os.rename = blocked_rename
""",
        encoding="utf-8",
    )
    interrupted = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{initial_fault}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_LOOSE_REF": str(loose_ref),
            "BEFORE_REF_DETACH": str(before_detach),
            "WATCHED_PARENT_DEVICE": str(loose_ref.parent.stat().st_dev),
            "WATCHED_PARENT_INODE": str(loose_ref.parent.stat().st_ino),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for(before_detach)
    interrupted.kill()
    interrupted.wait(timeout=5)
    assert lock_path.exists()

    private_claim = (
        common_dir
        / "escapement-worktree-locks"
        / f"{pending['creation_token']}-ref.published"
    )
    claim_detached = tmp_path / "stale-lock-private-claim"
    replay_fault = tmp_path / "stale-lock-replay-fault"
    replay_fault.mkdir()
    (replay_fault / "sitecustomize.py").write_text(
        """import os
import time
from pathlib import Path

real_unlink = os.unlink
watched = os.path.abspath(os.environ["WATCHED_PRIVATE_CLAIM"])

def blocked_unlink(path, *args, **kwargs):
    if (
        os.path.abspath(os.fspath(path)) == watched
        and not Path(os.environ["CLAIM_DETACHED"]).exists()
    ):
        Path(os.environ["CLAIM_DETACHED"]).touch()
        while True:
            time.sleep(0.1)
    return real_unlink(path, *args, **kwargs)

os.unlink = blocked_unlink
""",
        encoding="utf-8",
    )
    adopting = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{replay_fault}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_PRIVATE_CLAIM": str(private_claim),
            "CLAIM_DETACHED": str(claim_detached),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for(claim_detached)
    adopting.kill()
    adopting.wait(timeout=5)
    assert not lock_path.exists()
    assert private_claim.exists()
    assert receipt_path.exists()

    if replace_claim:
        original_identity = (private_claim.stat().st_dev, private_claim.stat().st_ino)
        content = private_claim.read_bytes()
        mode = private_claim.stat().st_mode & 0o777
        original_claim = tmp_path / "original-private-lock-claim"
        os.rename(private_claim, original_claim)
        private_claim.write_bytes(content)
        private_claim.chmod(mode)
        replacement_identity = (
            private_claim.stat().st_dev,
            private_claim.stat().st_ino,
        )
        assert replacement_identity != original_identity

        rejected = _recover(scenario.primary, harness, name)

        assert rejected.returncode == 0, rejected.stderr
        assert json.loads(rejected.stdout)["status"] == "pending"
        assert private_claim.read_bytes() == content
        assert loose_ref.read_text(encoding="utf-8").strip() == source
        assert receipt_path.exists()
        return

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "completed"
    assert not private_claim.exists()
    assert not receipt_path.exists()


def test_recovery_preserves_packed_branch_when_loose_ref_is_absent(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "packed-branch"
    branch = "feature/packed-branch"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    git(scenario.primary, "pack-refs", "--all", "--prune")
    assert not loose_ref.exists()
    assert rev(scenario.primary, branch_ref) == source

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert rev(scenario.primary, branch_ref) == source
    assert receipt_path.exists()


def test_recovery_preserves_branch_with_loose_and_packed_representations(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "loose-and-packed-branch"
    branch = "feature/loose-and-packed-branch"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    git(scenario.primary, "pack-refs", "--all", "--no-prune")
    assert loose_ref.exists()
    packed_entry = f"{source} {branch_ref}\n"
    assert packed_entry in (common_dir / "packed-refs").read_text(
        encoding="utf-8"
    )

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert rev(scenario.primary, branch_ref) == source
    assert loose_ref.exists()
    assert packed_entry in (common_dir / "packed-refs").read_text(encoding="utf-8")
    assert receipt_path.exists()


def test_recovery_rechecks_packed_refs_after_acquiring_its_lock(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "packed-before-lock"
    branch = "feature/packed-before-lock"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    packed_refs = common_dir / "packed-refs"
    packed_lock = common_dir / "packed-refs.lock"
    injected = tmp_path / "packed-before-lock-injected"
    fault_dir = tmp_path / "packed-before-lock-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import json
import os
import subprocess
from pathlib import Path

real_open = os.open
real_link = os.link

def inject_before_acquisition(path):
    if (
        os.path.abspath(os.fspath(path)) == os.environ["WATCHED_PACKED_LOCK"]
        and not Path(os.environ["PACKED_RACE_INJECTED"]).exists()
    ):
        existed = Path(os.environ["WATCHED_PACKED_LOCK"]).exists()
        result = subprocess.run(
            [
                os.environ["REAL_GIT"],
                "-C",
                os.environ["PRIMARY"],
                "pack-refs",
                "--all",
                "--no-prune",
            ],
            capture_output=True,
            text=True,
        )
        Path(os.environ["PACKED_RACE_INJECTED"]).write_text(json.dumps({
            "existed": existed,
            "returncode": result.returncode,
            "stderr": result.stderr,
        }))

def guarded_open(path, flags, *args, **kwargs):
    if (
        os.path.abspath(os.fspath(path)) == os.environ["WATCHED_PACKED_LOCK"]
        and flags & os.O_EXCL
    ):
        inject_before_acquisition(path)
    return real_open(path, flags, *args, **kwargs)

def guarded_link(source, destination, *args, **kwargs):
    inject_before_acquisition(destination)
    return real_link(source, destination, *args, **kwargs)

os.open = guarded_open
os.link = guarded_link
""",
        encoding="utf-8",
    )

    recovered = _recover(
        scenario.primary,
        harness,
        name,
        env={
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "REAL_GIT": shutil.which("git") or "git",
            "PRIMARY": str(scenario.primary),
            "WATCHED_PACKED_LOCK": str(packed_lock),
            "PACKED_RACE_INJECTED": str(injected),
        },
    )

    injection = json.loads(injected.read_text(encoding="utf-8"))
    assert injection["existed"] is False
    assert injection["returncode"] == 0, injection["stderr"]
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert loose_ref.read_text(encoding="utf-8").strip() == source
    assert f"{source} {branch_ref}\n" in packed_refs.read_text(encoding="utf-8")
    assert rev(scenario.primary, branch_ref) == source
    assert receipt_path.exists()


def test_branch_deletion_holds_packed_refs_lock_through_loose_detach(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "packed-ref-race"
    branch = "feature/packed-ref-race"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    packed_lock = common_dir / "packed-refs.lock"
    injected = tmp_path / "packed-ref-race-injected"
    fault_dir = tmp_path / "packed-ref-race-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import json
import os
import subprocess
from pathlib import Path

real_rename = os.rename
parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == os.environ["WATCHED_LOOSE_REF"]
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(os.environ["WATCHED_LOOSE_REF"]):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def guarded_rename(source, destination, *args, **kwargs):
    if (
        matches(source, kwargs.get("src_dir_fd"))
        and not Path(os.environ["PACKED_RACE_INJECTED"]).exists()
    ):
        result = subprocess.run(
            [
                os.environ["REAL_GIT"],
                "-C",
                os.environ["PRIMARY"],
                "pack-refs",
                "--all",
                "--no-prune",
            ],
            capture_output=True,
            text=True,
        )
        Path(os.environ["PACKED_RACE_INJECTED"]).write_text(json.dumps({
            "lock_held": Path(os.environ["WATCHED_PACKED_LOCK"]).exists(),
            "returncode": result.returncode,
            "stderr": result.stderr,
        }))
    return real_rename(source, destination, *args, **kwargs)

os.rename = guarded_rename
""",
        encoding="utf-8",
    )

    recovered = _recover(
        scenario.primary,
        harness,
        name,
        env={
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "REAL_GIT": shutil.which("git") or "git",
            "PRIMARY": str(scenario.primary),
            "WATCHED_LOOSE_REF": str(loose_ref),
            "WATCHED_PACKED_LOCK": str(packed_lock),
            "PACKED_RACE_INJECTED": str(injected),
            "WATCHED_PARENT_DEVICE": str(loose_ref.parent.stat().st_dev),
            "WATCHED_PARENT_INODE": str(loose_ref.parent.stat().st_ino),
        },
    )

    injection = json.loads(injected.read_text(encoding="utf-8"))
    assert injection["lock_held"] is True
    assert injection["returncode"] != 0
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "completed"
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            branch_ref,
            check=False,
        ).returncode
        == 1
    )
    assert not receipt_path.exists()


def test_recovery_preserves_a_foreign_packed_refs_lock(tmp_path: Path) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "foreign-packed-lock"
    branch = "feature/foreign-packed-lock"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    packed_lock = common_dir / "packed-refs.lock"
    packed_lock.write_text("foreign packed lock\n", encoding="utf-8")

    recovered = _recover(scenario.primary, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "pending"
    assert packed_lock.read_text(encoding="utf-8") == "foreign packed lock\n"
    assert loose_ref.read_text(encoding="utf-8").strip() == source
    assert rev(scenario.primary, branch_ref) == source
    assert receipt_path.exists()


@pytest.mark.parametrize(
    "replace_lock",
    [False, True],
    ids=["owned-lock", "same-content-replacement-lock"],
)
def test_packed_refs_lock_is_replayable_if_killed_after_acquisition(
    tmp_path: Path,
    replace_lock: bool,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "packed-lock-crash"
    branch = "feature/packed-lock-crash"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    packed_lock = common_dir / "packed-refs.lock"
    acquired = tmp_path / "packed-lock-acquired"
    fault_dir = tmp_path / "packed-lock-crash-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import os
import time
from pathlib import Path

real_open = os.open
real_link = os.link

def hold_after_acquisition(path):
    if (
        os.path.abspath(os.fspath(path)) == os.environ["WATCHED_PACKED_LOCK"]
        and not Path(os.environ["PACKED_LOCK_ACQUIRED"]).exists()
    ):
        Path(os.environ["PACKED_LOCK_ACQUIRED"]).touch()
        while True:
            time.sleep(0.1)

def guarded_open(path, flags, *args, **kwargs):
    descriptor = real_open(path, flags, *args, **kwargs)
    if (
        os.path.abspath(os.fspath(path)) == os.environ["WATCHED_PACKED_LOCK"]
        and flags & os.O_EXCL
    ):
        hold_after_acquisition(path)
    return descriptor

def guarded_link(source, destination, *args, **kwargs):
    result = real_link(source, destination, *args, **kwargs)
    hold_after_acquisition(destination)
    return result

os.open = guarded_open
os.link = guarded_link
""",
        encoding="utf-8",
    )
    recovery = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_PACKED_LOCK": str(packed_lock),
            "PACKED_LOCK_ACQUIRED": str(acquired),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for(acquired)
    recovery.kill()
    recovery.wait(timeout=5)
    assert packed_lock.exists()
    assert receipt_path.exists()
    lock_content = packed_lock.read_bytes()

    if replace_lock:
        original = packed_lock.stat()
        original_identity = (original.st_dev, original.st_ino)
        original_lock = tmp_path / "original-packed-lock"
        os.rename(packed_lock, original_lock)
        packed_lock.write_bytes(lock_content)
        packed_lock.chmod(original.st_mode & 0o777)
        replacement = packed_lock.stat()
        assert (replacement.st_dev, replacement.st_ino) != original_identity

        rejected = _recover(scenario.primary, harness, name)

        assert rejected.returncode == 0, rejected.stderr
        assert json.loads(rejected.stdout)["status"] == "pending"
        assert packed_lock.read_bytes() == lock_content
        assert loose_ref.read_text(encoding="utf-8").strip() == source
        assert rev(scenario.primary, branch_ref) == source
        assert receipt_path.exists()
        return

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "completed"
    assert not packed_lock.exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            branch_ref,
            check=False,
        ).returncode
        == 1
    )
    assert not receipt_path.exists()


@pytest.mark.parametrize(
    "replace_lock",
    [False, True],
    ids=["owned-lock", "replacement-lock"],
)
def test_branch_deletion_replays_after_durable_detach_before_receipt_cleanup(
    tmp_path: Path,
    replace_lock: bool,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "branch-unlink-crash"
    branch = "feature/branch-unlink-crash"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered_bootstrap = observations / f"{name}.entered"
    _wait_for(entered_bootstrap)
    receipt_path, _pending = _receipt(harness, name)
    _crash_creation(process, entered_bootstrap)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    lock_path = Path(f"{loose_ref}.lock")
    durable = tmp_path / "branch-detach-durable"
    unlocked_detach = tmp_path / "branch-detached-without-lock"
    release = tmp_path / "release-branch-detach"
    fault_dir = tmp_path / "branch-detach-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import os
import time
from pathlib import Path

real_rename = os.rename
real_fsync = os.fsync
ref_detached = False
parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == os.environ["WATCHED_LOOSE_REF"]
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(os.environ["WATCHED_LOOSE_REF"]):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def watched_rename(source, destination, *args, **kwargs):
    global ref_detached
    watched = matches(source, kwargs.get("src_dir_fd"))
    if watched and not Path(os.environ["WATCHED_REF_LOCK"]).exists():
        Path(os.environ["UNLOCKED_DETACH"]).touch()
    result = real_rename(source, destination, *args, **kwargs)
    if watched:
        ref_detached = True
    return result

def watched_fsync(descriptor):
    result = real_fsync(descriptor)
    parent = os.stat(os.environ["WATCHED_REF_PARENT"])
    observed = os.fstat(descriptor)
    if (
        ref_detached
        and (observed.st_dev, observed.st_ino) == (parent.st_dev, parent.st_ino)
        and not Path(os.environ["DETACH_DURABLE"]).exists()
    ):
        Path(os.environ["DETACH_DURABLE"]).touch()
        release = Path(os.environ["DETACH_RELEASE"])
        while not release.exists():
            time.sleep(0.01)
    return result

os.rename = watched_rename
os.fsync = watched_fsync
""",
        encoding="utf-8",
    )
    recovery = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_LOOSE_REF": str(loose_ref),
            "WATCHED_REF_LOCK": str(lock_path),
            "WATCHED_REF_PARENT": str(loose_ref.parent),
            "DETACH_DURABLE": str(durable),
            "UNLOCKED_DETACH": str(unlocked_detach),
            "DETACH_RELEASE": str(release),
            "WATCHED_PARENT_DEVICE": str(loose_ref.parent.stat().st_dev),
            "WATCHED_PARENT_INODE": str(loose_ref.parent.stat().st_ino),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while not durable.exists() and recovery.poll() is None:
        if time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    if not durable.exists():
        stdout, stderr = recovery.communicate(timeout=5)
        raise AssertionError(stderr or stdout or "loose-ref detach was not durable")
    recovery.kill()
    recovery.wait(timeout=5)
    assert not loose_ref.exists()
    assert lock_path.exists()
    assert not unlocked_detach.exists(), "loose ref was detached outside its lock"
    assert receipt_path.exists()

    if replace_lock:
        lock_path.unlink()
        lock_path.write_text("foreign replacement lock\n", encoding="utf-8")
        collision = _recover(scenario.primary, harness, name)
        assert collision.returncode == 0, collision.stderr
        assert lock_path.read_text(encoding="utf-8") == "foreign replacement lock\n"
        collision_status = json.loads(collision.stdout)["status"]
        assert collision_status in {"pending", "completed"}
        if collision_status == "completed":
            assert not receipt_path.exists()
            return
        assert receipt_path.exists()
        lock_path.unlink()

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "completed"
    assert not receipt_path.exists()
    assert not loose_ref.exists()
    assert not lock_path.exists()


def test_ref_rollback_replays_after_public_names_are_detached(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "detached-ref-replay"
    branch = "feature/detached-ref-replay"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    lock_path = Path(f"{loose_ref}.lock")
    detached = tmp_path / "detached-private-claim"
    fault_dir = tmp_path / "detached-ref-replay-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import json
import os
import time
from pathlib import Path

real_remove = os.remove
real_unlink = os.unlink

public_ref = os.path.abspath(os.environ["PUBLIC_REF"])
public_lock = os.path.abspath(os.environ["PUBLIC_LOCK"])
token = os.environ["CREATION_TOKEN"]
marker = Path(os.environ["DETACHED_MARKER"])

def maybe_block(path):
    candidate = os.path.abspath(os.fspath(path))
    if (
        token in candidate
        and candidate not in {public_ref, public_lock}
        and not Path(public_ref).exists()
        and not Path(public_lock).exists()
        and not marker.exists()
    ):
        marker.write_text(json.dumps({"claim": candidate}))
        while True:
            time.sleep(0.1)

def guarded_unlink(path, *args, **kwargs):
    maybe_block(path)
    return real_unlink(path, *args, **kwargs)

def guarded_remove(path, *args, **kwargs):
    maybe_block(path)
    return real_remove(path, *args, **kwargs)

os.remove = guarded_remove
os.unlink = guarded_unlink
""",
        encoding="utf-8",
    )
    recovery = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "PUBLIC_REF": str(loose_ref),
            "PUBLIC_LOCK": str(lock_path),
            "CREATION_TOKEN": pending["creation_token"],
            "DETACHED_MARKER": str(detached),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for(detached)
    recovery.kill()
    recovery.wait(timeout=5)
    private_claim = Path(
        json.loads(detached.read_text(encoding="utf-8"))["claim"]
    )
    assert not loose_ref.exists()
    assert not lock_path.exists()
    assert private_claim.exists()
    assert receipt_path.exists()

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "completed"
    assert not private_claim.exists()
    assert not receipt_path.exists()
    assert (
        git(
            scenario.primary,
            "show-ref",
            "--verify",
            "--quiet",
            branch_ref,
            check=False,
        ).returncode
        == 1
    )


def test_ref_rollback_replays_after_exact_branch_claim_is_consumed(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "consumed-ref-claim-replay"
    branch = "feature/consumed-ref-claim-replay"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    private_claim = (
        common_dir
        / "escapement-worktree-locks"
        / f"{pending['creation_token']}-branch"
    )
    consumed = tmp_path / "branch-claim-consumed"
    fault_dir = tmp_path / "branch-claim-consumed-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import os
import time
from pathlib import Path

real_unlink = os.unlink
watched = os.path.abspath(os.environ["WATCHED_BRANCH_CLAIM"])

def watched_unlink(path, *args, **kwargs):
    result = real_unlink(path, *args, **kwargs)
    if (
        os.path.abspath(os.fspath(path)) == watched
        and not Path(os.environ["CLAIM_CONSUMED"]).exists()
    ):
        Path(os.environ["CLAIM_CONSUMED"]).touch()
        while True:
            time.sleep(0.1)
    return result

os.unlink = watched_unlink
""",
        encoding="utf-8",
    )
    recovery = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "WATCHED_BRANCH_CLAIM": str(private_claim),
            "CLAIM_CONSUMED": str(consumed),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for(consumed)
    recovery.kill()
    recovery.wait(timeout=5)
    assert not private_claim.exists()
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["phase"] == (
        "rollback_ref_detached"
    )

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "completed"
    assert not receipt_path.exists()


def test_ref_restore_replays_after_publication_before_claim_cleanup(
    tmp_path: Path,
) -> None:
    scenario = make_remote_scenario(tmp_path)
    source, _release, observations = _blocking_source(scenario.primary, tmp_path)
    harness = tmp_path / "harness"
    name = "published-ref-restore-replay"
    branch = "feature/published-ref-restore-replay"
    branch_ref = f"refs/heads/{branch}"
    process = _start_create(scenario.primary, name, branch, source, harness)
    entered = observations / f"{name}.entered"
    _wait_for(entered)
    receipt_path, pending = _receipt(harness, name)
    _crash_creation(process, entered)
    common_dir = Path(
        git(
            scenario.primary,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).stdout.strip()
    )
    loose_ref = common_dir / branch_ref
    private_claim = (
        common_dir
        / "escapement-worktree-locks"
        / f"{pending['creation_token']}-branch"
    )
    replacement_worktree = scenario.primary / ".worktrees" / "restore-owner"
    owner_injected = tmp_path / "restore-owner-injected"
    restore_published = tmp_path / "restore-published"
    fault_dir = tmp_path / "restore-publication-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        """import json
import os
import subprocess
import time
from pathlib import Path

real_rename = os.rename
real_unlink = os.unlink
watched_ref = os.path.abspath(os.environ["WATCHED_REF"])
watched_claim = os.path.abspath(os.environ["WATCHED_CLAIM"])
parent_identity = (
    int(os.environ["WATCHED_PARENT_DEVICE"]),
    int(os.environ["WATCHED_PARENT_INODE"]),
)

def matches_ref(path, directory_fd=None):
    candidate = os.fspath(path)
    if os.path.isabs(candidate):
        return os.path.abspath(candidate) == watched_ref
    if directory_fd is None or os.path.basename(candidate) != os.path.basename(watched_ref):
        return False
    metadata = os.fstat(directory_fd)
    return (metadata.st_dev, metadata.st_ino) == parent_identity

def guarded_rename(source, destination, *args, **kwargs):
    if (
        matches_ref(source, kwargs.get("src_dir_fd"))
        and not Path(os.environ["OWNER_INJECTED"]).exists()
    ):
        result = subprocess.run(
            [
                os.environ["REAL_GIT"], "-C", os.environ["PRIMARY"],
                "worktree", "add", "--no-checkout",
                os.environ["OWNER_REPLACEMENT"], os.environ["OWNER_BRANCH"],
            ],
            capture_output=True,
            text=True,
        )
        Path(os.environ["OWNER_INJECTED"]).write_text(json.dumps({
            "returncode": result.returncode,
            "stderr": result.stderr,
        }))
    return real_rename(source, destination, *args, **kwargs)

def guarded_unlink(path, *args, **kwargs):
    candidate = os.path.abspath(os.fspath(path))
    public_ref = Path(watched_ref)
    claim = Path(watched_claim)
    if (
        candidate == watched_claim
        and public_ref.exists()
        and claim.exists()
        and public_ref.stat().st_ino == claim.stat().st_ino
        and not Path(os.environ["RESTORE_PUBLISHED"]).exists()
    ):
        Path(os.environ["RESTORE_PUBLISHED"]).touch()
        while True:
            time.sleep(0.1)
    return real_unlink(path, *args, **kwargs)

os.rename = guarded_rename
os.unlink = guarded_unlink
""",
        encoding="utf-8",
    )
    recovery = subprocess.Popen(
        [str(CLI), "recover", "--lifecycle-id", name],
        cwd=scenario.primary,
        env={
            **os.environ,
            "CONTINUATION_HARNESS_HOME": str(harness),
            "PYTHONPATH": f"{fault_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "REAL_GIT": shutil.which("git") or "git",
            "PRIMARY": str(scenario.primary),
            "WATCHED_REF": str(loose_ref),
            "WATCHED_CLAIM": str(private_claim),
            "WATCHED_PARENT_DEVICE": str(loose_ref.parent.stat().st_dev),
            "WATCHED_PARENT_INODE": str(loose_ref.parent.stat().st_ino),
            "OWNER_INJECTED": str(owner_injected),
            "OWNER_REPLACEMENT": str(replacement_worktree),
            "OWNER_BRANCH": branch,
            "RESTORE_PUBLISHED": str(restore_published),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _wait_for(restore_published)
    recovery.kill()
    recovery.wait(timeout=5)

    owner_result = json.loads(owner_injected.read_text(encoding="utf-8"))
    assert owner_result["returncode"] == 0, owner_result["stderr"]
    assert loose_ref.exists()
    assert private_claim.exists()
    assert (loose_ref.stat().st_dev, loose_ref.stat().st_ino) == (
        private_claim.stat().st_dev,
        private_claim.stat().st_ino,
    )
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["phase"] == (
        "rollback_ref_restoring"
    )

    replayed = _recover(scenario.primary, harness, name)

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["status"] == "pending"
    assert loose_ref.read_text(encoding="utf-8") == f"{source}\n"
    assert not private_claim.exists()
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["phase"] == (
        "rollback_worktree_removed"
    )
    assert rev(replacement_worktree) == source


def test_recovery_reports_active_before_the_creator_writes_a_receipt(
    tmp_path: Path,
) -> None:
    harness = tmp_path / "harness"
    name = "active-before-receipt"
    entered = tmp_path / "lock-entered"
    release = tmp_path / "release-lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os,sys,time; from pathlib import Path; "
                f"sys.path.insert(0,{str(ROOT / 'bin')!r}); "
                "from escapement_worktree_registry import lifecycle_lock; "
                f"entered=Path({str(entered)!r}); release=Path({str(release)!r}); "
                f"\nwith lifecycle_lock({name!r}):\n"
                " entered.touch()\n"
                " while not release.exists(): time.sleep(0.01)\n"
            ),
        ],
        env={**os.environ, "CONTINUATION_HARNESS_HOME": str(harness)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for(entered)
        recovered = _recover(tmp_path, harness, name)
        assert recovered.returncode == 0, recovered.stderr
        assert json.loads(recovered.stdout) == {
            "lifecycle_id": name,
            "reason": "bootstrap-active",
            "status": "pending",
        }
    finally:
        release.touch()
        holder.communicate(timeout=5)


def test_recovery_reports_removed_without_a_receipt_or_active_creator(
    tmp_path: Path,
) -> None:
    """The active-before-receipt guard must not create permanent ghost work."""
    harness = tmp_path / "harness"
    (harness / "worktrees").mkdir(parents=True, mode=0o700)
    name = "never-created"

    recovered = _recover(tmp_path, harness, name)

    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout) == {
        "lifecycle_id": name,
        "reason": "removed",
        "status": "completed",
    }
