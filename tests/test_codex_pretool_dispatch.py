from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = ROOT / "claude" / "hooks" / "codex_pretool_dispatch.py"


def _gate(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path


def _installed_dispatcher(plugin_root: Path) -> Path:
    installed = plugin_root / "claude" / "hooks" / DISPATCHER.name
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DISPATCHER, installed)
    return installed


def _run(
    plugin_root: Path,
    workspace: Path,
    *gates: Path,
) -> subprocess.CompletedProcess[str]:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pwd"},
        "cwd": str(workspace),
    }
    args = [sys.executable, "-B", str(_installed_dispatcher(plugin_root))]
    for gate in gates:
        args.extend(("--gate", str(gate.relative_to(plugin_root))))
    return subprocess.run(
        args,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=workspace,
        check=False,
        timeout=5,
    )


def test_dispatcher_preserves_context_and_strongest_public_decision(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = _gate(
        plugin_root / "first.py",
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'ask', "
        "'permissionDecisionReason': 'ask reason', "
        "'additionalContext': 'first context: ' + payload['tool_input']['command']}}))",
    )
    second = _gate(
        plugin_root / "second.py",
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', "
        "'permissionDecisionReason': 'deny reason', "
        "'additionalContext': 'second context'}}))",
    )

    result = _run(plugin_root, workspace, first, second)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    hook = output["hookSpecificOutput"]
    assert hook["permissionDecision"] == "deny"
    assert hook["permissionDecisionReason"] == "[ask] ask reason\n\n[deny] deny reason"
    assert hook["additionalContext"] == "first context: pwd\n\nsecond context"


def test_dispatcher_preserves_healthy_messages_and_equal_precedence_reasons(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = _gate(
        plugin_root / "first.py",
        "print(json.dumps({'systemMessage': 'first message', 'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'ask', "
        "'permissionDecisionReason': 'first ask'}}))",
    )
    second = _gate(
        plugin_root / "second.py",
        "print(json.dumps({'systemMessage': 'second message', 'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'ask', "
        "'permissionDecisionReason': 'second ask'}}))",
    )
    duplicate = _gate(
        plugin_root / "duplicate.py",
        "print(json.dumps({'systemMessage': 'first message', 'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'allow', "
        "'permissionDecisionReason': 'weaker allow'}}))",
    )

    result = _run(plugin_root, workspace, first, second, duplicate)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    hook = output["hookSpecificOutput"]
    assert hook["permissionDecision"] == "ask"
    assert hook["permissionDecisionReason"] == (
        "[ask] first ask\n\n[ask] second ask\n\n[allow] weaker allow"
    )
    assert output["systemMessage"] == "first message\n\nsecond message"


def test_dispatcher_never_short_circuits_later_deny_or_advisory_output(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first_deny = _gate(
        plugin_root / "first_deny.py",
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', "
        "'permissionDecisionReason': 'deny A'}}))",
    )
    advisory = _gate(
        plugin_root / "advisory.py",
        "print(json.dumps({'systemMessage': 'middle message', 'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'additionalContext': 'middle context'}}))",
    )
    second_deny = _gate(
        plugin_root / "second_deny.py",
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', "
        "'permissionDecisionReason': 'deny B'}}))",
    )

    result = _run(plugin_root, workspace, first_deny, advisory, second_deny)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    hook = output["hookSpecificOutput"]
    assert hook["permissionDecisionReason"] == "[deny] deny A\n\n[deny] deny B"
    assert hook["additionalContext"] == "middle context"
    assert output["systemMessage"] == "middle message"


def test_dispatcher_reports_one_broken_gate_and_continues_to_later_gate(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    broken = _gate(plugin_root / "broken.py", "raise RuntimeError('poison gate')")
    valid = _gate(
        plugin_root / "valid.py",
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'additionalContext': 'valid gate ran'}}))",
    )

    result = _run(plugin_root, workspace, broken, valid)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["additionalContext"] == "valid gate ran"
    assert "broken.py" in output["systemMessage"]
    assert "poison gate" in output["systemMessage"]


def test_dispatcher_accepts_normal_system_exit_and_reports_nonzero_exit(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    normal = _gate(
        plugin_root / "normal.py",
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'additionalContext': 'normal exit'}})); "
        "raise SystemExit(0)",
    )
    nonzero = _gate(plugin_root / "nonzero.py", "raise SystemExit(7)")

    result = _run(plugin_root, workspace, normal, nonzero)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["additionalContext"] == "normal exit", output
    assert "normal.py" not in output["systemMessage"]
    assert "nonzero.py" in output["systemMessage"]
    assert "status 7" in output["systemMessage"]


def test_dispatcher_runs_gates_in_its_own_process_from_an_unrelated_cwd(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gates = []
    pid_files = []
    for index in range(3):
        pid_file = tmp_path / f"gate-{index}.pid"
        pid_files.append(pid_file)
        gate = _gate(
            plugin_root / f"pid_gate_{index}.py",
            f"open({str(pid_file)!r}, 'w', encoding='utf-8').write(str(os.getpid())); "
            "print(json.dumps({'hookSpecificOutput': {"
            "'hookEventName': 'PreToolUse', 'additionalContext': payload['cwd']}}))",
        )
        gate.write_text(
            "import os\n" + gate.read_text(encoding="utf-8"), encoding="utf-8"
        )
        gates.append(gate)
    dispatcher = _installed_dispatcher(plugin_root)
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pwd"},
        "cwd": str(workspace),
    }
    args = [sys.executable, "-B", str(dispatcher)]
    for gate in gates:
        args.extend(("--gate", str(gate.relative_to(plugin_root))))
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=workspace,
    )
    stdout, stderr = process.communicate(json.dumps(payload), timeout=5)

    assert process.returncode == 0, stderr
    assert all(path.is_file() for path in pid_files), (stdout, stderr)
    assert [int(path.read_text(encoding="utf-8")) for path in pid_files] == [
        process.pid,
        process.pid,
        process.pid,
    ]
    assert json.loads(stdout)["hookSpecificOutput"]["additionalContext"] == str(workspace)


def test_dispatcher_restores_process_state_between_gates(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline_file = tmp_path / "baseline.json"
    baseline = _gate(
        plugin_root / "baseline.py",
        f"open({str(baseline_file)!r}, 'w', encoding='utf-8').write(json.dumps({{"
        "'cwd': os.getcwd(), 'environment': dict(os.environ), "
        "'sys_path': list(sys.path), 'json_id': id(sys.modules['json']), "
        "'pathlib_id': id(sys.modules['pathlib'])})); "
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'additionalContext': 'baseline ran'}}))",
    )
    baseline.write_text(
        "import os\n" + baseline.read_text(encoding="utf-8"), encoding="utf-8"
    )
    mutator = _gate(
        plugin_root / "mutator.py",
        "os.chdir(payload['tool_input']['poison_cwd']); "
        "os.environ['ESCAPEMENT_DISPATCH_BASELINE'] = 'overwritten'; "
        "del os.environ['ESCAPEMENT_DISPATCH_DELETE']; "
        "os.environ['ESCAPEMENT_DISPATCH_ADDED'] = 'present'; "
        "sys.path[:] = ['/poison/path']; "
        "sys.modules['json'] = None; "
        "del sys.modules['pathlib']; "
        "sys.modules['escapement_poison_module'] = types.ModuleType('poison'); "
        "print(json_module.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'additionalContext': 'mutator ran'}}))",
    )
    mutator.write_text(
        "import os, types\nimport json as json_module\n"
        + mutator.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    witness = _gate(
        plugin_root / "witness.py",
        f"baseline = json.loads(open({str(baseline_file)!r}, encoding='utf-8').read()); "
        "assert os.getcwd() == baseline['cwd']; "
        "assert dict(os.environ) == baseline['environment']; "
        "assert list(sys.path) == baseline['sys_path']; "
        "assert 'escapement_poison_module' not in sys.modules; "
        "assert id(sys.modules['json']) == baseline['json_id']; "
        "assert id(sys.modules['pathlib']) == baseline['pathlib_id']; "
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'additionalContext': 'state isolated'}}))",
    )
    witness.write_text(
        "import json, os\n" + witness.read_text(encoding="utf-8"), encoding="utf-8"
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pwd", "poison_cwd": str(plugin_root)},
        "cwd": str(workspace),
    }
    dispatcher = _installed_dispatcher(plugin_root)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(dispatcher),
            "--gate",
            "baseline.py",
            "--gate",
            "mutator.py",
            "--gate",
            "witness.py",
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=workspace,
        check=False,
        timeout=5,
        env=os.environ
        | {
            "ESCAPEMENT_DISPATCH_BASELINE": "original",
            "ESCAPEMENT_DISPATCH_DELETE": "keep",
        },
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["additionalContext"] == (
        "baseline ran\n\nmutator ran\n\nstate isolated"
    )
    assert "failed" not in output.get("systemMessage", "")


def test_dispatcher_bounds_each_gate_and_continues_after_timeout(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = _gate(
        plugin_root / "first.py",
        "time.sleep(0.1); print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'additionalContext': 'first gate ran'}}))",
    )
    first.write_text(
        "import time\n" + first.read_text(encoding="utf-8"), encoding="utf-8"
    )
    slow = _gate(plugin_root / "slow.py", "time.sleep(0.2)")
    slow.write_text("import time\n" + slow.read_text(encoding="utf-8"), encoding="utf-8")
    _gate(
        plugin_root / "valid.py",
        "print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'additionalContext': 'later gate ran'}}))",
    )
    dispatcher = _installed_dispatcher(plugin_root)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(dispatcher),
            "--gate",
            "first.py",
            "--gate-timeout",
            "1",
            "--gate",
            "slow.py",
            "--gate-timeout",
            "0.05",
            "--gate",
            "valid.py",
            "--gate-timeout",
            "1",
        ],
        input=json.dumps({"hook_event_name": "PreToolUse"}),
        text=True,
        capture_output=True,
        cwd=workspace,
        check=False,
        timeout=2,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["additionalContext"] == (
        "first gate ran\n\nlater gate ran"
    )
    assert "slow.py" in output["systemMessage"]
    assert "timed out after 0.05s" in output["systemMessage"]


def test_dispatcher_rejects_multibyte_payload_over_one_mib_before_gates(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = tmp_path / "gate-ran"
    gate = _gate(
        plugin_root / "unused.py",
        f"open({str(marker)!r}, 'w', encoding='utf-8').write('ran')",
    )
    payload = json.dumps(
        {"padding": "é" * 600_000}, ensure_ascii=False
    ).encode("utf-8")
    assert len("é" * 600_000) < 1_048_576 < len(payload)

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(_installed_dispatcher(plugin_root)),
            "--gate",
            str(gate.relative_to(plugin_root)),
        ],
        input=payload,
        capture_output=True,
        cwd=workspace,
        check=False,
        timeout=5,
    )

    assert result.returncode != 0
    assert b"payload exceeds 1048576 bytes" in result.stderr.lower()
    assert not marker.exists()


def test_dispatcher_stops_reading_after_limit_without_waiting_for_eof(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = tmp_path / "gate-ran"
    gate = _gate(
        plugin_root / "unused.py",
        f"open({str(marker)!r}, 'w', encoding='utf-8').write('ran')",
    )
    payload = json.dumps({"padding": "x" * 1_048_576}).encode()
    read_descriptor, write_descriptor = os.pipe()
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(_installed_dispatcher(plugin_root)),
            "--gate",
            str(gate.relative_to(plugin_root)),
        ],
        stdin=read_descriptor,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=workspace,
    )
    os.close(read_descriptor)
    try:
        written = 0
        while written < len(payload):
            written += os.write(write_descriptor, payload[written:])
        process.wait(timeout=3)
        stdout, stderr = process.communicate(timeout=1)
    finally:
        os.close(write_descriptor)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)

    assert process.returncode != 0, stdout
    assert b"payload exceeds 1048576 bytes" in stderr.lower()
    assert not marker.exists()


def test_dispatcher_rejects_gate_outside_plugin_root(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(_installed_dispatcher(plugin_root)),
            "--gate",
            "../outside.py",
        ],
        input=json.dumps({"hook_event_name": "PreToolUse"}),
        text=True,
        capture_output=True,
        cwd=workspace,
        check=False,
        timeout=5,
    )

    assert result.returncode != 0
    assert "outside plugin root" in result.stderr.lower()


def test_dispatcher_cannot_hide_the_process_storm_behind_child_processes() -> None:
    assert DISPATCHER.is_file(), "the single-process Codex dispatcher is missing"
    tree = ast.parse(DISPATCHER.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    imported.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert not imported & {
        "asyncio",
        "concurrent",
        "ctypes",
        "multiprocessing",
        "subprocess",
    }
    forbidden_calls = {
        "create_subprocess_exec",
        "create_subprocess_shell",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "fork",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "spawn",
        "system",
    }
    assert not called_attributes & forbidden_calls
    assert not called_names & forbidden_calls
