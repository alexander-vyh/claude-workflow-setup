"""The shapes real reviewers actually emit — escapement-1nzm.

WHY THIS FILE EXISTS SEPARATELY FROM test_review_verdict.py
------------------------------------------------------------
`classify_blocking` shipped with 251 passing tests and could not tell a clean
review from a rejected one. Not "misfired sometimes" — on the output format
that `claude/agents/adversarial-reviewer.md` MANDATES, it returned True for
both a PASS and a REJECT, so it carried zero information on the only shape that
matters.

The mechanism: the mandated format emits `### BLOCK` unconditionally, and
`_LINE_START` allowed `#` in its markdown-decoration class, so the heading read
as a verdict position all by itself. The negation ("None.") sits on the NEXT
line, where the negation check never looked.

Emitting the heading unconditionally is CORRECT design — it forces a reviewer to
state "no blockers" out loud instead of silently omitting the section — so the
format is not the defect and is not being changed to suit the parser.

The deeper lesson, and the reason this corpus is its own file: the original
tests asserted the shapes the author imagined. This file asserts the shapes the
repository's own reviewer agents are CONTRACTUALLY REQUIRED to produce, taken
from their agent definitions rather than invented here. It is a corpus, not a
unit test — when either agent's Output Format section changes, this is the file
that must fail.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[1]
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _review_verdict as rv  # noqa: E402

_AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"


# ---------------------------------------------------------------------------
# The adversarial-reviewer format, verbatim in shape
# ---------------------------------------------------------------------------

def _adversarial(verdict: str, block_body: str, concern_body: str = "- none") -> str:
    """One review in the exact section layout adversarial-reviewer.md mandates."""
    return (
        "## Adversarial Review: claude/hooks/_review_verdict.py\n"
        "\n"
        "### Outcome under review\n"
        "- **Intended outcome:** the recorded findings are the reviewer's own.\n"
        "- **Stated proof:** test_review_gate.py -k verdict\n"
        "- **Would that proof fail if the outcome were NOT delivered?** PARTIAL\n"
        "- **If this shipped subtly wrong:** an unread review would close work.\n"
        "\n"
        "### BLOCK\n"
        f"{block_body}\n"
        "\n"
        "### CONCERN\n"
        f"{concern_body}\n"
        "\n"
        "### NOTE\n"
        "- The docstring is long.\n"
        "\n"
        "### Verdict\n"
        f"{verdict}\n"
        "\n"
        "### What I'd break first\n"
        "I would dispatch a reviewer and ignore it.\n"
    )


CLEAN_PASS = _adversarial("PASS", "None.")
PASS_WITH_CONCERNS = _adversarial(
    "PASS WITH CONCERNS",
    "None.",
    "- `_LINE_START` permits `#`, which is fragile → a format change breaks it.",
)
REJECTED = _adversarial(
    "REJECT",
    "- `has_dispatch` never reads the stored fingerprint (_review_ledger.py:180) "
    "→ a reviewer that read state A vouches for a record written at state B.",
)


class TestTheMandatedAdversarialFormat:
    """The exact defect: both classes returned True, so the output was noise."""

    def test_a_clean_pass_is_not_blocking(self):
        assert rv.classify_blocking(CLEAN_PASS) is False

    def test_a_rejection_is_blocking(self):
        assert rv.classify_blocking(REJECTED) is True

    def test_pass_and_reject_are_actually_distinguished(self):
        """The property the shipped classifier lacked entirely.

        Asserting each case separately is not enough — a classifier hardwired
        to return True passes "reject is blocking" and a classifier hardwired
        to False passes "pass is not blocking". Only the INEQUALITY catches a
        constant, and a constant is precisely what shipped.
        """
        assert rv.classify_blocking(REJECTED) != rv.classify_blocking(CLEAN_PASS)

    def test_pass_with_concerns_does_not_block(self):
        """The ambiguous middle. CONCERNs are, by the format's own definition,
        the findings that are NOT blocking; treating them as blockers would
        deny most honest reviews."""
        assert rv.classify_blocking(PASS_WITH_CONCERNS) is False

    @pytest.mark.parametrize("empty_body", ["None.", "None", "- none", "n/a", "", "  "])
    def test_an_empty_block_section_is_not_a_finding(self, empty_body):
        """A heading is not self-certifying; its BODY decides.

        This is the whole repair. `### BLOCK` is emitted whether or not there
        are blockers, so the heading alone can never carry the signal.
        """
        assert rv.classify_blocking(_adversarial("PASS", empty_body)) is False

    def test_a_populated_block_section_is_a_finding_even_without_a_verdict(self):
        """The fallback path: a reviewer that skipped the Verdict section."""
        text = REJECTED.split("### Verdict")[0]
        assert "### BLOCK" in text
        assert rv.classify_blocking(text) is True

    def test_a_reviewer_that_omits_the_verdict_and_has_no_blockers_passes(self):
        text = CLEAN_PASS.split("### Verdict")[0]
        assert rv.classify_blocking(text) is False


class TestTheDeclaredVerdictWins:
    """The declared verdict is authoritative; prose is only the fallback.

    Inferring intent by scanning prose for scary words is what produced this
    bug. When a reviewer has stated its verdict in the section the format
    reserves for exactly that, reading anything else is second-guessing a
    direct answer.
    """

    def test_reject_beats_an_empty_block_section(self):
        """A reviewer can reject on something other than a BLOCK bullet —
        an unrecoverable outcome, say. The declared verdict still governs."""
        assert rv.classify_blocking(_adversarial("REJECT", "None.")) is True

    def test_pass_beats_the_word_blocker_appearing_in_prose(self):
        """The most common false positive: a reviewer explaining, in a passing
        review, what a blocker WOULD have looked like."""
        text = _adversarial(
            "PASS",
            "None.",
            "- I looked hard for a BLOCKER here and could not construct one; "
            "the fingerprint comparison rejects the stale-review attack.",
        )
        assert rv.classify_blocking(text) is False

    @pytest.mark.parametrize("verdict,expected", [
        ("PASS", False),
        ("pass", False),
        ("PASS WITH CONCERNS", False),
        ("Pass with concerns", False),
        ("REJECT", True),
        ("reject", True),
        ("REJECTED", True),
        ("**REJECT**", True),
        ("NEEDS WORK", True),
        ("PASS (all tests have outcome assertions)", False),
    ])
    def test_the_verdict_vocabulary_both_agents_use(self, verdict, expected):
        """`PASS WITH CONCERNS` must not be matched by the `PASS` rule alone,
        and `NEEDS WORK` is test-quality-reviewer.md's failing verdict."""
        assert rv.classify_blocking(_adversarial(verdict, "None.")) is expected

    def test_an_inline_verdict_label_is_read_too(self):
        assert rv.classify_blocking("Verdict: REJECT\nSee the BLOCK list.") is True
        assert rv.classify_blocking("### Verdict: PASS\nNothing to fix.") is False


class TestTheAgentContractIsStillWhatWeParse:
    """If either agent's Output Format changes, this file must fail.

    The classifier and the agent definitions are coupled, and until
    escapement-1nzm neither file said so — which is exactly how the coupling
    got broken without anyone noticing. These assertions are the tripwire.
    """

    def _agent(self, name: str) -> str:
        path = _AGENTS_DIR / f"{name}.md"
        assert path.is_file(), f"{path} moved; the classifier's contract is gone"
        return path.read_text(encoding="utf-8")

    def test_adversarial_reviewer_still_declares_a_verdict_section(self):
        body = self._agent("adversarial-reviewer")
        assert re.search(r"^#{1,6}\s*Verdict\s*$", body, re.MULTILINE), (
            "the classifier reads the '### Verdict' heading as authoritative"
        )
        assert "PASS / PASS WITH CONCERNS / REJECT" in body, (
            "the verdict vocabulary the classifier parses has changed"
        )

    def test_adversarial_reviewer_still_emits_block_unconditionally(self):
        """Pins the reason the fallback must be section-aware.

        If this ever becomes conditional, the section-body rule is no longer
        load-bearing — but until then, a bare heading must never classify.
        """
        assert re.search(r"^#{1,6}\s*BLOCK\s*$", self._agent("adversarial-reviewer"),
                         re.MULTILINE)

    def test_test_quality_reviewer_verdict_vocabulary_is_covered(self):
        body = self._agent("test-quality-reviewer")
        assert "NEEDS WORK" in body
        for token in ("PASS", "NEEDS WORK"):
            assert token in body, f"{token} is parsed by classify_blocking"

    def test_both_files_document_the_coupling(self):
        """Neither file said the other depended on it. That silence is how a
        format change becomes a silent classifier failure."""
        assert "classify_blocking" in self._agent("adversarial-reviewer") or (
            "_review_verdict" in self._agent("adversarial-reviewer")
        ), "adversarial-reviewer.md must name the parser that depends on it"
        assert "adversarial-reviewer" in (
            (_HOOKS_DIR / "_review_verdict.py").read_text(encoding="utf-8")
        ), "the classifier must name the agent contract it parses"


class TestPlainProseReviewsStillWork:
    """Not every reviewer emits the full format; the fallback must survive."""

    def test_a_prose_blocker_is_still_caught(self):
        assert rv.classify_blocking(
            "BLOCKER: the ledger is world-writable and unvalidated on read."
        ) is True

    def test_a_prose_clean_review_is_still_clean(self):
        assert rv.classify_blocking(
            "No blockers found. The fingerprint comparison is correct and the "
            "escape path is documented in the denial text itself."
        ) is False

    def test_the_word_block_as_an_ordinary_verb_is_not_a_verdict(self):
        assert rv.classify_blocking(
            "Denying on a task-store outage would block every close in the "
            "repository, so failing open there is the right call."
        ) is False


class TestQuotedMaterialIsNotAFinding:
    """Reviewers paste diffs, logs, and other reviews' output constantly.

    A `### BLOCK` quoted inside a fenced code block is indented past the
    heading threshold, so it never reaches the section scanner and falls
    through to the line-start regex instead. That regex is why `#` is excluded
    from its decoration class — mutation testing showed that exclusion was
    otherwise unreachable, which meant nothing tested the case it exists for.
    """

    def test_a_block_heading_quoted_in_a_code_fence_does_not_classify(self):
        text = (
            "### Verdict\n"
            "\n"
            "The previous review's output, for reference:\n"
            "\n"
            "```\n"
            "    ### BLOCK\n"
            "    - the fingerprint is never compared\n"
            "```\n"
            "\n"
            "That finding was addressed in the commit under review.\n"
        )
        assert rv.classify_blocking(text) is False

    def test_an_indented_marker_line_does_not_classify(self):
        assert rv.classify_blocking(
            "Here is the denial text the gate emits:\n"
            "\n"
            "    #BLOCKER - example text from the old implementation\n"
        ) is False

    def test_but_an_unindented_heading_with_content_still_does(self):
        """The control: excluding quoted material must not blunt the real case."""
        assert rv.classify_blocking(
            "### BLOCK\n- the fingerprint is never compared\n"
        ) is True
