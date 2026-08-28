"""Behavioral tests for verdict capture and blocking classification.

The outcome under test: *the findings on record must be what the reviewer
actually returned, and a reviewer that said "this is broken" must not be
closable until the code changes.*

The controls here are chosen against the specific bypass that survived every
earlier hardening pass (escapement-1l04): dispatch a genuinely isolated
reviewer, never read its answer, write your own verdict, close. The dispatch
ledger was written at `Agent` PreToolUse — before the subagent emitted a single
token — so the reviewer's verdict, or its total failure to produce one, was
never consulted at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[1]
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _review_verdict as rv  # noqa: E402


# ---------------------------------------------------------------------------
# Extracting the reviewer's text out of whatever shape the host hands us
# ---------------------------------------------------------------------------

class TestVerdictExtraction:
    """The host's `tool_response` shape is not ours to choose.

    Claude has shipped several shapes for tool results over time (bare string,
    `{"content": [...]}` blocks, a list of blocks). An extractor that only
    handles today's shape turns into a silent capability regression on upgrade:
    the gate keeps passing its tests while capture quietly returns None and
    every review degrades to dispatch-only. So every plausible shape is pinned.
    """

    def test_bare_string(self):
        assert rv.extract_verdict("BLOCKER: the fingerprint is never read.") == (
            "BLOCKER: the fingerprint is never read."
        )

    def test_content_block_list(self):
        payload = [{"type": "text", "text": "first half"},
                   {"type": "text", "text": "second half"}]
        assert rv.extract_verdict(payload) == "first half\nsecond half"

    def test_dict_with_content_blocks(self):
        payload = {"content": [{"type": "text", "text": "the verdict"}]}
        assert rv.extract_verdict(payload) == "the verdict"

    def test_dict_with_plain_text_key(self):
        assert rv.extract_verdict({"text": "the verdict"}) == "the verdict"

    def test_non_text_blocks_are_skipped_not_stringified(self):
        """An image block must not become the literal text "{'type': 'image'}".

        Stringifying whatever is present would let a reviewer that returned no
        prose at all still clear the 120-char substance bar with repr noise.
        """
        payload = {"content": [
            {"type": "image", "source": {"data": "AAAA" * 100}},
            {"type": "text", "text": "the actual verdict"},
        ]}
        assert rv.extract_verdict(payload) == "the actual verdict"

    def test_nothing_usable_returns_none(self):
        assert rv.extract_verdict(None) is None
        assert rv.extract_verdict({}) is None
        assert rv.extract_verdict([]) is None
        assert rv.extract_verdict({"content": []}) is None
        assert rv.extract_verdict("   ") is None


# ---------------------------------------------------------------------------
# Blocking classification — NEGATIVE CONTROLS FIRST
# ---------------------------------------------------------------------------

class TestBlockingClassification:
    """Derived from the reviewer's own text, never from a self-report.

    The false-positive direction matters as much as the false-negative one: a
    classifier that fires on "no blocking issues found" would deny every clean
    review, and agents would learn to route around it through the waiver. That
    trains the corpus to treat the waiver as routine, which is exactly how a
    gate rots into mock bureaucracy.
    """

    @pytest.mark.parametrize("text", [
        "BLOCKER: has_dispatch never compares the stored fingerprint.",
        "**BLOCKER** — the ledger lives in world-writable /tmp.",
        "- BLOCKING: the record can be hand-written.",
        "[BLOCKER] subagent_type is not re-validated on read.",
        "VERDICT: BLOCK\nThe close path trusts implementer-authored text.",
        "Verdict: blocking\nSee below.",
        "## CRITICAL\nThe corroboration is worth nothing.",
        "MUST FIX: the version bump has no migration path.",
        "P0: this ships a forgeable independence stamp.",
        "I found 3 blockers in the close path.",
        "There is one blocker: the verdict is never read.",
    ])
    def test_reviewer_asserting_a_blocker_is_blocking(self, text):
        assert rv.classify_blocking(text) is True, text

    @pytest.mark.parametrize("text", [
        "No blockers found. The fingerprint comparison is correct.",
        "no blocking issues; the escape path is documented in the denial.",
        "None of these are blocking — all four are follow-ups.",
        "0 blockers, 2 nits.",
        "I found zero blockers in the close path.",
        "Not blocking, but the docstring overstates what is enforced.",
        "Non-blocking observation: the constant could be named better.",
        "Nothing here is a blocker; the oracle rejects the bad cases.",
        "This is a solid change. The negative controls do reject the "
        "plausible bad implementations, and the escape path is honest.",
        "The word blockchain appears in a comment and is irrelevant here.",
    ])
    def test_reviewer_disclaiming_blockers_is_not_blocking(self, text):
        assert rv.classify_blocking(text) is False, text

    def test_a_disclaimer_does_not_cancel_a_real_blocker(self):
        """"No blockers in section 1" plus a real BLOCKER is still blocking.

        A per-line negation check that let any negation anywhere suppress the
        whole verdict would be trivially defeated by a reviewer's own habitual
        phrasing, and would silently downgrade real findings.
        """
        text = (
            "Section 1: no blockers, the parsing is fine.\n"
            "Section 2: BLOCKER — the record's version is never checked.\n"
        )
        assert rv.classify_blocking(text) is True

    def test_empty_text_is_not_blocking(self):
        assert rv.classify_blocking("") is False
        assert rv.classify_blocking(None) is False


# ---------------------------------------------------------------------------
# Digest — pins the whole verdict even when storage truncates it
# ---------------------------------------------------------------------------

class TestVerdictDigest:
    def test_digest_covers_text_beyond_the_storage_cap(self):
        """Truncation for storage must not truncate the evidence.

        A long verdict is stored clipped (Beads metadata is not a document
        store), so the digest has to be taken over the FULL text. If it were
        taken over the clipped text, everything after the cap — which is where
        a reviewer's conclusions usually live — would be outside the evidence.
        """
        head = "x" * rv.MAX_STORED_VERDICT_CHARS
        assert rv.verdict_digest(head + "BLOCKER") != rv.verdict_digest(head + "fine")

    def test_digest_is_stable_for_the_same_text(self):
        assert rv.verdict_digest("same") == rv.verdict_digest("same")

    def test_stored_text_is_clipped_with_an_explicit_marker(self):
        long_text = "y" * (rv.MAX_STORED_VERDICT_CHARS + 500)
        stored = rv.clip_verdict(long_text)
        assert len(stored) <= rv.MAX_STORED_VERDICT_CHARS + 100
        assert "truncated" in stored.lower()

    def test_short_text_is_stored_verbatim(self):
        assert rv.clip_verdict("short verdict") == "short verdict"


# ---------------------------------------------------------------------------
# Capability honesty
# ---------------------------------------------------------------------------

def test_capture_capability_is_declared_with_its_evidence():
    """The constant that decides whether this gate can enforce anything.

    This repo's standing rule is capability honesty: never describe as
    mechanically enforced something that is not. The flag that switches the
    strong rule on must carry, in the module itself, the evidence for its
    value — so that flipping it is a documented claim rather than a silent
    tightening or a silent surrender.
    """
    assert isinstance(rv.VERDICT_CAPTURE_SUPPORTED, bool)
    assert rv.CAPTURE_EVIDENCE.strip(), (
        "the capability constant must state what evidence set it"
    )
    assert len(rv.CAPTURE_EVIDENCE) >= 80


class TestTheDispatcherSPromptIsNotTheReviewersVerdict:
    """The echo hazard escapement-g27c recorded as an executable assertion.

    `PostToolUse.tool_response.prompt` repeats what the DISPATCHER asked for.
    A consumer that searches the payload broadly for verdict-shaped text can be
    satisfied by the implementer's own prompt instead of the subagent's reply —
    which is exactly the forgery this whole gate exists to prevent, arriving
    through the capture path rather than around it.

    So the extractor reads NAMED reply fields only. These pin that it never
    starts reading `prompt` or `description`, either by someone adding them to
    the key list or by a future "just stringify the payload" shortcut.
    """

    ECHO = (
        "Review escapement-1l04 and report BLOCKERS. Format: ### BLOCK / "
        "### Verdict with PASS or REJECT."
    )

    def test_a_tool_response_carrying_only_an_echoed_prompt_yields_nothing(self):
        assert rv.extract_verdict({
            "prompt": self.ECHO,
            "description": "review escapement-1l04",
            "agentId": "agent-1",
        }) is None

    def test_a_real_reply_is_preferred_over_the_echoed_prompt(self):
        got = rv.extract_verdict({
            "prompt": self.ECHO,
            "content": [{"type": "text", "text": "### Verdict\nPASS"}],
        })
        assert got == "### Verdict\nPASS"
        assert "Review escapement-1l04" not in got

    def test_the_agent_id_field_is_never_mistaken_for_prose(self):
        assert rv.extract_verdict({"agentId": "agent-abc123"}) is None
# ---------------------------------------------------------------------------
# The corpus that actually matters: what reviewers really emit (escapement-1nzm)
# ---------------------------------------------------------------------------

_REPO_ROOT = _HOOKS_DIR.parents[1]
_REVIEWER_AGENT = _REPO_ROOT / "claude" / "agents" / "adversarial-reviewer.md"

# A clean review, written in the exact format `adversarial-reviewer` mandates.
# Its BLOCK section is present and empty-by-negation, because the format
# requires the heading unconditionally so a reviewer must say "no blockers"
# out loud rather than silently omit the section.
CLEAN_REVIEW = """## Adversarial Review: _review_ledger.py

### Outcome under review
- **Intended outcome:** a dispatched reviewer's findings bind the close.
- **Would that proof fail if the outcome were NOT delivered?** YES.

### BLOCK
None. The fingerprint binding is correct and the tiebreak is sound.

### CONCERN
- `MAX_DISPATCH_AGE_SECONDS` is generous for a long session.

### NOTE
- Naming nit in `_candidate_paths`.

### Verdict
PASS

### What I'd break first
I would race two reviewers and hope the ledger picked the friendlier one.
"""

# The same format, same headings, but the reviewer actually found something.
BLOCKING_REVIEW = """## Adversarial Review: _review_ledger.py

### BLOCK
- `record_verdict` binds by recency (`_review_ledger.py:271`) -> two parallel
  reviewers cross-attach verdicts, so the stored bytes name the wrong dispatch.

### CONCERN
- `MAX_DISPATCH_AGE_SECONDS` is generous for a long session.

### Verdict
REJECT

### What I'd break first
The tiebreak, by dispatching twice.
"""


class TestMandatedReviewFormat:
    """The format the repo's own reviewer emits is the highest-probability input.

    escapement-1nzm: the classifier returned True for BOTH of these, because
    `### BLOCK` is an unconditional heading and the negation that answers it
    ("None.") sits on the next line. A classifier that cannot separate its two
    classes carries no information, and the denial it produces on a PASS is
    unrepairable by its own remedy -- the work fingerprint is unchanged, so
    re-recording cannot clear it and the only exit is the waiver.
    """

    def test_clean_review_in_the_mandated_format_is_not_blocking(self):
        assert rv.classify_blocking(CLEAN_REVIEW) is False

    def test_blocking_review_in_the_mandated_format_is_blocking(self):
        assert rv.classify_blocking(BLOCKING_REVIEW) is True

    def test_the_two_classes_are_actually_separated(self):
        """The property escapement-1nzm violated. Guards against a constant."""
        assert rv.classify_blocking(BLOCKING_REVIEW) != rv.classify_blocking(CLEAN_REVIEW)

    def test_declared_verdict_outranks_prose_markers(self):
        """A stated verdict is authoritative; do not re-infer it from prose.

        This review's prose is full of the word BLOCK -- it is *discussing* the
        gate -- while the reviewer stated PASS outright.
        """
        text = (
            "## Adversarial Review: review_gate.py\n\n"
            "### BLOCK\n"
            "None found.\n\n"
            "### NOTE\n"
            "- The BLOCK path in `evaluate_close` reads well.\n"
            "- `deny:blocking-findings` names its escape path.\n\n"
            "### Verdict\nPASS\n"
        )
        assert rv.classify_blocking(text) is False

    def test_unfilled_template_verdict_line_is_not_a_declaration(self):
        """`PASS / PASS WITH CONCERNS / REJECT` names every class at once.

        Reading it as a verdict would let an unfilled template decide the gate.
        It must fall through to prose rather than resolve to either class.
        """
        text = "### BLOCK\n- The ledger is world-writable.\n\n### Verdict\nPASS / PASS WITH CONCERNS / REJECT\n"
        assert rv.classify_blocking(text) is True

    def test_agent_definition_still_declares_the_sections_the_parser_reads(self):
        """The coupling, made mechanical rather than documented.

        `classify_blocking` treats the `### Verdict` section as authoritative
        and reads `### BLOCK` as a finding section. If the agent's Output
        Format stops emitting either, the classifier silently degrades to
        prose-scanning. This test is what makes that a failure instead.
        """
        spec = _REVIEWER_AGENT.read_text(encoding="utf-8")
        assert "### Verdict" in spec, "reviewer no longer declares a verdict section"
        assert "### BLOCK" in spec, "reviewer no longer emits a BLOCK section"


class TestNegationIsBoundedByClause:
    """A negation in an *earlier clause* must not cancel a later finding.

    The old window was five word-tokens with no clause boundary, so ordinary
    emphatic phrasing -- which the reviewer prompt explicitly trains -- silently
    downgraded real blockers to a clean pass.
    """

    @pytest.mark.parametrize("text", [
        "There is no way around this: BLOCKER - the migration drops the column.",
        "This is not a style nit - BLOCKER: tenant scope is missing.",
        "CVSS 9.0 - BLOCKER: unauthenticated RCE in the upload handler.",
        "I found nothing else. BLOCKER: the retry loop double-charges.",
        "Never mind the naming. BLOCKER: idempotency key is absent.",
    ])
    def test_negation_in_a_previous_clause_does_not_cancel(self, text):
        assert rv.classify_blocking(text) is True, text

    @pytest.mark.parametrize("text", [
        "No blockers found.",
        "None of these are blocking - all four are follow-ups.",
        "Nothing here is a blocker; the oracle rejects the bad cases.",
    ])
    def test_negation_in_the_same_clause_still_cancels(self, text):
        assert rv.classify_blocking(text) is False, text


class TestMarkerMustBeUsedAsALabel:
    """A marker word inside ordinary prose is not a verdict.

    `_LINE_START` matched case-insensitively at the head of any line, so a
    sentence merely *beginning* with "Critical" flagged the whole review.
    """

    @pytest.mark.parametrize("text", [
        "Critical sections are correctly guarded by the mutex.",
        "Blocking I/O here is intentional and fine.",
        "Critical to note: the tenant scope IS present.",
        "Summary: 2 concerns, one critical-path improvement, no blockers.",
        "The blocking behaviour of the queue is documented.",
    ])
    def test_marker_as_an_adjective_is_not_a_verdict(self, text):
        assert rv.classify_blocking(text) is False, text

    @pytest.mark.parametrize("text", [
        "BLOCKER: tenant scope missing on the billing query.",
        "**BLOCKER** - the ledger lives in world-writable /tmp.",
        "[BLOCKER] subagent_type is not re-validated on read.",
        "### BLOCK\n- The close path trusts implementer-authored text.\n",
    ])
    def test_marker_as_a_label_is_a_verdict(self, text):
        assert rv.classify_blocking(text) is True, text


class TestFindingSectionBody:
    """A finding heading answered with "None." is not a finding."""

    @pytest.mark.parametrize("text", [
        "### BLOCK\nNone.\n",
        "**BLOCKERS**\n\nNone identified.\n",
        "BLOCKERS: none\n",
        "## CRITICAL\n\nn/a\n",
        "### BLOCK\n\n_No blocking findings._\n",
    ])
    def test_heading_with_a_negating_body_is_not_blocking(self, text):
        assert rv.classify_blocking(text) is False, text

    @pytest.mark.parametrize("text", [
        "### BLOCK\n- Tenant scope is missing on the billing query.\n",
        "## CRITICAL\nThe corroboration is worth nothing.\n",
    ])
    def test_heading_with_a_real_body_is_blocking(self, text):
        assert rv.classify_blocking(text) is True, text


class TestDeclarationBeatsProse:
    """The cases where the two paths DISAGREE — the only ones that prove item 1.

    A corpus where prose-scanning happens to reach the same answer as the
    declared verdict cannot show the declaration is authoritative: deleting the
    declaration path entirely leaves it green. These three make them conflict.
    """

    def test_declared_reject_wins_over_an_empty_block_section(self):
        """Findings in CONCERN, verdict REJECT, BLOCK section empty.

        Prose-scanning alone reads this as clean. The reviewer said REJECT.
        """
        text = (
            "## Adversarial Review: escapement_review.py\n\n"
            "### BLOCK\nNone.\n\n"
            "### CONCERN\n"
            "- The recording CLI trusts `--host` without validating it.\n"
            "- Two of these together defeat the corroboration.\n\n"
            "### Verdict\nREJECT\n"
        )
        assert rv.classify_blocking(text) is True

    def test_declared_pass_wins_over_a_quoted_blocker(self):
        """A reviewer quoting the PREVIOUS round's blocker to say it is fixed.

        Prose-scanning reads the quoted label as a live finding. The reviewer
        declared PASS, and the reviewer is the authority on its own verdict.
        """
        text = (
            "## Adversarial Review: _review_ledger.py\n\n"
            "### BLOCK\nNone.\n\n"
            "### NOTE\n"
            "- Last round I wrote: BLOCKER: the tiebreak is unconstrained.\n"
            "  That is now fixed by the `bool(blocking)` sort key.\n\n"
            "### Verdict\nPASS\n"
        )
        assert rv.classify_blocking(text) is False

    def test_unfilled_template_falls_through_to_clean_prose(self):
        """The template names every class, so prose decides — and prose is clean.

        Paired with `test_unfilled_template_verdict_line_is_not_a_declaration`,
        which uses the same template line over BLOCKING prose. Together they pin
        that an ambiguous declaration resolves to *neither* class rather than
        defaulting to one.
        """
        text = (
            "### BLOCK\nNone.\n\n"
            "### Verdict\nPASS / PASS WITH CONCERNS / REJECT\n"
        )
        assert rv.classify_blocking(text) is False


class TestQuotedMaterialStrippedBeforeBothPaths:
    """Quoted text is not this reviewer's assertion — in EITHER path.

    Found by verdict-binding against a corpus built from the agent definitions.
    Their two reproducers live in test_review_verdict_corpus.py; these cover the
    parts the fix has to get right that a prose-only fix would miss, plus the
    false negative it could trade for.
    """

    def test_a_quoted_verdict_is_not_this_reviewers_declaration(self):
        """The hole a prose-only fix leaves open.

        Stripping quoted material from the prose scanner alone is not enough:
        `declared_verdict` runs FIRST, so a quoted REJECT would be adopted as
        this reviewer's own authoritative verdict.
        """
        text = (
            "## Adversarial Review: round 2\n\n"
            "Round 1 concluded:\n\n"
            "```\n"
            "### Verdict\n"
            "REJECT\n"
            "```\n\n"
            "Both findings are fixed at `billing.py:41`.\n\n"
            "### BLOCK\nNone.\n\n"
            "### Verdict\nPASS\n"
        )
        assert rv.classify_blocking(text) is False

    def test_a_quoted_clean_verdict_does_not_clear_a_real_blocker(self):
        """The same rule in the direction that must not go soft."""
        text = (
            "## Adversarial Review: round 2\n\n"
            "Round 1 concluded:\n\n"
            "```\n"
            "### Verdict\n"
            "PASS\n"
            "```\n\n"
            "### BLOCK\n- The tenant scope is still missing.\n"
        )
        assert rv.classify_blocking(text) is True

    def test_a_nested_list_item_is_still_a_finding(self):
        """The false negative the indented-code rule could have traded for.

        A four-space-indented sub-bullet is a nested finding, not a code block.

        The nested line is the ONLY content under the heading on purpose. An
        earlier version of this test put a parent bullet above it, which made it
        pass whether or not the nested line survived stripping — the parent
        alone was already a non-negating body. It asserted the right answer for
        the wrong reason, which is the exact defect class this file exists for.
        """
        assert rv.classify_blocking(
            "### BLOCK\n    - BLOCKER: tenant scope missing on the query.\n"
        ) is True

    def test_an_indented_line_that_is_not_a_list_item_is_code(self):
        """The control for the line above: without the list marker it IS code."""
        assert rv.classify_blocking(
            "### BLOCK\n    BLOCKER: tenant scope missing on the query.\n"
        ) is False

    def test_an_unclosed_fence_does_not_swallow_the_declared_verdict(self):
        text = "### BLOCK\n- Tenant scope missing.\n\n```\ntruncated paste\n"
        assert rv.classify_blocking(text) is True


class TestFalseNegativesUnderALabelOrAContradictingPass:
    """escapement-o84f — the direction escapement-1nzm under-weighted.

    1nzm arrived as a false-POSITIVE report (a clean PASS read as blocking), so
    the fix, its corpus, and its mutations were all aimed that way. Two false
    NEGATIVES survived, and those are the worse direction: a false positive
    denies an honest close and is loudly visible, while a false negative lets
    unreviewed work through and is silent.
    """

    def test_a_labelled_blocker_mid_sentence_blocks(self):
        """`_is_label` required the marker to OPEN a clause, so a marker
        introduced by ordinary sentence structure never registered — even with
        an explicit colon making it unambiguously a label."""
        assert rv.classify_blocking(
            "This is a BLOCKER: the migration drops the column."
        ) is True

    def test_findings_under_a_declared_pass_block(self):
        """A review that lists blockers and then declares PASS contradicts
        itself. The safe reading of a contradiction is the blocking one."""
        assert rv.classify_blocking(
            "### BLOCK\n- tenant scope missing.\n\n### Verdict\nPASS\n"
        ) is True

    def test_the_quoted_repair_case_still_passes(self):
        """THE interaction that can go wrong, pinned rather than assumed.

        A second review quoting round one's blockers to say they were addressed
        must still pass, or the repair loop cannot terminate and the waiver
        becomes the only exit. Findings-override-PASS must not reach into
        quoted material; `_strip_quoted` is what keeps it out, and this asserts
        the two rules compose rather than trusting that they do.
        """
        assert rv.classify_blocking(
            "## Adversarial Review: round 2\n\n"
            "### BLOCK\nNone.\n\n"
            "### NOTE\n"
            "Round 1 said:\n\n"
            "```\n"
            "### BLOCK\n"
            "- BLOCKER: the tiebreak is unconstrained.\n"
            "```\n\n"
            "Fixed at `_review_ledger.py:271`.\n\n"
            "### Verdict\nPASS\n"
        ) is False

    def test_an_empty_block_section_under_a_pass_still_passes(self):
        """The 1nzm control, restated here so a findings-override rule that
        ignored the section body would fail loudly rather than silently."""
        assert rv.classify_blocking(
            "### BLOCK\nNone.\n\n### Verdict\nPASS\n"
        ) is False

    def test_a_declared_reject_still_blocks(self):
        assert rv.classify_blocking("### Verdict\nREJECT\n") is True

    @pytest.mark.parametrize("text", [
        "### No blockers\nThe fingerprint comparison is correct.\n\n### Verdict\nPASS\n",
        "### Blocking behaviour\nThe queue blocks by design, and it is documented.\n\n### Verdict\nPASS\n",
        "### No blockers\nThe fingerprint comparison is correct.\n",
    ])
    def test_a_heading_that_negates_or_adjectivises_its_marker_is_not_findings(self, text):
        """The findings-override must apply the same label and negation
        discipline the prose scan does.

        Mutation testing found this: dropping the guard inside the section scan
        changed nothing, because the corpus had no heading that *negates* its
        own marker. "### No blockers" over a real body is an ordinary way to
        write a clean review, and without the guard all three of these override
        a declared PASS and deny the close.
        """
        assert rv.classify_blocking(text) is False, text
