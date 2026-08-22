#!/usr/bin/env python3
"""Claude Code hook: outcome assertion quality gate.

Fires as PreToolUse on Bash when command contains `gh pr create`.

Analyzes test files in the git diff for assertion quality. If any test
function contains ONLY structural assertions (is not None, len > 0,
isinstance, etc.) without at least one outcome assertion (specific value
comparison), the hook prompts the user to confirm.

Severity: ask (not block) — the user can always override.

Input (via stdin):
  JSON with hook_event_name, tool_name, tool_input
Exit codes:
  0 — allow or ask
"""

import enum
import json
import re
import subprocess
import sys
from pathlib import Path

# Shared signal capture per claude/rules/gate-design.md Rule 2.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None

try:
    from _gh_command import is_gh_pr_command
except ImportError:  # pragma: no cover - fail toward checking on a create-shaped command
    def is_gh_pr_command(command: str, *_verbs: str) -> bool:
        return "gh pr create" in (command or "")


# ---------------------------------------------------------------------------
# Assertion classification
# ---------------------------------------------------------------------------


class AssertionKind(enum.Enum):
    STRUCTURAL = "structural"
    OUTCOME = "outcome"
    NONE = "none"


# Patterns that indicate structural-only assertions (existence/type/count checks).
# These match the assertion portion of a line, after stripping whitespace.
_STRUCTURAL_PATTERNS: list[re.Pattern] = [
    # assert X is not None / assertIsNotNone
    re.compile(r"assert\s+\w[\w.]*\s+is\s+not\s+None", re.IGNORECASE),
    re.compile(r"assertIsNotNone\(", re.IGNORECASE),
    # assert X (bare truthiness, no operator)
    re.compile(r"^assert\s+\w[\w.]*\s*$"),
    # assert len(X) > 0 / assertTrue(len(...) > 0)
    re.compile(r"assert\s+len\(.+\)\s*>\s*0"),
    re.compile(r"assertTrue\(\s*len\(.+\)\s*>\s*0\s*\)"),
    re.compile(r"assertGreater\(\s*len\(.+\)\s*,\s*0\s*\)"),
    # assert isinstance(X, Y) / assertIsInstance
    re.compile(r"assert\s+isinstance\("),
    re.compile(r"assertIsInstance\("),
    # assert "key" in dict / assertIn("key", ...)
    re.compile(r'assert\s+["\'].+["\']\s+in\s+\w'),
    re.compile(r"assertIn\("),
    # assert callable(X) / assert hasattr(X, ...)
    re.compile(r"assert\s+callable\("),
    re.compile(r"assert\s+hasattr\("),
    # assert X != "" / assert X != [] / assert X != {}
    re.compile(r'assert\s+\w[\w.]*\s*!=\s*["\'\[\{](\s*["\'\]\}])'),
    # assert count/len > 0 (variable form)
    re.compile(r"assert\s+\w+\s*>\s*0\s*$"),
]

# Patterns that indicate outcome assertions (specific value checks).
_OUTCOME_PATTERNS: list[re.Pattern] = [
    # assert X == <specific value> (not None, not 0, not "")
    re.compile(r"assert\s+.+==\s*(?!None\b)(?!0\s*$)(?!\"\"\s*$)(?!\[\]\s*$).+"),
    # assertEqual(X, <specific value>)
    re.compile(r"assertEqual\(\s*.+,\s*(?!None\b)(?!0\s*$).+\)"),
    # assertAlmostEqual
    re.compile(r"assertAlmostEqual\("),
    # pytest.approx
    re.compile(r"pytest\.approx\("),
    # pytest.raises / assertRaises — testing behavior
    re.compile(r"pytest\.raises\("),
    re.compile(r"assertRaises\("),
    # Comparison to specific non-zero/non-empty values
    re.compile(r"assert\s+.+[><=!]=?\s*(?!0\s*$)(?!None\b)\d+"),
    # assert X > N where N > 0
    re.compile(r"assert\s+\w[\w.]*\s*(?:>=?|<=?)\s*(?:[1-9]\d*)"),
]


def classify_assertion(line: str) -> AssertionKind:
    """Classify a single line as STRUCTURAL, OUTCOME, or NONE.

    A line is classified based on assertion patterns:
    - STRUCTURAL: existence/type/count checks (is not None, len > 0, isinstance)
    - OUTCOME: specific value comparisons (== 75, == "active", pytest.raises)
    - NONE: not an assertion at all
    """
    stripped = line.strip()

    # Skip comments
    if stripped.startswith("#"):
        return AssertionKind.NONE

    # Must look like an assertion
    is_assertion = (
        "assert" in stripped.lower()
        or "pytest.raises" in stripped
    )
    if not is_assertion:
        return AssertionKind.NONE

    # Check outcome patterns FIRST — they're more specific
    for pattern in _OUTCOME_PATTERNS:
        if pattern.search(stripped):
            return AssertionKind.OUTCOME

    # Then check structural patterns
    for pattern in _STRUCTURAL_PATTERNS:
        if pattern.search(stripped):
            return AssertionKind.STRUCTURAL

    # Unrecognized assertion — conservatively treat as outcome
    # (we don't want false positives)
    if "assert" in stripped:
        return AssertionKind.OUTCOME

    return AssertionKind.NONE


# ---------------------------------------------------------------------------
# Test function extraction
# ---------------------------------------------------------------------------

# Match `def test_*(...):` or `async def test_*(...):` with any indentation
_TEST_FUNC_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+(test_\w+)\s*\(")


def extract_test_functions(code: str) -> list[tuple[str, list[str]]]:
    """Parse Python test code into (function_name, assertion_lines) pairs.

    Only extracts functions whose name starts with `test_`.
    Assertions include lines containing `assert` or `pytest.raises`.
    """
    functions: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_assertions: list[str] = []

    for line in code.splitlines():
        m = _TEST_FUNC_RE.match(line)
        if m:
            # Save previous function
            if current_name is not None:
                functions.append((current_name, current_assertions))

            current_name = m.group(2)
            current_assertions = []
            continue

        if current_name is not None:
            stripped = line.strip()
            # Detect end of function: non-empty line at same or lesser indent
            if stripped and not line[0].isspace():
                # Top-level line — previous function ended
                functions.append((current_name, current_assertions))
                current_name = None
                current_assertions = []
                # Check if this line starts a new test
                m2 = _TEST_FUNC_RE.match(line)
                if m2:
                    current_name = m2.group(2)
                continue

            # Collect assertion lines
            if stripped and (
                "assert" in stripped.lower()
                or "pytest.raises" in stripped
            ):
                current_assertions.append(stripped)

    # Don't forget the last function
    if current_name is not None:
        functions.append((current_name, current_assertions))

    return functions


# ---------------------------------------------------------------------------
# Test quality analysis
# ---------------------------------------------------------------------------


def analyze_test_quality(code: str, filepath: str) -> list[str]:
    """Analyze test code for structural-only test functions.

    Returns a list of human-readable issue descriptions for test functions
    that contain only structural assertions (no outcome assertions).
    """
    functions = extract_test_functions(code)
    issues: list[str] = []

    for func_name, assertions in functions:
        if not assertions:
            # No assertions at all — different problem, not our scope
            continue

        has_outcome = False
        has_structural = False

        for assertion_line in assertions:
            kind = classify_assertion(assertion_line)
            if kind == AssertionKind.OUTCOME:
                has_outcome = True
            elif kind == AssertionKind.STRUCTURAL:
                has_structural = True

        if has_structural and not has_outcome:
            issues.append(
                f"{filepath}::{func_name} — only structural assertions "
                f"(is not None, len > 0, isinstance). Add at least one "
                f"assertion against a specific expected value."
            )

    return issues


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def get_test_diff() -> str:
    """Get the combined diff of test files in the current changeset.

    Looks at both staged and unstaged changes, plus untracked test files.
    Returns the content of changed test files (not the diff format —
    we need the full function context to analyze assertions).
    """
    test_files: set[str] = set()

    try:
        # Staged changes
        r1 = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=5,
        )
        if r1.returncode == 0:
            test_files.update(
                f for f in r1.stdout.strip().split("\n")
                if f and _is_test_path(f)
            )

        # Unstaged changes
        r2 = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, timeout=5,
        )
        if r2.returncode == 0:
            test_files.update(
                f for f in r2.stdout.strip().split("\n")
                if f and _is_test_path(f)
            )

        # Untracked test files
        r3 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=5,
        )
        if r3.returncode == 0:
            test_files.update(
                f for f in r3.stdout.strip().split("\n")
                if f and _is_test_path(f)
            )
    except (subprocess.TimeoutExpired, OSError):
        return ""

    if not test_files:
        return ""

    # Read the actual content of each test file to analyze assertions
    combined: list[str] = []
    for tf in sorted(test_files):
        try:
            from pathlib import Path
            content = Path(tf).read_text(encoding="utf-8", errors="replace")
            combined.append(f"### FILE: {tf}\n{content}")
        except OSError:
            continue

    return "\n".join(combined)


def _is_test_path(filepath: str) -> bool:
    """Check if a file path looks like a Python test file."""
    import os
    name = os.path.basename(filepath)
    if not name.endswith(".py"):
        return False
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    parts = set(filepath.split("/"))
    return bool(parts & {"tests", "test", "__tests__"})


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def allow() -> int:
    return 0


def ask(message: str) -> int:
    """Prompt the user for confirmation.

    CANONICAL DECISION CONTRACT: signal the decision with a single mechanism —
    one permissionDecision JSON document on stdout, exit 0. Exit 2 is the
    mutually-exclusive legacy stderr-feedback path; emitting both the JSON
    decision *and* a non-zero exit is a contradictory double-signal. This gate
    is advisory (permissionDecision="ask"), but uses the same single-mechanism
    JSON-on-stdout-plus-exit-0 contract as the hard-deny gates, so it returns 0.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": message,
        }
    }))
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if hook_event != "PreToolUse":
        return 0
    if tool_name != "Bash":
        return 0

    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    # Command-position aware (shared _gh_command): catches `cd /wt && gh pr create` that
    # the argument-scoped matcher + a bare substring both let through, without asking on a
    # `gh pr create` literal inside a quoted echo/commit message.
    if not is_gh_pr_command(command, "create"):
        return 0

    # Get test file content from the diff
    diff_content = get_test_diff()
    if not diff_content:
        return allow()  # No test files changed — nothing to check

    # Parse and analyze each test file in the diff
    all_issues: list[str] = []

    for section in diff_content.split("### FILE: "):
        if not section.strip():
            continue
        lines = section.split("\n", 1)
        if len(lines) < 2:
            continue
        filepath = lines[0].strip()
        code = lines[1]
        issues = analyze_test_quality(code, filepath)
        all_issues.extend(issues)

    if not all_issues:
        _record_signal(
            gate_name="outcome_assertion_gate",
            decision="allow",
            reason="all test functions have outcome assertions",
        )
        return allow()

    # Build the ask message
    issue_list = "\n".join(f"  - {issue}" for issue in all_issues)
    message = (
        f"OUTCOME ASSERTION CHECK: {len(all_issues)} test function(s) have "
        f"only structural assertions (is not None, len > 0, isinstance) "
        f"without checking specific expected values:\n{issue_list}\n\n"
        f"Add at least one assertion per test that checks a specific expected "
        f"outcome, or say 'proceed' to create the PR anyway."
    )

    _record_signal(
        gate_name="outcome_assertion_gate",
        decision="ask",
        reason=f"{len(all_issues)} test functions have structural-only assertions",
        issue_count=len(all_issues),
        issues=all_issues[:10],  # cap for log size
    )
    return ask(message)


if __name__ == "__main__":
    sys.exit(main())
