"""Tests for _review_command.py — reading the shell command a close gate judges.

These exist because three real defects lived in the naive version of this
parsing, and none of them were caught by the gate's own behavioral tests: those
tests hand the gate a clean `bd close <id>` string, so anything that goes wrong
in *interpreting* the command string is invisible to them.

The most important case in this file is
`test_waiver_text_inside_a_close_reason_is_not_a_waiver`. The gate's denial
message contains the literal text `REVIEW_WAIVER="<>=20-char rationale>"`, and
that placeholder is 21 characters — long enough to clear the substance bar. So
with a whole-string regex, an agent that pasted the denial it had just received
into its `--reason` silently satisfied the gate that refused it. A gate its own
error message defeats is worse than no gate, because it reads as enforcement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[1]
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _review_command as rc  # noqa: E402


class TestRecognisingAClose:
    @pytest.mark.parametrize("command,expected", [
        ("bd close escapement-abc1", "escapement-abc1"),
        ('bd close "escapement-abc1"', "escapement-abc1"),
        ("bd  close   escapement-abc1", "escapement-abc1"),
        ("bd close escapement-abc1 && git push", "escapement-abc1"),
        ("cd /tmp && bd close escapement-abc1", "escapement-abc1"),
        ("bd ready; bd close escapement-abc1", "escapement-abc1"),
        ("$(bd close escapement-abc1)", "escapement-abc1"),
        ("bd close escapement-mol-4ef", "escapement-mol-4ef"),
        ("bd close escapement-858.4", "escapement-858.4"),
    ])
    def test_resolves_the_target(self, command, expected):
        assert rc.close_targets(command) == [expected]

    @pytest.mark.parametrize("command", [
        "bd ready",
        "bd show escapement-abc1",
        "bd update escapement-abc1 --status open",
        "mybd close escapement-abc1",
        "bd close --help",
    ])
    def test_not_a_close(self, command):
        assert rc.close_targets(command) is None

    @pytest.mark.parametrize("command", [
        'git commit -m "bd close escapement-abc1"',
        'echo "bd close escapement-abc1"',
        "grep -r 'bd close' claude/",
    ])
    def test_a_quoted_mention_is_not_a_close(self, command):
        """Blocking a commit because its message says 'bd close' is a pure
        false positive — it stops real work and closes nothing."""
        assert rc.close_targets(command) is None


class TestBdUpdateStatusClosed:
    @pytest.mark.parametrize("command", [
        "bd update escapement-abc1 --status closed",
        "bd update --status closed escapement-abc1",
        "bd update --status=closed escapement-abc1",
        'bd update --reason "done here" --status closed escapement-abc1',
    ])
    def test_resolves_the_bead_not_the_flag_value(self, command):
        """`--status closed` used to be read as the bead id itself.

        The gate then looked for a review of a bead named "closed", denied
        every time, and told the agent about an id that does not exist —
        unrepairable from the message.
        """
        assert rc.close_targets(command) == ["escapement-abc1"]

    def test_status_open_is_not_a_close(self):
        assert rc.close_targets("bd update escapement-abc1 --status open") is None


class TestWaiverIsOnlyAnEnvPrefix:
    @pytest.mark.parametrize("command,expected", [
        ('REVIEW_WAIVER="a genuinely long enough reason" bd close x',
         "a genuinely long enough reason"),
        ("REVIEW_WAIVER='single quoted and long enough' bd close x",
         "single quoted and long enough"),
        ('FOO=1 REVIEW_WAIVER="still a real assignment here" bd close x',
         "still a real assignment here"),
    ])
    def test_real_assignments_are_honoured(self, command, expected):
        assert rc.waiver_reason(command, "REVIEW_WAIVER") == expected

    def test_waiver_text_inside_a_close_reason_is_not_a_waiver(self):
        """The self-defeating case: echoing the denial must not satisfy it."""
        command = (
            'bd close escapement-abc1 --reason '
            '\'REVIEW_WAIVER="<>=20-char rationale>"\''
        )
        assert rc.waiver_reason(command, "REVIEW_WAIVER") is None

    @pytest.mark.parametrize("command", [
        'bd close x # REVIEW_WAIVER="sneaky in a trailing comment"',
        'echo "REVIEW_WAIVER=not really" && bd close x',
        'MY_REVIEW_WAIVER="a differently named variable" bd close x',
        'bd close x --reason "we discussed REVIEW_WAIVER=maybe"',
    ])
    def test_mentions_are_not_assignments(self, command):
        assert rc.waiver_reason(command, "REVIEW_WAIVER") is None

    def test_assignment_on_a_different_command_does_not_carry(self):
        """An env prefix binds to its own segment, not the whole line."""
        command = 'REVIEW_WAIVER="applies to the echo only" echo hi; bd close x'
        assert rc.waiver_reason(command, "REVIEW_WAIVER") is None


class TestSegmentation:
    @pytest.mark.parametrize("separator", ["&&", "||", ";", "|"])
    def test_splits_on_shell_separators(self, separator):
        assert rc.close_targets(
            f"true {separator} bd close escapement-abc1"
        ) == ["escapement-abc1"]

    def test_splits_on_newlines(self):
        assert rc.close_targets("set -e\nbd close escapement-abc1") == ["escapement-abc1"]

    @pytest.mark.parametrize("separator", [";", "&&", "|", "||"])
    def test_separators_inside_quotes_are_not_boundaries(self, separator):
        """Punctuation in prose must not be a bypass.

        Splitting on a quoted separator cut the command in half; the fragment
        holding `bd close` no longer parsed as a close, so the gate saw nothing
        to gate and allowed it. Any agent writing a semicolon in its waiver
        reason or close reason would have slipped through by accident.
        """
        command = (
            f'REVIEW_WAIVER="docs only{separator} no behavior touched at all" '
            "bd close escapement-abc1"
        )
        assert rc.close_targets(command) == ["escapement-abc1"]
        assert rc.waiver_reason(command, "REVIEW_WAIVER") == (
            f"docs only{separator} no behavior touched at all"
        )

    def test_quoted_separator_in_a_close_reason_still_resolves_the_bead(self):
        command = 'bd close escapement-abc1 --reason "shipped; verified live"'
        assert rc.close_targets(command) == ["escapement-abc1"]

    def test_unquoted_separator_after_a_quoted_one_still_splits(self):
        """The quote-aware scan must not swallow real boundaries."""
        command = 'echo "a; b" && bd close escapement-abc1'
        assert rc.close_targets(command) == ["escapement-abc1"]


class TestOrdinaryBdUsageIsStillGated:
    """Every case here was a live bypass, and none of them looks like evasion.

    That is what made them worse than an evadable gate: an agent typing
    `bd close -r "done"` never learns it skipped review, so no signal is
    produced and the lapse is invisible to the corpus that half-life review
    reads. `close_targets` returning `[]` — "a close I cannot identify" — is
    the mechanism that stops all of them; the gate must refuse on `[]` rather
    than treat it as "not a close".
    """

    @pytest.mark.parametrize("command,why", [
        ('bd close -r "finished the work here"',
         "-r is --reason; the prose was read as the bead id"),
        ('bd close --reason-file /tmp/r.md',
         "--reason-file value was read as the bead id"),
        ("bd close",
         "bd closes the last-touched issue when given no id"),
        ("bd close $ID",
         "an unexpanded variable is not a bead id"),
        ("bd close ${BEAD}",
         "an unexpanded brace variable is not a bead id"),
    ])
    def test_unidentifiable_close_is_reported_as_such(self, command, why):
        assert rc.close_targets(command) == [], why

    @pytest.mark.parametrize("command", [
        "bd -C . close escapement-abc1",
        "bd --actor bot close escapement-abc1",
        "bd --db /tmp/x.db close escapement-abc1",
        "bd --json --sandbox close escapement-abc1",
        "bd done escapement-abc1",
        "bd update escapement-abc1 -s closed",
        "bd update -s closed escapement-abc1",
    ])
    def test_global_flags_and_aliases_are_still_closes(self, command):
        """These used to parse as "not a close at all" and skip the gate."""
        assert rc.close_targets(command) == ["escapement-abc1"]

    def test_every_bead_in_a_multi_close_is_returned(self):
        """Checking only the first id let the rest close unreviewed."""
        assert rc.close_targets("bd close escapement-a1 escapement-b2") == [
            "escapement-a1", "escapement-b2",
        ]

    @pytest.mark.parametrize("command", [
        "bd close --help",
        "bd ready",
        "bd show escapement-abc1",
        "bd update escapement-abc1 --status open",
    ])
    def test_non_closes_stay_non_closes(self, command):
        assert rc.close_targets(command) is None


class TestReservedMetadataIsNotHandWritable:
    def test_direct_write_to_the_review_key_is_detected(self):
        """Otherwise an implementer mints its own independence stamp."""
        command = (
            'bd update escapement-abc1 --set-metadata '
            '\'escapement_review={"v":1,"independent":true}\''
        )
        assert rc.writes_reserved_metadata(command, "escapement_review")

    @pytest.mark.parametrize("command", [
        "bd update escapement-abc1 --set-metadata team=platform",
        "bd close escapement-abc1",
        'bd update escapement-abc1 --reason "escapement_review is fine"',
    ])
    def test_unrelated_metadata_is_untouched(self, command):
        assert not rc.writes_reserved_metadata(command, "escapement_review")
