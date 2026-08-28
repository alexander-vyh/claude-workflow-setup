"""Shared case builders for the review-gate suites.

`test_review_gate.py` grew past the repo's 1000-line hard limit while the
verdict-capture rules were being built, so those rules moved to
`test_review_gate_verdict.py`. Both files drive the gate the same way — build a
record, run a close, read the decision — so the builders live here rather than
being duplicated or imported across test modules.

Deliberately NOT a conftest fixture set: `conftest.py` is shared by every hook
suite in this directory, and an autouse fixture that redirects the review
ledger would reach tests that have nothing to do with review. Each review-gate
module declares its own one-line autouse fixture instead.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

_HOOKS_DIR = Path(__file__).resolve().parents[1]
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _review_record  # noqa: E402
import review_gate  # noqa: E402

SUBSTANTIVE = (
    "Read the diff against the bead's acceptance criteria. The close path now "
    "binds the review to the bead id, which the previous implementation did "
    "not do; verified the stale-fingerprint branch fires on a follow-up edit. "
    "One concern: the waiver reason is not length-checked against the bead "
    "title, so a paraphrase would pass."
)

FINGERPRINT = "a" * 64


def make_record(bead="escapement-abc1", findings=SUBSTANTIVE,
                fingerprint=FINGERPRINT, independent=True,
                reviewer="adversarial-reviewer", blocking=False,
                verdict_source="captured", verdict_digest="c" * 64):
    return {
        "v": _review_record.RECORD_VERSION,
        "bead": bead,
        "reviewer": reviewer,
        "fingerprint": fingerprint,
        "recorded_at": "2026-08-27T00:00:00+00:00",
        "findings": findings,
        "independent": independent,
        "blocking": blocking,
        "verdict_source": verdict_source,
        "verdict_digest": verdict_digest,
        "host": "cli",
    }


def run_close(command="bd close escapement-abc1", record=None,
              fingerprint=FINGERPRINT, session_id="s1"):
    """Run the gate on a close command. Returns (exit_code, decision|None, signal)."""
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": "/tmp",
    }
    if session_id is not None:
        payload["session_id"] = session_id

    out = io.StringIO()
    with patch.object(review_gate, "read_record", return_value=record), \
         patch.object(review_gate, "work_fingerprint", return_value=fingerprint), \
         patch.object(review_gate, "changed_paths_since", return_value=["a.py", "b.py"]), \
         patch.object(review_gate, "_record_signal") as signal, \
         patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", out):
        code = review_gate.main()

    raw = out.getvalue()
    decision = json.loads(raw)["hookSpecificOutput"] if raw.strip() else None
    return code, decision, signal


def run_dispatch(tool_input, session_id="s1", fingerprint=FINGERPRINT):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": tool_input,
        "cwd": "/tmp",
    }
    if session_id is not None:
        payload["session_id"] = session_id
    with patch.object(review_gate, "work_fingerprint", return_value=fingerprint), \
         patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", io.StringIO()):
        review_gate.main()


def run_event(payload):
    """Feed one raw hook payload through the gate (PostToolUse, SubagentStop)."""
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", io.StringIO()):
        review_gate.main()
