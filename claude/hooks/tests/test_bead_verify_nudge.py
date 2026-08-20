"""Behavioral tests for claude/hooks/bead_verify_nudge.py.

Business invariant: a bead created without a machine oracle silently degrades the
continuation harness to "does it run" — `derive_contract.py` is fail-closed, so no
```verify block means no contract, and completion falls back to a green suite.
Measured 2026-08-20: 475 open beads in a real repo, 278 with acceptance criteria,
0 with a verify block. The convention was documented only in a skill file and was
listed in the injected rules as NOT YET BUILT.

Independent source of truth: `derive_contract.extract_verify_oracle` — the same
function the harness uses. If it can pull a command out, the nudge stays quiet.
This is deliberate: the nudge must never disagree with the consumer.

Invalid solution classes:
  - blocking (this is advisory; a bad match must cost one paragraph, never a block)
  - firing on non-creating bd commands, or on prose that merely mentions bd create
  - accepting a trivial oracle the harness would itself reject
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HOOKS = Path(__file__).resolve().parents[1]
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

_spec = importlib.util.spec_from_file_location(
    "bead_verify_nudge", _HOOKS / "bead_verify_nudge.py")
nudge = importlib.util.module_from_spec(_spec)
sys.modules["bead_verify_nudge"] = nudge
assert _spec.loader is not None
_spec.loader.exec_module(nudge)


def _run(command: str) -> tuple[int, dict]:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    out = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(json.dumps(payload))),
        patch("sys.stdout", out),
        patch.object(nudge, "_record_signal", lambda *a, **k: None),
    ):
        code = nudge.main()
    raw = out.getvalue().strip()
    return code, (json.loads(raw) if raw else {})


def _nudged(command: str) -> bool:
    _, data = _run(command)
    return "additionalContext" in json.dumps(data)


# ---------------------------------------------------------------------------
# Positive control: a creating command with no oracle gets the nudge.
# ---------------------------------------------------------------------------

def test_bd_create_without_acceptance_is_nudged():
    assert _nudged('bd create --title="X" --description="Y" --type=bug')


def test_bd_create_with_acceptance_but_no_verify_block_is_nudged():
    assert _nudged('bd create --title="X" --acceptance="the banner stops lying"')


def test_nudge_is_advisory_never_a_block():
    code, data = _run('bd create --title="X"')
    assert code == 0
    hook_out = data.get("hookSpecificOutput", {})
    assert "permissionDecision" not in hook_out
    assert "permissionDecision" not in data


def test_nudge_names_the_convention_so_it_is_actionable():
    _, data = _run('bd create --title="X"')
    text = json.dumps(data)
    assert "```verify" in text or "verify" in text
    assert "acceptance" in text.lower()


# ---------------------------------------------------------------------------
# Negative controls: silence is required here.
# ---------------------------------------------------------------------------

def test_bd_create_carrying_a_real_verify_block_is_silent():
    cmd = (
        'bd create --title="X" --acceptance="The tab renders plain language.\n\n'
        '```verify\ncd src && npx vitest run outcome/x.test.jsx\n```"'
    )
    assert not _nudged(cmd)


@pytest.mark.parametrize("command", (
    "bd list --status=open",
    "bd show cro-executive-dashboard-1940",
    "bd close some-id",
    "bd update some-id --claim",
    "bd ready",
    "git commit -m 'mention bd create in a message'",
    'echo "run bd create later"',
))
def test_non_creating_or_quoted_commands_are_silent(command):
    assert not _nudged(command)


def test_non_bash_tools_are_ignored():
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
               "tool_input": {"file_path": "/tmp/x", "content": "bd create"}}
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stdout", out):
        assert nudge.main() == 0
    assert out.getvalue().strip() == ""


def test_non_pretooluse_events_are_ignored():
    payload = {"hook_event_name": "Stop", "tool_name": "Bash",
               "tool_input": {"command": "bd create --title=X"}}
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stdout", out):
        assert nudge.main() == 0
    assert out.getvalue().strip() == ""


def test_malformed_input_fails_soft():
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO("not json")), patch("sys.stdout", out):
        assert nudge.main() == 0


# ---------------------------------------------------------------------------
# The nudge must agree with the consumer it is advertising.
# ---------------------------------------------------------------------------

def test_agrees_with_derive_contract_on_what_counts_as_an_oracle():
    """If derive_contract would accept it, the nudge must stay quiet — and vice
    versa. Two components disagreeing about the same convention is worse than
    neither existing."""
    harness = Path(__file__).resolve().parents[3] / "harness" / "bin"
    sys.path.insert(0, str(harness))
    import derive_contract as dc  # noqa: E402

    with_block = ("Some prose.\n\n```verify\npytest -q tests/test_x.py\n```\n")
    without = "Some prose with no fenced oracle at all."

    assert dc.extract_verify_oracle(with_block) is not None
    assert dc.extract_verify_oracle(without) is None

    assert not _nudged(f'bd create --title=X --acceptance="{with_block}"')
    assert _nudged(f'bd create --title=X --acceptance="{without}"')


# ---------------------------------------------------------------------------
# Codex-surface fixtures (named in agent-surfaces/manifest.json).
#
# The Codex adapter delivers the same PreToolUse/Bash contract, and this hook's
# input is the command string plus the bead's own acceptance text — durable
# artifact state, no transcript and no host-specific runtime payload — so it must
# behave identically on both surfaces.
# ---------------------------------------------------------------------------

def _run_codex(command: str) -> dict:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    out = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(json.dumps(payload))),
        patch("sys.stdout", out),
        patch.object(nudge, "_record_signal", lambda *a, **k: None),
    ):
        assert nudge.main() == 0
    raw = out.getvalue().strip()
    return json.loads(raw) if raw else {}


def test_codex_bead_verify_nudge_fires_without_oracle():
    data = _run_codex('bd create --title="X" --type=bug')
    out = data.get("hookSpecificOutput", {})
    assert out.get("hookEventName") == "PreToolUse"
    assert "verify" in out.get("additionalContext", "")
    assert "permissionDecision" not in out


def test_codex_bead_verify_nudge_silent_with_oracle():
    cmd = ('bd create --title="X" --acceptance="observable outcome\n\n'
           '```verify\npytest -q\n```"')
    assert _run_codex(cmd) == {}
