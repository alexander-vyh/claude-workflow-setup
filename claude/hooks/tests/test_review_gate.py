"""Unit tests for review_gate.py — the soft review gate hook.

Coverage:
  - _is_review_agent: subagent_type allowlist, word-boundary match, false-positive rejection
  - _is_close_command: bd close / bd update --status closed detection
  - main(): end-to-end Agent tracking + Bash close-gate warning

Run from anywhere with:
  python -m pytest ~/.claude/hooks/tests/test_review_gate.py -v
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_hooks_dir = Path.home() / ".claude" / "hooks"
if not _hooks_dir.exists():
    pytest.skip("~/.claude/hooks/ not found", allow_module_level=True)

if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path):
    """Patch review_gate._STATE_DIR to a unique temp dir per test."""
    import review_gate
    with patch.object(review_gate, "_STATE_DIR", tmp_path / "state"):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_agent_hook(tool_input: dict, session_id: str = "test-session") -> tuple[int, str]:
    """Run review_gate.main() for an Agent call. Returns (exit_code, stdout)."""
    import review_gate
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": tool_input,
        "session_id": session_id,
    }
    stdin_data = json.dumps(payload)
    captured_out = io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", captured_out):
        code = review_gate.main()
    return code, captured_out.getvalue()


def _run_bash_hook(command: str, session_id: str = "test-session") -> tuple[int, str]:
    """Run review_gate.main() for a Bash call. Returns (exit_code, stdout)."""
    import review_gate
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": session_id,
    }
    stdin_data = json.dumps(payload)
    captured_out = io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", captured_out):
        code = review_gate.main()
    return code, captured_out.getvalue()


# ===========================================================================
# _is_review_agent — subagent_type allowlist (PRIMARY FIX)
# ===========================================================================

class TestIsReviewAgentSubagentType:
    """Reviewer subagent_type values should count even when prompt is blinded."""

    def test_adversarial_reviewer_subagent_type_with_blinded_prompt(self):
        """adversarial-reviewer dispatch with a neutral prompt still counts."""
        from review_gate import _is_review_agent
        assert _is_review_agent({
            "name": "critic",
            "subagent_type": "adversarial-reviewer",
            "description": "Check migration 0042",
            "prompt": "Attack db/migrations/0042.sql for locking issues.",
        })

    def test_code_reviewer_subagent_type_is_review(self):
        from review_gate import _is_review_agent
        assert _is_review_agent({
            "name": "checker",
            "subagent_type": "code-reviewer",
            "description": "Inspect the implementation",
            "prompt": "Examine PR #123 for defects.",
        })

    def test_superpowers_code_reviewer_subagent_type_no_longer_allowlisted(self):
        # superpowers:code-reviewer was removed upstream (v5.1.0) and de-listed in
        # the superpowers disconnect (epic e3o). A blinded prompt (no review words)
        # with that subagent_type must NOT satisfy the gate — regression guard that
        # the entry stays gone.
        from review_gate import _is_review_agent
        assert not _is_review_agent({
            "name": "checker",
            "subagent_type": "superpowers:code-reviewer",
            "description": "Inspect the implementation",
            "prompt": "Examine the changes.",
        })

    def test_test_quality_reviewer_subagent_type_is_review(self):
        from review_gate import _is_review_agent
        assert _is_review_agent({
            "name": "checker",
            "subagent_type": "test-quality-reviewer",
            "description": "Check test quality",
            "prompt": "Look at the tests for weak assertions.",
        })


# ===========================================================================
# _is_review_agent — word-boundary matching (precision fix)
# ===========================================================================

class TestIsReviewAgentWordBoundary:
    """Word-boundary regex matches review/reviewer/reviews/reviewed/reviewing."""

    def test_name_contains_reviewer_word(self):
        from review_gate import _is_review_agent
        assert _is_review_agent({"name": "security-reviewer", "description": "", "prompt": ""})

    def test_description_contains_review_word(self):
        from review_gate import _is_review_agent
        assert _is_review_agent({
            "name": "x",
            "description": "Review the auth middleware",
            "prompt": "",
        })

    def test_description_contains_reviewing_word(self):
        from review_gate import _is_review_agent
        assert _is_review_agent({
            "name": "x",
            "description": "Code reviewing session",
            "prompt": "",
        })

    def test_prompt_contains_review_word_backward_compat(self):
        """Backward compat: a review word in the prompt still counts."""
        from review_gate import _is_review_agent
        assert _is_review_agent({
            "name": "x",
            "description": "y",
            "prompt": "Please review and report findings.",
        })

    def test_description_contains_reviewed_word(self):
        from review_gate import _is_review_agent
        assert _is_review_agent({
            "name": "x",
            "description": "Has this been reviewed yet",
            "prompt": "",
        })


# ===========================================================================
# _is_review_agent — false-positive rejection
# ===========================================================================

class TestIsReviewAgentFalsePositives:
    """Known false-positive substrings must NOT count as review."""

    def test_reviewable_is_not_review(self):
        from review_gate import _is_review_agent
        assert not _is_review_agent({
            "name": "x",
            "description": "Reviewable patterns check",
            "prompt": "",
        })

    def test_previewer_is_not_review(self):
        from review_gate import _is_review_agent
        assert not _is_review_agent({
            "name": "previewer",
            "description": "",
            "prompt": "",
        })

    def test_preview_in_prompt_is_not_review(self):
        from review_gate import _is_review_agent
        assert not _is_review_agent({
            "name": "x",
            "description": "y",
            "prompt": "Preview the dashboard before launch.",
        })


# ===========================================================================
# _is_review_agent — negative cases
# ===========================================================================

class TestIsReviewAgentNegative:
    """Non-review dispatches."""

    def test_empty_input(self):
        from review_gate import _is_review_agent
        assert not _is_review_agent({})

    def test_general_purpose_subagent_type_with_neutral_fields(self):
        from review_gate import _is_review_agent
        assert not _is_review_agent({
            "name": "worker",
            "subagent_type": "general-purpose",
            "description": "Implement the feature",
            "prompt": "Build the endpoint per spec.",
        })

    def test_opportunity_finder_subagent_type_is_not_review(self):
        """subagent_type not in allowlist and no review word → not a review."""
        from review_gate import _is_review_agent
        assert not _is_review_agent({
            "name": "finder",
            "subagent_type": "opportunity-finder",
            "description": "Identify new dashboard ideas",
            "prompt": "What metrics could we build?",
        })


# ===========================================================================
# _is_close_command — regex correctness
# ===========================================================================

class TestIsCloseCommand:
    """bd close / bd update --status closed detection."""

    def test_bd_close_matches(self):
        from review_gate import _is_close_command
        assert _is_close_command("bd close bd-123")

    def test_bd_close_with_flags_matches(self):
        from review_gate import _is_close_command
        assert _is_close_command("bd close bd-123 --reason done")

    def test_bd_update_status_closed_space_matches(self):
        from review_gate import _is_close_command
        assert _is_close_command("bd update bd-123 --status closed")

    def test_bd_update_status_closed_equals_matches(self):
        from review_gate import _is_close_command
        assert _is_close_command("bd update bd-123 --status=closed")

    def test_bd_list_does_not_match(self):
        from review_gate import _is_close_command
        assert not _is_close_command("bd list")

    def test_ls_does_not_match(self):
        from review_gate import _is_close_command
        assert not _is_close_command("ls -la")


# ===========================================================================
# main() — end-to-end gate behavior
# ===========================================================================

class TestMainEndToEnd:
    """Integration tests through main()."""

    def test_review_dispatch_then_close_emits_no_warning(self):
        """adversarial-reviewer via subagent_type, then bd close → silent allow."""
        code1, _ = _run_agent_hook({
            "name": "critic",
            "subagent_type": "adversarial-reviewer",
            "description": "Attack migration",
            "prompt": "Check 0042.sql",
        }, session_id="sess-1")
        assert code1 == 0

        code2, out2 = _run_bash_hook("bd close bd-123 --reason done", session_id="sess-1")
        assert code2 == 0
        assert out2 == ""

    def test_no_review_then_close_emits_warning(self):
        """bd close with no prior review dispatch → soft warning on stdout."""
        code, out = _run_bash_hook("bd close bd-123", session_id="sess-nowarn")
        assert code == 0
        assert out != ""
        data = json.loads(out)
        assert "review" in data.get("systemMessage", "").lower()

    def test_non_reviewer_agent_then_close_still_warns(self):
        """Dispatching a non-reviewer agent does not satisfy the gate."""
        _run_agent_hook({
            "name": "implementer",
            "subagent_type": "general-purpose",
            "description": "Build endpoint",
            "prompt": "Implement the feature.",
        }, session_id="sess-nonrev")

        code, out = _run_bash_hook("bd close bd-456", session_id="sess-nonrev")
        assert code == 0
        assert out != ""

    def test_non_close_bash_is_silent(self):
        """Non-bd-close bash commands are passed through silently."""
        code, out = _run_bash_hook("ls -la", session_id="sess-x")
        assert code == 0
        assert out == ""

    def test_bd_update_status_closed_also_gated(self):
        """bd update --status closed triggers the gate like bd close."""
        code, out = _run_bash_hook("bd update bd-789 --status closed", session_id="sess-update")
        assert code == 0
        assert out != ""

    def test_reviewer_via_name_word_then_close_no_warning(self):
        """Agent with 'reviewer' in its name still satisfies the gate."""
        _run_agent_hook({
            "name": "security-reviewer",
            "description": "Check auth",
            "prompt": "Attack the middleware.",
        }, session_id="sess-name")

        code, out = _run_bash_hook("bd close bd-987", session_id="sess-name")
        assert code == 0
        assert out == ""


# ===========================================================================
# _read_state — must not raise KeyError on conforming-but-shapeless JSON
# ===========================================================================

class TestReadStateKeyError:
    def test_conforming_dict_without_reviews_key(self, tmp_path):
        """_read_state must not raise KeyError on dicts without 'reviews' key."""
        import review_gate as rg
        state_file = tmp_path / "session.json"
        state_file.write_text('{"foo": 1}')
        # Patch _STATE_DIR to point at tmp_path
        with patch("review_gate._STATE_DIR", tmp_path):
            result = rg._read_state("session")
        assert result == []

    def test_json_array_returns_empty(self, tmp_path):
        import review_gate as rg
        state_file = tmp_path / "session.json"
        state_file.write_text("[]")
        with patch("review_gate._STATE_DIR", tmp_path):
            result = rg._read_state("session")
        assert result == []

    def test_null_json_returns_empty(self, tmp_path):
        import review_gate as rg
        state_file = tmp_path / "session.json"
        state_file.write_text("null")
        with patch("review_gate._STATE_DIR", tmp_path):
            result = rg._read_state("session")
        assert result == []


# ===========================================================================
# Ask-decision on no-review-on-close
# ===========================================================================

class TestAskDecisionOnNoReview:
    """The no-review-on-close path should emit permissionDecision: ask."""

    def _run_close_no_review(self, session_id="test-no-review") -> dict:
        import review_gate as rg
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "bd close foo"},
            "session_id": session_id,
        }
        out = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(json.dumps(payload))),
            patch("sys.stdout", out),
            patch("review_gate._STATE_DIR", Path("/tmp/review-gate-test-no-review")),
        ):
            rg.main()
        text = out.getvalue().strip()
        return json.loads(text) if text else {}

    def test_emits_ask_decision(self):
        output = self._run_close_no_review()
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") == "ask"

    def test_emits_system_message_for_user(self):
        output = self._run_close_no_review()
        assert "systemMessage" in output

    def test_emits_additional_context_for_assistant(self):
        output = self._run_close_no_review()
        ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "review" in ctx.lower()

    def test_reason_mentions_remedy(self):
        output = self._run_close_no_review()
        reason = output.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "adversarial-reviewer" in reason or "code-reviewer" in reason

    def test_decision_honored_exactly_once(self):
        """CANONICAL DECISION CONTRACT: the gate signals its decision with a
        single mechanism — one permissionDecision JSON document on stdout AND
        exit 0 (NOT exit 2). A JSON decision *plus* a non-zero exit is a
        contradictory double-signal; asserting exit 0 rejects that shape, and
        ``json.loads`` raises on two stacked documents, rejecting a doubled
        signal. This is the regression guard for fxh.7.
        """
        code, out = _run_bash_hook("bd close bd-once", session_id="sess-once")
        assert code == 0, (
            "decision is carried by the stdout JSON, not exit 2 — "
            "a permissionDecision JSON plus a non-zero exit is a double-signal"
        )
        stripped = out.strip()
        # exactly one JSON document: json.loads raises on two stacked documents
        data = json.loads(stripped)
        assert data["hookSpecificOutput"]["permissionDecision"] == "ask"


# ===========================================================================
# bd close regex — word-boundary anchors prevent false matches
# ===========================================================================

class TestBdCloseRegexHardening:
    """Word-boundary anchors prevent false matches on mybd/subd."""

    def test_mybd_close_not_matched(self):
        import review_gate as rg
        assert not rg._BD_CLOSE_RE.search("mybd close foo")

    def test_subd_close_not_matched(self):
        import review_gate as rg
        assert not rg._BD_CLOSE_RE.search("subd close foo")

    def test_bd_close_matched(self):
        import review_gate as rg
        assert rg._BD_CLOSE_RE.search("bd close foo")

    def test_bd_close_after_semicolon(self):
        import review_gate as rg
        assert rg._BD_CLOSE_RE.search("git commit -m 'done'; bd close foo")
