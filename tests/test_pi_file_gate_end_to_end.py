"""Run the real Pi extension against a real Pi write, and see it block.

Business outcome
----------------
A Pi session that would grow a file past the hard limit is stopped, with the
same reason Claude and Codex give. Everything short of this — the gate's own
unit tests, the mapping tests, the rendered inventory — was green while the Pi
plugin did not actually ship the gate files its inventory named. The brake was
absent and every other signal said it was present.

Independent source of truth
---------------------------
The extension itself, loaded in node the way Pi loads it, invoked through its
registered `tool_call` handler with the payload shape CAPTURED from a live
`pi --mode json` session:

    write -> {"path": ..., "content": ...}
    edit  -> {"path": ..., "edits": [{"oldText": ..., "newText": ...}]}

Nothing here reimplements the bridge's mapping. It runs the TypeScript, which
spawns the dispatcher, which runs the gate.

Invalid solution classes this suite rejects
-------------------------------------------
- gates.json naming a gate the plugin does not ship -> the dispatcher finds
  nothing and the write proceeds
- the flat Claude deny shape, which the extension cannot read
- blocking an ordinary small write
- blocking a write whose payload the bridge cannot parse
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PI_ROOT = ROOT / "plugins" / "escapement-pi"

PROBE = """
const { default: extension } = await import(process.argv[2]);
const handlers = new Map();
const messages = [];
extension({
  on(event, handler) { handlers.set(event, handler); },
  sendMessage(message, options) { messages.push({ content: message.content, options }); },
});
const call = JSON.parse(process.argv[3]);
const result = await handlers.get("tool_call")(
  { type: "tool_call", toolCallId: "probe", toolName: call.toolName, input: call.input },
  { cwd: process.argv[4], signal: new AbortController().signal },
);
console.log(JSON.stringify({ result: result ?? null, messages }));
"""


@pytest.fixture(scope="module")
def plugin(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("pi") / "escapement-pi"
    shutil.copytree(PI_ROOT, target)
    return target


def call_tool(plugin: Path, tool_name: str, tool_input: dict, cwd: Path) -> dict:
    probe = plugin.parent / "probe.mjs"
    probe.write_text(PROBE, encoding="utf-8")
    completed = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            str(probe),
            (plugin / "extensions" / "index.ts").as_uri(),
            json.dumps({"toolName": tool_name, "input": tool_input}),
            str(cwd),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=dict(os.environ),
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


# --- the brake actually exists --------------------------------------------

def test_pi_write_over_the_hard_limit_is_blocked(plugin, tmp_path):
    out = call_tool(
        plugin,
        "write",
        {"path": str(tmp_path / "huge.py"), "content": "\n".join(f"x{i}" for i in range(1200))},
        tmp_path,
    )
    assert out["result"] is not None, "the write was allowed; Pi has no brake"
    assert out["result"]["block"] is True
    reason = out["result"]["reason"]
    assert "huge.py" in reason and "1000" in reason, reason
    assert "file-complexity-waiver" in reason, "a block must carry its escape"


def test_pi_edit_over_the_hard_limit_is_blocked(plugin, tmp_path):
    target = tmp_path / "grown.py"
    target.write_text("".join(f"line {i}\n" for i in range(1100)))
    out = call_tool(
        plugin,
        "edit",
        {"path": str(target), "edits": [{"oldText": "line 0", "newText": "line 0\nadded"}]},
        tmp_path,
    )
    assert out["result"] is not None and out["result"]["block"] is True


# --- and does not fire on ordinary work -----------------------------------

def test_pi_small_write_is_not_blocked(plugin, tmp_path):
    out = call_tool(
        plugin,
        "write",
        {"path": str(tmp_path / "small.py"), "content": "print(1)\n"},
        tmp_path,
    )
    assert out["result"] is None, out["result"]


def test_pi_unparseable_write_fails_open(plugin, tmp_path):
    """A payload the bridge cannot read is not evidence of a violation."""
    out = call_tool(plugin, "write", {"path": str(tmp_path / "x.py")}, tmp_path)
    assert out["result"] is None

    out = call_tool(plugin, "edit", {"path": str(tmp_path / "x.py"), "edits": []}, tmp_path)
    assert out["result"] is None


def test_pi_bash_still_reaches_its_own_gates(plugin, tmp_path):
    """Negative control: adding the file path must not disturb the bash path."""
    out = call_tool(plugin, "bash", {"command": "pwd"}, ROOT)
    assert out["result"] is None or out["result"].get("block") is not True
