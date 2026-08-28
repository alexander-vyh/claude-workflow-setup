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


# ---------------------------------------------------------------------------
# escapement-1nzm: a section heading is not a verdict
# ---------------------------------------------------------------------------

class TestRealisticReviewCorpus:
    """The corpus that caught it. Nine of these fourteen were misclassified.

    The defect was not a tuning problem. `adversarial-reviewer.md` mandates an
    UNCONDITIONAL `### BLOCK` heading — good format design, because it forces a
    reviewer to write "None." rather than silently omit the section — and the
    marker scanner read the heading itself as a verdict, with the negation on
    the next line where the window never looked. So it returned True for a
    clean review AND for a real one: on the mandated format it could not
    separate its two classes at all.

    It failed in BOTH directions. It also missed "This is a BLOCKER: ..." and a
    bare "### Verdict / REJECT" — false negatives, which is the direction that
    actually lets bad work through.

    These are the shapes real reviewers emit, not the shapes the parser's author
    imagined. 251 tests were green while nine of these were wrong, which is the
    whole argument for keeping them here.
    """

    CLEAN = [
        ("mandated format", "## Review\n### BLOCK\nNone.\n### CONCERN\nNaming.\n### Verdict\nPASS\n"),
        ("pass with concerns", "### BLOCK\nNone.\n### Verdict\nPASS WITH CONCERNS\n"),
        ("empty block body", "### BLOCK\n\n### CONCERN\n- minor\n### Verdict\nPASS\n"),
        ("dash placeholder", "### BLOCK\n-\n### Verdict\nPASS\n"),
        ("no blocking findings", "### BLOCK\nNo blocking findings.\n### Verdict\nPASS\n"),
        ("prose, no sections", "No blockers found. The change is sound.\n"),
        ("harmless prose mention",
         "Denying here would block every close in the repo, so the fail-open is right.\n"),
        ("non-blocking observation", "Non-blocking observation: naming could be clearer.\n"),
        ("block word inside a CONCERN body",
         "### BLOCK\nNone.\n### CONCERN\n- This could block a close if bd is slow.\n### Verdict\nPASS\n"),
        ("stray marker in a NOTE body",
         "### BLOCK\nNone.\n### NOTE\n- P0 systems often use this pattern.\n### Verdict\nPASS\n"),
    ]

    BLOCKING = [
        ("mandated format with a finding",
         "### BLOCK\n- Tax on pre-discount subtotal (pricing.py:21) -> customers overcharged.\n### Verdict\nREJECT\n"),
        ("declared REJECT only", "### Verdict\nREJECT\n"),
        ("labelled mid-sentence",
         "This is a BLOCKER: the migration drops the column before the backfill runs.\n"),
        ("counted", "I found three blockers in the close path.\n"),
    ]

    @pytest.mark.parametrize("name,text", CLEAN, ids=[n for n, _ in CLEAN])
    def test_a_clean_review_does_not_block_the_close(self, name, text):
        assert rv.classify_blocking(text) is False

    @pytest.mark.parametrize("name,text", BLOCKING, ids=[n for n, _ in BLOCKING])
    def test_a_real_blocker_still_blocks(self, name, text):
        assert rv.classify_blocking(text) is True

    def test_the_classifier_separates_its_two_classes(self):
        """The property that was actually violated, asserted directly.

        Every other test here could pass while the classifier returned a
        constant. This one cannot.
        """
        clean = {rv.classify_blocking(t) for _, t in self.CLEAN}
        blocking = {rv.classify_blocking(t) for _, t in self.BLOCKING}
        assert clean == {False}, "some clean reviews classify as blocking"
        assert blocking == {True}, "some real blockers classify as clean"


class TestDeclaredVerdictIsAuthoritative:
    def test_declared_reject_is_read(self):
        assert rv.declared_verdict("### Verdict\nREJECT\n") == "reject"

    def test_declared_pass_is_read(self):
        assert rv.declared_verdict("### Verdict\nPASS\n") == "pass"

    def test_inline_label_is_read(self):
        assert rv.declared_verdict("Verdict: REJECT — see below\n") == "reject"

    def test_the_unfilled_template_line_states_nothing(self):
        """`PASS / PASS WITH CONCERNS / REJECT` is the format's own placeholder.

        It contains both vocabularies. Reading it either way would be inventing
        a verdict the reviewer never gave, so it must fall through.
        """
        assert rv.declared_verdict("### Verdict\nPASS / PASS WITH CONCERNS / REJECT\n") is None

    def test_absent_verdict_falls_through(self):
        assert rv.declared_verdict("### BLOCK\nNone.\n") is None

    def test_findings_override_a_contradicting_pass(self):
        """A review listing blockers then saying PASS contradicts itself.

        The safe reading of a contradiction is the blocking one.
        """
        text = "### BLOCK\n- The migration drops the column before the backfill.\n### Verdict\nPASS\n"
        assert rv.classify_blocking(text) is True


class TestBlockSectionBody:
    @pytest.mark.parametrize("body", ["None.", "none", "-", "—", "n/a", "No blockers", "", "0"])
    def test_an_empty_body_is_not_a_finding(self, body):
        assert rv.block_section_has_findings(f"### BLOCK\n{body}\n### Verdict\nPASS\n") is False

    def test_a_populated_body_is_a_finding(self):
        assert rv.block_section_has_findings("### BLOCK\n- Real problem here.\n") is True

    def test_no_block_section_at_all(self):
        assert rv.block_section_has_findings("### CONCERN\n- minor\n") is False
