"""Behavioral tests for review_gate.py — the independent-review gate.

The outcome under test is a business rule, not a code shape: *work cannot be
closed until an independent critical review of that bead, at its current state,
is on record.* So these tests are written against what an agent trying to close
a bead actually experiences — allowed, or refused with a reason and a way
forward — rather than against the gate's internals.

The controls are chosen to reject the three ways the previous gate could be
satisfied without a review happening:

  - a self-written rubber stamp (an agent named "review-helper")
  - a review of a *different* bead
  - a review of an *earlier state* of the work

Each of those is a negative control below. If a future refactor makes any of
them pass, the gate has stopped enforcing the outcome even if it still enforces
a check.

Host coverage: Claude and Codex payload shapes are both exercised. Following
the repo convention (see test_codex_discovery_close_gate.py), an absent
`session_id` is the Codex shape, where isolated-dispatch corroboration is not
observable and must be skipped rather than failed.
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


SUBSTANTIVE = (
    "Read the diff against the bead's acceptance criteria. The close path now "
    "binds the review to the bead id, which the previous implementation did "
    "not do; verified the stale-fingerprint branch fires on a follow-up edit. "
    "One concern: the waiver reason is not length-checked against the bead "
    "title, so a paraphrase would pass."
)

FINGERPRINT = "a" * 64


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path):
    """Point the dispatch ledger at a per-test temp dir."""
    with patch.object(_review_ledger, "LEDGER_DIR", tmp_path / "ledger"):
        yield


def _record(bead="escapement-abc1", findings=SUBSTANTIVE, fingerprint=FINGERPRINT,
            independent=True, reviewer="adversarial-reviewer",
            blocking=False, verdict_source="captured",
            verdict_digest="c" * 64):
    return {
        "v": _review_record.RECORD_VERSION,
        "bead": bead,
        "reviewer": reviewer,
        "fingerprint": fingerprint,
        "recorded_at": "2026-08-27T00:00:00+00:00",
        "findings": findings,
        "independent": independent,
        "blocking": blocking,
        "verdict_source": verdict_source,
        "verdict_digest": verdict_digest,
        "host": "cli",
    }


def _close(command="bd close escapement-abc1", record=None,
           fingerprint=FINGERPRINT, session_id="s1"):
    """Run the gate on a close command. Returns (exit_code, decision|None)."""
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": "/tmp",
    }
    if session_id is not None:
        payload["session_id"] = session_id

    out = io.StringIO()
    with patch.object(review_gate, "read_record", return_value=record), \
         patch.object(review_gate, "work_fingerprint", return_value=fingerprint), \
         patch.object(review_gate, "changed_paths_since", return_value=["a.py", "b.py"]), \
         patch.object(review_gate, "_record_signal") as signal, \
         patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", out):
        code = review_gate.main()

    raw = out.getvalue()
    decision = json.loads(raw)["hookSpecificOutput"] if raw.strip() else None
    return code, decision, signal


def _dispatch(tool_input, session_id="s1", fingerprint=FINGERPRINT):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": tool_input,
        "cwd": "/tmp",
    }
    if session_id is not None:
        payload["session_id"] = session_id
    with patch.object(review_gate, "work_fingerprint", return_value=fingerprint), \
         patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", io.StringIO()):
        review_gate.main()


# ---------------------------------------------------------------------------
# Positive controls — a real review must not obstruct the close
# ---------------------------------------------------------------------------

class TestAllows:
    def test_corroborated_current_review_closes_silently(self):
        code, decision, signal = _close(record=_record())
        assert code == 0
        assert decision is None, "a genuine review must not prompt at all"
        assert signal.call_args.kwargs["decision"] == "allow"

    def test_claude_shape_denies_the_same_unverified_record(self):
        """Same record, host that CAN check — the check must actually run."""
        _, decision, _ = _close(record=_record(independent="unverified"))
        assert decision["permissionDecision"] == "deny"
        assert "not corroborated" in decision["permissionDecisionReason"]

    def test_unknowable_fingerprint_does_not_block(self):
        """Not a git tree: staleness is uncheckable, not stale."""
        _, decision, _ = _close(record=_record(fingerprint=None), fingerprint=None)
        assert decision is None

    def test_unreachable_task_store_does_not_block_every_close(self):
        """Our infrastructure failing must not read as the agent skipping review.

        `bd` not answering and a bead having no review both used to arrive as
        None. Denying on that would stop every close in the repository until
        the task store recovered — a far worse outcome than the lapse the gate
        prevents.
        """
        _, decision, signal = _close(record=_review_record.UNAVAILABLE)
        assert decision is None
        assert signal.call_args.kwargs["store_unavailable"] is True

    def test_non_close_commands_are_untouched(self):
        _, decision, _ = _close(command="bd ready", record=None)
        assert decision is None

    def test_unparseable_close_fails_open(self):
        _, decision, _ = _close(command="bd close --help", record=None)
        assert decision is None


# ---------------------------------------------------------------------------
# Negative controls — the three ways the old gate could be fooled
# ---------------------------------------------------------------------------

class TestNegativeControls:
    def test_no_review_on_record_is_refused(self):
        _, decision, signal = _close(record=None)
        assert decision["permissionDecision"] == "deny"
        assert signal.call_args.kwargs["decision"] == "deny:no-review"

    def test_review_of_another_bead_does_not_satisfy_this_close(self):
        """The old gate had no bead binding at all: any review satisfied any close."""
        _, decision, signal = _close(record=_record(bead="escapement-other"))
        assert decision["permissionDecision"] == "deny"
        assert signal.call_args.kwargs["decision"] == "deny:wrong-bead"
        assert "escapement-other" in decision["permissionDecisionReason"]

    @pytest.mark.parametrize("stamp", ["lgtm", "looks good", "No findings", "ok", ""])
    def test_rubber_stamp_verdicts_are_refused(self, stamp):
        _, decision, signal = _close(record=_record(findings=stamp))
        assert decision["permissionDecision"] == "deny"
        assert signal.call_args.kwargs["decision"] == "deny:insubstantial"

    def test_review_of_earlier_work_is_refused_as_stale(self):
        """Review, then keep editing, then close — the third old hole."""
        _, decision, signal = _close(
            record=_record(fingerprint="b" * 64), fingerprint="c" * 64
        )
        assert decision["permissionDecision"] == "deny"
        assert signal.call_args.kwargs["decision"] == "deny:stale"

    def test_stale_denial_names_what_changed(self):
        """A bare 'stale' assertion is unactionable; the agent needs the delta."""
        _, decision, _ = _close(
            record=_record(fingerprint="b" * 64), fingerprint="c" * 64
        )
        assert "a.py" in decision["permissionDecisionReason"]

    def test_bd_update_status_closed_is_gated_too(self):
        _, decision, _ = _close(
            command="bd update escapement-abc1 --status closed", record=None
        )
        assert decision["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# The gameable oracle the rewrite removed
# ---------------------------------------------------------------------------

class TestSelfWrittenReviewerIsNotIndependent:
    def test_agent_named_review_does_not_count_as_a_reviewer(self):
        """The old oracle was a word-match on name/description/prompt."""
        _dispatch({
            "name": "review-helper",
            "description": "review the work for escapement-abc1",
            "prompt": "Please review escapement-abc1 and confirm it is fine.",
            "subagent_type": "general-purpose",
        })
        assert not _review_ledger.has_dispatch("escapement-abc1")

    def test_isolated_reviewer_subagent_type_does_count(self):
        _dispatch({
            "name": "auditor",
            "description": "escapement-abc1",
            "prompt": "Find every way escapement-abc1 fails to deliver.",
            "subagent_type": "adversarial-reviewer",
        })
        assert _review_ledger.has_dispatch("escapement-abc1")

    def test_reviewer_dispatch_naming_no_bead_is_not_bound(self):
        """A reviewer that names no bead cannot vouch for a specific one."""
        _dispatch({
            "name": "auditor",
            "description": "look at the diff",
            "prompt": "Review the working tree.",
            "subagent_type": "adversarial-reviewer",
        })
        assert not _review_ledger.has_dispatch("escapement-abc1")


# ---------------------------------------------------------------------------
# Codex host shape (referenced as Codex fixtures in agent-surfaces/manifest.json)
#
# Module-level so the manifest can name them as flat pytest selectors, which is
# what the surface renderer validates.
# ---------------------------------------------------------------------------

def test_codex_shape_allows_unverified_independence():
    """Codex cannot observe dispatches; unverifiable must not read as failed.

    This is the whole reason the gate is host-neutral: the load-bearing
    evidence (a substantive, bead-bound, current review in Beads) is checked
    identically on both hosts, and only the corroborating dispatch check is
    skipped where the host cannot supply it.
    """
    _, decision, signal = _close(
        record=_record(independent="unverified"), session_id=None
    )
    assert decision is None
    assert signal.call_args.kwargs["decision"] == "allow"


def test_codex_shape_still_refuses_an_unreviewed_close():
    """Degrading the corroboration must not degrade the core enforcement."""
    _, decision, signal = _close(record=None, session_id=None)
    assert decision["permissionDecision"] == "deny"
    assert signal.call_args.kwargs["decision"] == "deny:no-review"


def test_codex_shape_records_no_dispatch():
    """No Agent event reaches Codex, so nothing may be inferred from one."""
    _dispatch({
        "subagent_type": "adversarial-reviewer",
        "prompt": "Review escapement-abc1.",
    }, session_id=None)
    assert not _review_ledger.has_dispatch("escapement-abc1")


# ---------------------------------------------------------------------------
# Escape path (gate-design.md Rule 1)
# ---------------------------------------------------------------------------

class TestEscapePath:
    def test_substantive_waiver_allows_the_close(self):
        _, decision, signal = _close(
            command=(
                'REVIEW_WAIVER="Docs-only typo fix with no behavior change; '
                'reviewed inline with the user" bd close escapement-abc1'
            ),
            record=None,
        )
        assert decision is None
        assert signal.call_args.kwargs["decision"] == "allow"

    def test_waiver_reason_persists_as_signal(self):
        """Rule 2: waiver reasons are the labeled corpus for half-life review."""
        _, _, signal = _close(
            command=(
                'REVIEW_WAIVER="Generated file regenerated from a reviewed '
                'source change" bd close escapement-abc1'
            ),
            record=None,
        )
        assert "Generated file" in signal.call_args.kwargs["waiver_reason"]

    @pytest.mark.parametrize("reason", ["tbd", "n/a", "short", ""])
    def test_placeholder_waivers_are_refused(self, reason):
        """Rule 3: a waiver that validates presence only is a checkbox."""
        _, decision, signal = _close(
            command=f'REVIEW_WAIVER="{reason}" bd close escapement-abc1',
            record=None,
        )
        assert decision["permissionDecision"] == "deny"
        assert signal.call_args.kwargs["decision"] == "deny:invalid-waiver"

    def test_rejected_waiver_reports_the_waiver_problem_not_the_missing_review(self):
        """The agent DID try the escape; hiding that would misdirect the repair."""
        _, decision, _ = _close(
            command='REVIEW_WAIVER="tbd" bd close escapement-abc1', record=None
        )
        assert "REVIEW_WAIVER" in decision["permissionDecisionReason"]

    @pytest.mark.parametrize("record,fingerprint", [
        (None, FINGERPRINT),
        (_record(bead="escapement-other"), FINGERPRINT),
        (_record(findings="lgtm"), FINGERPRINT),
        (_record(fingerprint="b" * 64), "c" * 64),
        (_record(independent="unverified"), FINGERPRINT),
    ])
    def test_every_denial_names_an_agent_invokable_way_forward(self, record, fingerprint):
        """A deny with no escape is on the coercive axis (Adler & Borys)."""
        _, decision, _ = _close(record=record, fingerprint=fingerprint)
        reason = decision["permissionDecisionReason"]
        assert "escapement_review.py" in reason, "no recording command offered"
        assert "REVIEW_WAIVER" in reason, "no waiver offered"
        assert "ask the user" not in reason.lower()


# ---------------------------------------------------------------------------
# Persistent signal (gate-design.md Rule 2)
# ---------------------------------------------------------------------------

class TestSignal:
    @pytest.mark.parametrize("record,fingerprint", [
        (None, FINGERPRINT),
        (_record(), FINGERPRINT),
        (_record(fingerprint="b" * 64), "c" * 64),
    ])
    def test_every_decision_is_recorded(self, record, fingerprint):
        _, _, signal = _close(record=record, fingerprint=fingerprint)
        assert signal.called
        assert signal.call_args.kwargs["gate_name"] == "review_gate"
        assert signal.call_args.kwargs["bead_id"] == "escapement-abc1"


# ---------------------------------------------------------------------------
# The bypasses an adversarial review found in the first implementation
# ---------------------------------------------------------------------------

class TestOrdinaryUsageCannotSlipThrough:
    """These are not evasion — they are how agents already type `bd close`.

    That is what made them the worst finding: an agent running
    `bd close -r "done"` never learned it had skipped review, so the lapse
    produced no signal and was invisible to the corpus half-life review reads.
    An evadable gate at least leaves a trace.
    """

    @pytest.mark.parametrize("command", [
        'bd close -r "finished the work here"',
        "bd close",
        "bd close $ID",
    ])
    def test_a_close_we_cannot_identify_is_refused(self, command):
        _, decision, signal = _close(command=command, record=None)
        assert decision["permissionDecision"] == "deny"
        assert signal.call_args.kwargs["decision"] == "deny:unidentified-target"

    def test_that_refusal_explains_how_to_name_the_bead(self):
        """Internal transparency: the fix must be readable from the refusal."""
        _, decision, _ = _close(command='bd close -r "done"', record=None)
        reason = decision["permissionDecisionReason"]
        assert "bd close <bead-id>" in reason
        assert "REVIEW_WAIVER" in reason

    @pytest.mark.parametrize("command", [
        "bd -C . close escapement-abc1",
        "bd --actor bot close escapement-abc1",
        "bd done escapement-abc1",
        "bd update escapement-abc1 -s closed",
    ])
    def test_global_flags_and_aliases_are_gated(self, command):
        """Each of these used to parse as 'not a close' and skip the gate."""
        _, decision, _ = _close(command=command, record=None)
        assert decision["permissionDecision"] == "deny"

    def test_a_second_bead_in_one_close_is_not_waved_through(self):
        """Only the first id used to be checked; the rest closed unreviewed."""
        def per_bead(bead_id, cwd=None):
            return _record() if bead_id == "escapement-abc1" else None

        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "bd close escapement-abc1 escapement-zzz9"},
            "session_id": "s1",
            "cwd": "/tmp",
        }
        out = io.StringIO()
        with patch.object(review_gate, "read_record", side_effect=per_bead), \
             patch.object(review_gate, "work_fingerprint", return_value=FINGERPRINT), \
             patch.object(review_gate, "_record_signal"), \
             patch("sys.stdin", io.StringIO(json.dumps(payload))), \
             patch("sys.stdout", out):
            review_gate.main()
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "escapement-zzz9" in decision["permissionDecisionReason"]


class TestRecordCannotBeSelfMinted:
    def test_hand_writing_the_review_metadata_is_refused(self):
        """Otherwise the author of the code stamps its own independence."""
        command = (
            'bd update escapement-abc1 --set-metadata '
            '\'escapement_review={"v":1,"independent":true}\''
        )
        _, decision, signal = _close(command=command, record=None)
        assert decision["permissionDecision"] == "deny"
        assert signal.call_args.kwargs["decision"] == "deny:reserved-metadata"
        assert "escapement_review.py" in decision["permissionDecisionReason"]

    def test_unrelated_metadata_is_untouched(self):
        _, decision, _ = _close(
            command="bd update escapement-abc1 --set-metadata team=platform",
            record=None,
        )
        assert decision is None


class TestUnfingerprintedRecord:
    def test_a_record_with_no_fingerprint_is_refused(self):
        """`--cwd /tmp` at record time would switch staleness off forever.

        It is documented in `--help`, needs no justification, and never has to
        be repeated — a far more attractive door than the waiver, which at
        least costs a reason that lands in the corpus.
        """
        _, decision, signal = _close(
            record=_record(fingerprint=None), fingerprint=FINGERPRINT
        )
        assert decision["permissionDecision"] == "deny"
        assert signal.call_args.kwargs["decision"] == "deny:unfingerprinted"


class TestDecisionContract:
    """One mechanism, always: JSON on stdout, exit 0.

    Emitting the JSON decision *and* a non-zero exit is a contradictory double
    signal, and the host resolves it by reporting an unexplained
    `hook error: No stderr output` — the agent never sees why it was blocked
    or how to proceed. That failure is live in `file_complexity_gate` today,
    and this gate briefly reintroduced it by returning its internal
    DENIED/ALLOWED sentinel straight out of `main()`.
    """

    @pytest.mark.parametrize("command,record", [
        ("bd close escapement-abc1", None),
        ('bd close -r "prose in the positional slot"', None),
        ("bd close", None),
        ('bd update escapement-abc1 --set-metadata \'escapement_review={}\'', None),
        ("bd close escapement-abc1", "STALE"),
        ("bd ready", None),
    ])
    def test_main_always_returns_zero(self, command, record):
        payload = _record(fingerprint="b" * 64) if record == "STALE" else record
        code, _, _ = _close(command=command, record=payload)
        assert code == 0, (
            "a non-zero exit alongside a JSON decision is the double signal "
            "that makes a denial unreadable to the agent"
        )

    def test_every_denial_is_valid_json_on_stdout(self):
        for command in (
            'bd close -r "prose in the positional slot"',
            "bd close",
            'bd update escapement-abc1 --set-metadata \'escapement_review={}\'',
        ):
            _, decision, _ = _close(command=command, record=None)
            assert decision["hookEventName"] == "PreToolUse"
            assert decision["permissionDecision"] == "deny"
            assert decision["permissionDecisionReason"].strip()


class TestCommandToBeadBinding:
    """The gate's single point of authority: WHICH bead is it protecting?

    `read_record` is mocked in every other test here, and none of them assert
    what it was called with — so the whole command→bead-id binding sat above
    the mock boundary and was untested. That is precisely where the `-r`
    bypass lived: the gate faithfully checked a review for a bead named
    "finished the work here".
    """

    def _bead_asked_for(self, command):
        asked = []

        def spy(bead_id, cwd=None):
            asked.append(bead_id)
            return _record(bead=bead_id)

        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "s1",
            "cwd": "/tmp",
        }
        with patch.object(review_gate, "read_record", side_effect=spy), \
             patch.object(review_gate, "work_fingerprint", return_value=FINGERPRINT), \
             patch.object(review_gate, "_record_signal"), \
             patch("sys.stdin", io.StringIO(json.dumps(payload))), \
             patch("sys.stdout", io.StringIO()):
            review_gate.main()
        return asked

    @pytest.mark.parametrize("command", [
        "bd close escapement-abc1",
        'bd close escapement-abc1 -r "shipped and verified"',
        'bd close -r "shipped and verified" escapement-abc1',
        "bd update escapement-abc1 -s closed",
        "bd -C . --actor bot done escapement-abc1",
    ])
    def test_the_real_bead_is_the_one_looked_up(self, command):
        assert self._bead_asked_for(command) == ["escapement-abc1"]

    def test_a_reason_is_never_looked_up_as_a_bead(self):
        """The `-r` bypass in one assertion."""
        asked = self._bead_asked_for('bd close -r "finished the work here"')
        assert "finished the work here" not in asked

    def test_both_beads_of_a_multi_close_are_looked_up(self):
        assert self._bead_asked_for(
            "bd close escapement-abc1 escapement-zzz9"
        ) == ["escapement-abc1", "escapement-zzz9"]


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
        """Capability honesty, asserted rather than promised.

        The docstring is the thing an agent reads to learn what this gate
        does. If it says the capture feature is off, no part of that feature
        may deny.
        """
        doc = review_gate.__doc__ or ""
        assert "NOT ENFORCED YET" in doc
        gated_section = doc.split("NOT ENFORCED YET")[1].split("A COROLLARY")[0]
        assert "blocking" in gated_section.lower(), (
            "the blocking rule is gated off; the docstring must not list it "
            "under ENFORCED NOW"
        )
