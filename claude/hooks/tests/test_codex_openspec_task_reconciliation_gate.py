"""Codex-specific behavioral tests for openspec_task_reconciliation_gate.py.

Loads the hook from the repo path (no ~/.claude/hooks/ dependency) so these run
in Codex environments. The bd-issue and tasks.md filesystem boundaries
(get_issue, extract_spec_id, check_tasks_spec_id) are patched so tests are
hermetic and do not depend on a real beads DB or openspec directory.

The hook fires as PreToolUse on Bash `bd close` commands. It denies closing a
bead that links (via spec_id) to an OpenSpec task whose `tasks.md` checkbox is
not yet `[x]` — enforcing that work is reconciled before close.

Positive control: bd close of a spec-linked bead whose tasks ARE complete -> allow.
Negative control: bd close of a spec-linked bead whose tasks are NOT complete -> deny.
Fast-paths: non-`bd close` commands, unknown issues, and beads without a
spec_id are always allowed.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HOOK_PATH = Path(__file__).resolve().parents[1] / "openspec_task_reconciliation_gate.py"
if not _HOOK_PATH.exists():
    pytest.fail(f"openspec_task_reconciliation_gate.py not found at {_HOOK_PATH}")

# Shared loader: reusing an equivalent registration keeps every test file on one
# module object, so mock.patch("<name>.*") cannot be redirected by import order.
from _hook_module import load_hook

gate = load_hook("openspec_task_reconciliation_gate", _HOOK_PATH)


def _run_main(
    command: str,
    issue: dict | None = None,
    spec_id: str | None = "openspec/changes/auth/specs/auth.md#Login",
    tasks_ok: bool = True,
) -> tuple[int, dict | None]:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    captured = io.StringIO()
    tasks_result = (True, "") if tasks_ok else (False, "tasks.md checkbox is `[ ]`, not `[x]`")
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        with patch.object(gate, "get_issue", return_value=issue):
            with patch.object(gate, "extract_spec_id", return_value=spec_id):
                with patch.object(gate, "check_tasks_spec_id", return_value=tasks_result):
                    with patch("sys.stdout", captured):
                        try:
                            code = gate.main()
                        except SystemExit as exc:
                            code = exc.code or 0
    out = captured.getvalue().strip()
    return code, json.loads(out) if out else None


def assert_denied(code: int, output: dict | None) -> None:
    assert code == 0, "deny is carried by stdout JSON, not exit 2"
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_close_allowed_when_linked_tasks_complete():
    """Positive control: spec-linked bead whose tasks.md is complete -> allow.

    Without this, an implementation that always denies would pass only the
    negative control.
    """
    code, output = _run_main("bd close cake-123", issue={"id": "cake-123"}, tasks_ok=True)

    assert code == 0
    assert output is None, f"close must be allowed when linked tasks are complete; got: {output!r}"


def test_codex_close_denied_when_linked_tasks_incomplete():
    """Negative control: spec-linked bead whose tasks.md is incomplete -> deny.

    This is the gate's core contract: a bead may not close while its referenced
    OpenSpec task is unchecked. An implementation that always allows passes the
    positive control but fails here.
    """
    code, output = _run_main("bd close cake-123", issue={"id": "cake-123"}, tasks_ok=False)

    assert_denied(code, output)
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert "cake-123" in reason, f"denial must name the bead; got: {reason!r}"
    assert "tasks.md" in reason, f"denial must point at the unchecked task; got: {reason!r}"


def test_codex_non_close_command_allowed():
    """Fast-path: a command that is not `bd close` is never gated."""
    code, output = _run_main("bd update cake-123 --claim", issue={"id": "cake-123"}, tasks_ok=False)

    assert code == 0
    assert output is None, "non-`bd close` commands must always be allowed"


def test_codex_close_unknown_issue_allowed():
    """Fast-path: when the close target resolves to no issue, do not gate."""
    code, output = _run_main("bd close cake-999", issue=None, tasks_ok=False)

    assert code == 0
    assert output is None, "an unresolvable close target must be allowed (fail-open)"


def test_codex_close_bead_without_spec_id_allowed():
    """Fast-path: a bead with no spec_id link is not subject to reconciliation."""
    code, output = _run_main("bd close cake-123", issue={"id": "cake-123"}, spec_id=None, tasks_ok=False)

    assert code == 0
    assert output is None, "a bead without a spec_id must be allowed"
