"""Failure-boundary oracle for the public continuation-supervisor installer.

These tests execute the Bash entrypoint in isolated homes.  The launchctl and
filesystem probes are outside the implementation, so success means the visible
installation transaction converged rather than that an internal helper ran.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "continuation-supervisor-install.sh"
LABEL = "com.escapement.continuation-supervisor"


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "uname", "#!/bin/sh\nprintf 'Darwin\\n'\n")
    waker = home / ".claude" / "harness" / "bin" / "wakeup_waker.py"
    _write_executable(
        waker,
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$HOME/waker.argv"\n',
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "LAUNCHCTL_LOG": str(home / "launchctl.log"),
        "BASH_FUNC_launchctl%%": _shell_launchctl(),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return home, fake_bin, env


def _run(
    env: dict[str, str], *args: str, timeout: float = 15
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _shell_launchctl() -> str:
    return r"""() {
  state="${LAUNCHCTL_STATE:-$HOME/launchctl.loaded}"
  label="com.escapement.continuation-supervisor"
  touch "$state"
  printf '%s\n' "$*" >> "$HOME/launchctl.log"
  if [[ "${1:-}" == print ]]; then
    grep -Fxq "$label" "$state" && return 0
    return 113
  fi
  if [[ "${1:-}" == bootout ]]; then
    if [[ "${BOOTOUT_RACE_TO_ABSENT:-0}" == 1 && ! -e "${BOOTOUT_RACE_MARKER:-}" ]]; then
      grep -Fvx "$label" "$state" > "$state.next" || true
      mv -f "$state.next" "$state"
      : > "$BOOTOUT_RACE_MARKER"
      return 3
    fi
    if [[ "${BOOTOUT_RACE_AFTER_72:-0}" == 1 && -e "${BOOTSTRAP_72_MARKER:-}" ]]; then
      grep -Fvx "$label" "$state" > "$state.next" || true
      mv -f "$state.next" "$state"
      return 3
    fi
    if [[ -n "${ORPHAN_SCHEDULE:-}" && -n "${ORPHAN_QUIESCE_MARKER:-}" ]]; then
      if [[ -e "$ORPHAN_SCHEDULE" || -L "$ORPHAN_SCHEDULE" ]]; then
        printf 'present\n' > "$ORPHAN_QUIESCE_MARKER"
      else
        printf 'missing\n' > "$ORPHAN_QUIESCE_MARKER"
      fi
    fi
    grep -Fxq "$label" "$state" || return 3
    [[ "${LAUNCHCTL_BOOTOUT_FAIL:-0}" != 1 ]] || return 78
    grep -Fvx "$label" "$state" > "$state.next" || true
    mv -f "$state.next" "$state"
    return 0
  fi
  if [[ "${1:-}" == bootstrap ]]; then
    if [[ -n "${LAUNCHCTL_BOOTSTRAP_GATE:-}" ]]; then
      mkdir -p "$LAUNCHCTL_BOOTSTRAP_GATE"
      : > "$LAUNCHCTL_BOOTSTRAP_GATE/entered-$$"
      count=0
      while [[ ! -e "$LAUNCHCTL_BOOTSTRAP_GATE/release" ]]; do
        sleep 0.01
        count=$((count + 1))
        [[ "$count" -lt 1000 ]] || return 79
      done
    fi
    [[ "${LAUNCHCTL_BOOTSTRAP_FAIL:-0}" != 1 ]] || return 75
    if [[ "${ASSERT_SAFE_SCHEDULES:-0}" == 1 ]]; then
      python3 -B -c 'import datetime as d,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); now=d.datetime.now(d.timezone.utc)
for path in root.glob("*/scheduled.json"):
 entries=json.loads(path.read_text())
 assert isinstance(entries,list)
 for entry in entries:
  wake=d.datetime.fromisoformat(entry["wake_at"].replace("Z","+00:00"))
  wake=wake if wake.tzinfo else wake.replace(tzinfo=d.timezone.utc)
  assert wake>now' "$HOME/.claude/harness/threads" || {
        printf 'unsafe\n' > "$HOME/unsafe-load-attempted"
        return 76
      }
    fi
    if grep -Fxq "$label" "$state"; then
      [[ -z "${BOOTSTRAP_72_MARKER:-}" ]] || : > "$BOOTSTRAP_72_MARKER"
      return 72
    fi
    printf '%s\n' "$label" >> "$state"
    python3 -B -c 'import plistlib,sys
with open(sys.argv[1],"rb") as handle: args=plistlib.load(handle)["ProgramArguments"]
print(" ".join(args[1:]))' "$3" >> "$HOME/waker.argv"
  fi
  return 0
}"""


def _write_hazards(home: Path) -> dict[Path, bytes]:
    threads = home / ".claude" / "harness" / "threads"
    due = threads / "due" / "scheduled.json"
    malformed = threads / "malformed" / "scheduled.json"
    due.parent.mkdir(parents=True, exist_ok=True)
    malformed.parent.mkdir(parents=True, exist_ok=True)
    due.write_text('[{"wake_at":"2020-01-01T00:00:00+00:00","prompt":"old"}]\n')
    malformed.write_text("{not-json\n")
    return {due: due.read_bytes(), malformed: malformed.read_bytes()}


def _manifest_records(home: Path) -> list[dict]:
    records = []
    quarantine = home / ".claude" / "harness" / "quarantine"
    for candidate in quarantine.rglob("*.json") if quarantine.exists() else ():
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, list):
            records.extend(entry for entry in entries if isinstance(entry, dict))
    return records


def _assert_records_cover(records: list[dict], hazards: dict[Path, bytes]) -> None:
    by_source = {record.get("source"): record for record in records}
    assert set(map(str, hazards)) <= set(by_source), "durable manifest omits a hazard"
    for source, data in hazards.items():
        record = by_source[str(source)]
        assert record.get("reason") in {"due", "malformed"}
        assert record.get("sha256") == hashlib.sha256(data).hexdigest()
        assert isinstance(record.get("archive"), str) and record["archive"]


def test_failed_bootstrap_leaves_no_installed_marker_or_loaded_job(tmp_path):
    home, _, env = _fixture(tmp_path)
    first = _run({**env, "LAUNCHCTL_BOOTSTRAP_FAIL": "1"})
    assert first.returncode != 0
    assert not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
    state = home / "launchctl.loaded"
    assert not (state.read_text().splitlines() if state.exists() else [])


def test_failed_bootstrap_does_not_make_later_hazards_look_migrated(tmp_path):
    home, _, env = _fixture(tmp_path)
    first = _run({**env, "LAUNCHCTL_BOOTSTRAP_FAIL": "1"})
    assert first.returncode != 0
    hazards = _write_hazards(home)

    retry = _run({**env, "ASSERT_SAFE_SCHEDULES": "1"})

    assert retry.returncode == 0, retry.stderr
    assert not (home / "unsafe-load-attempted").exists()
    assert all(not source.exists() for source in hazards)
    _assert_records_cover(_manifest_records(home), hazards)
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]
    assert (home / "waker.argv").read_text().splitlines() == ["--fire"]
    launch_commands = (home / "launchctl.log").read_text().splitlines()
    assert sum(command.startswith("bootstrap ") for command in launch_commands) == 2


@pytest.mark.parametrize("operation", ["--uninstall", "reinstall"])
def test_real_bootout_failure_preserves_loaded_job_and_plist(tmp_path, operation):
    home, fake_bin, env = _fixture(tmp_path)
    installed = _run(env)
    assert installed.returncode == 0, installed.stderr
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    before = plist.read_bytes()
    failed_env = {
        **env,
        "LAUNCHCTL_BOOTOUT_FAIL": "1",
        "PATH": f"{fake_bin}:/usr/bin:/bin:/changed-after-install",
    }

    result = _run(failed_env, *("--uninstall",) if operation == "--uninstall" else ())

    assert result.returncode != 0
    assert plist.read_bytes() == before
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]


def test_proven_not_loaded_uninstall_is_idempotent(tmp_path):
    home, _, env = _fixture(tmp_path)
    assert _run(env).returncode == 0
    (home / "launchctl.loaded").write_text("", encoding="utf-8")

    first = _run(env, "--uninstall")
    second = _run(env, "--uninstall")

    assert first.returncode == second.returncode == 0
    assert not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()


def test_loaded_orphan_with_legacy_state_fails_before_quiesce(tmp_path):
    home, _, env = _fixture(tmp_path)
    hazards = _write_hazards(home)
    due = next(iter(hazards))
    (home / "launchctl.loaded").write_text(f"{LABEL}\n", encoding="utf-8")
    marker = home / "orphan-quiesce.marker"

    result = _run(
        {
            **env,
            "ASSERT_SAFE_SCHEDULES": "1",
            "ORPHAN_SCHEDULE": str(due),
            "ORPHAN_QUIESCE_MARKER": str(marker),
        }
    )

    assert result.returncode != 0
    assert not marker.exists()
    assert all(path.exists() for path in hazards)
    assert not (home / "unsafe-load-attempted").exists()
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]


def test_concurrent_installers_serialize_the_whole_transaction(tmp_path):
    home, _, env = _fixture(tmp_path)
    gate = tmp_path / "bootstrap-gate"
    concurrent_env = {**env, "LAUNCHCTL_BOOTSTRAP_GATE": str(gate)}
    command = ["bash", str(INSTALLER)]
    first = subprocess.Popen(command, env=concurrent_env, cwd=ROOT)
    try:
        deadline = time.monotonic() + 5
        while len(list(gate.glob("entered-*"))) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(list(gate.glob("entered-*"))) == 1, (
            "first installer never reached bootstrap"
        )
        second = subprocess.Popen(
            [*command, "--uninstall"], env=concurrent_env, cwd=ROOT
        )
        try:
            overlap_deadline = time.monotonic() + 1
            while second.poll() is None and time.monotonic() < overlap_deadline:
                time.sleep(0.01)
            assert second.poll() is None, (
                "concurrent uninstall completed while an install transaction was still active"
            )
            launch_agents = home / "Library" / "LaunchAgents"
            assert (launch_agents / f".{LABEL}.pending.plist").is_file(), (
                "concurrent uninstall removed the pending install candidate"
            )
            assert not (launch_agents / f"{LABEL}.plist").exists(), (
                "install exposed the stable plist before launchd accepted its candidate"
            )
            (gate / "release").touch()
            assert first.wait(timeout=10) == 0
            assert second.wait(timeout=10) == 0
        finally:
            if second.poll() is None:
                second.kill()
                second.wait()
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()
    assert not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
    assert not (home / "launchctl.loaded").read_text().splitlines()


def _crash_sitecustomize(directory: Path) -> Path:
    site = directory / "fault-site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        r"""
import os
import signal
from pathlib import Path

real_replace = os.replace
count = 0

def replace(source, destination, *args, **kwargs):
    global count
    source_path = Path(source)
    destination_path = Path(destination)
    schedule_move = source_path.name == "scheduled.json" and "quarantine" in destination_path.parts
    phase = os.environ.get("ESCAPEMENT_TEST_KILL_PHASE", "")
    if schedule_move:
        count += 1
        if phase == f"before:{count}":
            os.kill(os.getpid(), signal.SIGKILL)
    result = real_replace(source, destination, *args, **kwargs)
    if schedule_move and phase == f"after:{count}":
        os.kill(os.getpid(), signal.SIGKILL)
    return result

os.replace = replace
""",
        encoding="utf-8",
    )
    return site


@pytest.mark.parametrize("phase", ["before:1", "after:1", "after:2"])
def test_quarantine_kill_boundaries_leave_wal_and_retry_safely(tmp_path, phase):
    home, _, env = _fixture(tmp_path)
    hazards = _write_hazards(home)
    site = _crash_sitecustomize(tmp_path)

    killed = _run(
        {
            **env,
            "PYTHONPATH": str(site),
            "ESCAPEMENT_TEST_KILL_PHASE": phase,
        }
    )

    assert killed.returncode != 0, f"fault boundary {phase} was never exercised"
    _assert_records_cover(_manifest_records(home), hazards)

    retry = _run({key: value for key, value in env.items() if key != "PYTHONPATH"})
    assert retry.returncode == 0, retry.stderr
    assert all(not source.exists() for source in hazards)
    records = _manifest_records(home)
    _assert_records_cover(records, hazards)
    archived = {
        Path(record["archive"])
        for record in records
        if record.get("source") in map(str, hazards)
    }
    assert len(archived) == len(hazards)
    for source, data in hazards.items():
        record = next(item for item in records if item.get("source") == str(source))
        assert Path(record["archive"]).read_bytes() == data
    assert not (home / "unsafe-load-attempted").exists()


def test_preexisting_truncated_wal_cannot_be_ignored_before_bootstrap(tmp_path):
    home, _, env = _fixture(tmp_path)
    source = home / ".claude" / "harness" / "threads" / "lost" / "scheduled.json"
    transaction = (
        home
        / ".claude"
        / "harness"
        / "quarantine"
        / "legacy-schedules-interrupted-fixture"
    )
    archive = transaction / "harness" / "threads" / "lost" / "scheduled.json"
    archive.parent.mkdir(parents=True)
    data = b'[{"wake_at":"2020-01-01T00:00:00+00:00","prompt":"lost"}]\n'
    archive.write_bytes(data)
    manifest = transaction / "manifest.json"
    manifest.write_bytes(b'{"entries":[{"source":')
    archive.chmod(0o600)
    manifest.chmod(0o600)
    before = {
        "archive": (archive.read_bytes(), archive.stat().st_mode & 0o777),
        "manifest": (manifest.read_bytes(), manifest.stat().st_mode & 0o777),
    }

    result = _run({**env, "ASSERT_SAFE_SCHEDULES": "1"})

    launch_commands = (
        (home / "launchctl.log").read_text().splitlines()
        if (home / "launchctl.log").exists()
        else []
    )
    if result.returncode == 0:
        assert not (home / "unsafe-load-attempted").exists()
        _assert_records_cover(_manifest_records(home), {source: data})
        assert (home / "waker.argv").read_text().splitlines() == ["--fire"]
        assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]
    else:
        assert not any(command.startswith("bootstrap ") for command in launch_commands)
        assert not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
        assert not (home / "waker.argv").exists()
        state = home / "launchctl.loaded"
        assert not (state.read_text().splitlines() if state.exists() else [])
        assert not source.exists()
        assert {
            "archive": (archive.read_bytes(), archive.stat().st_mode & 0o777),
            "manifest": (manifest.read_bytes(), manifest.stat().st_mode & 0o777),
        } == before


def test_plist_temp_collision_cannot_follow_a_symlink(tmp_path):
    home, fake_bin, env = _fixture(tmp_path)
    real_python = shutil.which("python3")
    assert real_python
    victim = tmp_path / "victim"
    victim.write_text("must-survive\n", encoding="utf-8")
    victim.chmod(0o640)
    wrapper = fake_bin / "python3"
    _write_executable(
        wrapper,
        "#!/bin/bash\n"
        'destination=""\n'
        'previous=""\n'
        'for argument in "$@"; do\n'
        '  if [[ "$previous" == --destination ]]; then destination="$argument"; fi\n'
        '  previous="$argument"\n'
        "done\n"
        'if [[ -n "$destination" ]]; then\n'
        '  ln -s "$PLIST_ATTACK_TARGET" "$destination"\n'
        '  : > "$PLIST_ATTACK_MARKER"\n'
        "fi\n"
        'exec "$REAL_PYTHON" "$@"\n',
    )
    before = (victim.read_bytes(), victim.stat().st_mode & 0o777)
    attack_marker = tmp_path / "plist-attack-reached"

    result = _run(
        {
            **env,
            "REAL_PYTHON": real_python,
            "PLIST_ATTACK_TARGET": str(victim),
            "PLIST_ATTACK_MARKER": str(attack_marker),
        }
    )

    assert attack_marker.exists(), "plist symlink attack never reached the writer"
    assert (victim.read_bytes(), victim.stat().st_mode & 0o777) == before
    if result.returncode == 0:
        assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]
    else:
        loaded = home / "launchctl.loaded"
        assert not loaded.exists() or not loaded.read_text().splitlines()


def test_plist_rename_is_followed_by_parent_directory_fsync(tmp_path):
    home, _, env = _fixture(tmp_path)
    site = tmp_path / "audit-site"
    site.mkdir()
    audit = tmp_path / "fsync-audit.jsonl"
    (site / "sitecustomize.py").write_text(
        r"""
import json
import os
import stat

real_fsync = os.fsync
real_replace = os.replace
audit = os.environ.get("ESCAPEMENT_FSYNC_AUDIT")

def record(event):
    if audit:
        with open(audit, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

def replace(source, destination, *args, **kwargs):
    result = real_replace(source, destination, *args, **kwargs)
    if str(destination).endswith("com.escapement.continuation-supervisor.plist"):
        record(["replace-plist", str(destination)])
    return result

def fsync(descriptor):
    result = real_fsync(descriptor)
    if stat.S_ISDIR(os.fstat(descriptor).st_mode):
        record(["fsync-directory"])
    return result

os.replace = replace
os.fsync = fsync
""",
        encoding="utf-8",
    )

    result = _run(
        {**env, "PYTHONPATH": str(site), "ESCAPEMENT_FSYNC_AUDIT": str(audit)}
    )

    assert result.returncode == 0, result.stderr
    events = [
        json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()
    ]
    replaced = next(
        index for index, event in enumerate(events) if event[0] == "replace-plist"
    )
    assert any(event[0] == "fsync-directory" for event in events[replaced + 1 :]), (
        "plist rename was not made durable with a later directory fsync"
    )


def test_ci_and_agent_surface_contract_execute_installer_oracle():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )
    surface_contract = (ROOT / "tests" / "test_agent_surfaces.py").read_text(
        encoding="utf-8"
    )
    command = "bash tests/test_continuation_supervisor_install.sh"
    assert command in workflow
    assert command in surface_contract


@pytest.mark.parametrize("prior_loaded", [False, True])
def test_marker_write_failure_restores_prior_service_generation(tmp_path, prior_loaded):
    home, fake_bin, env = _fixture(tmp_path)
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    marker = home / ".claude" / "harness" / "continuation-supervisor-installed.json"
    loaded = home / "launchctl.loaded"
    launch_env = {**env, "BASH_FUNC_launchctl%%": _shell_launchctl()}
    if prior_loaded:
        installed = _run(launch_env)
        assert installed.returncode == 0, installed.stderr
    before = {
        "plist": plist.read_bytes() if plist.exists() else None,
        "marker": marker.read_bytes() if marker.exists() else None,
        "loaded": loaded.read_bytes() if loaded.exists() else b"",
    }
    real_python = sys.executable
    _write_executable(
        fake_bin / "python3",
        f"#!{real_python}\n"
        + r"""
import os
import sys

if any(value.endswith("continuation-supervisor-state.py") for value in sys.argv[1:]) and "write-marker" in sys.argv[1:]:
    raise SystemExit(74)
os.execv(os.environ["REAL_PYTHON"], [os.environ["REAL_PYTHON"], *sys.argv[1:]])
""",
    )

    result = _run({**launch_env, "REAL_PYTHON": real_python})

    assert result.returncode != 0
    after = {
        "plist": plist.read_bytes() if plist.exists() else None,
        "marker": marker.read_bytes() if marker.exists() else None,
        "loaded": loaded.read_bytes() if loaded.exists() else b"",
    }
    assert after == before


def test_quarantine_ancestor_entries_are_durable_before_schedule_rename(tmp_path):
    home, _, env = _fixture(tmp_path)
    hazards = _write_hazards(home)
    audit = tmp_path / "quarantine-fsync.jsonl"
    site = tmp_path / "quarantine-audit-site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        r"""
import json
import os
import stat

audit = os.environ.get("ESCAPEMENT_QUARANTINE_AUDIT")
real_fsync = os.fsync
real_mkdir = os.mkdir
real_replace = os.replace

def record(value):
    if audit:
        with open(audit, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(value) + "\n")

def identity(path):
    value = os.stat(path)
    return [value.st_dev, value.st_ino]

def mkdir(path, *args, **kwargs):
    parent = os.path.dirname(str(path)) or "."
    result = real_mkdir(path, *args, **kwargs)
    if "/quarantine/" in str(path):
        record(["mkdir", str(path), identity(parent)])
    return result

def fsync(descriptor):
    result = real_fsync(descriptor)
    value = os.fstat(descriptor)
    if stat.S_ISDIR(value.st_mode):
        record(["dir-fsync", [value.st_dev, value.st_ino]])
    return result

def replace(source, destination, *args, **kwargs):
    if str(source).endswith("scheduled.json") and "/quarantine/" in str(destination):
        record(["schedule-rename", str(source), str(destination)])
    return real_replace(source, destination, *args, **kwargs)

os.mkdir = mkdir
os.fsync = fsync
os.replace = replace
""",
        encoding="utf-8",
    )
    threads = home / ".claude" / "harness" / "threads"
    quarantine = home / ".claude" / "harness" / "quarantine"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "continuation-supervisor-state.py"),
            "migrate",
            "--threads",
            str(threads),
            "--quarantine",
            str(quarantine),
            "--first-install",
        ],
        cwd=ROOT,
        env={
            **env,
            "PYTHONPATH": str(site),
            "ESCAPEMENT_QUARANTINE_AUDIT": str(audit),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    first_rename = next(
        i for i, event in enumerate(events) if event[0] == "schedule-rename"
    )
    created_parents = [
        (index, event[2])
        for index, event in enumerate(events[:first_rename])
        if event[0] == "mkdir"
    ]
    assert created_parents
    for created, identity_value in created_parents:
        assert ["dir-fsync", identity_value] in events[created + 1 : first_rename]
    assert all(not source.exists() for source in hazards)


def test_quiesce_loaded_race_to_absent_retains_candidate_and_converges(tmp_path):
    home, _, env = _fixture(tmp_path)
    launch_env = {**env, "BASH_FUNC_launchctl%%": _shell_launchctl()}
    installed = _run(launch_env)
    assert installed.returncode == 0, installed.stderr
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    marker = home / ".claude" / "harness" / "continuation-supervisor-installed.json"
    before_plist = plist.read_bytes()
    race_marker = home / "bootout-raced-to-absent"

    result = _run(
        {
            **launch_env,
            "BOOTOUT_RACE_MARKER": str(race_marker),
            "BOOTOUT_RACE_TO_ABSENT": "1",
        }
    )

    assert race_marker.exists(), "installer never exercised the quiesce race"
    assert result.returncode == 0, result.stdout + result.stderr
    assert plist.is_file()
    assert plist.read_bytes() == before_plist
    assert marker.is_file()
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]


def test_loaded_service_without_trusted_plist_fails_before_quiesce(tmp_path):
    home, _, env = _fixture(tmp_path)
    loaded = home / "launchctl.loaded"
    loaded.write_text(f"{LABEL}\n", encoding="utf-8")

    result = _run(env)

    commands = (home / "launchctl.log").read_text().splitlines()
    assert result.returncode != 0
    assert not any(
        command.startswith(("bootout ", "bootstrap ")) for command in commands
    ), "installer mutated a loaded service whose filesystem authority was absent"
    assert loaded.read_text().splitlines() == [LABEL]
    assert not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
    assert not (
        home / ".claude" / "harness" / "continuation-supervisor-installed.json"
    ).exists()


@pytest.mark.parametrize("failed_command", ["migrate", "write-plist"])
def test_post_quiesce_preload_failure_restores_prior_loaded_service(
    tmp_path, failed_command
):
    home, fake_bin, env = _fixture(tmp_path)
    installed = _run(env)
    assert installed.returncode == 0, installed.stderr
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    marker = home / ".claude" / "harness" / "continuation-supervisor-installed.json"
    loaded = home / "launchctl.loaded"
    before = (plist.read_bytes(), marker.read_bytes(), loaded.read_bytes())
    (home / "launchctl.log").write_text("", encoding="utf-8")
    real_python = sys.executable
    _write_executable(
        fake_bin / "python3",
        f"#!{real_python}\n"
        "import os,sys\n"
        "helper=any(value.endswith('continuation-supervisor-state.py') for value in sys.argv)\n"
        "if helper and os.environ['FAIL_STATE_COMMAND'] in sys.argv: raise SystemExit(74)\n"
        "os.execv(os.environ['REAL_PYTHON'], [os.environ['REAL_PYTHON'], *sys.argv[1:]])\n",
    )

    result = _run(
        {
            **env,
            "REAL_PYTHON": real_python,
            "FAIL_STATE_COMMAND": failed_command,
        }
    )

    assert result.returncode != 0
    assert any(
        command.startswith("bootout ")
        for command in (home / "launchctl.log").read_text().splitlines()
    ), "fault injection did not cross the post-quiesce boundary"
    assert (plist.read_bytes(), marker.read_bytes(), loaded.read_bytes()) == before
