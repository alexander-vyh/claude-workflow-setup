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
        assert not _review_ledger.has_dispatch("escapement-abc1", "s1")

    def test_isolated_reviewer_subagent_type_does_count(self):
        _dispatch({
            "name": "auditor",
            "description": "escapement-abc1",
            "prompt": "Find every way escapement-abc1 fails to deliver.",
            "subagent_type": "adversarial-reviewer",
        })
        assert _review_ledger.has_dispatch("escapement-abc1", "s1")

    def test_reviewer_dispatch_naming_no_bead_is_not_bound(self):
        """A reviewer that names no bead cannot vouch for a specific one."""
        _dispatch({
            "name": "auditor",
            "description": "look at the diff",
            "prompt": "Review the working tree.",
            "subagent_type": "adversarial-reviewer",
        })
        assert not _review_ledger.has_dispatch("escapement-abc1", "s1")


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
    assert not _review_ledger.has_dispatch("escapement-abc1", "s1")


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
