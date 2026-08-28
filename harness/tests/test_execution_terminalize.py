#!/usr/bin/env python3
"""Behavioral oracle for terminalizing a delegated child that never reported.

Business outcome
----------------
A session that dispatches an agent through the delegation hook can END NORMALLY
once that agent is done with it, WITHOUT anyone hand-editing executions.json.

Observed defect (session 575f5925, not hypothetical): `delegation_hook.pre_tool`
registers an execution in `queued` with `native_child_id: null`, and nothing
ever emits `child_bound`. `execution_ledger.apply_event` refuses `child_started`,
`child_terminal`, and `child_cancelled` without a bound native child, and
`execution_validation` refused a `cancelled` execution that had no child id. So
the ledger was STRUCTURALLY unable to leave `queued`: the Stop gate fired
`delegated_execution_overdue` forever and the session was wedged. The only
escape was excavating `execution_store.mutate_atomic` by hand.

Independent oracle
------------------
The public dispatch hook, the public Stop decision, the public
`execution_reconcile` command line, and the durable ledger file. Nothing here
asserts a private helper or a generated id.

Fragile implementations these tests REJECT
------------------------------------------
- The shipped behavior: no route from `queued`+unbound to a terminal state
  -> test_dispatched_child_that_never_reports_can_be_terminalized.
- "Just mark it terminal/applied so the gate shuts up" -> the cancellation must
  never claim a result was produced
  (test_cancelling_never_claims_the_child_produced_a_result), and must not
  release an execution that really is terminal-but-unconsumed
  (test_terminal_but_unapplied_is_not_released_by_the_cancel_path).
- A blanket "terminalize everything" switch -> an execution still inside its own
  deadlines is refused (test_execution_inside_its_deadlines_cannot_be_cancelled).
- Presence-only reason validation -> placeholders and stubs are refused
  (test_reason_must_carry_substance).
- Cross-session contamination -> another session cannot terminalize this
  session's work (test_cancel_refuses_a_foreign_parent_session).
- Shipping the escape only in library code -> the Stop denial itself must name
  the runnable command with this session's real id
  (test_overdue_denial_names_the_runnable_escape).

Run: python3 -m pytest harness/tests/test_execution_terminalize.py -q
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(BIN))

import delegation_hook  # noqa: E402
import execution_cancellation  # noqa: E402
import execution_ledger as ledger_api  # noqa: E402
import execution_reconcile  # noqa: E402
import stop_hook  # noqa: E402
from execution_store import load_trusted, mutate_atomic  # noqa: E402
from would_block_stop import execution_stop_decision  # noqa: E402

UTC = dt.timezone.utc
SESSION = "claude-parent-575f5925"
ROOT_BEAD = "escapement-mn2q-root"
BEAD = "escapement-mn2q"
AGENT = "review-iw8s"
DISPATCH_AT = dt.datetime(2026, 8, 26, 20, 0, tzinfo=UTC)
GOOD_REASON = "child review-iw8s went silent and never emitted a single event"


def _thread(tmp_path: pathlib.Path) -> pathlib.Path:
    thread_dir = tmp_path / "harness" / "threads" / SESSION
    thread_dir.mkdir(parents=True)
    mode = thread_dir / "session_mode.json"
    repo = tmp_path / "repo"
    (repo / ".beads").mkdir(parents=True)
    mode.write_text(
        json.dumps(
            {
                "mode": "task",
                "session_id": SESSION,
                "repo_cwd": str(repo),
                "task_id": BEAD,
                "parent_id": ROOT_BEAD,
            }
        ),
        encoding="utf-8",
    )
    mode.chmod(0o600)
    return thread_dir


def _dispatch(thread_dir: pathlib.Path, tool_use_id: str = "toolu-agent-1") -> str:
    """Register one execution exactly the way the live Agent hook does."""
    result = delegation_hook.pre_tool(
        {
            "session_id": SESSION,
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_use_id": tool_use_id,
            "tool_input": {"name": AGENT, "prompt": "review the ledger change"},
        },
        None,
        thread_dir / "executions.json",
    )
    assert result["decision"] == "allow"
    assert result["reason"] == "dispatch_registered"
    return result["execution_id"]


def _ledger(thread_dir: pathlib.Path) -> dict:
    loaded = load_trusted(thread_dir / "executions.json", SESSION)
    assert loaded is not None, "the ledger must stay trusted and schema-valid"
    return loaded


def _only(ledger: dict) -> dict:
    assert len(ledger["executions"]) == 1
    return ledger["executions"][0]


def _clock(thread_dir: pathlib.Path, **delta) -> dt.datetime:
    """Return a time relative to the registration the hook actually recorded.

    The live hook stamps its deadlines from the wall clock, so the test clock
    must be anchored to the durable `queued_at` rather than to a constant.
    """
    queued_at = _only(_ledger(thread_dir))["queued_at"]
    return dt.datetime.fromisoformat(queued_at.replace("Z", "+00:00")) + dt.timedelta(
        **delta
    )


def _cli(*argv: str) -> int:
    return execution_reconcile.main(list(argv))


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _make_terminal(thread_dir: pathlib.Path, execution_id: str) -> None:
    """Drive a real reporting child to terminal through the public event API."""
    path = thread_dir / "executions.json"

    def report(current: dict) -> dict:
        base = {
            "parent_session_id": SESSION,
            "execution_id": execution_id,
            "attempt": 1,
            "generation": 1,
            "native_child_id": "native-child-1",
        }
        ledger_api.apply_event(
            current, {**base, "kind": "child_bound"}, DISPATCH_AT
        )
        ledger_api.apply_event(
            current, {**base, "kind": "child_started"}, DISPATCH_AT
        )
        ledger_api.apply_event(
            current,
            {
                **base,
                "kind": "child_terminal",
                "terminal_event_id": "terminal-1",
                "terminal_reason": "completed",
                "result_digest": "sha256:deadbeef",
            },
            DISPATCH_AT,
        )
        return current

    mutate_atomic(path, report)


# ---------------------------------------------------------------------------
# The outcome
# ---------------------------------------------------------------------------


def test_dispatched_child_that_never_reports_can_be_terminalized(tmp_path) -> None:
    """A silent child must not wedge its parent session forever."""
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    overdue = _clock(thread_dir, hours=3)

    stuck = _only(_ledger(thread_dir))
    assert (stuck["state"], stuck["native_child_id"]) == ("queued", None)
    assert execution_stop_decision(
        "closed", _ledger(thread_dir), None, [], overdue
    ) == ("block", "delegated_execution_overdue")

    assert (
        _cli(
            "cancel",
            "--session",
            SESSION,
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--execution-id",
            execution_id,
            "--reason",
            GOOD_REASON,
            "--now",
            _iso(overdue),
        )
        == 0
    )

    assert execution_stop_decision(
        "closed", _ledger(thread_dir), None, [], overdue
    ) == ("allow", "delegated_outcome_complete")


def test_cancellation_is_audited_with_actor_reason_and_crossed_deadline(
    tmp_path,
) -> None:
    """A recovery must be reasoned and reviewable, not a silent flag flip."""
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    overdue = _clock(thread_dir, hours=3)

    _cli(
        "cancel",
        "--session",
        SESSION,
        "--ledger-path",
        str(thread_dir / "executions.json"),
        "--execution-id",
        execution_id,
        "--reason",
        GOOD_REASON,
        "--actor",
        "operator-alex",
        "--now",
        _iso(overdue),
    )

    incidents = [
        entry
        for entry in _ledger(thread_dir)["incidents"]
        if entry.get("type") == "unreported_child_cancelled"
    ]
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["execution_id"] == execution_id
    assert incident["actor"] == "operator-alex"
    assert incident["reason"] == GOOD_REASON
    assert incident["overdue_basis"] == "hard"
    assert incident["state_before"] == "queued"


def test_cancelling_never_claims_the_child_produced_a_result(tmp_path) -> None:
    """Cancelled means 'no result will arrive', never 'the work was done'."""
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    overdue = _clock(thread_dir, hours=3)

    _cli(
        "cancel",
        "--session",
        SESSION,
        "--ledger-path",
        str(thread_dir / "executions.json"),
        "--execution-id",
        execution_id,
        "--reason",
        GOOD_REASON,
        "--now",
        _iso(overdue),
    )

    item = _only(_ledger(thread_dir))
    assert item["state"] == "cancelled"
    assert item["result_digest"] is None
    assert item["result_application"]["state"] == "unapplied"
    # The result-application claim is the gate that guards "was this consumed".
    # A cancellation must never become claimable as a consumable result.
    assert (
        ledger_api.claim_result_application(
            _ledger(thread_dir),
            execution_id,
            overdue,
            "owner-1",
            60,
            attempt=1,
            generation=1,
        )
        is None
    )


def test_cancel_is_idempotent_and_records_one_incident(tmp_path) -> None:
    """A retried recovery must not stack duplicate audit evidence."""
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    overdue = _clock(thread_dir, hours=3)
    argv = (
        "cancel",
        "--session",
        SESSION,
        "--ledger-path",
        str(thread_dir / "executions.json"),
        "--execution-id",
        execution_id,
        "--reason",
        GOOD_REASON,
        "--now",
        _iso(overdue),
    )

    assert _cli(*argv) == 0
    assert _cli(*argv) == 0

    ledger = _ledger(thread_dir)
    assert _only(ledger)["state"] == "cancelled"
    assert (
        len(
            [
                entry
                for entry in ledger["incidents"]
                if entry.get("type") == "unreported_child_cancelled"
            ]
        )
        == 1
    )


# ---------------------------------------------------------------------------
# The safety properties a blanket "mark everything done" would destroy
# ---------------------------------------------------------------------------


def test_terminal_but_unapplied_is_not_released_by_the_cancel_path(
    tmp_path, capsys
) -> None:
    """Terminal is not applied. Cancelling must not launder an unconsumed result."""
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    _make_terminal(thread_dir, execution_id)
    later = _clock(thread_dir, hours=3)

    assert execution_stop_decision(
        "closed", _ledger(thread_dir), None, [], later
    ) == ("block", "delegated_execution_unresolved")

    assert (
        _cli(
            "cancel",
            "--session",
            SESSION,
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--execution-id",
            execution_id,
            "--reason",
            "the result was awkward to consume so cancel it away instead",
            "--now",
            _iso(later),
        )
        == 2
    )

    # The refusal must say WHY. Telling an operator that a finished child
    # "has not crossed its deadline" would send them off to wait instead of
    # to consume the result that is actually sitting there.
    refusal = capsys.readouterr().err
    assert "terminal evidence" in refusal
    assert "deadline" not in refusal

    item = _only(_ledger(thread_dir))
    assert item["state"] == "terminal"
    assert item["result_application"]["state"] == "unapplied"
    assert execution_stop_decision(
        "closed", _ledger(thread_dir), None, [], later
    ) == ("block", "delegated_execution_unresolved")


def _bind_and_start(thread_dir: pathlib.Path, execution_id: str, at: dt.datetime) -> None:
    """Bind a running child exactly as the Agent PostToolUse adapter does."""

    def observe(current: dict) -> dict:
        base = {
            "parent_session_id": SESSION,
            "execution_id": execution_id,
            "attempt": 1,
            "generation": 1,
            "native_child_id": "native-live-child",
        }
        ledger_api.apply_event(current, {**base, "kind": "child_bound"}, at)
        ledger_api.apply_event(current, {**base, "kind": "child_started"}, at)
        return current

    mutate_atomic(thread_dir / "executions.json", observe)


def test_a_bound_running_child_cannot_be_cancelled_as_unreported(
    tmp_path, capsys
) -> None:
    """A slow child is not a dead child, and the record must not say it is.

    Nothing emits `activity_completed` for a subagent, so a child's idle
    deadline measures elapsed time rather than silence. EVERY agent that works
    longer than IDLE_SECONDS crosses it while perfectly healthy — this is the
    common case, not the rare one. Cancelling there would write "no result will
    arrive" into the durable ledger about a child that is still writing, which
    is worse than the wedge it relieves: a false audit record that later reads
    as fact.

    `idle` is the only basis reachable here: a running execution is never
    overdue by `start`, so this guard is exactly "idle is not death".
    """
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    _bind_and_start(thread_dir, execution_id, _clock(thread_dir, seconds=1))
    # Past the idle deadline, but well inside the two-hour hard budget.
    overdue = _clock(thread_dir, minutes=20)

    assert (
        _cli(
            "cancel",
            "--session",
            SESSION,
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--execution-id",
            execution_id,
            "--reason",
            "this child has been quiet a while so I assume it is gone",
            "--now",
            _iso(overdue),
        )
        == 2
    )

    refusal = capsys.readouterr().err
    assert "bound and running" in refusal
    assert "hard deadline" in refusal, "the refusal must name when it WOULD be allowed"

    item = _only(_ledger(thread_dir))
    assert item["state"] == "running"
    assert _ledger(thread_dir)["incidents"] == []


def test_a_bound_running_child_is_still_cancellable_after_its_hard_deadline(
    tmp_path,
) -> None:
    """The refusal is a delay, not a dead end.

    A child that binds, starts, and then dies without ever reporting is real
    (it is what wedged session 575f5925). Once its full budget has elapsed,
    "no result will arrive" is supportable again and recovery must work.
    """
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    _bind_and_start(thread_dir, execution_id, _clock(thread_dir, seconds=1))
    past_hard = _clock(thread_dir, hours=3)

    assert (
        _cli(
            "cancel",
            "--session",
            SESSION,
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--execution-id",
            execution_id,
            "--reason",
            "child bound and started but never reported across its whole budget",
            "--now",
            _iso(past_hard),
        )
        == 0
    )

    item = _only(_ledger(thread_dir))
    assert item["state"] == "cancelled"
    assert item["result_digest"] is None
    incident = _ledger(thread_dir)["incidents"][0]
    assert incident["overdue_basis"] == "hard"
    assert incident["state_before"] == "running"


def test_an_unbound_child_is_still_cancellable_at_its_start_deadline(
    tmp_path,
) -> None:
    """The live-child guard must not close the door on the never-bound case.

    An execution the host never bound has no evidence of a child at all, so a
    crossed start deadline remains honest grounds to say no result is coming.
    """
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)

    assert (
        _cli(
            "cancel",
            "--session",
            SESSION,
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--execution-id",
            execution_id,
            "--reason",
            "the host never bound a native child for this dispatch at all",
            "--now",
            _iso(_clock(thread_dir, minutes=3)),
        )
        == 0
    )

    assert _only(_ledger(thread_dir))["state"] == "cancelled"


def test_the_overdue_denial_separates_the_slow_child_from_the_dead_one() -> None:
    """The denial must not send an agent to cancel a live child.

    Its previous wording offered `cancel` for "if the child died" with no way
    to tell whether it had, while `list` output is exactly what distinguishes
    the two. A denial that names a remedy the gate will refuse is the failure
    mode this repo keeps hitting.
    """
    message = stop_hook._TASK_MODE_DISPLAY["delegated_execution_overdue"]
    lowered = message.lower()

    assert "native_child_id" in message, "the agent must be told what to look at"
    assert "do not cancel it" in lowered
    assert "slow, not dead" in lowered
    assert "terminalizes itself" in lowered
    # It must not promise a remedy whose availability this gate cannot see.
    assert "schedulewakeup" not in lowered.replace(" ", "")


def test_execution_inside_its_deadlines_cannot_be_cancelled(tmp_path) -> None:
    """Recovery is for demonstrably overdue work, not for any inconvenient child."""
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    fresh = _clock(thread_dir, seconds=30)

    assert (
        _cli(
            "cancel",
            "--session",
            SESSION,
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--execution-id",
            execution_id,
            "--reason",
            "I would simply prefer this dispatched child to be over already",
            "--now",
            _iso(fresh),
        )
        == 2
    )
    assert _only(_ledger(thread_dir))["state"] == "queued"


@pytest.mark.parametrize(
    "damage",
    [
        {"hard_deadline": "not-a-timestamp"},
        {"hard_deadline": "2026-08-09T22:00:00"},  # naive, no offset
        {"hard_deadline": None},
        # An unreadable START deadline, with the hard deadline still in front
        # of us so it cannot legitimately supply the answer instead.
        {"start_deadline": "later on", "hard_deadline": "2031-01-01T00:00:00Z"},
    ],
    ids=["garbage", "naive", "null", "prose-start"],
)
def test_an_unreadable_deadline_is_never_evidence_of_being_overdue(damage) -> None:
    """`overdue_reason` is the eligibility check the whole recovery hangs on.

    It must fail closed in the direction that matters HERE, which is the
    opposite of the Stop gate's: the Stop gate blocks when it cannot read a
    deadline, but an unreadable deadline must never license terminalizing a
    child. A shared helper that fails closed in only one direction would be
    wrong for one of its two callers.
    """
    item = {
        "state": "queued",
        "reconcile_due": None,
        "start_deadline": "2026-08-09T20:02:00Z",
        "idle_deadline": "2026-08-09T20:20:00Z",
        "hard_deadline": "2026-08-09T22:00:00Z",
        **damage,
    }
    far_future = dt.datetime(2030, 1, 1, tzinfo=UTC)
    assert execution_cancellation.overdue_reason(item, far_future) is None


@pytest.mark.parametrize(
    "reason",
    [
        "",
        "   ",
        "tbd",
        "n/a",
        "unknown",
        "dead",
        "child died",
        "it did not report",
        # Padded null answers clear the character floor on length alone; the
        # floor is not the whole bar.
        "tbd tbd tbd tbd tbd tbd",
        "n/a, n/a, n/a, n/a, n/a, n/a",
        "unknown - unknown - unknown",
    ],
    ids=[
        "empty",
        "whitespace",
        "tbd",
        "n-a",
        "unknown",
        "dead",
        "too-short",
        "nineteen-chars",
        "padded-tbd",
        "padded-n-a",
        "padded-unknown",
    ],
)
def test_reason_must_carry_substance(tmp_path, reason) -> None:
    """The reason is the audit record; a placeholder is not a rationale."""
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    overdue = _clock(thread_dir, hours=3)

    assert (
        _cli(
            "cancel",
            "--session",
            SESSION,
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--execution-id",
            execution_id,
            "--reason",
            reason,
            "--now",
            _iso(overdue),
        )
        == 2
    )
    ledger = _ledger(thread_dir)
    assert _only(ledger)["state"] == "queued"
    assert ledger["incidents"] == []


def test_cancel_refuses_a_foreign_parent_session(tmp_path) -> None:
    """One session's recovery must never terminalize another session's work."""
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    overdue = _clock(thread_dir, hours=3)

    assert (
        _cli(
            "cancel",
            "--session",
            "some-other-session",
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--execution-id",
            execution_id,
            "--reason",
            GOOD_REASON,
            "--now",
            _iso(overdue),
        )
        == 2
    )
    assert _only(_ledger(thread_dir))["state"] == "queued"


def test_cancel_refuses_an_unknown_execution(tmp_path) -> None:
    """An unresolved execution identity is never a licence to mutate the ledger."""
    thread_dir = _thread(tmp_path)
    _dispatch(thread_dir)
    overdue = _clock(thread_dir, hours=3)

    assert (
        _cli(
            "cancel",
            "--session",
            SESSION,
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--execution-id",
            "exec-that-does-not-exist",
            "--reason",
            GOOD_REASON,
            "--now",
            _iso(overdue),
        )
        == 2
    )
    assert _only(_ledger(thread_dir))["state"] == "queued"


# ---------------------------------------------------------------------------
# Discoverability: the escape has to live IN the denial
# ---------------------------------------------------------------------------


def test_list_reports_what_is_stuck_and_why(tmp_path, capsys) -> None:
    """An agent must be able to find the execution id without reading the file."""
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)
    overdue = _clock(thread_dir, hours=3)

    assert (
        _cli(
            "list",
            "--session",
            SESSION,
            "--ledger-path",
            str(thread_dir / "executions.json"),
            "--now",
            _iso(overdue),
        )
        == 0
    )

    listed = json.loads(capsys.readouterr().out)
    assert listed["parent_session_id"] == SESSION
    assert listed["executions"] == [
        {
            "agent_name": AGENT,
            "attempt": 1,
            "bead_id": BEAD,
            "execution_id": execution_id,
            "generation": 1,
            "native_child_id": None,
            "overdue": "hard",
            "result_application": "unapplied",
            "state": "queued",
        }
    ]


def test_cancel_reason_reaches_the_half_life_signal_corpus(
    tmp_path, monkeypatch
) -> None:
    """Rule 2 of gate-design: a waiver reason that only lives in one thread's
    ledger cannot be counted, and a gate whose waivers cannot be counted cannot
    be revised. Half-life review reads `.gate-signal.jsonl` and nothing else.
    """
    beads = tmp_path / ".beads"
    beads.mkdir()
    monkeypatch.setenv("BEADS_DIR", str(beads))
    thread_dir = _thread(tmp_path)
    execution_id = _dispatch(thread_dir)

    _cli(
        "cancel",
        "--session",
        SESSION,
        "--ledger-path",
        str(thread_dir / "executions.json"),
        "--execution-id",
        execution_id,
        "--reason",
        GOOD_REASON,
        "--now",
        _iso(_clock(thread_dir, hours=3)),
    )

    lines = (beads / ".gate-signal.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["gate"] == "continuation-harness"
    assert record["decision"] == "waiver-accepted"
    assert record["session_id"] == SESSION
    assert record["extras"]["waiver_reason"] == GOOD_REASON
    assert record["extras"]["execution_id"] == execution_id
    assert record["extras"]["bead_id"] == BEAD


def _public_stop_output(monkeypatch, capsys, tmp_path) -> str:
    """Drive the real Stop hook to an overdue delegated block and capture it."""
    thread_dir = _thread(tmp_path)
    _dispatch(thread_dir)
    (thread_dir / "scheduled.json").write_text("[]", encoding="utf-8")
    (thread_dir / "scheduled.json").chmod(0o600)

    # The Stop hook reads the real wall clock, so make the child genuinely
    # overdue the way the supervisor does: run the public deadline
    # reconciliation forward. No fixture surgery on the deadline fields.
    def age(current: dict) -> dict:
        ledger_api.reconcile_deadlines(current, dt.datetime.now(UTC) + dt.timedelta(hours=3))
        return current

    mutate_atomic(thread_dir / "executions.json", age)

    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    bd = fakebin / "bd"
    bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = tuple(a for a in sys.argv[1:] if a != '--json')\n"
        "if args[:1] == ('show',):\n"
        f"    print(json.dumps([{{'id': {ROOT_BEAD!r}, 'status': 'in_progress'}}]))\n"
        "elif args[:1] in (('ready',), ('blocked',)):\n"
        "    print('[]')\n"
        "else:\n"
        "    raise SystemExit(1)\n",
        encoding="utf-8",
    )
    bd.chmod(0o755)

    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(stop_hook, "HARNESS_ROOT", tmp_path / "harness")
    monkeypatch.setattr(
        stop_hook, "INCIDENTS_LOG", tmp_path / "harness" / "incidents.jsonl"
    )
    monkeypatch.setattr(stop_hook.session_isolation, "write_checkout", lambda *a: None)
    monkeypatch.setattr(
        stop_hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": SESSION, "transcript_path": ""})),
    )
    assert stop_hook.main() == 0
    return capsys.readouterr().out


def test_overdue_denial_names_the_runnable_escape(monkeypatch, capsys, tmp_path) -> None:
    """The whole failure was that the escape existed only in library code.

    The denial the agent actually receives must carry a command it can run as
    written, bound to THIS session — not a bare reason code, and not a
    placeholder the agent has to resolve by reading the harness source.
    """
    output = _public_stop_output(monkeypatch, capsys, tmp_path)
    block = json.loads(output.strip().splitlines()[-1])

    assert block["decision"] == "block"
    message = block["reason"]
    assert "delegated_execution_overdue" in message
    assert "execution_reconcile.py list" in message
    assert "execution_reconcile.py cancel" in message
    assert "--execution-id" in message and "--reason" in message
    # Bound to the real session, with no unresolved template left behind.
    assert f"--session {SESSION}" in message
    assert "{session_id}" not in message
    # It must not read as permission to fake a result.
    assert "hand-edit executions.json" in message


def test_unresolved_denial_preserves_the_terminal_versus_applied_distinction() -> None:
    """The result-application escape must not be reducible to a reason string.

    `apply_verified_result` is emphatic that the business oracle is never
    substituted by the terminal state or the result digest. If the denial
    offered `execution_reconcile cancel` as the fix for an unconsumed result,
    that constraint would be dead in practice.
    """
    message = stop_hook._TASK_MODE_DISPLAY["delegated_execution_unresolved"]
    lowered = message.lower()
    assert "apply_verified_result" in message
    assert "claim_result_application" in message
    assert "terminal is not applied" in lowered
    assert "not substitutes" in lowered or "are not substitutes" in lowered


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
