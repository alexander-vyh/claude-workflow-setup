"""Oracle for the file-complexity gate (two-tier: 500 soft / 1000 hard).

Business invariant
------------------
A Write/Edit to a NON-exempt file is graded by projected line count:
  • <= 500           -> PASS, silent (exit 0, no stdout)
  • 501 .. 1000      -> SOFT guidance (exit 0, stdout ``systemMessage``) — allowed
  • > 1000           -> HARD block (exit 2, ``permissionDecision: deny``) — unless waived
A substantive waiver (``# file-complexity-waiver: <reason>`` in the first 5 lines, or
``FILE_COMPLEXITY_WAIVER`` env) suppresses BOTH tiers. Exempt paths/suffixes always
pass. The gate fails OPEN on malformed input or unreadable targets — never crashes a
session.

Independent source of truth
---------------------------
The 500/1000 thresholds and the waiver convention — asserted via the externally
observable hook contract (exit code + stdout decision), NOT by importing the
constants and recomputing them (that would be an implementation echo).

Fragile implementations this suite rejects
-------------------------------------------
- Fail-open / always-allow over the hard limit -> ``test_write_over_hard_limit_denies``
- "Just raise the limit to 1000" (kills the soft tier) -> ``test_six_hundred_is_soft_not_silent``
- "Everything over 500 is just a nudge now" (kills the hard block)
      -> ``test_twelve_hundred_is_hard_not_soft``
- Over-blocking at the soft boundary -> ``test_write_exactly_at_soft_limit_allows``
- Presence-only waiver (empty reason bypasses) -> ``test_empty_waiver_reason_still_denies``
- Ignoring exemptions -> ``test_exempt_suffix_allows`` / ``test_new_data_exemptions``
- Message the reader cannot act on -> ``test_soft_message_is_actionable``
- Message so long nobody reads it -> ``test_messages_stay_short_enough_to_read``
- Codex apply_patch writes ungated -> ``test_codex_file_complexity_gate.py``
"""

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

TEST_DIR = Path(__file__).resolve().parent
HOOK_PATH = TEST_DIR / "file_complexity_gate.py"
if not HOOK_PATH.exists():
    HOOK_PATH = TEST_DIR.parent / "file_complexity_gate.py"
spec = importlib.util.spec_from_file_location("file_complexity_gate", HOOK_PATH)
gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["file_complexity_gate"] = gate
spec.loader.exec_module(gate)


def run_hook(payload: dict) -> tuple[int, dict | None]:
    """Invoke the gate with a hook-protocol payload; return (exit_code, decision|None)."""
    out = io.StringIO()
    # Keep hermetic: the gate's best-effort signal emit must not touch the real corpus.
    with patch.object(gate, "_emit_signal", lambda *a, **k: None), \
            patch("sys.stdin", io.StringIO(json.dumps(payload))), \
            patch("sys.stdout", out):
        code = gate.main()
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None)


def _write_payload(file_path: str, n_lines: int, *, first_line: str | None = None) -> dict:
    lines = [f"line {i}" for i in range(n_lines)]
    if first_line is not None and lines:
        lines[0] = first_line
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": file_path, "content": "\n".join(lines)},
    }


def _is_soft(code: int, decision: dict | None) -> bool:
    return code == 0 and decision is not None and "systemMessage" in decision


def _is_hard(code: int, decision: dict | None) -> bool:
    return code == 2 and decision is not None and decision.get("permissionDecision") == "deny"


def _is_silent_pass(code: int, decision: dict | None) -> bool:
    return code == 0 and decision is None


# --- Tier boundaries ------------------------------------------------------

def test_write_well_under_limit_allows():
    code, decision = run_hook(_write_payload("/repo/src/small.py", 42))
    assert _is_silent_pass(code, decision)


def test_write_exactly_at_soft_limit_allows():
    # 500 is the boundary — silent pass (soft fires only when exceeded).
    code, decision = run_hook(_write_payload("/repo/src/ok.py", 500))
    assert _is_silent_pass(code, decision)


def test_just_over_soft_limit_nudges():
    code, decision = run_hook(_write_payload("/repo/src/big.py", 501))
    assert _is_soft(code, decision)


def test_at_hard_limit_is_still_soft():
    code, decision = run_hook(_write_payload("/repo/src/big.py", 1000))
    assert _is_soft(code, decision)


def test_write_over_hard_limit_denies():
    # Negative control: the gate must still BLOCK genuinely runaway files.
    code, decision = run_hook(_write_payload("/repo/src/huge.py", 1001))
    assert _is_hard(code, decision)


# --- The two fragile-implementation killers (wire level) ------------------

def test_six_hundred_is_soft_not_silent():
    # Kills "raise the limit to 1000" (would be a silent pass) AND
    # "keep the single 500 deny" (would be a hard block).
    code, decision = run_hook(_write_payload("/repo/src/six.py", 600))
    assert _is_soft(code, decision)
    assert not _is_silent_pass(code, decision)
    assert not _is_hard(code, decision)


def test_twelve_hundred_is_hard_not_soft():
    code, decision = run_hook(_write_payload("/repo/src/twelve.py", 1200))
    assert _is_hard(code, decision)
    assert not _is_soft(code, decision)


# --- Waiver suppresses BOTH tiers -----------------------------------------

def test_waiver_overrides_hard_block():
    code, decision = run_hook(
        _write_payload("/repo/src/big.py", 1500,
                       first_line="# file-complexity-waiver: cohesive renderer, split tracked in bead xyz")
    )
    assert _is_silent_pass(code, decision)


def test_waiver_suppresses_soft_nudge():
    # An acknowledged file in the soft band is not nagged.
    code, decision = run_hook(
        _write_payload("/repo/src/big.py", 700,
                       first_line="# file-complexity-waiver: deliberate, splitting adds indirection")
    )
    assert _is_silent_pass(code, decision)


def test_empty_waiver_reason_still_denies():
    # value-not-presence: a bare marker must NOT bypass the hard block.
    code, decision = run_hook(
        _write_payload("/repo/src/big.py", 1200, first_line="# file-complexity-waiver:")
    )
    assert _is_hard(code, decision)


def test_env_waiver_allows(monkeypatch):
    monkeypatch.setenv("FILE_COMPLEXITY_WAIVER", "deliberate large generated map")
    code, decision = run_hook(_write_payload("/repo/src/big.py", 1200))
    assert _is_silent_pass(code, decision)


# --- Exemptions: existing + newly added generated/data filetypes ----------

def test_exempt_suffix_allows():
    # .md is exempt even far over the hard limit.
    code, decision = run_hook(_write_payload("/repo/docs/huge.md", 1500))
    assert _is_silent_pass(code, decision)


def test_exempt_path_fragment_allows():
    code, decision = run_hook(_write_payload("/repo/node_modules/pkg/big.py", 1500))
    assert _is_silent_pass(code, decision)


def test_new_data_exemptions():
    for path in ("/repo/manifest.json", "/repo/conf.yaml", "/repo/conf.yml",
                 "/repo/data.csv", "/repo/icon.svg", "/repo/analysis.ipynb"):
        code, decision = run_hook(_write_payload(path, 2000))
        assert _is_silent_pass(code, decision), path


def test_new_generated_code_exemptions():
    for path in ("/repo/svc_pb2_grpc.py", "/repo/types.gen.go",
                 "/repo/model.g.dart", "/repo/model.freezed.dart"):
        code, decision = run_hook(_write_payload(path, 2000))
        assert _is_silent_pass(code, decision), path


def test_ordinary_source_over_hard_is_not_exempt():
    # Positive control: real code is still subject to the hard block.
    code, decision = run_hook(_write_payload("/repo/src/handler.py", 2000))
    assert _is_hard(code, decision)


# --- Message framing: enabling, not explanatory ---------------------------
#
# These assertions used to pin the message's vocabulary — "proxy", "duplicat",
# "24"/"100" — which forced a ~17-line essay at every firing and made it
# grow whenever someone added a rationale. What actually makes a gate
# enabling (per claude/rules/gate-design.md) is that the reader can act: which
# file, how big, which limit, what to do, how to override. Assert that instead,
# and let the wording stay short.

def _is_actionable(text: str, projected: int, limit: int) -> None:
    lower = text.lower()
    assert "big.py" in lower, "names the file"
    assert str(projected) in lower, "states the measured size"
    assert str(limit) in lower, "states the limit crossed"
    assert "sibling module" in lower or "extract" in lower, "names the repair"
    assert "file-complexity-waiver" in lower, "names the escape"


def test_soft_message_is_actionable():
    _, decision = run_hook(_write_payload("/repo/src/big.py", 700))
    _is_actionable(decision["systemMessage"], 700, 1000)


def test_hard_denial_is_actionable():
    _, decision = run_hook(_write_payload("/repo/src/big.py", 1300))
    _is_actionable(decision["denyReason"], 1300, 1000)


def test_messages_stay_short_enough_to_read():
    """A gate nobody reads is not enabling. Cap both tiers at six lines."""
    _, soft = run_hook(_write_payload("/repo/src/big.py", 700))
    _, hard = run_hook(_write_payload("/repo/src/big.py", 1300))
    assert len(soft["systemMessage"].splitlines()) <= 6
    assert len(hard["denyReason"].splitlines()) <= 6


# --- Non-target tools and malformed input fail open -----------------------

def test_non_write_edit_tool_allows():
    code, _ = run_hook({"hook_event_name": "PreToolUse", "tool_name": "Read",
                        "tool_input": {"file_path": "/repo/src/big.py"}})
    assert code == 0


def test_malformed_stdin_fails_open():
    out = io.StringIO()
    with patch.object(gate, "_emit_signal", lambda *a, **k: None), \
            patch("sys.stdin", io.StringIO("not json")), patch("sys.stdout", out):
        assert gate.main() == 0


# --- Edit path: delta projection across both tiers ------------------------

def test_edit_growing_existing_file_over_hard_denies(tmp_path):
    f = tmp_path / "grow.py"
    f.write_text("\n".join(f"line {i}" for i in range(1000)))  # at hard limit
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(f),
            "old_string": "line 0",
            "new_string": "line 0\n" + "\n".join("added" for _ in range(5)),  # +5 -> 1005
        },
    }
    code, decision = run_hook(payload)
    assert _is_hard(code, decision)


def test_edit_growing_into_soft_band_nudges(tmp_path):
    f = tmp_path / "grow.py"
    f.write_text("\n".join(f"line {i}" for i in range(600)))  # already soft band
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(f),
            "old_string": "line 0",
            "new_string": "line 0\nadded",  # +1 -> 601, still soft
        },
    }
    code, decision = run_hook(payload)
    assert _is_soft(code, decision)


def test_edit_missing_file_fails_open(tmp_path):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(tmp_path / "nope.py"),
                       "old_string": "a", "new_string": "b"},
    }
    code, _ = run_hook(payload)
    assert code == 0
