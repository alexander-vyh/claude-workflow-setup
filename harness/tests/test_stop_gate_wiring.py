#!/usr/bin/env python3
"""B1 regression — the SHIPPED plugin hooks.json must WIRE the harness Stop gate,
and the wired gate must actually BLOCK an unverified Stop.

Source / oracle brief: docs/assessments/2026-05-28-critical-assessment.md (B1),
bead escapement-fxh.1.

Business invariant
------------------
A user who installs the escapement plugin gets a LIVE
continuation-harness Stop gate — not merely the stop_hook.py file on disk. The
bug being guarded: the template's Stop block invoked only validate_no_shirking.py,
so distributees who merge it (per INSTALL.sh) symlinked the harness code but never
wired the gate. The harness was dead-on-arrival for everyone but the author, whose
hand-edited ~/.claude/settings.json masked the gap.

Fragile implementations these tests REJECT
-------------------------------------------
- "stop_hook.py exists on disk"  -> already TRUE via symlink; passes despite the bug.
- swapping validate_no_shirking for stop_hook -> must be ADDITIVE (both run); the two
  gates are complementary per claude/rules/continuation-harness.md.
- "INSTALL.sh mentions stop_hook" in a comment -> wiring must be in the template's
  parsed Stop command list, not prose.

Run: python3 -m pytest harness/tests/test_stop_gate_wiring.py -q
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
HARNESS_BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(HARNESS_BIN))

from would_block_stop import would_block_stop  # noqa: E402

# The Claude PLUGIN is the sole owner of hook registration (escapement-ptzz).
# The "shipped surface" is now the plugin's hooks.json, not settings.template.json.
TEMPLATE = REPO / "plugins" / "escapement-claude" / "hooks" / "hooks.json"


def _stop_commands() -> list[str]:
    """All command strings wired under hooks.Stop in the shipped plugin."""
    data = json.loads(TEMPLATE.read_text())
    cmds: list[str] = []
    for group in data.get("hooks", {}).get("Stop", []):
        for hook in group.get("hooks", []):
            cmds.append(hook.get("command", ""))
    return cmds


# --- wiring oracle (the regression) ---------------------------------------

def test_template_wires_harness_stop_hook() -> None:
    cmds = _stop_commands()
    assert any("stop_hook.py" in c for c in cmds), (
        "shipped template Stop block does not invoke harness/bin/stop_hook.py — "
        f"distributees get a dead continuation-harness. Stop commands: {cmds}"
    )




def test_wired_gate_blocks_unverified_stop() -> None:
    """Teeth: a DECLARED contract that isn't verified must still block. (No-contract
    is now 'conversational' → allow; the harness's bite lives on the committed-task
    path — declaring a contract is committing to a verifiable outcome.)"""
    decision, reason = would_block_stop(
        {"contract": {"goal": "x", "verification_command": "pytest"},
         "scheduled": None, "recent_user_message": None}
    )
    assert decision == "block" and reason == "no_completion_or_resumption_proof", (
        f"a declared-but-unverified contract must block Stop; got {decision}/{reason}"
    )


def test_no_contract_is_conversational_allow() -> None:  # the relaxed behavior
    """No contract = no committed task in flight = free to stop (no magic word)."""
    decision, reason = would_block_stop(
        {"contract": None, "scheduled": None, "recent_user_message": None}
    )
    assert decision == "allow" and reason == "conversational", (
        f"a conversational turn (no contract) must allow Stop; got {decision}/{reason}"
    )


def test_wired_gate_allows_when_wakeup_registered() -> None:  # negative control
    future = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)).isoformat()
    decision, _ = would_block_stop(
        {"contract": None, "scheduled": [{"wake_at": future}], "recent_user_message": None}
    )
    assert decision == "allow", (
        "a future wakeup must release the gate — otherwise the block is unconditional"
    )


if __name__ == "__main__":  # allow plain-script execution like test_gate.py
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))


def test_template_wires_the_harness_stop_hook() -> None:
    """The surviving Stop-time invariant.

    Previously this file asserted that validate_no_shirking stayed wired
    alongside stop_hook ("additive, not a swap"). That gate is retired — it
    produced 2,215 denials from 5 regex strings and was routed around 78% of
    the time — so the remaining requirement is simply that the harness stop
    hook is present.
    """
    cmds = _stop_commands()
    assert any("stop_hook.py" in c for c in cmds), (
        f"the continuation-harness Stop hook must remain wired. Stop commands: {cmds}"
    )
