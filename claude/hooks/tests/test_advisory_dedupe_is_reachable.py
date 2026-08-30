"""A dedupe consumer must have at least one test that can actually reach it.

Business outcome
----------------
Three gates in a row shipped, or nearly shipped, a dedupe feature no test could
execute:

  #212  oracle_downgrade_warning_gate  merged with zero coverage; 288 tests green
  #213  found only because the review gate forced a re-read
  #214  tdd_gate; all 61 existing tests pass and none can reach the dedupe path

The cause is identical each time and leaves no trace. ``_advisory_dedupe`` is
keyed on the session: ``_state_path`` matches ``session_id`` against
``_SESSION_RE``, an empty string fails, it returns None, and ``already_reported``
returns False unconditionally. A test harness that builds payloads without a
``session_id`` therefore cannot execute the feature at all -- and reports no
failure, no skip, and no warning while doing it.

This is the cheapest possible guard: no runtime cost, no hook, no waiver
machinery. It asserts only that somebody, somewhere, drives each consumer with a
real session id.

Invalid solution classes rejected here
--------------------------------------
- matching only the dict-literal form -> test_the_detector_sees_both_spellings
  (an earlier version of this check reported a false UNCOVERED because the quiet
  test sets payload["session_id"] = s by assignment rather than as a literal)
- a scan that silently finds no consumers -> test_there_are_consumers_to_check
"""

from __future__ import annotations

import pathlib
import re

import pytest

HOOKS = pathlib.Path(__file__).resolve().parents[1]
TESTS = HOOKS / "tests"
MODULE = "_advisory_dedupe"

# Both spellings that put a session id into a payload:
#   {"session_id": s}      dict literal
#   payload["session_id"] = s   assignment
SESSION_ID = re.compile(r"""["']session_id["']\s*(:|\]\s*=)""")


def _consumers() -> list[pathlib.Path]:
    return sorted(
        p for p in HOOKS.glob("*.py")
        if p.name != f"{MODULE}.py"
        and MODULE in p.read_text(encoding="utf-8", errors="replace")
    )


SELF = pathlib.Path(__file__).name


def _related_tests(hook: pathlib.Path) -> dict[pathlib.Path, str]:
    """Test files that actually drive ``hook``, not ones that merely mention it.

    Prose matching is not enough: this file names every consumer in its own
    docstring and contains a session_id literal in its detector control, so a
    body-substring rule made it count as coverage for all of them -- the check
    certified itself and survived having real coverage deleted. Relatedness now
    means the file names the hook in its filename, or loads it by module name or
    by file name.
    """
    stem = hook.stem.replace("-", "_")
    loaders = (
        f'"{stem}"', f"'{stem}'",          # load_hook / spec_from_file_location
        hook.name,                          # "tdd-gate.py"
        f"from test_{stem} import",         # reuse of a sibling's module object
    )
    out = {}
    for t in TESTS.glob("test_*.py"):
        if t.name == SELF:
            continue
        body = t.read_text(encoding="utf-8", errors="replace")
        if stem in t.name or any(tok in body for tok in loaders):
            out[t] = body
    return out


def test_there_are_consumers_to_check():
    """Positive control: an empty consumer list would make the check vacuous."""
    consumers = _consumers()
    assert consumers, (
        f"no hook imports {MODULE}; either the module was removed or this scan is broken"
    )


def test_the_detector_sees_both_spellings():
    """Negative control: the pattern must match assignment, not just a literal."""
    assert SESSION_ID.search('payload = {"session_id": "abc"}')
    assert SESSION_ID.search('payload["session_id"] = session')
    assert not SESSION_ID.search("session_id = data.get('session_id')"), (
        "a bare read is not a payload carrying a session id"
    )


@pytest.mark.parametrize("hook", _consumers(), ids=lambda p: p.name)
def test_every_dedupe_consumer_is_driven_with_a_real_session_id(hook: pathlib.Path):
    """Some test must send this hook a payload carrying a session id.

    Without one, the dedupe branch is unreachable and any bug in it ships green.
    """
    related = _related_tests(hook)
    assert related, f"{hook.name} imports {MODULE} but has no related test file at all"
    covered = [t.name for t, body in related.items() if SESSION_ID.search(body)]
    assert covered, (
        f"{hook.name} imports {MODULE}, but none of its tests "
        f"({', '.join(sorted(t.name for t in related))}) send a payload with a "
        f"session_id. _state_path rejects the empty string, so already_reported "
        f"returns False unconditionally and the dedupe path never executes -- the "
        f"suite would stay green over a completely broken feature."
    )
