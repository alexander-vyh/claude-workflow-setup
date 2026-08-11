"""Real-launchd oracle for detached continuation recovery."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.test_continuation_supervisor_recovery import (
    LABEL,
    _fixture,
    _run,
    _write_executable,
)


def _install_child_spawning_job(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    home, _, env = _fixture(tmp_path)
    waker = home / ".claude" / "harness" / "bin" / "wakeup_waker.py"
    child_program = (
        "import pathlib,time; "
        "time.sleep(0.35); "
        "pathlib.Path.home().joinpath('recovery-child.completed').write_text('done\\n')"
    )
    _write_executable(
        waker,
        f"#!{sys.executable}\n"
        "import os,pathlib,subprocess,sys\n"
        "home = pathlib.Path.home()\n"
        "home.joinpath('waker.pid').write_text(str(os.getpid()))\n"
        f"subprocess.Popen([sys.executable, '-c', {child_program!r}])\n"
        "home.joinpath('waker.returning').write_text('returning\\n')\n",
    )

    installed = _run(env, timeout=60)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    with plist.open("rb") as handle:
        return home, plistlib.load(handle)


def _wait_until(predicate, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _child_completes_under_real_launchd(
    home: Path, installed_job: dict[str, object], *, abandon: bool | None
) -> bool:
    """Bootstrap one disposable real job and observe lifetime outside launchd."""
    label = f"{LABEL}.oracle.{os.getpid()}.{uuid.uuid4().hex}"
    job = {**installed_job, "Label": label}
    if abandon is not None:
        job["AbandonProcessGroup"] = abandon
    job.pop("StartInterval", None)
    plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
    with plist.open("wb") as handle:
        plistlib.dump(job, handle)
    plist.chmod(0o600)

    target = f"gui/{os.getuid()}/{label}"
    marker = home / "recovery-child.completed"
    returning = home / "waker.returning"
    waker_pid_path = home / "waker.pid"
    for artifact in (marker, returning, waker_pid_path):
        artifact.unlink(missing_ok=True)

    subprocess.run(
        ["launchctl", "bootout", target], capture_output=True, text=True, timeout=5
    )
    try:
        loaded = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert loaded.returncode == 0, loaded.stdout + loaded.stderr
        assert _wait_until(returning.is_file), "LaunchAgent never reached waker return"
        assert waker_pid_path.is_file()
        waker_pid = int(waker_pid_path.read_text())
        assert _wait_until(lambda: not _pid_is_alive(waker_pid)), (
            "waker did not exit before the child-lifetime observation"
        )
        assert not marker.exists(), "child completed before the waker exit boundary"
        return _wait_until(marker.is_file, timeout=3)
    finally:
        subprocess.run(
            ["launchctl", "bootout", target],
            capture_output=True,
            text=True,
            timeout=5,
        )


@pytest.mark.skipif(
    sys.platform != "darwin"
    or os.environ.get("ESCAPEMENT_RUN_LIVE_LAUNCHD_ORACLE") != "1",
    reason="explicit disposable live-launchd oracle",
)
def test_disposable_real_launchagent_enforces_process_group_ownership(tmp_path):
    home, installed_job = _install_child_spawning_job(tmp_path)

    assert _child_completes_under_real_launchd(home, installed_job, abandon=None), (
        "real launchd terminated the recovery child after its short-lived waker exited"
    )
    assert not _child_completes_under_real_launchd(
        home, installed_job, abandon=False
    ), "real-launchd negative control cannot observe unsafe child termination"
