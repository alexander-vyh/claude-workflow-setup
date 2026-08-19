"""Unit tests for ~/.claude/hooks/outcome_assertion_gate.py.

Tests:
- classify_assertion: correctly distinguishes structural vs outcome assertions
- extract_test_assertions: parses Python test code to find assertions
- analyze_test_quality: flags test functions with only structural assertions
- main (PreToolUse): only fires on `gh pr create` Bash commands

Run from anywhere with:
  python -m pytest ~/.claude/hooks/tests/test_outcome_assertion_gate.py -v
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

from outcome_assertion_gate import (  # noqa: E402
    AssertionKind,
    classify_assertion,
    extract_test_functions,
    analyze_test_quality,
)


# ---------------------------------------------------------------------------
# classify_assertion — the core heuristic
# ---------------------------------------------------------------------------


class TestClassifyAssertion:
    """Verify that individual assertion lines are classified correctly."""

    # -- Structural (BAD when alone) --

    def test_is_not_none(self):
        assert classify_assertion("assert result is not None") == AssertionKind.STRUCTURAL

    def test_assertIsNotNone(self):
        assert classify_assertion("self.assertIsNotNone(result)") == AssertionKind.STRUCTURAL

    def test_len_greater_than_zero(self):
        assert classify_assertion("assert len(results) > 0") == AssertionKind.STRUCTURAL

    def test_assertTrue_len(self):
        assert classify_assertion("self.assertTrue(len(results) > 0)") == AssertionKind.STRUCTURAL

    def test_isinstance(self):
        assert classify_assertion("assert isinstance(result, dict)") == AssertionKind.STRUCTURAL

    def test_assertIsInstance(self):
        assert classify_assertion("self.assertIsInstance(result, list)") == AssertionKind.STRUCTURAL

    def test_bare_truthiness(self):
        assert classify_assertion("assert result") == AssertionKind.STRUCTURAL

    def test_key_in_dict(self):
        assert classify_assertion('assert "key" in result') == AssertionKind.STRUCTURAL

    def test_assertIn_key(self):
        assert classify_assertion('self.assertIn("status", response)') == AssertionKind.STRUCTURAL

    def test_callable(self):
        assert classify_assertion("assert callable(handler)") == AssertionKind.STRUCTURAL

    def test_hasattr(self):
        assert classify_assertion('assert hasattr(obj, "method")') == AssertionKind.STRUCTURAL

    def test_not_empty_string(self):
        assert classify_assertion('assert result != ""') == AssertionKind.STRUCTURAL

    def test_count_gt_zero(self):
        assert classify_assertion("assert count > 0") == AssertionKind.STRUCTURAL

    def test_assertGreater_zero(self):
        assert classify_assertion("self.assertGreater(len(data), 0)") == AssertionKind.STRUCTURAL

    # -- Outcome (GOOD) --

    def test_equals_specific_value(self):
        assert classify_assertion("assert result.score == 75") == AssertionKind.OUTCOME

    def test_assertEqual_specific(self):
        assert classify_assertion('self.assertEqual(result.status, "active")') == AssertionKind.OUTCOME

    def test_equals_string_literal(self):
        assert classify_assertion('assert error.message == "Email required"') == AssertionKind.OUTCOME

    def test_approx_value(self):
        assert classify_assertion("assert score == pytest.approx(42.5, abs=1.0)") == AssertionKind.OUTCOME

    def test_specific_numeric_comparison(self):
        assert classify_assertion("assert health_score >= 40") == AssertionKind.OUTCOME

    def test_assertEqual_numeric(self):
        assert classify_assertion("self.assertEqual(len(results), 3)") == AssertionKind.OUTCOME

    def test_assertAlmostEqual(self):
        assert classify_assertion("self.assertAlmostEqual(rate, 0.85, places=2)") == AssertionKind.OUTCOME

    def test_list_equality(self):
        assert classify_assertion('assert sorted(keys) == ["a", "b", "c"]') == AssertionKind.OUTCOME

    def test_dict_equality(self):
        assert classify_assertion('assert result == {"status": "ok", "count": 5}') == AssertionKind.OUTCOME

    def test_greater_than_nonzero(self):
        assert classify_assertion("assert score > 50") == AssertionKind.OUTCOME

    # -- Exception assertions (always OUTCOME — testing behavior) --

    def test_pytest_raises(self):
        assert classify_assertion("with pytest.raises(ValueError):") == AssertionKind.OUTCOME

    def test_assertRaises(self):
        assert classify_assertion("self.assertRaises(KeyError, func, arg)") == AssertionKind.OUTCOME

    # -- Edge cases --

    def test_not_an_assertion(self):
        assert classify_assertion("result = compute()") == AssertionKind.NONE

    def test_comment_line(self):
        assert classify_assertion("# assert something") == AssertionKind.NONE

    def test_whitespace_handling(self):
        assert classify_assertion("    assert result is not None  ") == AssertionKind.STRUCTURAL


# ---------------------------------------------------------------------------
# extract_test_functions — parsing test code into (name, assertions) pairs
# ---------------------------------------------------------------------------


class TestExtractTestFunctions:
    """Verify that test functions and their assertions are correctly parsed."""

    def test_single_function_with_assertions(self):
        code = '''\
def test_health_score():
    result = compute_score(company_id=123)
    assert result is not None
    assert result.score == 75
'''
        funcs = extract_test_functions(code)
        assert len(funcs) == 1
        assert funcs[0][0] == "test_health_score"
        assert len(funcs[0][1]) == 2  # two assertion lines

    def test_multiple_functions(self):
        code = '''\
def test_first():
    assert result is not None

def test_second():
    assert score == 42
'''
        funcs = extract_test_functions(code)
        assert len(funcs) == 2
        assert funcs[0][0] == "test_first"
        assert funcs[1][0] == "test_second"

    def test_ignores_non_test_functions(self):
        code = '''\
def helper_setup():
    return {"key": "value"}

def test_actual():
    assert result == 42
'''
        funcs = extract_test_functions(code)
        assert len(funcs) == 1
        assert funcs[0][0] == "test_actual"

    def test_class_method(self):
        code = '''\
class TestHealth:
    def test_score(self):
        assert score == 75
'''
        funcs = extract_test_functions(code)
        assert len(funcs) == 1
        assert funcs[0][0] == "test_score"

    def test_pytest_raises_captured(self):
        code = '''\
def test_raises_on_empty():
    with pytest.raises(ValueError):
        process("")
'''
        funcs = extract_test_functions(code)
        assert len(funcs) == 1
        assert len(funcs[0][1]) == 1  # pytest.raises counts as an assertion


# ---------------------------------------------------------------------------
# analyze_test_quality — the verdict
# ---------------------------------------------------------------------------


class TestAnalyzeTestQuality:
    """Verify that test quality analysis correctly flags structural-only tests."""

    def test_structural_only_flagged(self):
        code = '''\
def test_result_exists():
    result = get_data()
    assert result is not None
    assert len(result) > 0
'''
        issues = analyze_test_quality(code, "tests/test_example.py")
        assert len(issues) == 1
        assert "test_result_exists" in issues[0]

    def test_outcome_assertions_pass(self):
        code = '''\
def test_correct_score():
    result = compute(company_id=123)
    assert result is not None
    assert result.score == 75
'''
        issues = analyze_test_quality(code, "tests/test_example.py")
        assert len(issues) == 0

    def test_mixed_functions_only_flags_bad_ones(self):
        code = '''\
def test_good():
    assert result.name == "expected"

def test_bad():
    assert result is not None
    assert len(result) > 0
'''
        issues = analyze_test_quality(code, "tests/test_example.py")
        assert len(issues) == 1
        assert "test_bad" in issues[0]
        assert "test_good" not in issues[0]

    def test_exception_tests_pass(self):
        code = '''\
def test_raises_on_bad_input():
    with pytest.raises(ValueError):
        process(None)
'''
        issues = analyze_test_quality(code, "tests/test_example.py")
        assert len(issues) == 0

    def test_empty_test_not_flagged(self):
        """Tests with no assertions are a different problem — not our scope."""
        code = '''\
def test_smoke():
    result = get_data()
'''
        issues = analyze_test_quality(code, "tests/test_example.py")
        assert len(issues) == 0  # no assertions = not structural-only

    def test_filepath_in_message(self):
        code = '''\
def test_exists():
    assert result is not None
'''
        issues = analyze_test_quality(code, "tests/unit/test_health.py")
        assert "tests/unit/test_health.py" in issues[0]


# ---------------------------------------------------------------------------
# main() — integration with Claude Code hook protocol
# ---------------------------------------------------------------------------


class TestMain:
    """Verify the hook's entry point responds correctly to different inputs."""

    def _run_hook(self, tool_name: str, command: str, diff_output: str = "") -> int:
        """Simulate running the hook with given inputs."""
        from outcome_assertion_gate import main

        hook_input = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"command": command},
        }

        with patch("sys.stdin", __class__=type(sys.stdin)):
            with patch("outcome_assertion_gate.get_test_diff", return_value=diff_output):
                with patch("json.load", return_value=hook_input):
                    return main()

    def test_ignores_non_bash(self):
        assert self._run_hook("Write", "anything") == 0

    def test_ignores_non_pr_commands(self):
        assert self._run_hook("Bash", "git status") == 0

    def test_fires_on_gh_pr_create(self):
        bad_test = '''\
def test_exists():
    assert result is not None
'''
        # Should return 0 but print an ask JSON
        assert self._run_hook("Bash", "gh pr create --title test", bad_test) == 0

    def test_allows_when_no_test_files_changed(self):
        assert self._run_hook("Bash", "gh pr create --title test", "") == 0

    def test_allows_good_tests(self):
        good_test = '''\
def test_correct():
    assert result.score == 75
'''
        assert self._run_hook("Bash", "gh pr create --title test", good_test) == 0

    def test_decision_honored_exactly_once(self):
        """CANONICAL DECISION CONTRACT: the gate signals its decision with a
        single mechanism — one permissionDecision JSON document on stdout AND
        exit 0 (NOT exit 2). A JSON decision *plus* a non-zero exit is a
        contradictory double-signal; capturing the exit code and asserting it
        is 0 rejects that shape, and ``json.loads`` raises on two stacked
        documents, rejecting a doubled signal. This is the regression guard
        for fxh.7.
        """
        from outcome_assertion_gate import main

        bad_test = '''### FILE: tests/test_example.py
def test_exists():
    assert result is not None
'''
        hook_input = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title test"},
        }
        captured = io.StringIO()
        exit_code = 0
        with patch("outcome_assertion_gate.get_test_diff", return_value=bad_test):
            with patch("json.load", return_value=hook_input):
                with patch("sys.stdout", captured):
                    try:
                        ret = main()
                        exit_code = ret if ret is not None else 0
                    except SystemExit as exc:
                        exit_code = exc.code or 0

        assert exit_code == 0, (
            "decision is carried by the stdout JSON, not exit 2 — "
            "a permissionDecision JSON plus a non-zero exit is a double-signal"
        )
        data = json.loads(captured.getvalue().strip())
        assert data["hookSpecificOutput"]["permissionDecision"] == "ask"


# --- compound / prefixed gh pr create (escapement-hel4) --------------------------------

def _run_main(command: str, diff: str = "") -> tuple[int, str]:
    import io as _io
    from outcome_assertion_gate import main
    hook_input = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    captured = _io.StringIO()
    with patch("sys.stdout", captured), \
         patch("outcome_assertion_gate.get_test_diff", return_value=diff), \
         patch("json.load", return_value=hook_input):
        code = main()
    return code, captured.getvalue()


def test_fires_on_compound_gh_pr_create():
    # Broad Bash matcher + command-position detection now catch a create that is not the
    # first token — the Bash(gh pr create:*) matcher silently skipped this.
    bad = "### FILE: tests/test_x.py\ndef test_exists():\n    assert result is not None\n"
    code, out = _run_main("cd /wt && gh pr create --title test", diff=bad)
    assert code == 0
    assert "OUTCOME ASSERTION CHECK" in out


def test_ignores_echoed_create_literal():
    # A `gh pr create` literal inside a quoted echo is not an invocation — no ask.
    code, out = _run_main('echo "next step: gh pr create --title X"', diff="anything")
    assert code == 0
    assert out == "", f"unexpected output on echoed literal: {out!r}"
