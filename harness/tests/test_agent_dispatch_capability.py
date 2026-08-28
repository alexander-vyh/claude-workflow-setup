#!/usr/bin/env python3
"""Capability oracle for host subagent-dispatch observability.

Business invariant: Escapement may only claim to observe a dispatched
subagent's native identity and its final output if a real captured host payload
shows both. escapement-g27c settled this by observation, not inference, and
`harness/tests/fixtures/agent_dispatch_hook_payloads.json` is that capture.

The independent oracle is a unique sentinel word each subagent was told to
reply with. A field only counts as carrying the subagent's output if the
sentinel round-trips through it verbatim. Fields that merely look authoritative
(background_tasks[].status) are asserted to be untrustworthy so no consumer
adopts them.

Fragile implementations rejected here include reading the verdict from
PostToolUse alone (empty for a backgrounded dispatch), assuming PostToolUse
precedes SubagentStop (false for a foreground dispatch), joining SubagentStop
by tool_use_id (absent there), and treating TaskCreated/TaskCompleted/
TeammateIdle as subagent signals (registered, never fired).
"""

from __future__ import annotations

import json
import pathlib

import pytest

FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "agent_dispatch_hook_payloads.json"
)

# The word each trial's subagent was instructed to reply with.
SENTINELS = {"background_dispatch": "MANGO", "foreground_dispatch": "PAPAYA"}
SUCCESS_TRIALS = sorted(SENTINELS)


@pytest.fixture(scope="module")
def capture() -> dict:
    return json.loads(FIXTURE.read_text())


def _events(capture: dict, trial: str, event: str) -> list[dict]:
    return [
        entry["payload"]
        for entry in capture["trials"][trial]["events"]
        if entry["registered_as"] == event
    ]


def _one(capture: dict, trial: str, event: str) -> dict:
    matches = _events(capture, trial, event)
    assert len(matches) == 1, f"expected exactly one {event} in {trial}"
    return matches[0]


@pytest.mark.parametrize("trial", SUCCESS_TRIALS)
def test_post_tool_use_agent_carries_the_native_child_identifier(capture, trial):
    """PostToolUse fires for Agent and binds a native child id in BOTH modes."""
    post = _one(capture, trial, "PostToolUse")
    assert post["tool_name"] == "Agent"
    agent_id = post["tool_response"]["agentId"]
    assert agent_id, "tool_response.agentId is the native child identifier"


@pytest.mark.parametrize("trial", SUCCESS_TRIALS)
def test_agent_id_joins_dispatch_to_subagent_lifecycle(capture, trial):
    """tool_response.agentId == SubagentStart/SubagentStop agent_id."""
    agent_id = _one(capture, trial, "PostToolUse")["tool_response"]["agentId"]
    assert _one(capture, trial, "SubagentStart")["agent_id"] == agent_id
    assert _one(capture, trial, "SubagentStop")["agent_id"] == agent_id


@pytest.mark.parametrize("trial", SUCCESS_TRIALS)
def test_subagent_stop_carries_the_final_text_in_every_mode(capture, trial):
    """SubagentStop.last_assistant_message is the mode-independent verdict source."""
    stop = _one(capture, trial, "SubagentStop")
    assert stop["last_assistant_message"] == SENTINELS[trial]


def test_foreground_post_tool_use_also_carries_the_final_text(capture):
    """A synchronous dispatch resolves the subagent inside tool_response."""
    response = _one(capture, "foreground_dispatch", "PostToolUse")["tool_response"]
    assert response["status"] == "completed"
    texts = [block["text"] for block in response["content"] if block["type"] == "text"]
    assert SENTINELS["foreground_dispatch"] in texts


def test_backgrounded_post_tool_use_is_a_launch_receipt_only(capture):
    """Negative control: a backgrounded dispatch has NO verdict at PostToolUse.

    This is why a consumer must not read the verdict from PostToolUse alone.
    """
    response = _one(capture, "background_dispatch", "PostToolUse")["tool_response"]
    assert response["status"] == "async_launched"
    assert response["isAsync"] is True
    assert "content" not in response

    # The sentinel must not survive in any field that could be mistaken for the
    # subagent's reply. `prompt` and `description` are excluded because they are
    # echoes of the dispatch request -- see the echo-hazard test below.
    echoed = {"prompt", "description"}
    reply_bearing = {key: value for key, value in response.items() if key not in echoed}
    assert SENTINELS["background_dispatch"] not in json.dumps(reply_bearing)


@pytest.mark.parametrize("trial", SUCCESS_TRIALS)
def test_tool_response_echoes_the_dispatch_prompt(capture, trial):
    """Echo hazard: tool_response.prompt repeats what the DISPATCHER asked for.

    A consumer that merely searches tool_response for expected verdict text can
    be satisfied by the dispatcher's own prompt rather than the subagent's
    reply. Verdict text must be read from a named reply field --
    tool_response.content or SubagentStop.last_assistant_message -- never by
    substring search over the whole payload.
    """
    response = _one(capture, trial, "PostToolUse")["tool_response"]
    assert SENTINELS[trial] in response["prompt"]


def test_relative_order_of_post_tool_use_and_subagent_stop_is_mode_dependent(capture):
    """Consumers must be order-tolerant: neither event reliably comes first."""

    def order(trial: str) -> tuple[int, int]:
        seen = capture["trials"][trial]["observed_order"]
        return seen.index("PostToolUse"), seen.index("SubagentStop")

    background_post, background_stop = order("background_dispatch")
    foreground_post, foreground_stop = order("foreground_dispatch")
    assert background_post < background_stop
    assert foreground_post > foreground_stop


@pytest.mark.parametrize("trial", SUCCESS_TRIALS)
def test_subagent_events_cannot_be_joined_by_tool_use_id(capture, trial):
    """tool_use_id spans the tool events only; the subagent events lack it."""
    pre = _one(capture, trial, "PreToolUse")
    post = _one(capture, trial, "PostToolUse")
    assert pre["tool_use_id"] == post["tool_use_id"]
    assert "tool_use_id" not in _one(capture, trial, "SubagentStart")
    assert "tool_use_id" not in _one(capture, trial, "SubagentStop")


@pytest.mark.parametrize("trial", SUCCESS_TRIALS)
def test_background_tasks_status_is_not_a_terminality_signal(capture, trial):
    """A field that looks authoritative and is not.

    The backgrounded trial still reported its own agent as "running" inside that
    agent's own SubagentStop payload, so terminality must come from the event.
    """
    stop = _one(capture, trial, "SubagentStop")
    stale = [
        task
        for task in stop["background_tasks"]
        if task["id"] == stop["agent_id"] and task["status"] == "running"
    ]
    if trial == "background_dispatch":
        assert stale, "expected the observed stale 'running' self-report"


def test_failed_dispatch_reports_an_error_and_binds_no_child(capture):
    """A rejected dispatch must not leave a queued execution behind."""
    order = capture["trials"]["dispatch_failure"]["observed_order"]
    assert "PostToolUseFailure" in order
    assert "PostToolUse" not in order
    assert "SubagentStart" not in order
    assert "SubagentStop" not in order

    failure = _one(capture, "dispatch_failure", "PostToolUseFailure")
    assert failure["tool_name"] == "Agent"
    assert failure["error"]
    assert failure["is_interrupt"] is False
    assert failure["tool_use_id"]


def test_undocumented_event_names_are_recorded_honestly(capture):
    """SubagentStart is real; the other undocumented names never fired."""
    provenance = capture["_provenance"]
    assert "SubagentStart" in provenance["events_that_fired"]
    for name in ("TaskCreated", "TaskCompleted", "TeammateIdle"):
        assert name in provenance["registered_events"]
        assert name in provenance["events_accepted_but_never_fired"]
        for trial in capture["trials"].values():
            assert name not in trial["observed_order"]
