"""Codex-specific oracle for file_complexity_gate.py.

Codex writes files through `apply_patch`, not Write/Edit, so the gate was
Claude-only and Codex sessions had no brake on file growth at all.

The payload shape here is CAPTURED, not invented — see
`fixtures/codex_apply_patch_pretooluse.json` for provenance. That matters: the
patch text arrives as `tool_input["command"]`, and an earlier gate in this repo
guessed `tool_input["input"]`, which its own hand-written fixture happily
agreed with. Tests that assert a shape their author made up cannot fail.

Positive control: a patch that would push a real file past the hard limit is
denied through the same decision path Claude uses.
Negative control: a small patch, an exempt path, and an unparseable payload all
pass — the gate fails open on anything it cannot read.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

TEST_DIR = Path(__file__).resolve().parent
FIXTURE = json.loads(
    (TEST_DIR / "fixtures" / "codex_apply_patch_pretooluse.json").read_text()
)

# Shared loader: reusing an equivalent registration keeps every test file on one
# module object, so mock.patch("<name>.*") cannot be redirected by import order.
from _hook_module import load_hook


def _load(name: str):
    return load_hook(name, TEST_DIR.parent / f"{name}.py")


# The independent reader of Codex's PreToolUse contract. It was written against
# Codex and is what escapement already ships to aggregate gates there, so
# routing this gate's output through it checks the wire shape against something
# this test did not author.
dispatch = _load("codex_pretool_dispatch")


HOOK_PATH = TEST_DIR.parent / "file_complexity_gate.py"
gate = load_hook("file_complexity_gate", HOOK_PATH)


def run_hook(payload: dict) -> tuple[int, dict | None]:
    out = io.StringIO()
    with patch.object(gate, "_emit_signal", lambda *a, **k: None), \
            patch("sys.stdin", io.StringIO(json.dumps(payload))), \
            patch("sys.stdout", out):
        code = gate.main()
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None)


def assert_codex_denies(code: int, decision: dict | None, *, naming: str) -> None:
    """Codex blocks on a stdout envelope and status 0 -- never on status 2.

    Status 2 is how Claude Code blocks; Codex treats a non-zero gate as failed
    and drops its verdict. A live Codex session appended to a 1050-line file
    while this hook fired and "denied" in the Claude shape, which is why the
    check below goes through the dispatcher rather than reading the keys this
    module happens to emit.
    """
    assert code == 0, "Codex discards a gate that exits non-zero"
    assert decision is not None
    aggregated = dispatch._aggregate([decision], [])
    hook_output = aggregated.get("hookSpecificOutput", {})
    assert hook_output.get("permissionDecision") == "deny", (
        f"dispatcher did not read a deny out of {decision!r}"
    )
    assert naming in hook_output.get("permissionDecisionReason", "")


def _patch_payload(patch_text: str, cwd: str) -> dict:
    """A PreToolUse payload with the captured apply_patch shape."""
    captured = FIXTURE["payloads"]["apply_patch_add_file"]
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": captured["tool_name"],
        "tool_input": {"command": patch_text},
        "cwd": cwd,
    }


def test_codex_captured_payload_uses_command_not_input():
    """Guards the exact mistake that made a previous gate silently dead."""
    captured = FIXTURE["payloads"]["apply_patch_add_file"]
    assert captured["tool_name"] == "apply_patch"
    assert "command" in captured["tool_input"]
    assert "input" not in captured["tool_input"]
    assert captured["tool_input"]["command"].startswith("*** Begin Patch")


def test_codex_add_file_over_hard_limit_is_denied(tmp_path):
    body = "".join(f"+line {i}\n" for i in range(1200))
    text = f"*** Begin Patch\n*** Add File: huge.py\n{body}*** End Patch"
    code, decision = run_hook(_patch_payload(text, str(tmp_path)))
    assert_codex_denies(code, decision, naming="huge.py")


def test_codex_add_file_in_soft_band_nudges(tmp_path):
    body = "".join(f"+line {i}\n" for i in range(700))
    text = f"*** Begin Patch\n*** Add File: big.py\n{body}*** End Patch"
    code, decision = run_hook(_patch_payload(text, str(tmp_path)))
    assert code == 0
    assert "systemMessage" in decision


def test_codex_small_add_file_passes_silently(tmp_path):
    """The captured payload verbatim — one line — must not fire."""
    captured = FIXTURE["payloads"]["apply_patch_add_file"]
    payload = dict(captured, cwd=str(tmp_path))
    payload["hook_event_name"] = "PreToolUse"
    assert run_hook(payload) == (0, None)


def test_codex_update_file_projects_against_the_file_on_disk(tmp_path):
    """Update must count the existing file, not just the added lines."""
    target = tmp_path / "grown.py"
    target.write_text("".join(f"line {i}\n" for i in range(1100)))
    text = "*** Begin Patch\n*** Update File: grown.py\n@@\n+one more\n*** End Patch"
    code, decision = run_hook(_patch_payload(text, str(tmp_path)))
    assert_codex_denies(code, decision, naming="grown.py")


def test_codex_update_of_a_small_file_passes(tmp_path):
    target = tmp_path / "small.py"
    target.write_text("".join(f"line {i}\n" for i in range(10)))
    text = "*** Begin Patch\n*** Update File: small.py\n@@\n+one more\n*** End Patch"
    assert run_hook(_patch_payload(text, str(tmp_path))) == (0, None)


def test_codex_exempt_path_passes(tmp_path):
    body = "".join(f"+line {i}\n" for i in range(2000))
    text = f"*** Begin Patch\n*** Add File: node_modules/pkg/huge.py\n{body}*** End Patch"
    assert run_hook(_patch_payload(text, str(tmp_path))) == (0, None)


def test_codex_update_of_missing_file_fails_open(tmp_path):
    text = "*** Begin Patch\n*** Update File: nope.py\n@@\n+x\n*** End Patch"
    assert run_hook(_patch_payload(text, str(tmp_path))) == (0, None)


def test_codex_unparseable_patch_fails_open(tmp_path):
    for text in ("", "not a patch at all", "*** Begin Patch\n*** End Patch"):
        assert run_hook(_patch_payload(text, str(tmp_path))) == (0, None), text


def test_codex_delete_file_is_not_gated(tmp_path):
    """Removing a file cannot push it past a length limit."""
    target = tmp_path / "gone.py"
    target.write_text("".join(f"line {i}\n" for i in range(2000)))
    text = "*** Begin Patch\n*** Delete File: gone.py\n*** End Patch"
    assert run_hook(_patch_payload(text, str(tmp_path))) == (0, None)


def test_codex_bash_payload_is_not_gated(tmp_path):
    """The captured Bash shape shares the `command` key — it must not be parsed."""
    captured = FIXTURE["payloads"]["bash"]
    payload = dict(captured, cwd=str(tmp_path))
    payload["hook_event_name"] = "PreToolUse"
    assert run_hook(payload) == (0, None)
