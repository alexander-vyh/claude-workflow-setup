import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "agent-surfaces" / "manifest.json"
PI_ROOT = ROOT / "plugins" / "escapement-pi"
EXTENSION = PI_ROOT / "extensions" / "index.ts"
RENDERER = ROOT / "tools" / "render_agent_surfaces.py"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _pi_ready_bash_gates(manifest: dict) -> list[dict]:
    adapter = manifest["adapters"]["pi"]
    gates = []
    for hook in manifest["hooks"]:
        host = hook["hosts"][adapter["gate_source_host"]]
        if host["status"] != "ready":
            continue
        for event in host.get("events", []):
            if (
                event["event"] == adapter["source_event"]
                and event["matcher"] == adapter["source_matcher"]
            ):
                gates.append(
                    {
                        "id": hook["id"],
                        "source": hook["source"],
                        "timeout_seconds": event["timeout_seconds"],
                    }
                )
    return gates


def test_pi_is_an_explicit_shared_root_adapter() -> None:
    manifest = _manifest()

    assert manifest["documents"]["hosts"]["pi"]["target"] == (
        "plugins/escapement-pi/PI.md"
    )
    assert manifest["adapters"]["pi"] == {
        "gate_source_host": "codex",
        "source_event": "PreToolUse",
        "source_matcher": "Bash",
        "target_event": "tool_call",
        "target_matcher": "bash",
    }


def test_root_package_exposes_pi_resources_from_the_shared_root() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert "pi-package" in package["keywords"]
    assert package["pi"] == {
        "extensions": ["./plugins/escapement-pi/extensions/index.ts"],
        "skills": ["./.agents/skills"],
    }
    assert EXTENSION.is_file()
    assert (PI_ROOT / "PI.md").is_file()


def test_generated_gate_inventory_exactly_matches_pi_ready_manifest_gates() -> None:
    manifest = _manifest()
    inventory = json.loads((PI_ROOT / "gates.json").read_text(encoding="utf-8"))

    assert inventory == {
        "version": 1,
        "dispatcher": "claude/hooks/codex_pretool_dispatch.py",
        "gates": _pi_ready_bash_gates(manifest),
    }
    assert inventory["gates"], "Pi must ship at least one behavioral gate"
    sources = [gate["source"] for gate in inventory["gates"]]
    assert len(sources) == len(set(sources)), "Pi gate inventory must not duplicate gates"
    assert all((PI_ROOT / source).is_file() for source in sources)


def test_renderer_recomputes_pi_inventory_when_shared_manifest_changes() -> None:
    spec = importlib.util.spec_from_file_location("pi_renderer_mutation", RENDERER)
    assert spec and spec.loader
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)

    manifest = _manifest()
    mutated = copy.deepcopy(manifest)
    planted = copy.deepcopy(
        next(hook for hook in manifest["hooks"] if hook["id"] == "beads_worktree_guard")
    )
    planted["id"] = "pi_inventory_mutation_control"
    mutated["hooks"].append(planted)

    original = json.loads(renderer._render_pi_gate_inventory(manifest))
    changed = json.loads(renderer._render_pi_gate_inventory(mutated))
    assert changed != original
    assert changed["gates"][-1]["id"] == "pi_inventory_mutation_control"


def _assert_thin_pi_extension(source: str) -> None:
    run_dispatcher = source.split("function runDispatcher", 1)[1].split(
        "function surfaceDiagnostics", 1
    )[0]
    handler = source.split('pi.on("tool_call"', 1)[1]

    assert source.count("spawn(") == 1, "one tool event must start one dispatcher"
    assert "codex_pretool_dispatch.py" not in source, (
        "the generated inventory, not TypeScript, owns the dispatcher path"
    )
    for gate_specific_policy in (
        "beads_worktree_guard.py",
        "test_oracle_brief_gate.py",
        "implementation_echo_test_gate.py",
    ):
        assert gate_specific_policy not in source
    assert run_dispatcher.count("payload") == 2
    assert run_dispatcher.count("JSON.stringify(payload)") == 1
    assert "child.stdin.end(JSON.stringify(payload));" in run_dispatcher
    assert "permissionDecision" not in run_dispatcher
    assert "hookSpecificOutput" not in run_dispatcher
    assert source.count("command") == 4
    assert handler.count("event.input") == 1
    assert handler.count("event.toolName") == 1
    assert handler.count("event.toolCallId") == 1
    assert handler.count("context.cwd") == 1
    assert handler.count("context.signal") == 1
    assert "const command = event.input?.command;" in handler
    assert 'if (typeof command !== "string") {' in handler
    assert [
        line.strip()
        for line in handler.splitlines()
        if line.strip().startswith("if (")
    ] == [
        'if (event.toolName !== "bash") return;',
        "if (runtime instanceof Error) {",
        'if (typeof command !== "string") {',
        'if (decision === "deny" || decision === "ask") {',
    ]
    for forbidden_policy_syntax in (
        ".includes(",
        ".indexOf(",
        ".match(",
        ".startsWith(",
        ".endsWith(",
        "RegExp(",
        "switch (",
        "switch(",
        "case ",
    ):
        assert forbidden_policy_syntax not in source
    assert "rm -rf" not in source


def test_pi_extension_is_a_thin_single_dispatch_bridge() -> None:
    _assert_thin_pi_extension(EXTENSION.read_text(encoding="utf-8"))


def test_pi_architecture_check_rejects_selective_typescript_policy() -> None:
    source = EXTENSION.read_text(encoding="utf-8")
    mutant = source.replace(
        "    try {\n      const result",
        "    if (command.includes(\"rm -rf\")) {\n"
        "      return { block: true, reason: \"TypeScript safety policy\" };\n"
        "    }\n\n"
        "    try {\n      const result",
        1,
    )

    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(mutant)

    helper_mutant = source.replace(
        "function runDispatcher",
        "function invalidBash(value: unknown): boolean {\n"
        '  return typeof value !== "string" || value === "rm -rf";\n'
        "}\n\n"
        "function runDispatcher",
        1,
    ).replace(
        'if (typeof command !== "string") {',
        "if (invalidBash(command)) {",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(helper_mutant)

    dispatcher_mutant = source.replace(
        "  const args =",
        '  if (JSON.stringify(payload).indexOf("rm -rf") >= 0) {\n'
        "    return Promise.resolve({ hookSpecificOutput: { "
        'permissionDecision: "deny" } });\n'
        "  }\n"
        "  const args =",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(dispatcher_mutant)

    regex_mutant = source.replace(
        "  const args =",
        "  if (/sudo/.test(JSON.stringify(payload))) {\n"
        "    return Promise.resolve({ hookSpecificOutput: { "
        'hookEventName: "PreToolUse", permissionDecision: "deny" } });\n'
        "  }\n"
        "  const args =",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(regex_mutant)

    input_mutant = source.replace(
        "    const command = event.input?.command;",
        "    if (Object.values(event.input ?? {}).some((value) => value === \"sudo\")) {\n"
        "      return { block: true, reason: \"TypeScript safety policy\" };\n"
        "    }\n"
        "    const command = event.input?.command;",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(input_mutant)

    cwd_mutant = source.replace(
        "    const command = event.input?.command;",
        '    if (context.cwd === "/") {\n'
        '      return { block: true, reason: "TypeScript root policy" };\n'
        "    }\n"
        "    const command = event.input?.command;",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(cwd_mutant)


def test_pi_extension_runs_one_dispatcher_per_tool_call_for_allow_and_deny(
    tmp_path,
) -> None:
    process_log = tmp_path / "python-processes.log"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    python_shim = shim_dir / "python3"
    python_shim.write_text(
        "#!/bin/sh\n"
        f"printf 'dispatch\\n' >> {process_log!s}\n"
        f"exec {sys.executable} \"$@\"\n",
        encoding="utf-8",
    )
    python_shim.chmod(0o755)
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        """
const { default: extension } = await import(process.argv[2]);

const handlers = new Map();
extension({ on(event, handler) { handlers.set(event, handler); } });
const toolCall = handlers.get("tool_call");
if (!toolCall) throw new Error("Pi extension did not register tool_call");
const context = { cwd: process.argv[3] };
const beforeAgentStart = handlers.get("before_agent_start");
const injected = await beforeAgentStart({
  type: "before_agent_start", systemPrompt: "base prompt",
}, context);
const safe = await toolCall({
  type: "tool_call", toolCallId: "safe", toolName: "bash",
  input: { command: "pwd" },
}, context);
const denied = await toolCall({
  type: "tool_call", toolCallId: "denied", toolName: "bash",
  input: { command: "git worktree add ../bad feat/bad" },
}, context);
console.log(JSON.stringify({ safe: safe ?? null, denied, injected }));
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            str(probe),
            EXTENSION.as_uri(),
            str(ROOT),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["safe"] is None
    assert output["denied"]["block"] is True
    assert "escapement-worktree create" in output["denied"]["reason"]
    assert output["injected"]["systemPrompt"].startswith("base prompt\n\n")
    assert "# Escapement Shared Workflow" in output["injected"]["systemPrompt"]
    assert process_log.read_text(encoding="utf-8").splitlines() == [
        "dispatch",
        "dispatch",
    ], "each Pi tool call must use exactly one shared dispatcher process"


def test_real_pi_sdk_loads_installed_extension_skills_and_nonce_gate(tmp_path) -> None:
    pi = shutil.which("pi")
    assert pi, "Pi CLI is required for the package contract test"
    pi_sdk = Path(pi).resolve().with_name("index.js")
    assert pi_sdk.is_file(), "Pi SDK must sit beside the selected CLI entrypoint"
    package = tmp_path / "escapement-package"
    package.mkdir()
    shutil.copy2(ROOT / "package.json", package / "package.json")
    shutil.copytree(ROOT / ".agents", package / ".agents")
    shutil.copytree(PI_ROOT, package / "plugins" / "escapement-pi")

    deny_nonce = f"deny-{uuid.uuid4()}"
    safe_nonce = f"safe-{uuid.uuid4()}"
    deny_reason = f"python-gate-{uuid.uuid4()}"
    pid_log = tmp_path / "gate-pids.log"
    test_gates = package / "plugins" / "escapement-pi" / "test-gates"
    test_gates.mkdir()
    (test_gates / "nonce_gate.py").write_text(
        "import json, os, sys\n"
        "payload = json.load(sys.stdin)\n"
        "with open(os.environ['PI_TEST_PID_LOG'], 'a') as out: "
        "out.write(str(os.getpid()) + '\\n')\n"
        "if payload.get('tool_input', {}).get('command') == os.environ['PI_DENY_NONCE']:\n"
        "    print(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', "
        "'permissionDecisionReason': os.environ['PI_DENY_REASON']}}))\n"
        "else:\n"
        "    print('{}')\n",
        encoding="utf-8",
    )
    (test_gates / "pid_witness.py").write_text(
        "import os\n"
        "with open(os.environ['PI_TEST_PID_LOG'], 'a') as out: "
        "out.write(str(os.getpid()) + '\\n')\n"
        "print('{}')\n",
        encoding="utf-8",
    )
    inventory_path = package / "plugins" / "escapement-pi" / "gates.json"
    inventory_path.write_text(
        json.dumps(
            {
                "version": 1,
                "dispatcher": "claude/hooks/codex_pretool_dispatch.py",
                "gates": [
                    {
                        "id": "nonce_gate",
                        "source": "test-gates/nonce_gate.py",
                        "timeout_seconds": 5,
                    },
                    {
                        "id": "pid_witness",
                        "source": "test-gates/pid_witness.py",
                        "timeout_seconds": 5,
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    config = tmp_path / "pi-agent"
    env = {
        **os.environ,
        "PI_CODING_AGENT_DIR": str(config),
        "PI_OFFLINE": "1",
        "PI_TEST_PID_LOG": str(pid_log),
        "PI_DENY_NONCE": deny_nonce,
        "PI_DENY_REASON": deny_reason,
    }

    install = subprocess.run(
        [pi, "install", str(package), "--approve"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert install.returncode == 0, install.stderr

    listed = subprocess.run(
        [pi, "list", "--approve"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert listed.returncode == 0, listed.stderr
    assert "escapement" in listed.stdout.lower()
    assert str(package) in listed.stdout

    probe = tmp_path / "installed-session.mjs"
    probe.write_text(
        """
const { createAgentSession } = await import(process.argv[2]);
const packageRoot = process.argv[4];
const created = await createAgentSession({
  agentDir: process.argv[3], cwd: process.argv[5], noTools: "all",
});
const { session, extensionsResult } = created;
const safe = await session.extensionRunner.emitToolCall({
  type: "tool_call", toolCallId: "safe", toolName: "bash",
  input: { command: process.argv[7] },
});
const denied = await session.extensionRunner.emitToolCall({
  type: "tool_call", toolCallId: "denied", toolName: "bash",
  input: { command: process.argv[6] },
});
const nonBash = await session.extensionRunner.emitToolCall({
  type: "tool_call", toolCallId: "read", toolName: "read",
  input: { path: process.argv[6] },
});
console.log(JSON.stringify({
  errors: extensionsResult.errors,
  extensions: extensionsResult.extensions.map((item) => item.resolvedPath),
  packageSkills: session.resourceLoader.getSkills().skills
    .filter((skill) => skill.sourceInfo.baseDir === packageRoot)
    .map((skill) => skill.name),
  safe: safe ?? null,
  denied,
  nonBash: nonBash ?? null,
}));
""",
        encoding="utf-8",
    )
    loaded = subprocess.run(
        [
            "node",
            str(probe),
            str(pi_sdk),
            str(config),
            str(package),
            str(tmp_path),
            deny_nonce,
            safe_nonce,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert loaded.returncode == 0, loaded.stderr
    result = json.loads(loaded.stdout)
    assert result["errors"] == []
    assert result["extensions"] == [str(package / "plugins/escapement-pi/extensions/index.ts")]
    assert "openspec-explore" in result["packageSkills"]
    assert result["safe"] is None
    assert result["denied"] == {"block": True, "reason": f"[deny] {deny_reason}"}
    assert result["nonBash"] is None
    assert deny_nonce not in EXTENSION.read_text(encoding="utf-8")

    pids = pid_log.read_text(encoding="utf-8").splitlines()
    assert len(pids) == 4, "two Bash events must each execute both Python gates"
    assert sorted(pids.count(pid) for pid in set(pids)) == [2, 2], (
        "each Bash event must run both gates inside one dispatcher PID"
    )
