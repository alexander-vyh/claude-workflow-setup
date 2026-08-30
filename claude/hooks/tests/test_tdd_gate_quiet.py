"""The TDD nudge must be said once per session, not once per write.

Business outcome
----------------
An agent editing one implementation file gets the same "write the failing test
first" prompt on every single write. Measured over the full gate-signal corpus:
tdd_gate emitted 2,712 ``ask`` decisions across 155 sessions, with a worst run of
22 consecutive identical nudges about the same file. The advice does not change
between them, so repeating it only trains the reader to skip it.

Why a set and not a last-value memory
-------------------------------------
tdd_gate fires per file. An agent that writes impl A, then B, then A again would
be nudged about A twice under last-value memory, because B displaced it. Measured
on the corpus, that alternation is the difference between suppressing 41% of the
repeats and suppressing 81%. The nudge is identical regardless of which file
triggered it, so a file already mentioned is not new information -- but a file
never mentioned is, which is why a blanket once-per-session rule (94%) goes too
far and is not what this implements.

Independent source of truth
---------------------------
``tdd_gate.main()`` driven over a real payload against a real git repo, reading
the permissionDecision the gate actually emits.

Invalid solution classes rejected here
--------------------------------------
- reverting to per-write repetition   -> test_the_same_file_is_nudged_only_once
- suppressing a file never mentioned  -> test_a_new_file_is_still_nudged
- a memory that outlives the condition, so the gate goes quiet forever once
  tests are touched and then abandoned -> test_touching_tests_reopens_the_nudge
- clearing on paths that prove nothing -> test_an_ungated_call_does_not_reset
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HOOKS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tdd_gate", _HOOKS / "tdd-gate.py")
tdd_gate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["tdd_gate"] = tdd_gate
_spec.loader.exec_module(tdd_gate)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo with committed tests and impl, and a clean working tree."""
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path / "harness"))
    d = tmp_path / "repo"
    (d / "tests").mkdir(parents=True)
    (d / "src").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    (d / "pyproject.toml").write_text("[project]\nname='x'\n")
    (d / "tests" / "test_app.py").write_text("def test_a():\n    assert True\n")
    (d / "src" / "a.py").write_text("A = 1\n")
    (d / "src" / "b.py").write_text("B = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=d, check=True)
    return d


def nudged(repo: Path, rel: str, session: str = "sess", tool: str = "Write") -> bool:
    """True when the gate emitted an 'ask' for writing ``rel``."""
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(repo / rel)},
        "session_id": session,
    }
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        with patch("sys.stdout", out):
            tdd_gate.main()
    text = out.getvalue().strip()
    if not text:
        return False
    decision = json.loads(text).get("hookSpecificOutput", {}).get("permissionDecision")
    return decision == "ask"


def touch_test(repo: Path) -> None:
    """Modify a test file, so the gate's own condition is resolved."""
    (repo / "tests" / "test_app.py").write_text(
        "def test_a():\n    assert True\n\ndef test_b():\n    assert True\n"
    )


def test_the_fixture_actually_nudges(repo):
    """Positive control: without a real nudge every test below proves nothing."""
    assert nudged(repo, "src/a.py"), (
        "writing impl with no test changes must produce a TDD nudge; "
        "without it the dedupe tests below are vacuous"
    )


def test_the_same_file_is_nudged_only_once(repo):
    """The regression: 22 consecutive identical nudges about one file."""
    assert nudged(repo, "src/a.py")
    assert not nudged(repo, "src/a.py"), (
        "the same file must not be nudged twice in one session"
    )


def test_a_new_file_is_still_nudged(repo):
    """Negative control: dedupe must not swallow a file never mentioned."""
    assert nudged(repo, "src/a.py")
    assert nudged(repo, "src/b.py"), (
        "a file this session has not been nudged about is new information"
    )


def test_an_earlier_file_stays_quiet_after_a_later_one(repo):
    """A -> B -> A must stay quiet on the second A.

    This is the case a last-value memory gets wrong: B displaces A, so A is
    nudged again. On the corpus that alternation is 1,065 of the 2,186
    suppressible repeats.
    """
    assert nudged(repo, "src/a.py")
    assert nudged(repo, "src/b.py")
    assert not nudged(repo, "src/a.py"), (
        "a file already nudged about must stay quiet even after another file"
    )


def test_touching_tests_reopens_the_nudge(repo):
    """A resolved condition must clear the memory.

    Otherwise the gate goes permanently quiet for a session the moment tests are
    touched once, which is a missed nudge rather than a suppressed repeat.
    """
    assert nudged(repo, "src/a.py")

    touch_test(repo)
    assert not nudged(repo, "src/a.py"), "with tests modified the gate allows"

    subprocess.run(["git", "checkout", "--", "tests/test_app.py"], cwd=repo, check=True)
    assert nudged(repo, "src/a.py"), (
        "once the test changes are gone the nudge is live again and must fire"
    )


def test_an_ungated_call_does_not_reset(repo):
    """Invalidation must be narrow, or it silently restores the repetition.

    Only the branch that observes real test changes proves the condition is
    resolved. Clearing on the gate's other early returns -- a read, an exempt
    file, a non-gated tool -- would reset the memory constantly and re-nudge
    about a file the session was already nudged about.
    """
    assert nudged(repo, "src/a.py")
    nudged(repo, "README.md")          # exempt file -> early allow
    nudged(repo, "src/a.py", tool="Read")  # ungated tool -> early return
    assert not nudged(repo, "src/a.py"), (
        "an exempt file or ungated tool must not reset the dedupe memory"
    )


def test_a_different_session_is_not_silenced(repo):
    """Dedupe is per session; a fresh session must still be nudged."""
    assert nudged(repo, "src/a.py", session="sess-one")
    assert nudged(repo, "src/a.py", session="sess-two"), (
        "a different session must still be nudged"
    )
