"""Gate behaviour for the verdict-capture feature (escapement-1l04).

Split out of `test_review_gate.py`, which crossed the repo's 1000-line hard
limit as these rules were built. The split is by responsibility, not by size:
everything here concerns ONE feature — the reviewer's own returned text being
what the record holds, and a blocking verdict refusing the close — which ships
behind a single flag and was dark for four merges before being enabled.

Shared case builders live in `_review_gate_case.py`.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[1]
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _review_ledger  # noqa: E402
import _review_record  # noqa: E402
import review_gate  # noqa: E402

from _review_gate_case import (  # noqa: E402
    FINGERPRINT,
    SUBSTANTIVE,
    make_record as _record,
    run_close as _close,
    run_dispatch as _dispatch,
)


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path):
    """Point the dispatch ledger at a per-test temp dir."""
    with patch.object(_review_ledger, "LEDGER_DIR", tmp_path / "ledger"):
        yield




# ---------------------------------------------------------------------------
# escapement-1l04 — the recorded findings must be the reviewer's own output
# ---------------------------------------------------------------------------

class TestVerdictBinding:
    """The bypass that survived every earlier hardening pass.

    Dispatch a genuinely isolated `escapement:adversarial-reviewer` naming the
    bead, never read its answer, write your own >=120-character verdict, close.
    `independent: true` was granted for the DISPATCH — recorded at `Agent`
    PreToolUse, before the subagent emitted a single token — so the reviewer's
    verdict, or its total failure to produce one, was never consulted.

    These are the controls for that. Each one must FAIL to close.
    """

    def test_verdict_written_by_the_implementer_is_refused(self):
        """The whole bead in one control.

        A dispatch happened (so `independent` is legitimately true) but the
        findings are the implementer's own prose. If this ever passes again,
        the gate has gone back to certifying that a reviewer was *summoned*
        rather than that a reviewer was *read*.
        """
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(
                record=_record(verdict_source="implementer")
            )
        assert decision is not None, "an unread reviewer must not close the bead"
        assert decision["permissionDecision"] == "deny"
        reason = decision["permissionDecisionReason"]
        assert "reviewer" in reason.lower()
        assert "REVIEW_WAIVER" in reason, "every deny must carry its escape"

    def test_verdict_missing_entirely_is_refused(self):
        """The reviewer crashed, or returned nothing, and nobody noticed."""
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(
                record=_record(verdict_source=None, verdict_digest=None)
            )
        assert decision["permissionDecision"] == "deny"

    def test_captured_verdict_closes_normally(self):
        """Positive control: the honest path must stay frictionless."""
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, signal = _close(record=_record())
        assert decision is None
        assert signal.call_args.kwargs["decision"] == "allow"

    def test_blocking_verdict_at_an_unchanged_tree_is_refused(self):
        """Re-review-after-repair, expressed through the existing fingerprint.

        The reviewer said BLOCK. The fingerprint on the record equals the
        current one, so *nothing has changed since it said so* — the blockers
        cannot have been addressed. The complementary case (the code did
        change) is already refused by the staleness rule, so between the two a
        blocking verdict forces both a repair and a fresh review.
        """
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=_record(blocking=True))
        assert decision is not None, "unaddressed blockers must not close"
        assert decision["permissionDecision"] == "deny"
        assert "blocking" in decision["permissionDecisionReason"].lower()

    def test_blocking_verdict_closes_after_repair_and_a_fresh_verdict(self):
        """Positive control for the repair loop.

        Repair moved the tree to a new fingerprint and a re-review recorded a
        non-blocking verdict there. That must close — a gate that cannot be
        satisfied after the work is fixed is a dead end, not a gate.
        """
        repaired = "b" * 64
        _, decision, _ = _close(
            record=_record(blocking=False, fingerprint=repaired),
            fingerprint=repaired,
        )
        assert decision is None

    def test_blocking_verdict_can_still_be_waived_with_a_reason(self):
        """Rule 1: the escape path has to actually work on this branch too."""
        _, decision, signal = _close(
            command=(
                'REVIEW_WAIVER="reviewer flagged a pre-existing defect that '
                'is tracked separately as escapement-mn2q" bd close escapement-abc1'
            ),
            record=_record(blocking=True),
        )
        assert decision is None
        assert signal.call_args.kwargs["decision"] == "allow"

    def test_verdict_schema_predating_this_gate_is_refused_accurately(self):
        """Migration: a v1 record must not be described as "no review".

        v1 records were written under an oracle that never looked at the
        reviewer's output. Honouring them would grandfather in exactly the
        evidence this bead exists to stop accepting. But the denial has to say
        *which* thing is wrong: telling an agent that no review exists, when
        one plainly does, sends it to re-run a reviewer without ever
        explaining why the first one stopped counting.
        """
        stale_schema = _record()
        stale_schema["v"] = 1
        stale_schema.pop("verdict_source", None)
        _, decision, _ = _close(record=stale_schema)
        assert decision["permissionDecision"] == "deny"
        reason = decision["permissionDecisionReason"]
        assert "no independent review is on record" not in reason.lower(), (
            "a v1 record is an OUTDATED review, not an absent one"
        )
        assert "re-record" in reason.lower() or "record" in reason.lower()

    def test_codex_shape_skips_verdict_capture_rather_than_failing(self):
        """A check we cannot run must not read as a check that failed.

        Codex exposes no `Agent` event at all, so no verdict can ever be
        captured there. Denying on that would make the gate unsatisfiable on
        one of the two supported hosts.
        """
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(
                record=_record(independent="unverified", verdict_source=None),
                session_id=None,
            )
        assert decision is None


class TestVerdictChainEndToEnd:
    """The whole path with nothing between the reviewer and the close mocked.

    Three real defects in this gate shipped green because the tests mocked
    exactly the boundary that was broken: `read_record` and `work_fingerprint`
    here, `write_record` in test_review_record.py. Every assertion above this
    class sits above at least one of those mocks, so a regression in the
    PreToolUse -> PostToolUse -> record -> close chain — the ledger never being
    updated, the verdict binding to the wrong dispatch entry, the record's
    schema version drifting out of the readable set — would keep them all green
    while the shipped gate stopped delivering the outcome.

    So here the ledger is a real directory, the record is built by the real
    recording CLI from what the real ledger holds, and only `bd` itself is
    replaced. The question each test asks is the user-facing one: after this
    exact sequence of events, does the bead close?
    """

    BLOCKING_VERDICT = (
        "BLOCKER: the dispatch ledger is written at PreToolUse, so the "
        "reviewer's verdict is never consulted. An implementer can dispatch a "
        "real reviewer, ignore it, and write its own findings. Also: "
        "--reviewer is never validated against the allowlist."
    )
    CLEAN_VERDICT = (
        "Read the close path against the bead's acceptance criteria and traced "
        "the ledger through PreToolUse and PostToolUse. No blockers: the "
        "captured verdict is what reaches the record, and the fingerprint "
        "comparison rejects a reviewer that read an earlier state."
    )

    _agent_counter = 0

    def _next_agent_id(self):
        type(self)._agent_counter += 1
        return type(self)._agent_counter

    @staticmethod
    def _event(payload):
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
             patch("sys.stdout", io.StringIO()):
            review_gate.main()

    def _run_chain(self, verdict, *, capture=True, bead="escapement-abc1",
                   fingerprint=FINGERPRINT):
        """Dispatch a reviewer, optionally let it answer, then record.

        Returns the record the CLI actually wrote, or None if it wrote none.
        """
        import escapement_review as cli

        tool_input = {
            "subagent_type": "escapement:adversarial-reviewer",
            "description": f"review {bead}",
            "prompt": f"Review the implementation of {bead}.",
        }
        agent_id = f"agent-{self._next_agent_id()}"
        _dispatch(tool_input, fingerprint=fingerprint)

        if capture and verdict is not None:
            # The REAL two-event sequence escapement-g27c established.
            # PostToolUse supplies the join key only — for a background
            # dispatch its tool_response carries no reply at all, and its
            # `prompt` field echoes the dispatcher's own text. The verdict
            # arrives on SubagentStop, which carries no tool_input and so
            # cannot name the bead itself.
            self._event({
                "hook_event_name": "PostToolUse",
                "tool_name": "Agent",
                "session_id": "s1",
                "tool_input": tool_input,
                "tool_response": {
                    "agentId": agent_id,
                    "prompt": tool_input["prompt"],
                },
                "cwd": "/tmp",
            })
            self._event({
                "hook_event_name": "SubagentStop",
                "session_id": "s1",
                "agent_id": agent_id,
                "last_assistant_message": verdict,
                "cwd": "/tmp",
            })

        written = []
        with patch.object(cli, "write_record",
                          side_effect=lambda b, r, cwd=None: written.append(r) or True), \
             patch.object(cli, "work_fingerprint", return_value=fingerprint), \
             patch.dict("os.environ", {"CLAUDE_SESSION_ID": "s1"}):
            cli.main([
                "record", "--bead", bead,
                "--findings", "I read the reviewer's output and it looked fine "
                              "to me, so I am recording that the work is good "
                              "and ready to land without further changes.",
            ])
        return written[0] if written else None

    def test_verdict_the_reviewer_actually_returned_is_what_gets_stored(self):
        record = self._run_chain(self.CLEAN_VERDICT)
        assert record["findings"] == self.CLEAN_VERDICT
        assert record["verdict_source"] == "captured"
        assert "looked fine to me" in (record["response"] or ""), (
            "the implementer's own account should survive as advisory text"
        )
        assert record["reviewer"] == "escapement:adversarial-reviewer"

    def test_clean_captured_verdict_closes_the_bead(self):
        record = self._run_chain(self.CLEAN_VERDICT)
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=record)
        assert decision is None, "an honestly reviewed bead must still close"

    def test_reviewer_findings_ignored_by_the_implementer_do_not_close(self):
        """The bead's acceptance criterion, end to end.

        A real isolated reviewer ran and said BLOCKER. The implementer recorded
        its own cheerful summary anyway. The close must be refused, and refused
        because of what the REVIEWER said — not because of anything the
        implementer typed.
        """
        record = self._run_chain(self.BLOCKING_VERDICT)
        assert record["blocking"] is True, (
            "the reviewer's own text must drive the classification"
        )
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=record)
        assert decision is not None
        assert decision["permissionDecision"] == "deny"
        assert "blocking" in decision["permissionDecisionReason"].lower()

    def test_dispatch_with_no_answer_is_not_a_review(self):
        """The reviewer never returned — crashed, aborted, or still running.

        Before this bead, this was indistinguishable from a completed clean
        review, because the ledger entry that vouched for it was written before
        the subagent started.
        """
        record = self._run_chain(None, capture=False)
        assert record["verdict_source"] is None
        assert record["independent"] is True, (
            "a dispatch DID happen; that fact is not what is in doubt"
        )
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=record)
        assert decision["permissionDecision"] == "deny"
        assert "never captured" in decision["permissionDecisionReason"]

    def test_the_repair_loop_actually_terminates(self):
        """Blocked -> repair -> re-review -> closes. The full cycle.

        A repair is not just "a second review"; it MOVES THE FINGERPRINT,
        because changing the code is what a repair is. That is what retires the
        first reviewer's blocking entry — it no longer corroborates anything at
        the new tree state — and it is why the blocking rule needs no expiry of
        its own.

        This test originally simulated the repair without moving the
        fingerprint, and the concurrent-reviewer fix (blocking outranks clean)
        correctly broke it: at one unchanged tree state, an earlier BLOCK is
        not superseded by a later clean opinion. Two reviews at the same
        fingerprint are second opinions; two reviews at different fingerprints
        are a repair loop. Only the second one closes.
        """
        blocked = self._run_chain(self.BLOCKING_VERDICT)
        assert blocked["blocking"] is True
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=blocked)
        assert decision["permissionDecision"] == "deny"

        repaired_fp = "b" * 64
        after = self._run_chain(self.CLEAN_VERDICT, fingerprint=repaired_fp)
        assert after["findings"] == self.CLEAN_VERDICT
        assert after["blocking"] is False
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=after, fingerprint=repaired_fp)
        assert decision is None, (
            "a gate that cannot be satisfied after the work is fixed is a dead "
            "end, not a gate"
        )

    def test_a_later_clean_opinion_does_not_retire_a_blocker(self):
        """Same tree, two reviewers, one blocking — the blocker still stands.

        The negative half of the test above. Without this, "run a second
        reviewer until one of them likes it" replaces "fix what the first one
        found", and no code has to change for the close to succeed.
        """
        self._run_chain(self.BLOCKING_VERDICT)
        second = self._run_chain(self.CLEAN_VERDICT)
        assert second["blocking"] is True, (
            "a second opinion at the same tree state does not overrule a BLOCK"
        )
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=second)
        assert decision["permissionDecision"] == "deny"

    def test_the_record_written_is_readable_by_the_gate(self):
        """Guards the version bump against a one-sided edit.

        `RECORD_VERSION` is what the CLI writes and `READABLE_RECORD_VERSIONS`
        is what the gate will parse. Bumping the first without the second makes
        every fresh record unreadable, and the gate would then tell every agent
        in the repository that no review is on record — with all unit tests
        still green, because they construct their records by hand.
        """
        record = self._run_chain(self.CLEAN_VERDICT)
        assert record["v"] == _review_record.RECORD_VERSION
        assert record["v"] in _review_record.READABLE_RECORD_VERSIONS


class TestTheCaptureFeatureIsGatedAsOneUnit:
    """A known-broken classifier must not be able to deny anything.

    `VERDICT_CAPTURE_SUPPORTED` gates the traceability rule, and the gate's
    docstring says the verdict-capture feature is off pending escapement-g27c.
    But the BLOCKING deny was not gated by it, while `record_verdict` runs at
    `Agent` PostToolUse unconditionally. So half the feature shipped live:

      capture works -> record_verdict runs -> classify_blocking sets blocking
      -> evaluate_close denies, with the flag still False

    That is not hypothetical. escapement-1nzm establishes that the classifier
    returns True for a clean PASS on the format `adversarial-reviewer.md`
    mandates, so the first path this reached would have denied an honest close
    with a denial its own remedy cannot clear ("fix what the reviewer flagged"
    when nothing was flagged, at an unchanged fingerprint that re-recording
    cannot move). REVIEW_WAIVER as the only exit, by the honest path.

    Capture, classification, and the blocking deny are one feature and are now
    gated as one. Flipping the flag turns them all on together, which is also
    what makes the flag's documented meaning true.
    """

    def test_a_blocking_verdict_does_not_deny_while_capture_is_off(self):
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", False):
            _, decision, _ = _close(record=_record(blocking=True))
        assert decision is None, (
            "an ungated blocking deny lets an unproven classifier refuse work"
        )

    def test_a_blocking_verdict_denies_once_capture_is_on(self):
        """The control: gating must not become quiet deletion."""
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=_record(blocking=True))
        assert decision["permissionDecision"] == "deny"
        assert "blocking" in decision["permissionDecisionReason"].lower()

    def test_the_gate_docstring_matches_what_is_actually_enforced(self):
        """Capability honesty, asserted rather than promised — in BOTH directions.

        The docstring is what an agent reads to learn what this gate does, so
        it has to track the flag. Written to FOLLOW the flag rather than to pin
        one state: the first version hard-asserted the disabled wording and went
        red the moment the feature was legitimately enabled. A test that
        punishes correct behaviour gets deleted by whoever it blocks, and then
        the property it guarded is unguarded — so it now asserts the
        correspondence whichever way the flag points.

        Over-claiming is the dangerous direction, but under-claiming has a real
        cost too: an agent told the gate is inert will not bother recording a
        verdict properly.
        """
        doc = review_gate.__doc__ or ""
        if review_gate.VERDICT_CAPTURE_SUPPORTED:
            assert "ENFORCED AS OF" in doc, (
                "capture is ON; the docstring must not still say it is pending"
            )
            assert "NOT ENFORCED YET" not in doc
        else:
            assert "NOT ENFORCED YET" in doc, (
                "capture is OFF; the docstring must say so rather than "
                "describing a rule that cannot fire"
            )
            assert "blocking" in doc.split("NOT ENFORCED YET")[1].lower(), (
                "the blocking rule is gated with capture; it must not sit "
                "under ENFORCED NOW while the flag is False"
            )


class TestALateBlockingVerdictIsStillConsulted:
    """escapement-a7is — `evaluate_close` read a snapshot, not the evidence.

    `record["blocking"]` is frozen at the moment `escapement_review record`
    runs. The ledger keeps accumulating afterwards. So a reviewer that returns
    BLOCK *after* the record is written was never consulted again, the
    fingerprint is unchanged so staleness cannot fire, and the close succeeds
    with an unread blocker sitting in the ledger.

    The verdict-capture wiring makes this the NORMAL case rather than an exotic
    one: a backgrounded reviewer's text arrives at SubagentStop, asynchronously,
    and nothing orders that against when the implementer chooses to record. The
    original bug was "the ledger is written before the reviewer speaks"; this is
    the same temporal blind spot moved to "the record is sealed before the
    reviewer speaks".

    Fix under test: re-read the ledger at close time and refuse if it NOW shows
    a blocking verdict for this bead at this fingerprint.
    """

    def _ledger_with_blocking_verdict(self, fingerprint=FINGERPRINT):
        _review_ledger.record_dispatch(
            "s1", ["escapement-abc1"], "escapement:adversarial-reviewer", fingerprint
        )
        _review_ledger.record_agent_id(
            "s1", ["escapement-abc1"], "escapement:adversarial-reviewer", "agent-late"
        )
        _review_ledger.record_subagent_verdict(
            "s1", "agent-late",
            "BLOCKER: the close path never re-reads the ledger after recording.",
        )

    def test_a_blocker_arriving_after_the_record_still_refuses_the_close(self):
        """The record says clean; the ledger says blocked. Evidence wins."""
        self._ledger_with_blocking_verdict()
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=_record(blocking=False))
        assert decision is not None, (
            "a blocking verdict that landed after the record must not be "
            "invisible just because the record was sealed first"
        )
        assert decision["permissionDecision"] == "deny"
        assert "blocking" in decision["permissionDecisionReason"].lower()

    def test_a_clean_ledger_does_not_invent_a_blocker(self):
        """Positive control: re-reading must not become a second denial source."""
        _review_ledger.record_dispatch(
            "s1", ["escapement-abc1"], "escapement:adversarial-reviewer", FINGERPRINT
        )
        _review_ledger.record_agent_id(
            "s1", ["escapement-abc1"], "escapement:adversarial-reviewer", "agent-ok"
        )
        _review_ledger.record_subagent_verdict(
            "s1", "agent-ok",
            "No blockers. The fingerprint comparison rejects the stale case.",
        )
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=_record(blocking=False))
        assert decision is None

    def test_a_blocker_at_a_different_fingerprint_does_not_refuse(self):
        """After a repair the tree moved, so the old blocker is retired.

        Without this the repair loop would never terminate: the blocking entry
        would outlive the fix that addressed it and deny forever.
        """
        self._ledger_with_blocking_verdict(fingerprint="old" + "0" * 61)
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", True):
            _, decision, _ = _close(record=_record(blocking=False))
        assert decision is None

    def test_the_late_blocker_check_is_gated_with_the_rest_of_capture(self):
        """Consistency with escapement-1nzm: an unproven classifier may not deny."""
        self._ledger_with_blocking_verdict()
        with patch.object(review_gate, "VERDICT_CAPTURE_SUPPORTED", False):
            _, decision, _ = _close(record=_record(blocking=False))
        assert decision is None


class TestTheGateActuallyEnforcesTheOutcomeNow:
    """escapement-1l04's headline outcome, with the feature switched ON.

    Every other test in this file that exercises the capture rules patches
    `VERDICT_CAPTURE_SUPPORTED` to True, because for the whole life of this
    work the shipped value was False and the rules were dark. That is exactly
    the shape that lets a flag flip ship a feature nobody has run: the tests
    prove the rules work *when enabled*, and nothing proves they are enabled.

    These read the MODULE-LEVEL value with no patch. If someone sets the flag
    back to False, or lands a new deny path that only fires under a patch,
    these fail.
    """

    def test_the_capture_feature_is_actually_on(self):
        assert review_gate.VERDICT_CAPTURE_SUPPORTED is True, (
            "the traceability and blocking rules are inert while this is False"
        )

    def test_an_unread_reviewer_cannot_close_the_bead(self):
        """THE BEAD, unpatched.

        A reviewer was genuinely dispatched, so `independent` is true — but the
        findings are the implementer's own prose rather than the reviewer's
        captured output. This is the bypass escapement-1l04 exists to close and
        the one that survived every earlier hardening pass.
        """
        _, decision, _ = _close(record=_record(verdict_source="implementer"))
        assert decision is not None, "an unread reviewer must not close the bead"
        assert decision["permissionDecision"] == "deny"
        assert "REVIEW_WAIVER" in decision["permissionDecisionReason"]

    def test_a_reviewer_that_returned_nothing_cannot_close_the_bead(self):
        _, decision, _ = _close(
            record=_record(verdict_source=None, verdict_digest=None)
        )
        assert decision["permissionDecision"] == "deny"

    def test_unresolved_blocking_findings_cannot_close_the_bead(self):
        _, decision, _ = _close(record=_record(blocking=True))
        assert decision["permissionDecision"] == "deny"
        assert "blocking" in decision["permissionDecisionReason"].lower()

    def test_a_genuine_captured_review_still_closes_cleanly(self):
        """The positive control that makes the whole thing usable.

        A gate that is on and cannot be satisfied is worse than one that is
        off — it routes every close through the waiver.
        """
        _, decision, signal = _close(record=_record())
        assert decision is None
        assert signal.call_args.kwargs["decision"] == "allow"

    def test_codex_shape_is_still_skipped_not_failed(self):
        """Codex exposes no Agent event, so no verdict can ever be captured.

        With the flag now True this is the case that would break a whole host
        if the rule were applied blindly.
        """
        _, decision, _ = _close(
            record=_record(independent="unverified", verdict_source=None),
            session_id=None,
        )
        assert decision is None
