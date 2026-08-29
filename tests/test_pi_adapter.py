import copy
import importlib.util
import json
import re
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


def _pi_ready_file_gates(manifest: dict) -> list[dict]:
    """Recompute the file-gate selection independently of the renderer."""
    adapter = manifest["adapters"]["pi"]
    gates = []
    for hook in manifest["hooks"]:
        host = hook.get("hosts", {}).get(adapter["gate_source_host"], {})
        if host.get("status") != "ready":
            continue
        for event in host.get("events", []):
            if (
                event.get("event") == adapter["source_event"]
                and event.get("matcher") == adapter["file_source_matcher"]
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
        # Pi's file tools, captured from a live `pi --mode json` session.
        # Pinned so inventing a tool name fails here instead of shipping a
        # gate that silently never matches anything.
        "file_source_matcher": "apply_patch",
        "file_target_matchers": ["write", "edit"],
    }


def test_root_package_exposes_pi_resources_from_the_shared_root() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert "pi-package" in package["keywords"]
    assert package["pi"] == {
        "extensions": ["./plugins/escapement-pi/extensions/index.ts"],
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
        "file_gates": _pi_ready_file_gates(manifest),
    }
    assert inventory["file_gates"], (
        "Pi must ship the file-write gates; an empty list means Pi has no brake "
        "on file growth, which is the gap this inventory key exists to close"
    )
    assert inventory["gates"], "Pi must ship at least one behavioral gate"
    # Every gate named must also be SHIPPED. gates.json names a gate by path and
    # the dispatcher opens it from the plugin root, so a gate listed but not
    # vendored reads as a healthy inventory with the brake missing.
    for key in ("gates", "file_gates"):
        sources = [gate["source"] for gate in inventory[key]]
        assert len(sources) == len(set(sources)), f"Pi {key} must not duplicate gates"
        missing = [s for s in sources if not (PI_ROOT / s).is_file()]
        assert not missing, f"Pi {key} names gates it does not ship: {missing}"


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


EXPECTED_TS_FUNCTIONS = {
    "fail",
    "confinedFile",
    "loadRuntime",
    "parseDispatcherResponse",
    "runDispatcher",
    "surfaceDiagnostics",
    "fileGatePayload",
}


def _assert_thin_pi_extension(source: str) -> None:
    """The Pi extension is a bridge, not a policy engine.

    Asserted as properties rather than as occurrence counts. The previous
    version pinned things like ``run_dispatcher.count("payload") == 2`` and the
    exact text of a for-loop, which made every legitimate refactor look like a
    policy violation while catching nothing the properties below miss.

    What must hold: one dispatcher process per tool event, one tool handler, no
    gate named in TypeScript, no dispatcher path in TypeScript, no decision
    logic in the transport or the diagnostics, no inspection of tool content,
    and no functions beyond the bridge's own.
    """
    run_dispatcher = source.split("function runDispatcher", 1)[1].split(
        "function surfaceDiagnostics", 1
    )[0]
    diagnostics = source.split("function surfaceDiagnostics", 1)[1].split(
        "export default function", 1
    )[0]
    response_parser = source.split("function parseDispatcherResponse", 1)[1].split(
        "function runDispatcher", 1
    )[0]

    assert source.count("spawn(") == 1, "one tool event must start one dispatcher"
    assert source.count('pi.on("tool_call"') == 1
    assert source.count(
        ': `${event.systemPrompt}\\n\\n${runtime.instructions}`,'
    ) == 1

    # The extension must not read what the tool is doing. Every mutation that
    # smuggles policy into TypeScript has to look at the payload to decide.
    for inspector in (".includes(", ".indexOf(", ".match(", ".search(", ".test("):
        assert inspector not in source, (
            f"TypeScript inspects tool content via {inspector}; policy belongs in a gate"
        )

    # The bridge may block for exactly three reasons: a gate said so, its
    # configuration is broken, or the dispatcher failed. A `reason:` that is a
    # TypeScript literal is the extension inventing policy of its own -- which
    # is how every remaining mutant below smuggles it in without touching the
    # transport or adding a helper.
    handler = source.split('pi.on("tool_call"', 1)[1]
    allowed_reasons = {
        'hook.permissionDecisionReason || "Escapement blocked this Bash call"',
        'hook.permissionDecisionReason || "Escapement blocked this file write"',
        '`Escapement Pi configuration error: ${runtime.message}`',
        '`Escapement Pi adapter error: ${error}`',
        '"Escapement received an invalid Pi Bash payload"',
    }
    for match in re.finditer(r"reason: (.+?),?\n", handler):
        reason = match.group(1).strip().rstrip(",").removesuffix("};").strip()
        assert reason in allowed_reasons, (
            f"extension blocks with a reason of its own: {reason}. "
            "Policy belongs in a gate, not in the bridge."
        )

    declared = set(re.findall(r"^\s*function (\w+)", source, re.M))
    assert declared == EXPECTED_TS_FUNCTIONS, (
        f"unexpected TypeScript functions: {declared ^ EXPECTED_TS_FUNCTIONS}. "
        "A new helper here is usually policy that belongs in a gate."
    )

    # Transport and diagnostics carry decisions; they must not make them.
    assert "permissionDecision" not in run_dispatcher
    assert "hookSpecificOutput" not in run_dispatcher
    assert "child.stdin.end(JSON.stringify(payload));" in run_dispatcher
    assert "permissionDecision" not in diagnostics
    assert "block:" not in diagnostics
    assert "throw " not in diagnostics
    assert "pi.sendMessage(" in diagnostics
    assert "JSON.parse(stdout)" in response_parser
    assert "return {" not in response_parser

    # The bridge runs the inventory as given. Selecting among gates -- by
    # filtering the list or by comparing an id -- is the extension deciding
    # which policy applies, which is the manifest's job.
    runtime_loader = source.split("function loadRuntime", 1)[1].split(
        "function parseDispatcherResponse", 1
    )[0]
    for scope, name in ((runtime_loader, "loadRuntime"), (run_dispatcher, "runDispatcher")):
        assert ".filter(" not in scope, (
            f"{name} selects among gates; it must run the inventory as given"
        )
    inventory = json.loads((PI_ROOT / "gates.json").read_text(encoding="utf-8"))
    for gate in [*inventory["gates"], *inventory.get("file_gates", [])]:
        assert gate["id"] not in source, (
            f"extension names {gate['id']}; the bridge must not know a gate by id"
        )

    assert "codex_pretool_dispatch.py" not in source, (
        "the generated inventory, not TypeScript, owns the dispatcher path"
    )
    for gate_specific_policy in (
        "beads_worktree_guard.py",
        "test_oracle_brief_gate.py",
        "implementation_echo_test_gate.py",
        "file_complexity_gate.py",
    ):
        assert gate_specific_policy not in source


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

    parser_mutant = source.replace(
        "  const result = JSON.parse(stdout);",
        "  const result = JSON.parse(stdout);\n"
        "  if (/sudo/.test(stdout)) {\n"
        "    return { hookSpecificOutput: { hookEventName: \"PreToolUse\", "
        'permissionDecision: "deny" } };\n'
        "  }",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(parser_mutant)

    diagnostics_mutant = source.replace(
        "  if (messages.length === 0) return;",
        "  if (/credential/.test(String(messages))) {\n"
        '    throw new Error("TypeScript diagnostics policy");\n'
        "  }\n"
        "  if (messages.length === 0) return;",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(diagnostics_mutant)

    gate_filter_mutant = source.replace(
        "    gates: parsed.gates,",
        "    gates: parsed.gates.filter(\n"
        "      (gate: Gate) => gate.id !== \"merge_authorization_gate\",\n"
        "    ),",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(gate_filter_mutant)

    transport_filter_mutant = source.replace(
        "  for (const gate of gates) {\n"
        "    args.push(\"--gate\", gate.source, \"--gate-timeout\", "
        "String(gate.timeout_seconds));\n"
        "  }",
        "  for (const gate of gates) {\n"
        "    if (gate.id === \"merge_authorization_gate\") continue;\n"
        "    args.push(\"--gate\", gate.source, \"--gate-timeout\", "
        "String(gate.timeout_seconds));\n"
        "  }",
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(transport_filter_mutant)

    prompt_policy_mutant = source.replace(
        ': `${event.systemPrompt}\\n\\n${runtime.instructions}`,',
        ': `${event.systemPrompt}\\n\\n${runtime.instructions}'
        '\\n\\nPi-only policy: never execute sudo`,',
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(prompt_policy_mutant)

    duplicate_handler_mutant = source.replace(
        '  pi.on("before_agent_start",',
        '  pi.on("tool_call", async ({ input }) => {\n'
        '    const value = Object.values(input ?? {})[0];\n'
        '    return value === "sudo"\n'
        '      ? { block: true, reason: "Pi-only policy" }\n'
        '      : undefined;\n'
        '  });\n\n'
        '  pi.on("before_agent_start",',
        1,
    )
    with pytest.raises(AssertionError):
        _assert_thin_pi_extension(duplicate_handler_mutant)


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
extension({
  on(event, handler) {
    const registered = handlers.get(event) ?? [];
    registered.push(handler);
    handlers.set(event, registered);
  },
});
const toolCalls = handlers.get("tool_call") ?? [];
if (toolCalls.length !== 1) {
  throw new Error(`Expected one Pi tool_call handler, got ${toolCalls.length}`);
}
const toolCall = toolCalls[0];
const context = { cwd: process.argv[3] };
const beforeAgentStarts = handlers.get("before_agent_start") ?? [];
if (beforeAgentStarts.length !== 1) {
  throw new Error(
    `Expected one Pi before_agent_start handler, got ${beforeAgentStarts.length}`,
  );
}
const beforeAgentStart = beforeAgentStarts[0];
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
    assert output["injected"]["systemPrompt"] == (
        "base prompt\n\n" + (PI_ROOT / "PI.md").read_text(encoding="utf-8")
    )
    assert process_log.read_text(encoding="utf-8").splitlines() == [
        "dispatch",
        "dispatch",
    ], "each Pi tool call must use exactly one shared dispatcher process"


def test_real_pi_sdk_loads_installed_extension_without_duplicate_skills_and_nonce_gate(
    tmp_path,
) -> None:
    pi = shutil.which("pi")
    assert pi, "Pi CLI is required for the package contract test"
    pi_sdk = Path(pi).resolve().with_name("index.js")
    assert pi_sdk.is_file(), "Pi SDK must sit beside the selected CLI entrypoint"
    package = tmp_path / "escapement-package"
    package.mkdir()
    shutil.copy2(ROOT / "package.json", package / "package.json")
    shutil.copytree(PI_ROOT, package / "plugins" / "escapement-pi")
    native_skill = tmp_path / ".agents" / "skills" / "openspec-explore"
    native_skill.mkdir(parents=True)
    shutil.copy2(
        ROOT / ".agents" / "skills" / "openspec-explore" / "SKILL.md",
        native_skill / "SKILL.md",
    )

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
const skillsResult = session.resourceLoader.getSkills();
console.log(JSON.stringify({
  errors: extensionsResult.errors,
  extensions: extensionsResult.extensions.map((item) => item.resolvedPath),
  packageSkills: skillsResult.skills
    .filter((skill) => skill.sourceInfo.baseDir === packageRoot)
    .map((skill) => skill.name),
  nativeSkills: skillsResult.skills
    .filter((skill) => skill.name === "openspec-explore")
    .map((skill) => ({
      name: skill.name,
      filePath: skill.filePath,
      scope: skill.sourceInfo.scope,
      origin: skill.sourceInfo.origin,
    })),
  skillDiagnostics: skillsResult.diagnostics,
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
    assert result["packageSkills"] == []
    assert result["nativeSkills"] == [
        {
            "name": "openspec-explore",
            "filePath": str(native_skill / "SKILL.md"),
            "scope": "project",
            "origin": "top-level",
        }
    ]
    assert result["skillDiagnostics"] == []
    assert result["safe"] is None
    assert result["denied"] == {"block": True, "reason": f"[deny] {deny_reason}"}
    assert result["nonBash"] is None
    assert deny_nonce not in EXTENSION.read_text(encoding="utf-8")

    pids = pid_log.read_text(encoding="utf-8").splitlines()
    assert len(pids) == 4, "two Bash events must each execute both Python gates"
    assert sorted(pids.count(pid) for pid in set(pids)) == [2, 2], (
        "each Bash event must run both gates inside one dispatcher PID"
    )
