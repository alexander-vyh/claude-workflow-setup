from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify_codex_hook_runtime.py"
DISPATCHER = ROOT / "claude" / "hooks" / "codex_pretool_dispatch.py"


def _installed_fixture(
    tmp_path: Path,
    *,
    output_loss: bool = False,
    overlap: bool = False,
    traversal: bool = False,
    two_bash: bool = False,
) -> tuple[Path, Path]:
    assert DISPATCHER.is_file(), "dispatcher must exist before its installed verifier can run"
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True)
    plugin_root = tmp_path / "installed-plugin"
    hook_dir = plugin_root / "claude" / "hooks"
    hook_dir.mkdir(parents=True)
    shutil.copy2(DISPATCHER, hook_dir / DISPATCHER.name)
    if output_loss:
        (hook_dir / DISPATCHER.name).write_text(
            "import json, sys\njson.load(sys.stdin)\nprint('{}')\n",
            encoding="utf-8",
        )
    (hook_dir / "allow_gate.py").write_text(
        "import json, os, sys, time\n"
        "data = json.load(sys.stdin)\n"
        "barrier = os.environ.get('ESCAPEMENT_TEST_BARRIER')\n"
        "if barrier:\n"
        "    descriptor = os.open(barrier, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)\n"
        "    try:\n"
        "        os.write(descriptor, f'{os.getpid()}\\n'.encode())\n"
        "    finally:\n"
        "        os.close(descriptor)\n"
        "    deadline = time.monotonic() + 3\n"
        "    expected = int(os.environ['ESCAPEMENT_TEST_BARRIER_COUNT'])\n"
        "    while len(open(barrier, encoding='utf-8').read().splitlines()) < expected:\n"
        "        if time.monotonic() >= deadline:\n"
        "            raise RuntimeError('concurrency barrier timed out')\n"
        "        time.sleep(0.01)\n"
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', "
        "'additionalContext': 'verified ' + data['tool_input']['command']}}))\n",
        encoding="utf-8",
    )
    command = (
        'python3 -B "${PLUGIN_ROOT}/claude/hooks/codex_pretool_dispatch.py" '
        + (
            "--gate ../outside.py"
            if traversal
            else "--gate claude/hooks/allow_gate.py"
        )
    )
    if overlap:
        legacy_name = "test_oracle_brief_gate.py"
        shutil.copy2(ROOT / "claude" / "hooks" / legacy_name, hook_dir / legacy_name)
        command += f" --gate claude/hooks/{legacy_name}"
    bash_groups = [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": 10}]}
    ]
    if two_bash:
        bash_groups.append(
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'python3 -B "${PLUGIN_ROOT}/claude/hooks/allow_gate.py"',
                        "timeout": 10,
                    }
                ],
            }
        )
    (plugin_root / "hooks").mkdir()
    (plugin_root / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": bash_groups}}, indent=2) + "\n",
        encoding="utf-8",
    )
    if overlap:
        legacy = codex_home / "hooks" / "test_oracle_brief_gate.py"
        legacy.parent.mkdir()
        shutil.copy2(ROOT / "claude" / "hooks" / legacy.name, legacy)
        global_hook = {
            "command": f"python3 {legacy}",
            "statusMessage": "Checking Test Oracle Brief gate",
            "timeout": 30,
            "type": "command",
        }
    else:
        global_hook = {"command": "python3 /opt/personal/hooks/unrelated.py"}
    (codex_home / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [global_hook]}
                    ]
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return codex_home, plugin_root


def _run(codex_home: Path, plugin_root: Path) -> subprocess.CompletedProcess[str]:
    barrier = codex_home.parent / "probe-barrier.txt"
    return subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--codex-home",
            str(codex_home),
            "--plugin-root",
            str(plugin_root),
            "--concurrency",
            "4",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=os.environ
        | {
            "ESCAPEMENT_TEST_BARRIER": str(barrier),
            "ESCAPEMENT_TEST_BARRIER_COUNT": "4",
        },
    )


def _fake_codex(tmp_path: Path, payload: dict) -> tuple[Path, Path]:
    executable = tmp_path / "fake-codex"
    observed_home = tmp_path / "observed-codex-home"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['FAKE_OBSERVED_CODEX_HOME']).write_text("
        "os.environ.get('CODEX_HOME', ''), encoding='utf-8')\n"
        "print(os.environ['FAKE_PLUGIN_PAYLOAD'])\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, observed_home


def _run_require_installed(
    codex_home: Path,
    fake_codex: Path,
    observed_home: Path,
    payload: dict,
) -> subprocess.CompletedProcess[str]:
    barrier = codex_home.parent / "installed-probe-barrier.txt"
    return subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--codex-home",
            str(codex_home),
            "--require-installed",
            "--codex-bin",
            str(fake_codex),
            "--concurrency",
            "4",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=os.environ
        | {
            "ESCAPEMENT_TEST_BARRIER": str(barrier),
            "ESCAPEMENT_TEST_BARRIER_COUNT": "4",
            "FAKE_OBSERVED_CODEX_HOME": str(observed_home),
            "FAKE_PLUGIN_PAYLOAD": json.dumps(payload),
        },
    )


def test_verifier_exercises_one_installed_dispatcher_concurrently(tmp_path: Path) -> None:
    codex_home, plugin_root = _installed_fixture(tmp_path)

    result = _run(codex_home, plugin_root)

    assert result.returncode == 0, result.stderr
    assert "4 concurrent installed dispatcher probes passed" in result.stdout
    assert "one Escapement Bash hook" in result.stdout
    arrivals = (codex_home.parent / "probe-barrier.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(arrivals) == 4
    assert len(set(arrivals)) == 4


def test_verifier_rejects_more_than_one_escapement_bash_process(tmp_path: Path) -> None:
    codex_home, plugin_root = _installed_fixture(tmp_path, two_bash=True)

    result = _run(codex_home, plugin_root)

    assert result.returncode != 0
    assert "expected exactly one" in result.stderr.lower()


def test_verifier_rejects_legacy_global_overlap(tmp_path: Path) -> None:
    codex_home, plugin_root = _installed_fixture(tmp_path, overlap=True)

    result = _run(codex_home, plugin_root)

    assert result.returncode != 0
    assert "legacy global overlap" in result.stderr.lower()


def test_verifier_rejects_dispatcher_gate_path_traversal(tmp_path: Path) -> None:
    codex_home, plugin_root = _installed_fixture(tmp_path, traversal=True)

    result = _run(codex_home, plugin_root)

    assert result.returncode != 0
    assert "outside plugin root" in result.stderr.lower()


def test_verifier_rejects_dispatcher_that_loses_advisory_output(tmp_path: Path) -> None:
    codex_home, plugin_root = _installed_fixture(tmp_path, output_loss=True)

    result = _run(codex_home, plugin_root)

    assert result.returncode != 0
    assert "advisory output" in result.stderr.lower()


def test_require_installed_binds_cli_and_cache_to_selected_codex_home(
    tmp_path: Path,
) -> None:
    fixture_home, fixture_plugin = _installed_fixture(tmp_path / "fixture")
    codex_home = tmp_path / "custom-codex-home"
    codex_home.mkdir()
    cache = codex_home / "plugins" / "cache" / "escapement" / "escapement" / "1.0.0"
    shutil.copytree(fixture_plugin, cache)
    (codex_home / "hooks.json").write_bytes((fixture_home / "hooks.json").read_bytes())
    fake_codex, observed_home = _fake_codex(tmp_path, {})
    payload = {
        "installed": [
            {
                "pluginId": "escapement@escapement",
                "name": "escapement",
                "marketplaceName": "escapement",
                "version": "1.0.0",
                "enabled": True,
                "source": {"path": str(tmp_path / "wrong-home-plugin")},
            }
        ]
    }

    result = _run_require_installed(
        codex_home, fake_codex, observed_home, payload
    )

    assert result.returncode == 0, result.stderr
    assert observed_home.read_text(encoding="utf-8") == str(codex_home)


def test_require_installed_rejects_disabled_plugin_with_valid_selected_cache(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "custom-codex-home"
    codex_home.mkdir()
    _fixture_home, external_plugin = _installed_fixture(tmp_path / "external")
    cache = codex_home / "plugins" / "cache" / "escapement" / "escapement" / "1.0.0"
    shutil.copytree(external_plugin, cache)
    fake_codex, observed_home = _fake_codex(tmp_path, {})
    base = {
        "pluginId": "escapement@escapement",
        "name": "escapement",
        "marketplaceName": "escapement",
        "version": "1.0.0",
        "source": {"path": str(external_plugin)},
    }

    result = _run_require_installed(
        codex_home,
        fake_codex,
        observed_home,
        {"installed": [base | {"enabled": False}]},
    )

    assert result.returncode != 0
    assert "not installed" in result.stderr.lower()


def test_require_installed_rejects_cross_home_source_without_selected_cache(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "custom-codex-home"
    codex_home.mkdir()
    _fixture_home, external_plugin = _installed_fixture(tmp_path / "external")
    fake_codex, observed_home = _fake_codex(tmp_path, {})
    payload = {
        "installed": [
            {
                "pluginId": "escapement@escapement",
                "name": "escapement",
                "marketplaceName": "escapement",
                "version": "1.0.0",
                "enabled": True,
                "source": {"path": str(external_plugin)},
            }
        ]
    }

    result = _run_require_installed(
        codex_home,
        fake_codex,
        observed_home,
        payload,
    )

    assert result.returncode != 0
    assert "selected codex home" in result.stderr.lower()


def test_require_installed_uses_reported_version_not_any_cache_version(
    tmp_path: Path,
) -> None:
    _fixture_home, fixture_plugin = _installed_fixture(tmp_path / "fixture")
    codex_home = tmp_path / "custom-codex-home"
    codex_home.mkdir()
    old_cache = codex_home / "plugins" / "cache" / "escapement" / "escapement" / "1.0.0"
    shutil.copytree(fixture_plugin, old_cache)
    fake_codex, observed_home = _fake_codex(tmp_path, {})
    payload = {
        "installed": [
            {
                "pluginId": "escapement@escapement",
                "name": "escapement",
                "marketplaceName": "escapement",
                "version": "2.0.0",
                "enabled": True,
                "source": {"path": str(fixture_plugin)},
            }
        ]
    }

    result = _run_require_installed(
        codex_home, fake_codex, observed_home, payload
    )

    assert result.returncode != 0
    assert "selected codex home" in result.stderr.lower()


def test_require_installed_rejects_cache_symlink_escaping_selected_home(
    tmp_path: Path,
) -> None:
    _fixture_home, fixture_plugin = _installed_fixture(tmp_path / "fixture")
    codex_home = tmp_path / "custom-codex-home"
    cache_parent = codex_home / "plugins" / "cache" / "escapement" / "escapement"
    cache_parent.mkdir(parents=True)
    (cache_parent / "1.0.0").symlink_to(fixture_plugin, target_is_directory=True)
    fake_codex, observed_home = _fake_codex(tmp_path, {})
    payload = {
        "installed": [
            {
                "pluginId": "escapement@escapement",
                "name": "escapement",
                "marketplaceName": "escapement",
                "version": "1.0.0",
                "enabled": True,
                "source": {"path": str(fixture_plugin)},
            }
        ]
    }

    result = _run_require_installed(
        codex_home, fake_codex, observed_home, payload
    )

    assert result.returncode != 0
    assert "escapes selected codex home" in result.stderr.lower()


def test_require_installed_rejects_stale_selected_cache(tmp_path: Path) -> None:
    _fixture_home, fixture_plugin = _installed_fixture(tmp_path / "fixture")
    codex_home = tmp_path / "custom-codex-home"
    codex_home.mkdir()
    cache = codex_home / "plugins" / "cache" / "escapement" / "escapement" / "1.0.0"
    shutil.copytree(fixture_plugin, cache)
    (cache / "claude" / "hooks" / DISPATCHER.name).unlink()
    fake_codex, observed_home = _fake_codex(tmp_path, {})
    payload = {
        "installed": [
            {
                "pluginId": "escapement@escapement",
                "name": "escapement",
                "marketplaceName": "escapement",
                "version": "1.0.0",
                "enabled": True,
                "source": {"path": str(fixture_plugin)},
            }
        ]
    }

    result = _run_require_installed(
        codex_home, fake_codex, observed_home, payload
    )

    assert result.returncode != 0
    assert "dispatcher is missing" in result.stderr.lower()
