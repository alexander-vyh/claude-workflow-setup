"""Codex oracle for gate_design_nudge.py.

Codex authors gates through `apply_patch`, so a nudge matching only Write/Edit
was absent on the host that writes the most gates. This is advisory — it may
only ever emit `systemMessage` and exit 0 — so the controls here are about
reaching the right edits and, just as importantly, staying silent on the rest.

Payload shape is captured, not invented: see
`fixtures/codex_apply_patch_pretooluse.json`.

Positive control: a patch that adds a hook `.py` nudges.
Negative controls: an ordinary source file, a Bash payload, and an unparseable
patch all stay silent, and nothing this hook emits can ever block an edit.
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

def _load(name: str):
    path = TEST_DIR.parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


dispatch = _load("codex_pretool_dispatch")

HOOK_PATH = TEST_DIR.parent / "gate_design_nudge.py"
_spec = importlib.util.spec_from_file_location("gate_design_nudge", HOOK_PATH)
nudge = importlib.util.module_from_spec(_spec)
sys.modules["gate_design_nudge"] = nudge
assert _spec.loader is not None
_spec.loader.exec_module(nudge)


def run_hook(payload: dict) -> tuple[int, dict | None]:
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stdout", out):
        code = nudge.main()
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None)


def _patch_payload(target: str, cwd: str = "/repo") -> dict:
    captured = FIXTURE["payloads"]["apply_patch_add_file"]
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": captured["tool_name"],
        "tool_input": {
            "command": f"*** Begin Patch\n*** Add File: {target}\n+x = 1\n*** End Patch"
        },
        "cwd": cwd,
    }


def test_codex_patch_to_a_hook_file_nudges():
    code, decision = run_hook(_patch_payload("claude/hooks/my_new_gate.py"))
    assert code == 0
    assert "gate-design" in decision["systemMessage"]
    # The channel that actually reaches a Codex model. Asserting only
    # systemMessage is what let this nudge ship inert.
    assert "gate-design" in decision["hookSpecificOutput"]["additionalContext"]


def test_codex_patch_to_a_gate_named_file_nudges():
    _, decision = run_hook(_patch_payload("tools/review_gate_helper.py"))
    assert decision is not None and "gate-design" in decision["systemMessage"]


def test_codex_patch_to_settings_template_nudges():
    """Gate wiring is gate authoring."""
    _, decision = run_hook(_patch_payload("claude/settings.template.json"))
    assert decision is not None


def test_codex_ordinary_source_patch_stays_silent():
    """Anti-noise control: this fires on gate authoring, not on every edit."""
    assert run_hook(_patch_payload("src/app/handlers.py")) == (0, None)


def test_codex_bash_payload_stays_silent():
    """Bash shares the `command` key — it must not be read as a patch."""
    payload = dict(FIXTURE["payloads"]["bash"], cwd="/repo")
    payload["hook_event_name"] = "PreToolUse"
    assert run_hook(payload) == (0, None)


def test_codex_unparseable_patch_stays_silent():
    for text in ("", "not a patch", "*** Begin Patch\n*** End Patch"):
        payload = _patch_payload("x.py")
        payload["tool_input"]["command"] = text
        assert run_hook(payload) == (0, None), text


def test_nudge_can_never_block_on_codex():
    """The invariant that makes this advisory. It must hold on every path."""
    for target in ("claude/hooks/g.py", "src/app.py", "settings.template.json"):
        code, decision = run_hook(_patch_payload(target))
        assert code == 0
        if decision is None:
            continue
        # The invariant is that nothing here can block. The nudge does use
        # hookSpecificOutput -- that envelope carries `additionalContext`, the
        # only advisory channel Codex passes to the model -- so the check is
        # for a decision field, not for the envelope.
        assert "permissionDecision" not in json.dumps(decision), (
            f"{target}: an advisory hook emitted a decision"
        )
        assert dispatch._aggregate([decision], []).get(
            "hookSpecificOutput", {}
        ).get("permissionDecision") is None
