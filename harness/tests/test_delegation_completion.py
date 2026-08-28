#!/usr/bin/env python3
"""Behavioral oracle for observing a delegated child through to a verdict.

Business outcome
----------------
When a dispatched agent finishes, the ledger learns that automatically. Nobody
runs a recovery command, and nobody hand-edits executions.json.

What was broken
---------------
`delegation_hook` registered Agent PreToolUse only. Nothing emitted
`child_bound`, and `execution_ledger.apply_event` refuses `child_started` and
`child_terminal` without a bound native child, so every dispatched execution
sat `queued` forever and `claim_result_application` — which requires
`state == "terminal"` — could never be claimed at all.

Independent oracle
------------------
The REAL captured host payloads in
`harness/tests/fixtures/agent_dispatch_hook_payloads.json` (Claude Code
2.1.248, escapement-g27c), replayed verbatim in their captured order, plus the
sentinel word each probe subagent was told to reply with. A digest counts as
the child's verdict only if it is the digest of the sentinel EXACTLY.

That last point is the discriminating check, not a formality: the dispatch
prompt in the capture ("...reply with the single word MANGO...") CONTAINS the
sentinel, and `tool_response.prompt` echoes it back before the child has
produced anything. An implementation that digested the prompt would satisfy any
"sentinel appears somewhere" assertion. Only exact-digest equality separates the
child's answer from the parent's question.

The one edit made to the captured payloads is adding `tool_input.name`, which
this repo's named-agent policy requires and the probe's `general-purpose`
dispatch did not set. Everything else is byte-for-byte as the host emitted it.

Fragile implementations these tests REJECT
------------------------------------------
- Treating Agent PostToolUse as completion. For a backgrounded dispatch it is a
  launch receipt (`status: async_launched`, `content: null`) and terminalizing
  on it would invent a verdict
  (test_backgrounded_post_tool_use_is_a_launch_receipt_not_a_verdict).
- Assuming PostToolUse precedes SubagentStop. It does not under a foreground
  dispatch (test_captured_foreground_dispatch_reaches_terminal...).
- Joining the subagent events by `tool_use_id`, which is absent from them
  (the foreground replay only passes if the join is by `agent_id`).
- Digesting anything but the named reply field (every exact-digest assertion).
- Leaving a host-rejected dispatch queued for two hours
  (test_host_rejected_dispatch_is_released_immediately).

Run: python3 -m pytest harness/tests/test_delegation_completion.py -q
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import io
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(BIN))

import delegation_hook  # noqa: E402
import execution_ledger as ledger_api  # noqa: E402
from execution_store import load_trusted  # noqa: E402
from would_block_stop import execution_stop_decision  # noqa: E402

UTC = dt.timezone.utc
CAPTURE = json.loads(
    (
        pathlib.Path(__file__).parent / "fixtures" / "agent_dispatch_hook_payloads.json"
    ).read_text()
)
SENTINELS = {"background_dispatch": "MANGO", "foreground_dispatch": "PAPAYA"}
BEAD = "escapement-mn2q"
ROOT_BEAD = "escapement-mn2q-root"
AGENT = "captured-probe-child"


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _events(trial: str) -> list[dict]:
    """Captured payloads for one trial, in the order the host emitted them."""
    return [copy.deepcopy(entry["payload"]) for entry in CAPTURE["trials"][trial]["events"]]


def _session_of(trial: str) -> str:
    return _events(trial)[0]["session_id"]


def _thread(tmp_path: pathlib.Path, session_id: str) -> pathlib.Path:
    thread_dir = tmp_path / "harness" / "threads" / session_id
    thread_dir.mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".beads").mkdir(parents=True)
    mode = thread_dir / "session_mode.json"
    mode.write_text(
        json.dumps(
            {
                "mode": "task",
                "session_id": session_id,
                "repo_cwd": str(repo),
                "task_id": BEAD,
                "parent_id": ROOT_BEAD,
            }
        ),
        encoding="utf-8",
    )
    mode.chmod(0o600)
    return thread_dir


def _replay(thread_dir: pathlib.Path, events: list[dict]) -> list[dict]:
    """Feed captured payloads to the same handlers the hook entry point routes to."""
    ledger_path = thread_dir / "executions.json"
    results = []
    for payload in events:
        event = payload.get("hook_event_name")
        if event == "PreToolUse" and payload.get("tool_name") == "Agent":
            # Named-agent policy: this repo only tracks a dispatch that names
            # its agent. The probe dispatched an unnamed general-purpose child.
            payload["tool_input"]["name"] = AGENT
            results.append(delegation_hook.pre_tool(payload, None, ledger_path))
        elif event == "PostToolUse":
            # Apply the same documented named-agent normalization to the
            # matching completion envelope, where identity is re-verified.
            payload["tool_input"]["name"] = AGENT
            results.append(delegation_hook.post_tool(payload, ledger_path))
        elif event == "PostToolUseFailure":
            results.append(delegation_hook.post_tool_failure(payload, ledger_path))
        elif event == "SubagentStop":
            results.append(delegation_hook.subagent_stop(payload, ledger_path))
    return results


def _only(thread_dir: pathlib.Path, session_id: str) -> dict:
    ledger = load_trusted(thread_dir / "executions.json", session_id)
    assert ledger is not None, "the ledger must stay trusted and schema-valid"
    assert len(ledger["executions"]) == 1
    return ledger["executions"][0]


# ---------------------------------------------------------------------------
# The outcome, under both dispatch modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trial", sorted(SENTINELS), ids=sorted(SENTINELS))
def test_captured_dispatch_reaches_terminal_carrying_the_childs_own_verdict(
    tmp_path, trial
) -> None:
    """Replaying the real host capture must leave a consumable result.

    Before this adapter existed the execution never left `queued`, so
    `claim_result_application` was unreachable by construction and the parent
    session could never account for the child's work at all.
    """
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)

    _replay(thread_dir, _events(trial))

    item = _only(thread_dir, session_id)
    assert item["state"] == "terminal"
    assert item["native_child_id"], "the native child must be bound, not guessed"
    # The sentinel EXACTLY — not the prompt that merely mentions it.
    assert item["result_digest"] == _digest(SENTINELS[trial])

    # The unlock: a terminal execution with a real digest is claimable, which is
    # the precondition for applying a verified result. This was impossible before.
    ledger = load_trusted(thread_dir / "executions.json", session_id)
    claim = ledger_api.claim_result_application(
        ledger,
        item["execution_id"],
        dt.datetime.now(UTC),
        "owner-1",
        60,
        attempt=item["attempt"],
        generation=item["generation"],
    )
    assert claim is not None


@pytest.mark.parametrize("trial", sorted(SENTINELS), ids=sorted(SENTINELS))
def test_the_child_is_bound_to_the_hosts_own_identifier(tmp_path, trial) -> None:
    """`agent_id` is the only join the subagent events carry; it must be used."""
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    expected = next(
        payload["agent_id"] for payload in events if payload.get("agent_id")
    )

    _replay(thread_dir, events)

    assert _only(thread_dir, session_id)["native_child_id"] == expected


def test_backgrounded_post_tool_use_is_a_launch_receipt_not_a_verdict(
    tmp_path,
) -> None:
    """Terminalizing on the async receipt would invent a result out of nothing.

    The captured backgrounded PostToolUse carries `status: async_launched`,
    `content: null`, and a 33ms duration — the child has not said anything yet.
    """
    trial = "background_dispatch"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    upto_post_tool = events[: [e.get("hook_event_name") for e in events].index("PostToolUse") + 1]

    _replay(thread_dir, upto_post_tool)

    item = _only(thread_dir, session_id)
    assert item["state"] == "running"
    assert item["native_child_id"], "identity IS proven by the receipt"
    assert item["result_digest"] is None, "but the verdict is not"


def test_subagent_stop_for_an_unknown_child_changes_nothing(tmp_path) -> None:
    """A foreground SubagentStop arrives before binding; that is normal, not an error.

    It must be a quiet no-op rather than an exception or a guess at which
    queued execution it belongs to.
    """
    trial = "foreground_dispatch"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    pre, stop = events[0], next(
        e for e in events if e.get("hook_event_name") == "SubagentStop"
    )

    _replay(thread_dir, [pre])
    before = copy.deepcopy(_only(thread_dir, session_id))
    result = delegation_hook.subagent_stop(stop, thread_dir / "executions.json")

    assert result == {"status": "unmatched", "reason": "native_child_not_bound"}
    assert _only(thread_dir, session_id) == before


@pytest.mark.parametrize("verdict", [None, 42, "", "   ", "missing"])
def test_malformed_subagent_stop_cannot_create_a_claimable_result(
    tmp_path, verdict
) -> None:
    trial = "background_dispatch"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    stop = copy.deepcopy(
        next(event for event in events if event.get("hook_event_name") == "SubagentStop")
    )
    post_index = next(
        index
        for index, event in enumerate(events)
        if event.get("hook_event_name") == "PostToolUse"
    )
    _replay(thread_dir, events[: post_index + 1])
    path = thread_dir / "executions.json"
    before = path.read_bytes()
    if verdict == "missing":
        stop.pop("last_assistant_message", None)
    else:
        stop["last_assistant_message"] = verdict

    result = delegation_hook.subagent_stop(stop, path)

    assert result == {"status": "unresolved", "reason": "child_result_unverified"}
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# The dispatch that never produced a child at all
# ---------------------------------------------------------------------------


def test_host_rejected_dispatch_is_released_immediately(tmp_path) -> None:
    """A rejected dispatch fires PostToolUseFailure and no subagent event.

    Without handling it, the execution registered at PreToolUse waits out a
    two-hour hard deadline for a child that was never created.
    """
    trial = "dispatch_failure"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)

    _replay(thread_dir, _events(trial))

    item = _only(thread_dir, session_id)
    assert item["state"] == "aborted"
    assert item["native_child_id"] is None
    assert item["terminal_reason"] == "dispatch_failed"
    assert item["result_digest"] is None, "a failed dispatch produced no result"

    ledger = load_trusted(thread_dir / "executions.json", session_id)
    # Released now, at the real clock — not two hours from now.
    assert execution_stop_decision(
        "closed", ledger, None, [], dt.datetime.now(UTC)
    ) == ("allow", "delegated_outcome_complete")


def test_host_rejected_dispatch_records_the_hosts_own_error(tmp_path) -> None:
    """The cancellation must carry why, or it is an unexplained flag flip."""
    trial = "dispatch_failure"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    host_error = next(
        e["error"] for e in events if e.get("hook_event_name") == "PostToolUseFailure"
    )

    _replay(thread_dir, events)

    ledger = load_trusted(thread_dir / "executions.json", session_id)
    incidents = [e for e in ledger["incidents"] if e.get("type") == "dispatch_failed"]
    assert len(incidents) == 1
    assert incidents[0]["host_error"] == host_error
    assert incidents[0]["state_before"] == "queued"
    assert incidents[0]["recorded_at"] == ledger["executions"][0]["terminal_at"]
    assert incidents[0]["recorded_at"] is not None


def test_failure_after_the_child_started_cannot_release_completion(tmp_path) -> None:
    """A late failure event is not evidence that a running child never existed."""
    trial = "background_dispatch"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    post_index = next(
        index
        for index, event in enumerate(events)
        if event.get("hook_event_name") == "PostToolUse"
    )
    _replay(thread_dir, events[: post_index + 1])
    path = thread_dir / "executions.json"
    before = path.read_bytes()
    failure = copy.deepcopy(events[0])
    failure["hook_event_name"] = "PostToolUseFailure"
    failure["error"] = "late failure must not erase a running child"

    result = delegation_hook.post_tool_failure(failure, path)

    assert result["status"] == "unresolved"
    assert path.read_bytes() == before


@pytest.mark.parametrize("trial", ["foreground_dispatch", "dispatch_failure"])
def test_generation_one_host_event_cannot_mutate_recovery_generation(
    tmp_path, trial
) -> None:
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    _replay(thread_dir, [events[0]])
    path = thread_dir / "executions.json"

    def advance_generation(current: dict) -> dict:
        item = current["executions"][0]
        due = dt.datetime.fromisoformat(item["start_deadline"].replace("Z", "+00:00"))
        ledger_api.reconcile_deadlines(current, due)
        assert ledger_api.claim_recovery(current, item["execution_id"], due, "a", 1)
        assert ledger_api.claim_recovery(
            current, item["execution_id"], due + dt.timedelta(seconds=1), "b", 30
        )
        return current

    ledger_api.mutate_atomic(path, advance_generation)
    before = path.read_bytes()
    if trial == "foreground_dispatch":
        event = copy.deepcopy(
            next(
                payload
                for payload in events
                if payload.get("hook_event_name") == "PostToolUse"
            )
        )
        event["tool_input"]["name"] = AGENT
        result = delegation_hook.post_tool(event, path)
    else:
        event = copy.deepcopy(events[1])
        result = delegation_hook.post_tool_failure(event, path)

    assert result == {
        "status": "unresolved",
        "reason": "lifecycle_generation_unverified",
    }
    assert path.read_bytes() == before
    ledger = load_trusted(path, session_id)
    assert ledger is not None
    assert execution_stop_decision(
        "closed", ledger, None, [], dt.datetime.now(UTC)
    )[0] == "block"


# ---------------------------------------------------------------------------
# Identity safety: observation must not be a way into another session's ledger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler", ["post_tool", "subagent_stop", "post_tool_failure"]
)
def test_completion_events_refuse_a_foreign_parent_session(tmp_path, handler) -> None:
    """One session's child must never terminalize another session's execution."""
    trial = "background_dispatch"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    _replay(thread_dir, [events[0]])
    before = copy.deepcopy(_only(thread_dir, session_id))

    wanted = {
        "post_tool": "PostToolUse",
        "subagent_stop": "SubagentStop",
        "post_tool_failure": "PostToolUseFailure",
    }[handler]
    payload = next(
        (copy.deepcopy(e) for e in events if e.get("hook_event_name") == wanted),
        None,
    )
    if payload is None:  # PostToolUseFailure is not in this trial; synthesize it
        payload = copy.deepcopy(events[0])
        payload["hook_event_name"] = "PostToolUseFailure"
        payload["error"] = "Agent type not found"
    payload["session_id"] = "some-other-session"

    result = getattr(delegation_hook, handler)(payload, thread_dir / "executions.json")

    assert result["status"] == "unresolved"
    assert _only(thread_dir, session_id) == before


@pytest.mark.parametrize("trial", sorted(SENTINELS), ids=sorted(SENTINELS))
def test_redelivering_the_whole_sequence_changes_nothing(tmp_path, trial) -> None:
    """A re-delivered hook event must be a no-op, not a conflict.

    Both observation routes describe the SAME child terminating, so they share
    one terminal event id per attempt generation. Distinct ids would make a
    second arrival collide with "execution already has different terminal
    evidence" and strand the execution in exactly the state this work exists to
    prevent.
    """
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)

    _replay(thread_dir, events)
    path = thread_dir / "executions.json"
    after_first = path.read_bytes()
    _replay(thread_dir, _events(trial))

    assert path.read_bytes() == after_first


@pytest.mark.parametrize(
    "damage",
    ["missing-content", "missing-agent-name", "surplus-native-child"],
)
def test_malformed_foreground_completion_never_becomes_claimable(
    tmp_path, damage
) -> None:
    """Only the captured foreground result envelope may assert a verdict."""
    trial = "foreground_dispatch"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    _replay(thread_dir, [events[0]])
    payload = next(
        copy.deepcopy(event)
        for event in events
        if event.get("hook_event_name") == "PostToolUse"
    )
    if damage != "missing-agent-name":
        payload["tool_input"]["name"] = AGENT
    if damage == "missing-content":
        payload["tool_response"].pop("content")
    elif damage == "surplus-native-child":
        payload["tool_response"]["native_child_id"] = "invented-child"
    path = thread_dir / "executions.json"
    before = path.read_bytes()

    result = delegation_hook.post_tool(payload, path)

    assert result["status"] == "unresolved"
    assert path.read_bytes() == before
    ledger = load_trusted(path, session_id)
    assert ledger is not None
    item = _only(thread_dir, session_id)
    assert item["state"] == "queued"
    assert ledger_api.claim_result_application(
        ledger,
        item["execution_id"],
        dt.datetime.now(UTC),
        "owner-1",
        60,
        attempt=1,
        generation=1,
    ) is None


def test_altered_foreground_replay_is_rejected_without_mutation(tmp_path) -> None:
    """One host identity cannot be replayed with a different child verdict."""
    trial = "foreground_dispatch"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    events = _events(trial)
    _replay(thread_dir, events)
    path = thread_dir / "executions.json"
    before = path.read_bytes()
    altered = next(
        copy.deepcopy(event)
        for event in events
        if event.get("hook_event_name") == "PostToolUse"
    )
    altered["tool_input"]["name"] = AGENT
    altered["tool_response"]["content"][0]["text"] = "TAMPERED"

    result = delegation_hook.post_tool(altered, path)

    assert result["status"] == "unresolved"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "event", ["PostToolUse", "SubagentStop", "PostToolUseFailure"]
)
def test_the_real_entry_point_stays_silent_on_completion_events(
    tmp_path, monkeypatch, capsys, event
) -> None:
    """Completion events carry no decision, and the hook must not invent one.

    Driven through `_hook_main` — the thing the host actually executes — not
    the handlers, because the routing IS the behavior under test. A
    SubagentStop hook that printed a decision would be steering the child
    rather than observing it, and a PostToolUse hook that printed one would be
    answering a question the host did not ask.
    """
    trial = "background_dispatch"
    session_id = _session_of(trial)
    thread_dir = _thread(tmp_path, session_id)
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path / "harness"))
    events = _events(trial)
    _replay(thread_dir, [events[0]])
    capsys.readouterr()

    payload = next(
        (copy.deepcopy(e) for e in events if e.get("hook_event_name") == event), None
    )
    if payload is None:  # PostToolUseFailure is not in this trial; synthesize it
        payload = copy.deepcopy(events[0])
        payload["hook_event_name"] = "PostToolUseFailure"
        payload["error"] = "Agent type not found"
    monkeypatch.setattr(delegation_hook.sys, "stdin", io.StringIO(json.dumps(payload)))

    assert delegation_hook._hook_main() == 0
    assert capsys.readouterr().out == ""


def test_the_real_entry_point_still_answers_pre_tool_use(
    tmp_path, monkeypatch, capsys
) -> None:
    """The routing must not have swallowed the one event that DOES need a reply.

    PreToolUse is a permission decision; returning nothing there would leave the
    host without the allow that keeps native Agent capacity intact.
    """
    trial = "background_dispatch"
    session_id = _session_of(trial)
    _thread(tmp_path, session_id)
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path / "harness"))
    payload = _events(trial)[0]
    payload["tool_input"]["name"] = AGENT
    monkeypatch.setattr(delegation_hook.sys, "stdin", io.StringIO(json.dumps(payload)))

    assert delegation_hook._hook_main() == 0

    emitted = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert emitted["permissionDecision"] == "allow"
    assert emitted["permissionDecisionReason"] == "dispatch_registered"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
