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
    codex_home.mkdir()
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
    global_command = (
        f"python3 {codex_home}/hooks/allow_gate.py"
        if overlap
        else "python3 /opt/personal/hooks/unrelated.py"
    )
    (codex_home / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"command": global_command}]}
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
