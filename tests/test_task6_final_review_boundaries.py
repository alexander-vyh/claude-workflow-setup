"""Independent crash and lock boundaries for the Task 6 deployment transaction."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tests.test_continuation_supervisor_recovery import (
    INSTALLER,
    LABEL,
    _assert_records_cover,
    _fixture as _installer_fixture,
    _manifest_records,
    _run as _run_installer,
    _write_hazards,
)
from tests.test_plugin_update_supervisor_transaction import (
    ROOT,
    UPDATER,
    _cutover_fixture,
    _run as _run_updater,
    _write_executable,
)


def _plugin_snapshot(home: Path, mode_root: Path) -> dict[str, object]:
    claude = home / ".claude"
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    marker = claude / "harness" / "continuation-supervisor-installed.json"
    loaded = home / "launchctl.loaded"
    return {
        "settings": (claude / "settings.json").read_bytes(),
        "registry": (claude / "plugins" / "installed_plugins.json").read_bytes(),
        "bin": os.readlink(claude / "harness" / "bin"),
        "schemas": os.readlink(claude / "harness" / "schemas"),
        "modes": {
            path.name: path.stat().st_mode & 0o777
            for path in mode_root.iterdir()
            if path.is_file() and not path.is_symlink()
        },
        "marker": marker.read_bytes() if marker.exists() else None,
        "plist": plist.read_bytes() if plist.exists() else None,
        "loaded": loaded.read_bytes() if loaded.exists() else b"",
    }


def test_post_begin_command_substitution_failure_rolls_back_once(tmp_path):
    home, _, new_cache, fake_bin, env = _cutover_fixture(tmp_path)
    before = _plugin_snapshot(home, new_cache / "harness" / "bin")
    real_python = sys.executable
    counter = tmp_path / "registry-resolves"
    _write_executable(
        fake_bin / "python3",
        f"#!{real_python}\n"
        "import os,sys\n"
        "args=sys.argv[1:]\n"
        "if len(args)==2 and args[0]=='-' and args[1].endswith('installed_plugins.json'):\n"
        " count=int(open(os.environ['RESOLVE_COUNT']).read() or '0')+1 if os.path.exists(os.environ['RESOLVE_COUNT']) else 1\n"
        " open(os.environ['RESOLVE_COUNT'],'w').write(str(count))\n"
        " if count==3: raise SystemExit(73)\n"
        "os.execv(os.environ['REAL_PYTHON'],[os.environ['REAL_PYTHON'],*args])\n",
    )

    result = _run_updater(
        {**env, "REAL_PYTHON": real_python, "RESOLVE_COUNT": str(counter)}
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert counter.read_text() == "3", "fault did not reach the post-begin resolver"
    assert output.count("prior deployment generation restored") == 1
    assert "rollback FAILED" not in output
    assert not (home / ".claude" / ".plugin-update-transaction.json").exists()
    assert _plugin_snapshot(home, new_cache / "harness" / "bin") == before


def test_marker_parent_fsync_failure_rolls_back_first_install_and_retry_migrates(
    tmp_path,
):
    home, _, env = _installer_fixture(tmp_path)
    site = tmp_path / "marker-fsync-fault"
    fault = tmp_path / "marker-replaced"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        r"""
import os,stat
real_fsync=os.fsync; real_replace=os.replace; marker_replaced=False
def replace(source,destination,*args,**kwargs):
 global marker_replaced
 result=real_replace(source,destination,*args,**kwargs)
 if str(destination).endswith('continuation-supervisor-installed.json'):
  marker_replaced=True; open(os.environ['MARKER_REPLACED'],'a').close()
 return result
def fsync(descriptor):
 if marker_replaced and stat.S_ISDIR(os.fstat(descriptor).st_mode):
  raise OSError('injected marker parent fsync failure')
 return real_fsync(descriptor)
os.replace=replace; os.fsync=fsync
""",
        encoding="utf-8",
    )

    failed = _run_installer(
        {**env, "PYTHONPATH": str(site), "MARKER_REPLACED": str(fault)}
    )

    marker = home / ".claude" / "harness" / "continuation-supervisor-installed.json"
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    loaded = home / "launchctl.loaded"
    assert failed.returncode != 0
    assert fault.exists(), "write-marker never crossed its successful replace"
    assert not marker.exists()
    assert not plist.exists()
    assert not (loaded.read_text().splitlines() if loaded.exists() else [])

    hazards = _write_hazards(home)
    retry = _run_installer({**env, "ASSERT_SAFE_SCHEDULES": "1"})

    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert all(not source.exists() for source in hazards)
    _assert_records_cover(_manifest_records(home), hazards)
    assert loaded.read_text().splitlines() == [LABEL]
    assert not (home / "unsafe-load-attempted").exists()


def test_commit_parent_fsync_failure_keeps_prior_generation_recoverable(tmp_path):
    home, _, new_cache, _, env = _cutover_fixture(tmp_path)
    before = _plugin_snapshot(home, new_cache / "harness" / "bin")
    site = tmp_path / "commit-fsync-fault"
    fault = tmp_path / "journal-unlinked"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        r"""
import os,stat
real_fsync=os.fsync; real_unlink=os.unlink; journal_unlinked=False
def unlink(path,*args,**kwargs):
 global journal_unlinked
 actual=path if isinstance(path,(str,bytes,os.PathLike)) else args[0]
 result=real_unlink(actual)
 if str(actual).endswith('.plugin-update-transaction.json'):
  journal_unlinked=True; open(os.environ['JOURNAL_UNLINKED'],'a').close()
 return result
def fsync(descriptor):
 if journal_unlinked and stat.S_ISDIR(os.fstat(descriptor).st_mode):
  raise OSError('injected journal parent fsync failure')
 return real_fsync(descriptor)
os.unlink=unlink; os.fsync=fsync
""",
        encoding="utf-8",
    )

    failed = _run_updater(
        {**env, "PYTHONPATH": str(site), "JOURNAL_UNLINKED": str(fault)}
    )

    journal = home / ".claude" / ".plugin-update-transaction.json"
    assert failed.returncode != 0
    assert fault.exists(), "commit never crossed journal unlink"
    if journal.exists():
        retry = _run_updater({**env, "CLAUDE_UPDATE_FAIL": "1"})
        assert retry.returncode != 0
        assert "recovered interrupted plugin cutover" in retry.stdout
    assert not journal.exists()
    assert _plugin_snapshot(home, new_cache / "harness" / "bin") == before


def test_caller_lock_flag_cannot_cross_uncommitted_plugin_generation(tmp_path):
    home, _, _, _, env = _cutover_fixture(tmp_path)
    site = tmp_path / "commit-gate-site"
    gate = tmp_path / "commit-gate"
    site.mkdir()
    gate.mkdir()
    (site / "sitecustomize.py").write_text(
        "import os,time\n"
        "_unlink=os.unlink\n"
        "def unlink(path,*args,**kwargs):\n"
        " actual=path if isinstance(path,(str,bytes,os.PathLike)) else args[0]\n"
        " if str(actual).endswith('.plugin-update-transaction.json'):\n"
        "  open(os.path.join(os.environ['COMMIT_GATE'],'entered'),'a').close()\n"
        "  while not os.path.exists(os.path.join(os.environ['COMMIT_GATE'],'release')): time.sleep(.01)\n"
        " return _unlink(actual)\n"
        "os.unlink=unlink\n",
        encoding="utf-8",
    )
    updater = subprocess.Popen(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(site), "COMMIT_GATE": str(gate)},
    )
    standalone = None
    try:
        deadline = time.monotonic() + 30
        while not (gate / "entered").exists() and time.monotonic() < deadline:
            assert updater.poll() is None, "updater exited before commit gate"
            time.sleep(0.01)
        assert (gate / "entered").exists()
        launch_log = home / "launchctl.log"
        before_launch = launch_log.read_bytes()
        standalone = subprocess.Popen(
            ["bash", str(INSTALLER), "--uninstall"],
            cwd=ROOT,
            env={**env, "ESCAPEMENT_SUPERVISOR_LOCK_HELD": "1"},
        )
        time.sleep(0.5)
        assert standalone.poll() is None
        assert launch_log.read_bytes() == before_launch, (
            "caller lock flag bypassed the shared lifecycle lock"
        )
        (gate / "release").touch()
        assert updater.wait(timeout=20) == 0
        assert standalone.wait(timeout=20) == 0
    finally:
        (gate / "release").touch()
        for process in (standalone, updater):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
    assert not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()


def test_committed_backup_cleanup_failure_keeps_new_generation_and_retries(tmp_path):
    home, _, new_cache, _, env = _cutover_fixture(tmp_path)
    site = tmp_path / "committed-cleanup-fault"
    fault = tmp_path / "committed-rmtree-failed"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        r"""
import json,os,shutil
real_rmtree=shutil.rmtree
def rmtree(path,*args,**kwargs):
 guard=os.path.join(os.environ['HOME'],'.claude','.plugin-update-transaction.json.commit-guard')
 committed=False
 if os.path.exists(guard):
  try: committed=json.load(open(guard,encoding='utf-8')).get('committed') is True
  except (OSError,ValueError,AttributeError): pass
 if committed and os.path.basename(str(path)).startswith('.cutover-backup-') and not os.path.exists(os.environ['RMTREE_FAULT']):
  open(os.environ['RMTREE_FAULT'],'a').close()
  raise OSError('injected committed backup cleanup failure')
 return real_rmtree(path,*args,**kwargs)
shutil.rmtree=rmtree
""",
        encoding="utf-8",
    )

    first = subprocess.run(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(site), "RMTREE_FAULT": str(fault)},
        capture_output=True,
        text=True,
        timeout=40,
    )

    output = first.stdout + first.stderr
    claude = home / ".claude"
    guard = claude / ".plugin-update-transaction.json.commit-guard"
    assert fault.exists(), "cleanup fault did not follow committed guard publication"
    assert first.returncode == 0, output
    assert "prior deployment generation restored" not in output
    assert "rollback FAILED" not in output
    assert guard.is_file()
    committed = json.loads(guard.read_text(encoding="utf-8"))
    backup = Path(committed["backup"])
    assert committed.get("committed") is True and backup.is_dir()
    assert os.readlink(claude / "harness" / "bin") == str(new_cache / "harness" / "bin")
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]

    retry = _run_updater(env)

    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert "recovered interrupted plugin cutover" in retry.stdout
    assert not guard.exists()
    assert not backup.exists()
    assert not list(claude.glob(".cutover-backup-*"))
    assert os.readlink(claude / "harness" / "bin") == str(new_cache / "harness" / "bin")
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]


def test_commit_helper_sigkill_after_durable_guard_preserves_committed_result(tmp_path):
    home, _, new_cache, _, env = _cutover_fixture(tmp_path)
    site = tmp_path / "commit-kill-site"
    fault = tmp_path / "durable-guard-kill"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        r"""
import os,signal,sys
real_fsync=os.fsync; real_replace=os.replace
def replace(source,destination,*args,**kwargs):
 result=real_replace(source,destination,*args,**kwargs)
 commit_helper='commit' in sys.argv and any(str(value).endswith('plugin-update-transaction.py') for value in sys.argv)
 if commit_helper and str(destination).endswith('.plugin-update-transaction.json.commit-guard'):
  descriptor=os.open(os.path.dirname(str(destination)),os.O_RDONLY)
  try: real_fsync(descriptor)
  finally: os.close(descriptor)
  open(os.environ['DURABLE_GUARD_KILL'],'a').close()
  os.kill(os.getpid(),signal.SIGKILL)
 return result
os.replace=replace
""",
        encoding="utf-8",
    )

    first = subprocess.run(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(site), "DURABLE_GUARD_KILL": str(fault)},
        capture_output=True,
        text=True,
        timeout=40,
    )

    output = first.stdout + first.stderr
    claude = home / ".claude"
    guard = claude / ".plugin-update-transaction.json.commit-guard"
    assert fault.exists(), "commit helper was not killed after durable guard replace"
    assert first.returncode == 0, output
    assert "prior deployment generation restored" not in output
    assert "rollback FAILED" not in output
    assert guard.is_file()
    committed = json.loads(guard.read_text(encoding="utf-8"))
    backup = Path(committed["backup"])
    assert committed.get("committed") is True and backup.is_dir()
    assert os.readlink(claude / "harness" / "bin") == str(new_cache / "harness" / "bin")
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]

    retry = _run_updater(env)

    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert "recovered interrupted plugin cutover" in retry.stdout
    assert not guard.exists()
    assert not backup.exists()
    assert not list(claude.glob(".cutover-backup-*"))
    assert os.readlink(claude / "harness" / "bin") == str(new_cache / "harness" / "bin")
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]


def test_transaction_helper_sigkill_after_guard_cleanup_preserves_success(tmp_path):
    home, _, new_cache, _, env = _cutover_fixture(tmp_path)
    site = tmp_path / "commit-cleanup-kill-site"
    fault = tmp_path / "guard-unlinked-kill"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        r"""
import os,signal,sys
real_unlink=os.unlink
def unlink(path,*args,**kwargs):
 actual=path if isinstance(path,(str,bytes,os.PathLike)) else args[0]
 result=real_unlink(actual)
 transaction_helper=any(str(value).endswith('plugin-update-transaction.py') for value in sys.argv)
 cleanup_command=any(value in {'commit','recover'} for value in sys.argv)
 if transaction_helper and cleanup_command and str(actual).endswith('.plugin-update-transaction.json.commit-guard'):
  open(os.environ['GUARD_UNLINKED_KILL'],'a').close()
  os.kill(os.getpid(),signal.SIGKILL)
 return result
os.unlink=unlink
""",
        encoding="utf-8",
    )

    first = subprocess.run(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(site), "GUARD_UNLINKED_KILL": str(fault)},
        capture_output=True,
        text=True,
        timeout=40,
    )

    output = first.stdout + first.stderr
    claude = home / ".claude"
    journal = claude / ".plugin-update-transaction.json"
    guard = claude / ".plugin-update-transaction.json.commit-guard"
    assert fault.exists(), "commit helper was not killed after real guard unlink"
    assert first.returncode == 0, output
    assert "prior deployment generation restored" not in output
    assert "rollback FAILED" not in output
    assert not journal.exists()
    assert not guard.exists()
    assert not list(claude.glob(".cutover-backup-*"))
    assert os.readlink(claude / "harness" / "bin") == str(new_cache / "harness" / "bin")
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]

    retry = _run_updater(env)

    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert not journal.exists()
    assert not guard.exists()
    assert not list(claude.glob(".cutover-backup-*"))
    assert os.readlink(claude / "harness" / "bin") == str(new_cache / "harness" / "bin")
    assert (home / "launchctl.loaded").read_text().splitlines() == [LABEL]


def test_successful_commit_fsyncs_every_current_file_authority(tmp_path):
    _, _, _, _, env = _cutover_fixture(tmp_path)
    site = tmp_path / "commit-audit-site"
    audit = tmp_path / "commit-audit.jsonl"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        r"""
import json,os,stat
audit=os.environ['COMMIT_AUDIT']; real_fsync=os.fsync; real_unlink=os.unlink
def identity(path):
 value=os.stat(path); return [value.st_dev,value.st_ino]
def record(value):
 with open(audit,'a',encoding='utf-8') as handle: handle.write(json.dumps(value)+'\n')
def fsync(fd):
 result=real_fsync(fd); value=os.fstat(fd)
 if stat.S_ISREG(value.st_mode): record(['file-fsync',[value.st_dev,value.st_ino]])
 return result
def unlink(path,*args,**kwargs):
 actual=path if isinstance(path,(str,bytes,os.PathLike)) else args[0]
 if str(actual).endswith('.plugin-update-transaction.json'):
  home=os.environ['HOME']; root=os.path.join(os.environ['NEW_PLUGIN_CACHE'],'harness','bin')
  paths=[os.path.join(home,'.claude','settings.json'),os.path.join(home,'.claude','plugins','installed_plugins.json')]
  paths += [entry.path for entry in os.scandir(root) if entry.is_file(follow_symlinks=False)]
  record(['commit',[[path,identity(path)] for path in paths]])
 return real_unlink(actual)
os.fsync=fsync; os.unlink=unlink
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(site), "COMMIT_AUDIT": str(audit)},
        capture_output=True,
        text=True,
        timeout=40,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    committed = next(i for i, event in enumerate(events) if event[0] == "commit")
    fsynced = {
        tuple(event[1]) for event in events[:committed] if event[0] == "file-fsync"
    }
    missing = [
        path
        for path, identity in events[committed][1]
        if tuple(identity) not in fsynced
    ]
    assert not missing, (
        f"journal committed before current authorities were durable: {missing}"
    )
