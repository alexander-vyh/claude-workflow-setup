import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PI_ROOT = ROOT / "plugins" / "escapement-pi"


def copy_pi_plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "escapement-pi"
    shutil.copytree(PI_ROOT, plugin)
    return plugin


def run_extension(plugin: Path, command: str, *, env: dict | None = None):
    probe = plugin.parent / "probe.mjs"
    probe.write_text(
        """
const { default: extension } = await import(process.argv[2]);
const handlers = new Map();
const messages = [];
extension({
  on(event, handler) { handlers.set(event, handler); },
  sendMessage(message) { messages.push(message.content); },
});
const result = await handlers.get("tool_call")({
  type: "tool_call", toolCallId: "probe", toolName: "bash",
  input: { command: process.argv[3] },
}, { cwd: process.argv[4] });
console.log(JSON.stringify({ result: result ?? null, messages }));
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            str(probe),
            (plugin / "extensions" / "index.ts").as_uri(),
            command,
            str(ROOT),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **(env or {})},
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def inventory(plugin: Path) -> dict:
    return json.loads((plugin / "gates.json").read_text(encoding="utf-8"))


def write_inventory(plugin: Path, payload: dict) -> None:
    (plugin / "gates.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_inventory_keeps_handler_registered_and_blocks(tmp_path) -> None:
    plugin = copy_pi_plugin(tmp_path)
    (plugin / "gates.json").unlink()

    output = run_extension(plugin, "pwd")

    assert output["result"]["block"] is True
    assert "configuration" in output["result"]["reason"].lower()


def test_installed_pi_keeps_extension_loaded_when_inventory_is_missing(tmp_path) -> None:
    pi = shutil.which("pi")
    assert pi
    pi_sdk = Path(pi).resolve().with_name("index.js")
    package = tmp_path / "package"
    package.mkdir()
    shutil.copy2(ROOT / "package.json", package / "package.json")
    shutil.copytree(ROOT / ".agents", package / ".agents")
    shutil.copytree(PI_ROOT, package / "plugins" / "escapement-pi")
    (package / "plugins" / "escapement-pi" / "gates.json").unlink()
    agent_dir = tmp_path / "agent"
    env = {**os.environ, "PI_CODING_AGENT_DIR": str(agent_dir), "PI_OFFLINE": "1"}
    installed = subprocess.run(
        [pi, "install", str(package), "--approve"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert installed.returncode == 0, installed.stderr
    probe = tmp_path / "installed-missing-inventory.mjs"
    probe.write_text(
        """
const { createAgentSession } = await import(process.argv[2]);
const created = await createAgentSession({
  agentDir: process.argv[3], cwd: process.argv[4], noTools: "all",
});
const blocked = await created.session.extensionRunner.emitToolCall({
  type: "tool_call", toolCallId: "missing", toolName: "bash",
  input: { command: "pwd" },
});
console.log(JSON.stringify({
  errors: created.extensionsResult.errors,
  extensions: created.extensionsResult.extensions.map((item) => item.resolvedPath),
  blocked,
}));
""",
        encoding="utf-8",
    )
    loaded = subprocess.run(
        ["node", str(probe), str(pi_sdk), str(agent_dir), str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert loaded.returncode == 0, loaded.stderr
    result = json.loads(loaded.stdout)
    assert result["errors"] == []
    assert result["extensions"] == [
        str(package / "plugins" / "escapement-pi" / "extensions" / "index.ts")
    ]
    assert result["blocked"]["block"] is True
    assert "configuration" in result["blocked"]["reason"].lower()


@pytest.mark.parametrize("escape", ["absolute", "traversal", "symlink"])
def test_dispatcher_path_escape_blocks_without_execution(tmp_path, escape) -> None:
    plugin = copy_pi_plugin(tmp_path)
    outside = tmp_path / "outside.py"
    marker = tmp_path / "executed"
    outside.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    payload = inventory(plugin)
    if escape == "absolute":
        payload["dispatcher"] = str(outside)
    elif escape == "traversal":
        payload["dispatcher"] = "../outside.py"
    else:
        (plugin / "escape.py").symlink_to(outside)
        payload["dispatcher"] = "escape.py"
    write_inventory(plugin, payload)

    output = run_extension(plugin, "pwd")

    assert output["result"]["block"] is True
    assert "configuration" in output["result"]["reason"].lower()
    assert not marker.exists()


@pytest.mark.parametrize(
    "rendered",
    [
        '{"garbage": true}',
        '{"hookSpecificOutput": []}',
        '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "DENY"}}',
    ],
)
def test_malformed_dispatcher_response_blocks(tmp_path, rendered) -> None:
    plugin = copy_pi_plugin(tmp_path)
    dispatcher = plugin / "malformed.py"
    dispatcher.write_text(f"print({rendered!r})\n", encoding="utf-8")
    payload = inventory(plugin)
    payload["dispatcher"] = "malformed.py"
    write_inventory(plugin, payload)

    output = run_extension(plugin, "pwd")

    assert output["result"]["block"] is True
    assert "invalid" in output["result"]["reason"].lower()


def test_dispatcher_output_is_bounded(tmp_path) -> None:
    plugin = copy_pi_plugin(tmp_path)
    dispatcher = plugin / "flood.py"
    dispatcher.write_text("print('x' * 1048577)\n", encoding="utf-8")
    payload = inventory(plugin)
    payload["dispatcher"] = "flood.py"
    write_inventory(plugin, payload)

    output = run_extension(plugin, "pwd")

    assert output["result"]["block"] is True
    assert "exceeded 1048576 bytes" in output["result"]["reason"]


def test_advisory_and_timeout_are_surfaced_before_later_deny(tmp_path) -> None:
    plugin = copy_pi_plugin(tmp_path)
    gates = plugin / "test-gates"
    gates.mkdir()
    (gates / "advisory.py").write_text(
        "import json\nprint(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'additionalContext': 'pi-advisory'}}))\n",
        encoding="utf-8",
    )
    (gates / "slow.py").write_text(
        "import time\ntime.sleep(1)\nprint('{}')\n", encoding="utf-8"
    )
    (gates / "deny.py").write_text(
        "import json\nprint(json.dumps({'hookSpecificOutput': {"
        "'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', "
        "'permissionDecisionReason': 'pi-denial'}}))\n",
        encoding="utf-8",
    )
    payload = inventory(plugin)
    payload["gates"] = [
        {"id": "advisory", "source": "test-gates/advisory.py", "timeout_seconds": 1},
        {"id": "slow", "source": "test-gates/slow.py", "timeout_seconds": 0.01},
        {"id": "deny", "source": "test-gates/deny.py", "timeout_seconds": 1},
    ]
    write_inventory(plugin, payload)

    output = run_extension(plugin, "nonce-command")

    assert output["result"] == {"block": True, "reason": "[deny] pi-denial"}
    surfaced = "\n".join(output["messages"])
    assert "pi-advisory" in surfaced
    assert "timed out after 0.01s" in surfaced
