#!/usr/bin/env python3
"""Every delegated block reason must have its own denial message.

Business invariant
------------------
An agent blocked on a delegated path is told something true and actionable
about THAT reason. No delegated reason may fall through to the generic
`RESUMPTION_PROMPT`.

Why the fallback is worse than nothing
--------------------------------------
`RESUMPTION_PROMPT` advertises two remedies that cannot work on a delegated
path:

- run `verify` to exit 0 — `stop_hook.main` calls `decide_task_mode()` and
  returns before the contract gate is ever consulted, so a green contract
  changes nothing on any `delegated_*` path;
- call ScheduleWakeup — observed refusing with "the /loop dynamic runtime gate
  is off", a host runtime condition this gate cannot see.

A denial that names a remedy which cannot succeed is worse than a bare reason
code: it spends the agent's turns on something that was never going to work,
and it teaches the agent that the gate's instructions are noise. This repo has
hit that exact shape repeatedly (escapement-3dzd, escapement-6ge8,
escapement-a6h1, escapement-9pz2), which is why this test asserts the CLASS
rather than one more instance.

Independent oracle
------------------
The reason strings are read out of the adapters' own source with `ast`, not
copied into a list here. A hand-maintained list is exactly how the third
instance got missed: someone adds a `return ("block", "new_reason")` and no
test notices. This fails the moment that happens.

Fragile implementations this REJECTS
------------------------------------
- Adding a new delegated block reason with no display entry.
- "Fixing" a gap by pointing the reason at another reason's message (each
  message must name its own reason code).
- Re-introducing verify-exit-0 or ScheduleWakeup as a delegated remedy.

Run: python3 -m pytest harness/tests/test_delegated_denial_coverage.py -q
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(BIN))

import stop_hook  # noqa: E402

# The two functions whose ("block", reason) pairs reach the delegated render
# path in stop_hook.main. `would_block_stop.would_block_stop` is deliberately
# NOT here: it is the contract path, where verify genuinely is the escape and
# RESUMPTION_PROMPT is the right message.
DELEGATED_SOURCES = (
    ("would_block_stop.py", "execution_stop_decision"),
    ("execution_stop_adapter.py", "decide_task_mode"),
)


def _block_reasons(filename: str, function: str) -> set[str]:
    """Collect every literal ("block", <reason>) tuple returned by one function."""
    tree = ast.parse((BIN / filename).read_text(encoding="utf-8"))
    target = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == function
        ),
        None,
    )
    assert target is not None, f"{function} not found in {filename}"

    reasons: set[str] = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
            continue
        first, second = node.elts
        if (
            isinstance(first, ast.Constant)
            and first.value == "block"
            and isinstance(second, ast.Constant)
            and isinstance(second.value, str)
        ):
            reasons.add(second.value)
    assert reasons, f"no block reasons parsed from {function}; the walk is broken"
    return reasons


def delegated_block_reasons() -> set[str]:
    reasons: set[str] = set()
    for filename, function in DELEGATED_SOURCES:
        reasons |= _block_reasons(filename, function)
    return reasons


def test_the_reason_walk_actually_finds_the_known_reasons() -> None:
    """Negative control: an AST walk that silently found nothing would make
    every assertion below vacuously true."""
    reasons = delegated_block_reasons()
    assert {
        "delegated_execution_overdue",
        "delegated_execution_unresolved",
        "managed_wake_unresolved",
        "parent_outcome_unresolved",
        "supervisor_health_unresolved",
    } <= reasons


@pytest.mark.parametrize("reason", sorted(delegated_block_reasons()))
def test_every_delegated_block_reason_has_its_own_message(reason) -> None:
    """No delegated reason may fall through to the generic template."""
    message = stop_hook._TASK_MODE_DISPLAY.get(reason)
    assert message is not None, (
        f"{reason} has no _TASK_MODE_DISPLAY entry, so it renders as the generic "
        "RESUMPTION_PROMPT — which offers verify-exit-0 and ScheduleWakeup, neither "
        "of which can clear a delegated block."
    )
    assert reason in message, (
        f"{reason}'s message does not name its own reason code, so an agent cannot "
        "tell which gate it is reading about."
    )


@pytest.mark.parametrize("reason", sorted(delegated_block_reasons()))
def test_no_delegated_message_promises_a_remedy_that_cannot_work(reason) -> None:
    """The two dead remedies must not come back on any delegated path."""
    message = stop_hook._TASK_MODE_DISPLAY[reason]
    lowered = message.lower()

    assert "schedulewakeup" not in lowered.replace(" ", ""), (
        f"{reason} offers ScheduleWakeup, whose availability depends on a host "
        "runtime condition this gate cannot observe."
    )
    assert "~/.claude/harness/bin/verify" not in message, (
        f"{reason} offers the contract verify, but stop_hook.main returns on the "
        "delegated path before the contract gate is reached, so a green verify "
        "cannot clear it."
    )


@pytest.mark.parametrize("reason", sorted(delegated_block_reasons()))
def test_every_delegated_message_offers_something_the_agent_can_do_now(
    reason,
) -> None:
    """A reason code with no forward action is the failure this gate had.

    Every delegated block resolves one of three ways: the agent keeps working
    and the child reports, the agent runs a real command, or the agent records
    a real outcome in Beads. At least one must be named.
    """
    lowered = stop_hook._TASK_MODE_DISPLAY[reason].lower()
    actionable = (
        "execution_reconcile.py" in lowered
        or "keep working" in lowered
        or "bd close" in lowered
    )
    assert actionable, (
        f"{reason}'s message blocks the stop without naming a runnable command, a "
        "Beads action, or 'keep working'."
    )


@pytest.mark.parametrize("reason", sorted(delegated_block_reasons()))
def test_no_delegated_message_invites_the_winddown(reason) -> None:
    """Same invariant test_stop_messages.py locks for the older messages."""
    lowered = stop_hook._TASK_MODE_DISPLAY[reason].lower()
    assert "ask the user to release" not in lowered
    assert "do not" in lowered or "don't" in lowered
    assert any(
        phrase in lowered
        for phrase in ("summariz", "what to do next", "hand off", "wind down")
    ), f"{reason} prohibits nothing nameable"


def test_session_scoped_commands_are_bound_to_the_real_session() -> None:
    """A copy-pasteable command must not still contain the placeholder.

    stop_hook substitutes `{session_id}` at render time. A message that used a
    different spelling would ship a literal placeholder to the agent, who has
    no way to resolve it.
    """
    for reason in sorted(delegated_block_reasons()):
        message = stop_hook._TASK_MODE_DISPLAY[reason]
        if "--session" not in message:
            continue
        assert "--session {session_id}" in message, (
            f"{reason} passes --session with something other than the "
            "{session_id} token stop_hook actually substitutes."
        )
        rendered = message.replace("{session_id}", "sess-real-1")
        assert "{session_id}" not in rendered
        assert "--session sess-real-1" in rendered


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
