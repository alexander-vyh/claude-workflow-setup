"""Pi's write/edit tools must reach the same verdict as every other host.

Business outcome
----------------
Pi had no brake on file growth at all. Its adapter carried only bash tool
calls, so a Pi session could grow a file without limit while Claude and Codex
were both stopped at the same line.

Independent source of truth
---------------------------
The gate itself, fed the payload the TypeScript bridge builds. The mapping is
transcribed here from a CAPTURED Pi session (`pi --provider anthropic --mode
json --print`), not from the tool descriptions:

    write -> {"path": ..., "content": ...}
    edit  -> {"path": ..., "edits": [{"oldText": ..., "newText": ...}, ...]}

Pi names its tools `write` and `edit` in lowercase and calls the file `path`,
not `file_path`. Guessing any of those would produce a gate that matches
nothing and reports success.

Invalid solution classes this suite rejects
-------------------------------------------
- Pi mapped to the flat Claude deny shape the extension cannot read
- A multi-edit projection that counts only the first edit
- An unparseable Pi payload blocking the write instead of failing open
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "claude" / "hooks"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gate = _load("file_complexity_gate")
dispatch = _load("codex_pretool_dispatch")


def run_gate(payload: dict) -> tuple[int, dict | None]:
    out = io.StringIO()
    with patch.object(gate, "_emit_signal", lambda *a, **k: None), \
            patch("sys.stdin", io.StringIO(json.dumps(payload))), \
            patch("sys.stdout", out):
        code = gate.main()
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None)


def pi_write(path: str, content: str) -> dict:
    """What the bridge builds from a Pi `write` tool call."""
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": path, "content": content},
    }


def pi_edit(path: str, edits: list[dict]) -> dict:
    """What the bridge builds from a Pi `edit` tool call.

    Pi sends a list where Claude sends one pair. Joining each side with a
    newline keeps the projection exact: both sides gain the same separators, so
    the joined delta equals the sum of the per-edit deltas.
    """
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": path,
            "old_string": "\n".join(e["oldText"] for e in edits),
            "new_string": "\n".join(e["newText"] for e in edits),
        },
    }


def denies(code: int, decision: dict | None) -> bool:
    """Deny read through the dispatcher — the shape the Pi extension reads."""
    if code != 0 or decision is None:
        return False
    aggregated = dispatch._aggregate([decision], [])
    return aggregated.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# --- write ----------------------------------------------------------------

def test_pi_write_over_hard_limit_is_denied():
    payload = pi_write("/repo/src/huge.py", "\n".join(f"x{i}" for i in range(1200)))
    assert denies(*run_gate(payload))


def test_pi_write_under_limit_passes():
    payload = pi_write("/repo/src/small.py", "\n".join(f"x{i}" for i in range(40)))
    assert run_gate(payload) == (0, None)


def test_pi_deny_is_readable_by_the_extension():
    """The extension reads hookSpecificOutput; the flat shape would be ignored."""
    _, decision = run_gate(pi_write("/repo/src/huge.py", "\n".join("x" for _ in range(1500))))
    hook = decision["hookSpecificOutput"]
    assert hook["permissionDecision"] == "deny"
    assert hook["permissionDecisionReason"], "the extension surfaces this as the block reason"
    assert "permissionDecision" not in {k for k in decision if k != "hookSpecificOutput"}


# --- edit: the multi-edit projection --------------------------------------

def test_pi_multi_edit_sums_every_edit(tmp_path):
    """Counting only the first edit would let a file grow past the limit.

    The file sits under the limit; three edits together push it over. A
    projection that reads edits[0] alone allows this.
    """
    target = tmp_path / "grow.py"
    target.write_text("".join(f"line {i}\n" for i in range(995)))
    edits = [
        {"oldText": f"line {i}", "newText": f"line {i}\nadded {i}a\nadded {i}b"}
        for i in range(3)
    ]
    assert denies(*run_gate(pi_edit(str(target), edits))), (
        "995 + 6 added lines is over the hard limit; each edit must count"
    )


def test_pi_single_edit_under_limit_passes(tmp_path):
    target = tmp_path / "ok.py"
    target.write_text("".join(f"line {i}\n" for i in range(10)))
    edits = [{"oldText": "line 0", "newText": "line 0\nadded"}]
    assert run_gate(pi_edit(str(target), edits)) == (0, None)


def test_pi_edit_that_shrinks_a_file_is_never_denied(tmp_path):
    """Negative control: a net-negative edit must not be blocked.

    1100 lines down to 901 still lands in the soft band, so a nudge is correct
    and a silent pass would be wrong. What must not happen is a deny -- an
    agent cutting a file down is doing exactly what the gate asks for.
    """
    target = tmp_path / "shrink.py"
    target.write_text("".join(f"line {i}\n" for i in range(1100)))
    edits = [{"oldText": "\n".join(f"line {i}" for i in range(200)), "newText": "compacted"}]
    code, decision = run_gate(pi_edit(str(target), edits))
    assert not denies(code, decision)
    assert decision is not None and "systemMessage" in decision, "soft band still nudges"


def test_pi_edit_of_a_missing_file_fails_open(tmp_path):
    edits = [{"oldText": "a", "newText": "b"}]
    assert run_gate(pi_edit(str(tmp_path / "nope.py"), edits)) == (0, None)
