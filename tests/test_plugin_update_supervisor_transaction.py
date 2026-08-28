"""Transactional controls joining plugin cutover to supervisor installation."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts" / "plugin-update.sh"
PLUGIN = ROOT / "plugins" / "escapement-claude"
PLUGIN_ID = "escapement@escapement"


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _cutover_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[str, str]]:
    home = tmp_path / "home"
    claude = home / ".claude"
    old_cache = claude / "plugins" / "cache" / "escapement" / "escapement" / "old"
    new_cache = claude / "plugins" / "cache" / "escapement" / "escapement" / "new"
    shutil.copytree(PLUGIN, old_cache)
    shutil.copytree(PLUGIN, new_cache)
    pin = claude / ".escapement-pinned"
    for relative in (
        "harness/bin",
        "harness/schemas",
        "claude/skills/discovery",
    ):
        (pin / relative).mkdir(parents=True, exist_ok=True)
    for relative, target in (
        ("harness/bin", pin / "harness" / "bin"),
        ("harness/schemas", pin / "harness" / "schemas"),
        ("skills/discovery", pin / "claude" / "skills" / "discovery"),
    ):
        destination = claude / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(target)

    settings = claude / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "model": "opus[1m]",
                "enabledPlugins": {PLUGIN_ID: False},
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 -B ~/.claude/hooks/validate_no_shirking.py",
                                }
                            ]
                        }
                    ]
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    registry = claude / "plugins" / "installed_plugins.json"
    registry.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    PLUGIN_ID: [
                        {
                            "scope": "user",
                            "installPath": str(old_cache),
                            "version": "old",
                        }
                    ]
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "uname", "#!/bin/sh\nprintf 'Darwin\\n'\n")
    _write_executable(
        fake_bin / "python3",
        f"#!{sys.executable}\n"
        "import os,sys\n"
        "if any(value.endswith('delegation-canary.py') for value in sys.argv[1:]): raise SystemExit(0)\n"
        "os.execv(os.environ['REAL_PYTHON'], [os.environ['REAL_PYTHON'], *sys.argv[1:]])\n",
    )
    _write_executable(
        fake_bin / "claude",
        f"#!{sys.executable}\n"
        + r"""
import json
import os
import sys
from pathlib import Path

home = Path(os.environ["HOME"])
settings = home / ".claude" / "settings.json"
registry = home / ".claude" / "plugins" / "installed_plugins.json"
args = sys.argv[1:]
if args[:2] == ["plugin", "update"]:
    log = os.environ.get("CLAUDE_INVOCATION_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("plugin update\n")
    if os.environ.get("CLAUDE_UPDATE_FAIL") == "1":
        raise SystemExit(81)
    data = json.loads(settings.read_text())
    data.setdefault("enabledPlugins", {})["escapement@escapement"] = True
    data.pop("model", None)
    settings.write_text(json.dumps(data, indent=2) + "\n")
    installed = json.loads(registry.read_text())
    installed["plugins"]["escapement@escapement"] = [{
        "scope": "user",
        "installPath": os.environ["NEW_PLUGIN_CACHE"],
        "version": "new",
    }]
    registry.write_text(json.dumps(installed, indent=2) + "\n")
elif args[:2] == ["plugin", "disable"]:
    data = json.loads(settings.read_text())
    data.setdefault("enabledPlugins", {})["escapement@escapement"] = False
    settings.write_text(json.dumps(data, indent=2) + "\n")
elif "--print" in args:
    data = json.loads(settings.read_text())
    if data.get("enabledPlugins", {}).get("escapement@escapement") is not False:
        raise SystemExit(91)
    session = "post-rollback-native-session"
    tool = "toolu_post_rollback_native"
    task = "post-rollback-native-child"
    records = [
        {"type":"system","subtype":"init","session_id":session,"claude_code_version":"2.1.248"},
        {"type":"assistant","session_id":session,"message":{"role":"assistant","content":[{"type":"tool_use","id":tool,"name":"Agent","input":{"name":"post-rollback-native","subagent_type":"general-purpose","run_in_background":True}}]}},
        {"type":"system","subtype":"task_started","session_id":session,"tool_use_id":tool,"task_id":task,"is_backgrounded":True,"task_type":"local_agent"},
        {"type":"system","subtype":"task_notification","session_id":session,"tool_use_id":tool,"task_id":task,"status":"completed","uuid":"post-rollback-terminal"},
        {"type":"result","subtype":"success","session_id":session,"result":"NATIVE_AGENT_OK"},
    ]
    print("\n".join(json.dumps(record) for record in records))
raise SystemExit(0)
""",
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "NEW_PLUGIN_CACHE": str(new_cache),
        "REAL_PYTHON": sys.executable,
        "BASH_FUNC_launchctl%%": _shell_launchctl(),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return home, old_cache, new_cache, fake_bin, env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )


def _native_agent_probe(fake_bin: Path, env: dict[str, str]) -> list[dict]:
    result = subprocess.run(
        [
            str(fake_bin / "claude"),
            "--print",
            "--output-format",
            "stream-json",
            "Use Agent once in the background and return NATIVE_AGENT_OK.",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


def _link_target(path: Path) -> str | None:
    return os.readlink(path) if path.is_symlink() else None


def _write_trusted_supervisor_plist(home: Path) -> Path:
    path = (
        home
        / "Library"
        / "LaunchAgents"
        / "com.escapement.continuation-supervisor.plist"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(
            {
                "Label": "com.escapement.continuation-supervisor",
                "ProgramArguments": ["/usr/bin/true"],
            },
            handle,
        )
    path.chmod(0o600)
    return path


def _shell_launchctl() -> str:
    return r"""() {
  state="$HOME/launchctl.loaded"
  label="com.escapement.continuation-supervisor"
  printf '%s\n' "$*" >> "$HOME/launchctl.log"
  if [[ "${1:-}" == print ]]; then
    [[ -f "$state" ]] && grep -Fxq "$label" "$state" && return 0
    return 113
  fi
  if [[ "${1:-}" == bootout ]]; then
    [[ -f "$state" ]] && grep -Fxq "$label" "$state" || return 3
    grep -Fvx "$label" "$state" > "$state.next" || true
    mv -f "$state.next" "$state"
    [[ -s "$state" ]] || rm -f "$state"
    return 0
  fi
  if [[ "${1:-}" == bootstrap ]]; then
    if [[ "${LAUNCHCTL_BOOTSTRAP_FAIL_ONCE:-0}" == 1 && ! -e "$HOME/bootstrap-failed-once" ]]; then
      : > "$HOME/bootstrap-failed-once"
      return 75
    fi
    [[ "${LAUNCHCTL_BOOTSTRAP_FAIL:-0}" != 1 ]] || return 75
    [[ -f "$state" ]] && grep -Fxq "$label" "$state" && return 72
    printf '%s\n' "$label" >> "$state"
  fi
  return 0
}"""


def _valid_transaction_state(home: Path, backup: Path) -> dict:
    claude = home / ".claude"
    backup.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copy2(claude / "settings.json", backup / "settings.json")
    shutil.copy2(
        claude / "plugins" / "installed_plugins.json",
        backup / "installed_plugins.json",
    )
    return {
        "files": [
            {
                "kind": "settings",
                "source": str(claude / "settings.json"),
                "backup": str(backup / "settings.json"),
                "exists": True,
            },
            {
                "kind": "registry",
                "source": str(claude / "plugins" / "installed_plugins.json"),
                "backup": str(backup / "installed_plugins.json"),
                "exists": True,
            },
        ],
        "wrappers": [
            {
                "kind": kind,
                "path": str(claude / "harness" / kind),
                "exists": True,
                "target": os.readlink(claude / "harness" / kind),
            }
            for kind in ("bin", "schemas")
        ],
        "mode_root": None,
        "modes": [],
        "supervisor": {
            "loaded": False,
            "marker": {
                "kind": "supervisor_marker",
                "source": str(
                    claude / "harness" / "continuation-supervisor-installed.json"
                ),
                "backup": None,
                "exists": False,
            },
            "plist": {
                "kind": "supervisor_plist",
                "source": str(
                    home
                    / "Library"
                    / "LaunchAgents"
                    / "com.escapement.continuation-supervisor.plist"
                ),
                "backup": None,
                "exists": False,
            },
        },
    }


def test_supervisor_failure_rolls_back_every_cutover_authority(tmp_path):
    home, _, new_cache, _, env = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    settings = claude / "settings.json"
    registry = claude / "plugins" / "installed_plugins.json"
    wrapper_bin = claude / "harness" / "bin"
    wrapper_schemas = claude / "harness" / "schemas"
    legacy_skill = claude / "skills" / "discovery"
    executable = new_cache / "harness" / "bin" / "wakeup_waker.py"
    executable.chmod(0o644)
    plist = _write_trusted_supervisor_plist(home)
    loaded = home / "launchctl.loaded"
    loaded.write_text("com.escapement.continuation-supervisor\n", encoding="utf-8")
    before = {
        "settings": settings.read_bytes(),
        "registry": registry.read_bytes(),
        "bin": os.readlink(wrapper_bin),
        "schemas": os.readlink(wrapper_schemas),
        "skill": _link_target(legacy_skill),
        "mode": executable.stat().st_mode & 0o777,
        "plist": plist.read_bytes(),
        "loaded": loaded.read_bytes(),
    }

    result = _run({**env, "LAUNCHCTL_BOOTSTRAP_FAIL_ONCE": "1"})

    assert result.returncode != 0
    assert "==> done" not in result.stdout + result.stderr
    after = {
        "settings": settings.read_bytes(),
        "registry": registry.read_bytes(),
        "bin": os.readlink(wrapper_bin),
        "schemas": os.readlink(wrapper_schemas),
        "skill": _link_target(legacy_skill),
        "mode": executable.stat().st_mode & 0o777,
        "plist": plist.read_bytes(),
        "loaded": loaded.read_bytes(),
    }
    assert after == before, (
        "supervisor failure left registry/settings/wrappers/links/modes in different generations"
    )


def test_wrapper_replacement_never_removes_stable_name_mid_cutover(tmp_path):
    home, _, new_cache, fake_bin, env = _cutover_fixture(tmp_path)
    wrapper = home / ".claude" / "harness" / "bin"
    old_target = os.readlink(wrapper)
    system_ln = shutil.which("ln", path="/usr/bin:/bin")
    assert system_ln
    gate = tmp_path / "ln-gate"
    gate.mkdir()
    _write_executable(
        fake_bin / "ln",
        "#!/bin/bash\n"
        'dest="${@: -1}"\n'
        'if [[ "$dest" == "$HOME/.claude/harness/"* && ! -e "$LN_GATE/claimed" ]]; then\n'
        '  : > "$LN_GATE/claimed"\n'
        '  : > "$LN_GATE/entered"\n'
        '  while [[ ! -e "$LN_GATE/release" ]]; do /bin/sleep 0.01; done\n'
        "fi\n"
        'exec "$SYSTEM_LN" "$@"\n',
    )
    process = subprocess.Popen(
        ["bash", str(UPDATER)],
        cwd=ROOT,
        env={
            **env,
            "LN_GATE": str(gate),
            "SYSTEM_LN": system_ln,
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 8
        while (
            not (gate / "entered").exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert (gate / "entered").exists(), (
            "wrapper replacement never reached the concurrency probe"
        )
        assert os.path.lexists(wrapper), "stable wrapper disappeared during replacement"
        assert wrapper.is_symlink()
        assert os.readlink(wrapper) == old_target
        (gate / "release").touch()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stdout + stderr
    finally:
        if process.poll() is None:
            (gate / "release").touch()
            process.kill()
            process.wait()
    assert os.readlink(wrapper) == str(new_cache / "harness" / "bin")


def test_interrupted_cutover_is_recovered_before_the_next_update(tmp_path):
    home, _, new_cache, fake_bin, env = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    settings = claude / "settings.json"
    registry = claude / "plugins" / "installed_plugins.json"
    wrapper_bin = claude / "harness" / "bin"
    wrapper_schemas = claude / "harness" / "schemas"
    legacy_skill = claude / "skills" / "discovery"
    executable = new_cache / "harness" / "bin" / "wakeup_waker.py"
    executable.chmod(0o644)
    _write_trusted_supervisor_plist(home)
    loaded = home / "launchctl.loaded"
    loaded.write_text("com.escapement.continuation-supervisor\n", encoding="utf-8")
    before = {
        "settings": settings.read_bytes(),
        "registry": registry.read_bytes(),
        "bin": os.readlink(wrapper_bin),
        "schemas": os.readlink(wrapper_schemas),
        "skill": _link_target(legacy_skill),
        "mode": executable.stat().st_mode & 0o777,
        "loaded": loaded.read_bytes(),
    }
    system_ln = shutil.which("ln", path="/usr/bin:/bin")
    assert system_ln
    _write_executable(
        fake_bin / "ln",
        "#!/bin/bash\n"
        '"$SYSTEM_LN" "$@"\n'
        'if [[ "${2:-}" == */harness/schemas ]]; then kill -9 "$PPID"; fi\n',
    )

    interrupted = _run({**env, "SYSTEM_LN": system_ln})

    assert interrupted.returncode != 0
    assert os.readlink(wrapper_bin) == str(new_cache / "harness" / "bin"), (
        interrupted.stdout + interrupted.stderr
    )
    assert os.readlink(wrapper_schemas) == before["schemas"]
    journal = claude / ".plugin-update-transaction.json"
    assert journal.is_file(), "interrupted cutover lost its durable recovery intent"
    _write_executable(
        fake_bin / "ln",
        '#!/bin/bash\nexec "$SYSTEM_LN" "$@"\n',
    )

    retry = _run(
        {
            **env,
            "SYSTEM_LN": system_ln,
            "CLAUDE_UPDATE_FAIL": "1",
        }
    )

    assert retry.returncode != 0
    assert "recovered interrupted plugin cutover" in retry.stdout
    assert not journal.exists()
    after = {
        "settings": settings.read_bytes(),
        "registry": registry.read_bytes(),
        "bin": os.readlink(wrapper_bin),
        "schemas": os.readlink(wrapper_schemas),
        "skill": _link_target(legacy_skill),
        "mode": executable.stat().st_mode & 0o777,
        "loaded": loaded.read_bytes(),
    }
    assert after == before


def test_wrapper_renames_are_directory_durable_before_transaction_commit(tmp_path):
    home, _, _, _, env = _cutover_fixture(tmp_path)
    site = tmp_path / "audit-site"
    site.mkdir()
    audit = tmp_path / "wrapper-durability.jsonl"
    (site / "sitecustomize.py").write_text(
        r"""
import json
import os
import stat
import sys

audit = os.environ.get("ESCAPEMENT_WRAPPER_AUDIT")
real_fsync = os.fsync
real_chmod = os.chmod
real_replace = os.replace
real_unlink = os.unlink

def record(value):
    if audit:
        with open(audit, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(value) + "\n")

if os.path.basename(sys.argv[0]) == "launchctl":
    record(["launchctl-start"])

def identity(path):
    value = os.stat(path)
    return [value.st_dev, value.st_ino]

def replace(source, destination, *args, **kwargs):
    result = real_replace(source, destination, *args, **kwargs)
    text = str(destination)
    if text.endswith("/harness/bin") or text.endswith("/harness/schemas"):
        record(["wrapper-replace", text, identity(os.path.dirname(text))])
    return result

def fsync(descriptor):
    result = real_fsync(descriptor)
    metadata = os.fstat(descriptor)
    if stat.S_ISDIR(metadata.st_mode):
        record(["dir-fsync", [metadata.st_dev, metadata.st_ino]])
    elif stat.S_ISREG(metadata.st_mode):
        record(["file-fsync", [metadata.st_dev, metadata.st_ino]])
    return result

def chmod(path, mode, *args, **kwargs):
    if not isinstance(mode, int):
        path, mode = mode, args[0]
    result = real_chmod(path, mode)
    text = str(path)
    if "/plugins/cache/" in text and "/harness/bin/" in text:
        record(["mode-chmod", text, identity(text)])
    return result

def unlink(path, *args, **kwargs):
    if not isinstance(path, (str, bytes, os.PathLike)):
        path = args[0]
    if str(path).endswith(".plugin-update-transaction.json"):
        record(["journal-unlink"])
    return real_unlink(path)

os.replace = replace
os.fsync = fsync
os.chmod = chmod
os.unlink = unlink
""",
        encoding="utf-8",
    )

    result = _run(
        {
            **env,
            "PYTHONPATH": str(site),
            "ESCAPEMENT_WRAPPER_AUDIT": str(audit),
            "WRAPPER_LAUNCH_GATE": str(tmp_path / "launch-gate"),
            "BASH_FUNC_launchctl%%": (
                r"""() {
  if [[ "${1:-}" == print ]]; then return 113; fi
  if [[ ! -e "$WRAPPER_LAUNCH_GATE" ]]; then
    : > "$WRAPPER_LAUNCH_GATE"
    printf '["launchctl-start"]\n' >> "$ESCAPEMENT_WRAPPER_AUDIT"
    return 75
  fi
  [[ "${1:-}" == bootout ]] && return 3
  return 0
}"""
            ),
        }
    )

    assert audit.exists(), result.stdout + result.stderr
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    launch = next(i for i, event in enumerate(events) if event[0] == "launchctl-start")
    replacements = [
        (i, event)
        for i, event in enumerate(events[:launch])
        if event[0] == "wrapper-replace"
    ]
    assert len(replacements) == 2
    for replaced, event in replacements:
        assert any(
            candidate == ["dir-fsync", event[2]]
            for candidate in events[replaced + 1 : launch]
        ), f"wrapper directory was not durable before commit: {event[1]}"
    finished_events = [
        i for i, event in enumerate(events) if event[0] == "journal-unlink"
    ]
    assert finished_events, result.stdout + result.stderr
    finished = finished_events[0]
    mode_changes = [
        (i, event)
        for i, event in enumerate(events[:finished])
        if event[0] == "mode-chmod"
    ]
    assert mode_changes
    for changed, event in mode_changes:
        assert ["file-fsync", event[2]] in events[changed + 1 : finished]


@pytest.mark.parametrize(
    ("prior_marker", "prior_loaded"),
    [(False, False), (True, False), (False, True)],
)
def test_recovery_restores_exact_prior_supervisor_generation(
    tmp_path, prior_marker, prior_loaded
):
    home, _, _, fake_bin, env = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    launch_env = {**env, "BASH_FUNC_launchctl%%": _shell_launchctl()}
    marker = claude / "harness" / "continuation-supervisor-installed.json"
    plist = (
        home
        / "Library"
        / "LaunchAgents"
        / "com.escapement.continuation-supervisor.plist"
    )
    loaded = home / "launchctl.loaded"
    if prior_marker or prior_loaded:
        installed = _run(launch_env)
        assert installed.returncode == 0, installed.stdout + installed.stderr
        if not prior_marker:
            marker.unlink()
        if not prior_loaded:
            loaded.write_text("", encoding="utf-8")
    before_settings = (claude / "settings.json").read_bytes()
    before_registry = (claude / "plugins" / "installed_plugins.json").read_bytes()
    before_service = {
        "marker": marker.read_bytes() if marker.exists() else None,
        "plist": plist.read_bytes() if plist.exists() else None,
        "loaded": loaded.read_bytes() if loaded.exists() else b"",
    }
    real_python = sys.executable
    _write_executable(
        fake_bin / "python3",
        f"#!{real_python}\n"
        + r"""
import os
import signal
import sys

if any(value.endswith("plugin-update-transaction.py") for value in sys.argv[1:]) and "commit" in sys.argv[1:]:
    os.kill(os.getppid(), signal.SIGKILL)
    os.kill(os.getpid(), signal.SIGKILL)
if any(value.endswith("delegation-canary.py") for value in sys.argv[1:]):
    raise SystemExit(0)
os.execv(os.environ["REAL_PYTHON"], [os.environ["REAL_PYTHON"], *sys.argv[1:]])
""",
    )

    interrupted = _run(
        {
            **env,
            "REAL_PYTHON": real_python,
            "BASH_FUNC_launchctl%%": _shell_launchctl(),
        }
    )

    assert interrupted.returncode != 0
    assert (claude / ".plugin-update-transaction.json").is_file()
    assert loaded.read_text().splitlines() == ["com.escapement.continuation-supervisor"]
    (fake_bin / "python3").unlink()

    retry = _run(
        {
            **env,
            "BASH_FUNC_launchctl%%": _shell_launchctl(),
            "CLAUDE_UPDATE_FAIL": "1",
        }
    )

    assert retry.returncode != 0
    assert "recovered interrupted plugin cutover" in retry.stdout
    assert (claude / "settings.json").read_bytes() == before_settings
    assert (
        claude / "plugins" / "installed_plugins.json"
    ).read_bytes() == before_registry
    after_service = {
        "marker": marker.read_bytes() if marker.exists() else None,
        "plist": plist.read_bytes() if plist.exists() else None,
        "loaded": loaded.read_bytes() if loaded.exists() else b"",
    }
    assert after_service == before_service


def test_cutover_journal_cannot_delete_external_backup_tree(tmp_path):
    home, _, _, _, _ = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    external = tmp_path / ".cutover-backup-external"
    state = _valid_transaction_state(home, external)
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"must-survive\n")
    (external / "state.json").write_text(json.dumps(state) + "\n", encoding="utf-8")
    journal = claude / ".plugin-update-transaction.json"
    journal.write_text(json.dumps({"backup": str(external)}) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "plugin-update-transaction.py"),
            "recover",
            "--journal",
            str(journal),
            "--supervisor-installer",
            str(ROOT / "scripts" / "continuation-supervisor-install.sh"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert sentinel.read_bytes() == b"must-survive\n"
    assert external.is_dir()
    assert journal.is_file()


@pytest.mark.parametrize("attack", ["file", "wrapper", "mode", "mode-root"])
def test_contained_journal_cannot_mutate_external_authorities(tmp_path, attack):
    home, old_cache, _, _, _ = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    backup = claude / ".cutover-backup-contained"
    state = _valid_transaction_state(home, backup)
    external = tmp_path / "external-authority"
    external.write_text("must-survive\n", encoding="utf-8")
    external.chmod(0o640)
    if attack == "file":
        state["files"][0]["source"] = str(external)
    if attack == "wrapper":
        state["wrappers"][0].update(
            {"path": str(external), "exists": False, "target": None}
        )
    if attack == "mode":
        state["mode_root"] = str(old_cache / "harness" / "bin")
        state["modes"] = [{"path": str(external), "mode": 0o777}]
    if attack == "mode-root":
        cache_root = old_cache.parent
        resolved = tmp_path / "external" / "harness" / "bin"
        resolved.mkdir(parents=True)
        mode_root = cache_root / os.path.relpath(resolved, cache_root)
        victim = resolved / "authority"
        victim.write_bytes(external.read_bytes())
        victim.chmod(0o640)
        external = victim
        state["mode_root"] = str(mode_root)
        state["modes"] = [{"path": str(mode_root / victim.name), "mode": 0o777}]
    (backup / "state.json").write_text(json.dumps(state) + "\n", encoding="utf-8")
    journal = claude / ".plugin-update-transaction.json"
    journal.write_text(json.dumps({"backup": str(backup)}) + "\n", encoding="utf-8")
    before = (external.read_bytes(), external.stat().st_mode & 0o777)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "plugin-update-transaction.py"),
            "recover",
            "--journal",
            str(journal),
            "--supervisor-installer",
            str(ROOT / "scripts" / "continuation-supervisor-install.sh"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert (external.read_bytes(), external.stat().st_mode & 0o777) == before
    assert journal.is_file()


def test_wrong_owner_update_lock_fails_before_claude_or_authority_mutation(tmp_path):
    home, _, _, _, env = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    before = {
        "settings": (claude / "settings.json").read_bytes(),
        "registry": (claude / "plugins" / "installed_plugins.json").read_bytes(),
    }
    site = tmp_path / "wrong-owner-site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        "import os\n_real = os.getuid\nos.getuid = lambda: _real() + 1\n",
        encoding="utf-8",
    )
    invocation_log = tmp_path / "claude.log"

    result = _run(
        {
            **env,
            "PYTHONPATH": str(site),
            "CLAUDE_INVOCATION_LOG": str(invocation_log),
        }
    )

    assert result.returncode != 0
    assert not invocation_log.exists()
    assert (claude / "settings.json").read_bytes() == before["settings"]
    assert (claude / "plugins" / "installed_plugins.json").read_bytes() == before[
        "registry"
    ]


@pytest.mark.parametrize("authority", ["settings", "registry"])
def test_symlink_authority_fails_closed_before_claude_mutation(tmp_path, authority):
    home, _, _, _, env = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    path = (
        claude / "settings.json"
        if authority == "settings"
        else claude / "plugins" / "installed_plugins.json"
    )
    target = tmp_path / f"{authority}-target.json"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    before = target.read_bytes()
    invocation_log = tmp_path / "claude.log"

    result = _run({**env, "CLAUDE_INVOCATION_LOG": str(invocation_log)})

    assert result.returncode != 0
    assert not invocation_log.exists()
    assert target.read_bytes() == before
    assert path.is_symlink()


def test_loaded_service_without_trusted_plist_fails_before_plugin_cutover(tmp_path):
    home, _, _, _, env = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    loaded = home / "launchctl.loaded"
    loaded.write_text("com.escapement.continuation-supervisor\n", encoding="utf-8")
    before = {
        "settings": (claude / "settings.json").read_bytes(),
        "registry": (claude / "plugins" / "installed_plugins.json").read_bytes(),
        "bin": os.readlink(claude / "harness" / "bin"),
        "loaded": loaded.read_bytes(),
    }
    invocation_log = tmp_path / "claude.log"

    result = _run({**env, "CLAUDE_INVOCATION_LOG": str(invocation_log)})

    assert result.returncode != 0
    assert not invocation_log.exists()
    assert not (claude / ".plugin-update-transaction.json").exists()
    assert not (home / "Library" / "LaunchAgents").exists()
    assert {
        "settings": (claude / "settings.json").read_bytes(),
        "registry": (claude / "plugins" / "installed_plugins.json").read_bytes(),
        "bin": os.readlink(claude / "harness" / "bin"),
        "loaded": loaded.read_bytes(),
    } == before


@pytest.mark.parametrize("fault", ["record-modes", "chmod"])
def test_unguarded_post_begin_failure_restores_transaction(tmp_path, fault):
    home, _, new_cache, fake_bin, env = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    modes = {
        path: path.stat().st_mode & 0o777 for path in new_cache.glob("harness/bin/*")
    }
    before = {
        "settings": (claude / "settings.json").read_bytes(),
        "registry": (claude / "plugins" / "installed_plugins.json").read_bytes(),
        "bin": os.readlink(claude / "harness" / "bin"),
        "schemas": os.readlink(claude / "harness" / "schemas"),
    }
    if fault == "record-modes":
        real_python = sys.executable
        _write_executable(
            fake_bin / "python3",
            f"#!{real_python}\n"
            "import os,sys\n"
            "if 'record-modes' in sys.argv: raise SystemExit(73)\n"
            "os.execv(os.environ['REAL_PYTHON'], [os.environ['REAL_PYTHON'], *sys.argv[1:]])\n",
        )
        env = {**env, "REAL_PYTHON": real_python}
    else:
        _write_executable(
            fake_bin / "chmod",
            '#!/bin/bash\n/bin/chmod "$@"\nexit 73\n',
        )

    result = _run(env)

    assert result.returncode != 0
    assert not (claude / ".plugin-update-transaction.json").exists()
    assert {
        "settings": (claude / "settings.json").read_bytes(),
        "registry": (claude / "plugins" / "installed_plugins.json").read_bytes(),
        "bin": os.readlink(claude / "harness" / "bin"),
        "schemas": os.readlink(claude / "harness" / "schemas"),
    } == before
    assert {path: path.stat().st_mode & 0o777 for path in modes} == modes


def test_recovery_quiesces_new_service_before_restoring_filesystem(tmp_path):
    home, _, _, fake_bin, env = _cutover_fixture(tmp_path)
    claude = home / ".claude"
    real_python = sys.executable
    _write_executable(
        fake_bin / "python3",
        f"#!{real_python}\n"
        "import os,signal,sys\n"
        "if 'commit' in sys.argv: os.kill(os.getppid(), signal.SIGKILL); os.kill(os.getpid(), signal.SIGKILL)\n"
        "if any(value.endswith('delegation-canary.py') for value in sys.argv[1:]): raise SystemExit(0)\n"
        "os.execv(os.environ['REAL_PYTHON'], [os.environ['REAL_PYTHON'], *sys.argv[1:]])\n",
    )
    interrupted = _run({**env, "REAL_PYTHON": real_python})
    assert interrupted.returncode != 0
    assert (claude / ".plugin-update-transaction.json").exists()
    (fake_bin / "python3").unlink()
    site = tmp_path / "restore-audit"
    site.mkdir()
    observed = tmp_path / "loaded-at-first-restore"
    (site / "sitecustomize.py").write_text(
        "import os\n"
        "_replace=os.replace\n"
        "def replace(source,destination,*args,**kwargs):\n"
        " if str(destination)==os.path.join(os.environ['HOME'],'.claude','settings.json') and not os.path.exists(os.environ['RESTORE_OBSERVED']):\n"
        "  state=os.path.join(os.environ['HOME'],'launchctl.loaded')\n"
        "  open(os.environ['RESTORE_OBSERVED'],'wb').write(open(state,'rb').read() if os.path.exists(state) else b'')\n"
        " return _replace(source,destination,*args,**kwargs)\n"
        "os.replace=replace\n",
        encoding="utf-8",
    )

    retry = _run(
        {
            **env,
            "PYTHONPATH": str(site),
            "RESTORE_OBSERVED": str(observed),
            "CLAUDE_UPDATE_FAIL": "1",
        }
    )

    assert retry.returncode != 0
    assert observed.read_bytes() == b"", (
        "rollback restored filesystem authority while the new service was still loaded"
    )
    loaded = home / "launchctl.loaded"
    assert not loaded.exists() or not loaded.read_text().splitlines()
