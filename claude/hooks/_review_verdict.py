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
    "CAPABILITY PROVEN, FEATURE STILL GATED. escapement-g27c (PR #183) "
    "established by observation, not inference, that a reviewer subagent's "
    "final text is SubagentStop.last_assistant_message, joined to its dispatch "
    "by tool_response.agentId == SubagentStop.agent_id; review_gate reads "
    "exactly those fields. This flag is False because of escapement-1nzm, not "
    "because of the host: classify_blocking returns blocking for a clean PASS "
    "on the format adversarial-reviewer.md mandates, so enabling capture would "
    "deny honest closes with a denial whose own remedy cannot clear it. Flip "
    "to True when 1nzm lands and the classifier separates its two classes."
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

# WHY A DECLARED VERDICT IS READ FIRST (escapement-1nzm)
# ------------------------------------------------------
# The previous classifier inferred the reviewer's conclusion by scanning prose
# for marker words. On the format `adversarial-reviewer` is actually mandated to
# emit it returned True for a clean PASS *and* for a genuine REJECT — because
# `### BLOCK` is an unconditional heading and the "None." that answers it sits on
# the next line. A classifier that cannot separate its two classes carries no
# information, and the denial it produced on a PASS was unrepairable by its own
# remedy: the work fingerprint is unchanged by definition, so re-recording could
# not clear it and the only exit was the waiver. Waiver rot arriving by the
# honest path.
#
# The fix is not a wider heuristic. The reviewer already STATES its verdict in a
# `### Verdict` section, so that statement is authoritative and prose scanning is
# only the fallback for output that declared nothing. Inferring an intent the
# author wrote down is the error; a look-ahead would have patched this one shape
# and left the same fragility one shape further out.
#
# COUPLING, DECLARED: this reads a section heading owned by
# `claude/agents/adversarial-reviewer.md` § "Output Format". If that format stops
# emitting `### Verdict` or `### BLOCK`, this degrades silently — so
# test_review_verdict.py asserts the agent definition still declares both. Change
# one, run that test.

#: Verdict words, by class. A value naming BOTH classes (the unfilled template
#: line `PASS / PASS WITH CONCERNS / REJECT`) resolves to neither: letting a
#: template decide the gate is how an unwritten review passes for a written one.
_BLOCKING_VERDICTS = (
    "REJECT", "REJECTED", "BLOCKED", "BLOCKING", "BLOCK",
    "FAIL", "FAILED", "NEEDS WORK", "CHANGES REQUESTED",
)
_PASSING_VERDICTS = ("PASS WITH CONCERNS", "PASS", "APPROVED", "APPROVE", "LGTM")

_VERDICT_HEADING = re.compile(r"^[#\s*_]*(?:VERDICT|STATUS|RESULT)\s*:?\s*\**\s*$", re.IGNORECASE)
_VERDICT_INLINE = re.compile(r"^[#\s*_\->]*(?:VERDICT|STATUS|RESULT)\s*[:\-]\s*(.+)$", re.IGNORECASE)
_HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*]+\*\*\s*$)")

# Markers that name a blocking finding. `BLOCKER`/`BLOCKING`/`CRITICAL` are the
# vocabulary the repo's own reviewer agents use; `MUST FIX` and `P0` cover the
# other two conventions seen in this corpus.
_MARKER = r"(?:BLOCKERS?|BLOCKING|BLOCK|CRITICAL|MUST[ \-]?FIX|P0)"
_MARKER_RE = re.compile(_MARKER, re.IGNORECASE)

# A clause boundary. The old negation scan walked back five word-tokens with no
# boundary at all, so "This is not a style nit - BLOCKER: tenant scope is
# missing" read as negated and a real blocker was silently downgraded. Emphatic,
# contrastive phrasing is exactly what the reviewer prompt trains, so the miss
# landed on the reviewers following their instructions. Note the dash must be
# SPACED: an unspaced hyphen is inside a word, and "Non-blocking" must keep its
# negation attached.
_CLAUSE_BOUNDARY = re.compile(r"(?:[.:;!?]|\s[-–—]\s)")

# Decoration a marker may sit behind and still open a clause: list bullets,
# emphasis, blockquote arrows, heading hashes, a bracket.
_DECORATION = " \t-*\u2022#>[_"

#: Words that flip a marker's meaning when they sit in the SAME clause before it.
_NEGATIONS = {"no", "not", "non", "none", "nothing", "never", "zero", "0", "without"}

#: Openers that make a finding-section body an explicit "nothing here".
_EMPTY_BODY = _NEGATIONS | {"n/a", "na", "nil", "clean"}

#: Count words that make "3 blockers" a claim even mid-sentence.
_COUNTED_RE = re.compile(
    rf"(?:\b\d+|\bone|\btwo|\bthree|\bfour|\bfive|\bseveral|\bmultiple)\s+{_MARKER}\b(?!-\w)",
    re.IGNORECASE,
)


def declared_verdict(text: str) -> bool | None:
    """The reviewer's own stated verdict: True blocking, False clean, None absent.

    None means "not stated unambiguously" — including the unfilled template,
    which names every class at once — and sends the caller to prose scanning.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        value = None
        inline = _VERDICT_INLINE.match(line)
        if inline:
            value = inline.group(1)
        elif _VERDICT_HEADING.match(line):
            value = next(
                (nxt for nxt in lines[index + 1:index + 4] if nxt.strip()), None
            )
        if not value:
            continue
        resolved = _resolve_verdict_value(value)
        if resolved is not None:
            return resolved
    return None


def _resolve_verdict_value(value: str) -> bool | None:
    upper = value.upper()
    blocking = any(word in upper for word in _BLOCKING_VERDICTS)
    passing = any(word in upper for word in _PASSING_VERDICTS)
    if blocking == passing:
        return None  # both (a template) or neither (prose): not a declaration
    return blocking


def classify_blocking(text: str | None) -> bool:
    """True when the reviewer's own text asserts an unresolved blocking finding.

    Reads what the reviewer declared; falls back to marker scanning only when it
    declared nothing. Still a text classifier, and still named as one — it reads
    the reviewer's vocabulary, not its meaning, and the waiver exists for what it
    gets wrong. What makes it worth having is whose text it reads: the
    implementer does not author it, so unlike a `--blocking` flag it cannot
    simply be set to false.
    """
    if not text:
        return False
    declared = declared_verdict(text)
    if declared is not None:
        return declared
    return _prose_asserts_blocker(text)


def _prose_asserts_blocker(text: str) -> bool:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        for match in _MARKER_RE.finditer(line):
            if _negated(line, match.start()):
                continue
            if not _is_label(line, match) and not _is_counted(line, match):
                continue
            body = line[match.end():].strip(" \t*:>-\u2013\u2014]")
            if not body:
                body = _section_body(lines, index)
            if not _body_is_empty(body):
                return True
    return False


def _is_label(line: str, match: re.Match) -> bool:
    """True when the marker is used to LABEL a finding, not as an adjective.

    Two halves, both required. It must OPEN a clause — otherwise "This will
    block - every close" reads as a verdict — and it must be TERMINATED like a
    label (`:`, `]`, a spaced dash, or end of line) rather than continuing into
    a sentence, which is what "Critical sections are correctly guarded" does.
    """
    before = line[:match.start()]
    if before.strip(_DECORATION):
        if not _CLAUSE_BOUNDARY.search(before) or before.rstrip()[-1:].isalnum():
            return False
    after = line[match.end():].lstrip("*_")
    return not after.strip() or after[:1] in ":]).," or after[:2] in (" -", " \u2013", " \u2014")


def _is_counted(line: str, match: re.Match) -> bool:
    """"3 blockers" is a claim even mid-sentence; "one critical-path" is not."""
    return any(
        m.end() == match.end() for m in _COUNTED_RE.finditer(line)
    )


def _section_body(lines: list[str], heading_index: int) -> str:
    """The lines a finding heading introduces, up to the next heading."""
    body: list[str] = []
    for line in lines[heading_index + 1:]:
        if _HEADING_LINE.match(line):
            break
        if line.strip():
            body.append(line.strip())
    return "\n".join(body)


def _body_is_empty(body: str) -> bool:
    """True when a finding section says, in words, that it found nothing."""
    content = [ln for ln in body.splitlines() if ln.strip()]
    if not content:
        return True
    for line in content:
        if not _words(line) or _words(line)[0] not in _EMPTY_BODY:
            return False
    return True


def _negated(line: str, marker_start: int) -> bool:
    """True when a negation sits in the same clause, before the marker."""
    before = line[:marker_start]
    boundaries = list(_CLAUSE_BOUNDARY.finditer(before))
    if boundaries:
        before = before[boundaries[-1].end():]
    return any(word in _NEGATIONS for word in _words(before))


def _words(text: str) -> list[str]:
    """Lowercase word tokens, treating markdown emphasis as a separator.

    `\\w` includes the underscore, so a `_No blocking findings._` body tokenised
    as `_no` and read as a real finding — the emphasis markup silently defeated
    the negation check.
    """
    return re.findall(r"[a-z0-9/']+", text.lower())
