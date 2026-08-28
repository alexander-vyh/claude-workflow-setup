#!/usr/bin/env python3
"""Capture what a reviewer actually said, and classify how severe it was.

THE HOLE THIS EXISTS TO CLOSE (escapement-1l04)
-----------------------------------------------
The dispatch ledger (`_review_ledger.py`) is written at `Agent` **PreToolUse** —
before the subagent emits a single token. So `independent: true` was granted for
the DISPATCH, never for the REVIEW. Two independent adversarial reviewers
converged on the same consequence: dispatch a genuinely isolated
`escapement:adversarial-reviewer` naming the bead, never read its answer, write
your own >=120-character verdict through the recording CLI, close. Every check
passes. The reviewer's findings — or its total failure to produce any — were
never consulted.

WHY THE CAPTURED TEXT IS THE RECORD, RATHER THAN SOMETHING COMPARED AGAINST IT
------------------------------------------------------------------------------
The obvious fix is to keep letting the implementer write `findings` and have the
gate check that it resembles the reviewer's output — by digest equality, prefix,
containment, or similarity. Every one of those is a threshold applied to text
authored by the party the gate constrains, which makes it tunable from the
inside. Containment is the worst of them: quoting the reviewer's opening
paragraph satisfies it while dropping the three BLOCKs underneath. Equality is
the opposite failure — brittle enough that honest work gets denied over trailing
whitespace, which trains agents to route around the gate.

So the reviewer's own bytes become the `findings` field. The implementer is
removed from the evidence path entirely and there is nothing left to tune
against. Their own account of the work is still worth keeping, so the CLI's
`--findings` is retained and stored as `response` — explicitly advisory, and
never what the gate reads.

This is also what makes the blocking classification below worth anything: it
reads text the implementer did not write. Classifying implementer-authored prose
for severity would be a self-report, and a self-report of "did the reviewer stop
me" has exactly one answer.

CAPABILITY HONESTY
------------------
Whether a hook can observe a subagent's final text at all is a property of the
host, not a design choice. `VERDICT_CAPTURE_SUPPORTED` carries that answer with
its evidence, and the strong rule is conditional on it. If the host cannot show
us the verdict, this module must not be described as enforcing anything — see
the note in `review_gate.py`'s docstring and the `review_gate` entry in
`agent-surfaces/manifest.json`.
"""

from __future__ import annotations

import hashlib
import re

#: Whether this host surfaces a reviewer subagent's final text to a hook.
#:
#: Load-bearing: when False, `findings` stays implementer-authored and neither
#: the traceability rule nor the blocking rule can be honestly enforced. It is a
#: plain constant rather than a runtime probe on purpose — a self-detecting
#: fallback ("assume capture is unavailable if we have not seen any") is
#: trivially triggered by an attacker, because *no captured verdicts* is the
#: default state of a fresh ledger. An honest constant that a human must flip is
#: safer than a detector that the adversary controls the input to.
VERDICT_CAPTURE_SUPPORTED = False

#: The evidence behind the constant above. Required to be non-empty by
#: test_review_verdict.py so that flipping the flag is a documented claim rather
#: than a silent tightening or a silent surrender.
CAPTURE_EVIDENCE = (
    "UNPROVEN as of 2026-08-27. escapement-g27c holds the open probe of whether "
    "a PostToolUse hook with matcher 'Agent' receives the subagent's final text. "
    "The only evidence today is inference from Claude Code binary strings — "
    "HOOK_EVENT_REGISTRY lists 'Agent' among the PostToolUse tools and the "
    "embedded docs say tool_response is populated there — and inference is not "
    "a captured payload. Because an over-claimed True would deny every close on "
    "this host, the conservative value is the honest one until the probe "
    "returns. Flip to True only when a real captured payload is pasted into "
    "escapement-g27c, and rewrite this string to cite it."
)

#: Beads metadata is not a document store, so a long verdict is stored clipped.
#: The digest is taken over the FULL text (see `verdict_digest`), so nothing
#: past the cap falls outside the evidence.
MAX_STORED_VERDICT_CHARS = 4000

_TRUNCATION_MARKER = "\n\n[... verdict truncated for storage; digest covers the full text]"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_verdict(tool_response: object) -> str | None:
    """Pull a reviewer's final text out of a host `tool_response`, or None.

    Deliberately shape-tolerant. Claude has shipped tool results as a bare
    string, as `{"content": [blocks]}`, and as a bare list of blocks; a host
    upgrade that changes the shape must not turn into a silent capability
    regression where the gate keeps passing its tests while capture quietly
    returns None and every review degrades to dispatch-only.

    Non-text blocks are skipped rather than stringified. Stringifying whatever
    is present would let a reviewer that returned no prose at all clear the
    120-character substance bar on `repr` noise.
    """
    text = _coerce_text(tool_response)
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def _coerce_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return _blocks_to_text(value)
    if isinstance(value, dict):
        for key in ("content", "output", "result", "response"):
            nested = value.get(key)
            if nested is not None:
                found = _coerce_text(nested)
                if found and found.strip():
                    return found
        text = value.get("text")
        return text if isinstance(text, str) else None
    return None


def _blocks_to_text(blocks: list) -> str | None:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            # Only text-bearing blocks contribute. An image or tool_use block
            # carries no verdict, and rendering its repr would be noise that
            # counts toward the substance bar.
            if block.get("type") in (None, "text") and isinstance(block.get("text"), str):
                parts.append(block["text"])
    joined = "\n".join(p for p in parts if p.strip())
    return joined or None


# ---------------------------------------------------------------------------
# Digest and storage
# ---------------------------------------------------------------------------

def verdict_digest(text: str | None) -> str | None:
    """SHA-256 of the reviewer's FULL text.

    Taken before clipping so that a verdict's conclusions — which reviewers
    tend to put last, past any cap — stay inside the evidence.
    """
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def clip_verdict(text: str) -> str:
    """Bound the stored copy, marking explicitly that it was cut."""
    if len(text) <= MAX_STORED_VERDICT_CHARS:
        return text
    return text[:MAX_STORED_VERDICT_CHARS] + _TRUNCATION_MARKER


# ---------------------------------------------------------------------------
# Blocking classification
# ---------------------------------------------------------------------------

# Markers that name a blocking finding. `BLOCKER`/`BLOCKING`/`CRITICAL` are the
# vocabulary the repo's own reviewer agents use; `MUST FIX` and `P0` cover the
# other two conventions seen in this corpus.
_MARKER = r"(?:BLOCKERS?|BLOCKING|BLOCK|CRITICAL|MUST[ \-]?FIX|P0)"

# A marker only counts in a *verdict position*. Bare mid-sentence "block" is
# ordinary review prose — "this would block every close in the repo" is a
# sentence a reviewer writes while explaining why something is fine — and
# treating it as a verdict would deny clean reviews, teaching agents that the
# waiver is the normal path. Three positions qualify:
#
#   (a) line-start, allowing markdown decoration (`- `, `**`, `## `, `[`)
#   (b) after an explicit verdict label (`VERDICT: BLOCK`)
#   (c) after a count ("3 blockers", "one blocker")
#   (d) opening a clause, after a colon or dash ("Section 2: BLOCKER — ...")
_LINE_START = re.compile(rf"^[\s\-*•#>\[\]_]*(?:\*\*)?{_MARKER}\b", re.IGNORECASE)
_LABELLED = re.compile(rf"\b(?:VERDICT|STATUS|RESULT)\s*[:\-]\s*\**\s*{_MARKER}\b", re.IGNORECASE)
_COUNTED = re.compile(
    rf"\b(?:\d+|one|two|three|four|five|several|multiple)\s+{_MARKER}\b",
    re.IGNORECASE,
)
# Position (d). Reviewers label findings inline as often as they head a line
# with them, and requiring a line start would miss "Section 2: BLOCKER". The
# negation layer is what keeps this from firing on "Non-blocking observation",
# where the separator is the hyphen inside the word itself.
_CLAUSE_OPENER = re.compile(r"[:\-–—]\s*\**\s*$")

# Position (e). "This is a BLOCKER: the migration drops the column" is how
# reviewers label a finding mid-sentence, and the prose fallback used to miss it
# entirely — a false NEGATIVE on a genuine blocker, which is the direction that
# actually lets bad work through.
_MARKER_LABELS = re.compile(rf"{_MARKER}\s*\**\s*[:\-–—]", re.IGNORECASE)

# Words that flip a marker's meaning when they sit just before it. "No blockers
# found" and "none of these are blocking" are the single most common way a clean
# review is phrased, so missing this would make the classifier fire on precisely
# the reviews that should sail through. `zero` and `0` live here, not in the
# count set above, for the same reason.
_NEGATIONS = {"no", "not", "non", "none", "nothing", "never", "zero", "0", "without"}

# How far back to look for a negation. Wide enough for "none of these are
# blocking", narrow enough that a negation in an unrelated earlier clause does
# not silently cancel a real finding.
_NEGATION_WINDOW_WORDS = 5


# ---------------------------------------------------------------------------
# Structure: a section heading is not a verdict
# ---------------------------------------------------------------------------
#
# THE DEFECT THIS EXISTS TO FIX (escapement-1nzm). `adversarial-reviewer.md`
# mandates an output format that emits a `### BLOCK` heading UNCONDITIONALLY,
# so a reviewer must write "None." rather than silently omit the section. That
# is good format design. But a marker-scanning classifier read the heading
# itself as a verdict position, and the negation sat on the next line where the
# negation window never looked — so a clean PASS review classified as blocking.
#
# It was worse than a false positive: scanning returned True for a clean review
# AND for a real one, so on the mandated format the classifier could not
# separate its two classes at all. A classifier that cannot separate its classes
# is not a weak oracle; it is not an oracle. It also missed real blockers phrased
# as "This is a BLOCKER: ..." and a bare "### Verdict / REJECT".
#
# The fix is structural rather than another pattern: read what the reviewer
# DECLARED, and read a section's BODY rather than its heading. Prose scanning
# survives only as the fallback for reviews that use neither.

# COUPLING: the headings parsed here are mandated by
# `claude/agents/adversarial-reviewer.md`. That file emits `### BLOCK` and
# `### Verdict` unconditionally; this module depends on both. Neither file used
# to say so, which is how a format change and a parser silently disagreed until
# a clean review was classified as blocking. Change one, change the other.
_BLOCK_HEADING = re.compile(rf"^\s*#{{1,6}}\s*\**\s*{_MARKER}\b", re.IGNORECASE)
_ANY_HEADING = re.compile(r"^\s*#{1,6}\s+")
_VERDICT_HEADING = re.compile(r"^\s*#{1,6}\s*\**\s*VERDICT\b", re.IGNORECASE)
_VERDICT_INLINE = re.compile(
    r"\b(?:VERDICT|STATUS|RESULT)\s*[:\-]\s*\**\s*(.+)$", re.IGNORECASE
)

# Section bodies that mean "nothing here". A reviewer following the mandated
# format writes one of these when it found no blockers.
_EMPTY_BODY = {
    "", "-", "--", "---", "—", "n/a", "na", "none", "nothing", "no findings",
    "no blockers", "no blocking findings", "no blocking issues", "0", "zero",
}

_REJECT_WORDS = re.compile(
    r"\b(?:REJECT(?:ED)?|BLOCKED|BLOCKING|FAIL(?:ED)?|NO[ \-]?GO)\b", re.IGNORECASE
)
_PASS_WORDS = re.compile(r"\b(?:PASS(?:ED)?|APPROVED?|LGTM)\b", re.IGNORECASE)


def _normalise(line: str) -> str:
    """Strip markdown decoration and punctuation for an emptiness comparison."""
    return re.sub(r"^[\s\-*•+>\[\]_#]*|[\s.:;!*_\]]*$", "", line).strip().lower()


def _section_body(text: str, heading: re.Pattern) -> list[str] | None:
    """Return the lines under the first matching heading, or None if absent."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not heading.match(line):
            continue
        body = []
        for following in lines[index + 1:]:
            if _ANY_HEADING.match(following):
                break
            body.append(following)
        return body
    return None


def declared_verdict(text: str | None) -> str | None:
    """Return "reject", "pass", or None for the reviewer's stated verdict.

    Authoritative when present, because the reviewer states it outright and the
    implementer does not author it. Inferring a verdict by scanning prose when
    one has been declared is guessing at an answer already given.

    Returns None when the verdict is absent OR ambiguous — notably when the
    mandated template line `PASS / PASS WITH CONCERNS / REJECT` is left unfilled,
    which contains both vocabularies and states nothing.
    """
    if not text:
        return None
    body = _section_body(text, _VERDICT_HEADING)
    if body is None:
        for line in text.splitlines():
            inline = _VERDICT_INLINE.search(line)
            if inline:
                body = [inline.group(1)]
                break
    if not body:
        return None
    blob = " ".join(body).strip()
    if not blob:
        return None
    rejects, passes = bool(_REJECT_WORDS.search(blob)), bool(_PASS_WORDS.search(blob))
    if rejects and not passes:
        return "reject"
    if passes and not rejects:
        return "pass"
    return None


def block_section_has_findings(text: str | None) -> bool:
    """True when a BLOCK-style section exists and its body lists something.

    The heading is never itself the signal — it is emitted unconditionally by
    design. Only the body counts, and a body of "None." counts as nothing.
    """
    if not text:
        return False
    body = _section_body(text, _BLOCK_HEADING)
    if body is None:
        return False
    for line in body:
        normalised = _normalise(line)
        if not normalised or normalised in _EMPTY_BODY:
            continue
        if any(normalised.startswith(word + " ") for word in _NEGATIONS):
            continue
        return True
    return False


def classify_blocking(text: str | None) -> bool:
    """True when the reviewer's own text asserts an unresolved blocking finding.

    Three layers, most reliable first:

      1. the reviewer's DECLARED verdict, when it states one unambiguously;
      2. the BODY of a BLOCK section, which overrides a declared "pass" — a
         review that lists blockers and then says PASS is contradicting itself,
         and the safe reading of a contradiction is the blocking one;
      3. prose scanning, only for reviews that declare neither.

    Layer 3 remains a conservative vocabulary matcher and is named as one: it
    reads the reviewer's words, not its meaning, and will miss a blocker
    described with no marker at all. What makes the whole thing worth having is
    whose text it reads — the implementer does not author it, so unlike a
    `--blocking` flag it cannot simply be set to `false`.
    """
    if not text:
        return False

    declared = declared_verdict(text)
    if declared == "reject":
        return True
    if block_section_has_findings(text):
        return True
    if declared == "pass":
        return False

    for line in text.splitlines():
        if not line.strip():
            continue
        for match in re.finditer(_MARKER, line, re.IGNORECASE):
            if _negated(line, match.start()):
                continue
            if _in_verdict_position(line, match):
                return True
    return False


def _in_verdict_position(line: str, match: re.Match) -> bool:
    if match.start() == 0 or _LINE_START.match(line):
        # Only credit the line-start rule to the marker that is actually there.
        head = _LINE_START.match(line)
        if head and head.end() >= match.end():
            return True
    if _LABELLED.search(line):
        return True
    if _CLAUSE_OPENER.search(line[:match.start()]):
        return True
    if _MARKER_LABELS.match(line, match.start()):
        return True
    return bool(_COUNTED.search(line))


def _negated(line: str, marker_start: int) -> bool:
    """True when a negation sits within the preceding few words."""
    preceding = re.findall(r"[\w']+", line[:marker_start])
    return any(w.lower() in _NEGATIONS for w in preceding[-_NEGATION_WINDOW_WORDS:])
