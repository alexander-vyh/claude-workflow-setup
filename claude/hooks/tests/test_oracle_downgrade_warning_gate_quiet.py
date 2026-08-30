"""The PreToolUse advisory must say a thing once, and hear it again if it returns.

Business outcome
----------------
This is the louder of the two oracle-downgrade gates: 6,914 of the 11,399 fires
in the signal corpus came from here, and 6,393 of those (92%) restated a finding
the session had already been shown. Dedupe landed in escapement #212 with no test
covering it -- every existing test in this directory sends a payload with no
``session_id``, which fails the session pattern and makes dedupe inert, so the
suite stayed green while the feature was unverified.

Independent source of truth
---------------------------
``gate.main()`` driven over a real payload against a real git repo, reading the
decision the hook actually emits.

Invalid solution classes rejected here
--------------------------------------
- reverting to per-turn repetition -> test_unchanged_finding_is_silent_the_second_time
- a memory that outlives the condition, so a weakening removed and reintroduced
  identically is never mentioned again -> test_a_reintroduced_finding_warns_again
- silencing a different session -> test_a_different_session_is_not_silenced
"""

from __future__ import annotations

import pytest

from test_oracle_downgrade_warning_gate import (
    commit_file,
    hook_payload,
    init_repo,
    run_hook,
)

STRONG = "def test_value():\n    assert result.status == 'active'\n"
WEAK = "def test_value():\n    assert result.status is not None\n"


@pytest.fixture
def harnessed(tmp_path, monkeypatch):
    """Point the dedupe state at a throwaway root so tests cannot leak into each other."""
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path / "harness"))
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = init_repo(repo_dir)
    commit_file(repo, "tests/test_app.py", STRONG)
    return repo


def _payload(repo, session: str) -> dict:
    p = hook_payload(repo)
    p["session_id"] = session
    return p


def _write(repo, content: str) -> None:
    (repo / "tests" / "test_app.py").write_text(content, encoding="utf-8")


def _warned(output) -> bool:
    if not output:
        return False
    reason = output.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    return "strong-assertion-weakened" in reason


def test_the_fixture_actually_warns(harnessed):
    """Positive control: without a real warning the dedupe tests below prove nothing."""
    _write(harnessed, WEAK)
    _, output = run_hook(_payload(harnessed, "sess-control"))
    assert _warned(output), "the weakened-assertion fixture must produce a warning"


def test_unchanged_finding_is_silent_the_second_time(harnessed):
    """The regression: 6,393 unchanged repeats out of 6,914 fires."""
    _write(harnessed, WEAK)
    assert _warned(run_hook(_payload(harnessed, "sess-quiet"))[1])
    assert not _warned(run_hook(_payload(harnessed, "sess-quiet"))[1]), (
        "an unchanged finding must not be warned about again in the same session"
    )


def test_a_reintroduced_finding_warns_again(harnessed):
    """A clean run must clear the memory.

    Without invalidation the stored fingerprint outlives the condition it
    describes, so weaken -> fix -> weaken identically is silently swallowed the
    second time -- a missed warning, which is worse than the noise it replaced.
    """
    _write(harnessed, WEAK)
    assert _warned(run_hook(_payload(harnessed, "sess-cycle"))[1]), "first weakening warns"

    _write(harnessed, STRONG)
    assert not _warned(run_hook(_payload(harnessed, "sess-cycle"))[1]), "a clean tree is quiet"

    _write(harnessed, WEAK)
    assert _warned(run_hook(_payload(harnessed, "sess-cycle"))[1]), (
        "the same weakening reintroduced after a clean run is a NEW event and must warn again"
    )


def test_a_different_session_is_not_silenced(harnessed):
    """Dedupe is per session; a fresh session must still be warned."""
    _write(harnessed, WEAK)
    assert _warned(run_hook(_payload(harnessed, "sess-one"))[1])
    assert _warned(run_hook(_payload(harnessed, "sess-two"))[1]), (
        "a different session must still be warned"
    )
