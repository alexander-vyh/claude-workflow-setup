#!/usr/bin/env python3
# file-complexity-waiver: pre-existing 930-line gate; move-1 retires the verification-evidence machinery (~150 lines) but it stays >500 — full split tracked in claude-workflow-setup-e9v.7
"""Claude Code hook: block outcome-shirking ("pre-existing failure" evasion).

Fires as:
  • PreToolUse  — on git commit / gh pr create / git push
  • Stop        — when the agent declares itself done

When the agent recently used dismissive language about test/job failures
("pre-existing", "not in anything I changed", "unrelated to our changes"),
this hook blocks the finishing action and forces the agent to either:
  1. Fix the failing test/job, OR
  2. Obtain explicit user approval to proceed with a known failure.

Input (via stdin):
  JSON with hook_event_name, tool_name, tool_input, transcript_path
Exit codes:
  0 — allow
  2 — block (JSON output explains why)
"""

# PEP 604 annotations below are evaluated when each def executes, so without
# this import the module raises TypeError on import under Python 3.9.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NoReturn

# Shared signal capture per claude/rules/gate-design.md Rule 2.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None

try:
    import _local_judge_client as _lj
except ImportError:  # pragma: no cover
    _lj = None

# ---------------------------------------------------------------------------
# Shirking patterns — all case-insensitive
# ---------------------------------------------------------------------------
_PATTERNS: list[str] = [
    # Classic "pre-existing" language
    r"pre[- ]?existing\s+(?:failure|issue|bug|problem|test|error)",
    r"(?:appears?|seems?|looks?|is)\s+(?:to\s+be\s+)?a\s+pre[- ]?existing",
    r"(?:let\s+me\s+check|check(?:ing)?)\s+if\s+(?:this\s+is\s+)?(?:a\s+)?pre[- ]?existing",
    # "not in anything I changed / touched"
    r"not\s+in\s+anything\s+I\s+(?:changed|modified|touched|wrote)",
    r"not\s+in\s+(?:the\s+)?(?:files?|code)\s+I\s+(?:changed|modified|touched)",
    # "unrelated" — only in dismissive contexts (not bare "unrelated modules" etc.)
    r"\bunrelated\s+(?:to\s+(?:this|the|my|our)|issue|bug|problem|failure|change)\b",
    r"\bnot\s+related\s+(?:to\s+(?:this|the|my|our)|issue|bug|problem|failure|change)\b",
    r"not\s+(?:related|relevant)\s+to\s+(?:our|my|this|the)\s+changes?",
    r"not\s+(?:caused\s+by|from|part\s+of)\s+(?:our|my|this|the)\s+changes?",
    # "was already failing / broken"
    r"was\s+already\s+(?:failing|broken)",
    r"(?:failing|failed|broken)\s+before\s+(?:our|my|this|the)\s+changes?",
    r"(?:failing|failed|broken)\s+(?:before|prior\s+to)\s+(?:my|our|this)",
    # "completely different problem" / "separate issue from …"
    r"completely\s+different\s+(?:problem|issue|bug|failure)",
    r"separate\s+(?:issue|problem|bug|failure)\s+(?:from|to|than)",
    # "I didn't change/touch that test file"
    r"I\s+didn['\u2019]?t\s+(?:change|touch|modify|write|create)\s+.{1,80}(?:test|file|module)",
    r"didn['\u2019]?t\s+touch\s+.{1,80}(?:test|file|module)",
    # "test failure is unrelated"
    r"test\s+(?:failure|error)\s+(?:is|was|seems?|appears?)\s+(?:unrelated|separate|not\s+related)",
    # Attribution deflection
    r"not\s+(?:my|our)\s+(?:problem|issue|fault|change|doing|code|responsibility|bug|job|concern|area)",
    r"(?:that['\u2019]?s|this\s+is)\s+(?:a\s+)?(?:separate|different|unrelated)\s+(?:bug|issue|problem|failure)",
    # Acceptance evasion — explicitly deciding to live with failures and move on
    r"note\s+(?:this\s+|it\s+)?and\s+move\s+(?:on|past)",
    r"just\s+accept\s+the\s+(?:errors?|failures?|issues?)",
    r"accept\s+the\s+(?:errors?|failures?|issues?)\s+until",
    # Infrastructure / CI blame — externalizing to other systems. Blame needs the
    # DISMISSIVE COPULA FRAMING ("it's / looks like / probably an environment
    # issue"), not the bare noun phrase: "fixed the deployment bug" is ownership,
    # and the bare form looped the Stop gate on it (live FP 2026-07-01, cake xubhk).
    r"(?:\bis|\bwas|it['’]?s|that['’]?s|this\s+is|seems?|appears?|looks?\s+like|"
    r"must\s+be|probably|likely|just)\s+(?:to\s+be\s+)?(?:probably\s+|likely\s+|just\s+)?"
    r"(?:an?\s+|the\s+|some\s+)?CI\s+(?:infra(?:structure)?\s+)?(?:issue|problem|failure|error|bug)",
    r"(?:\bis|\bwas|it['’]?s|that['’]?s|this\s+is|seems?|appears?|looks?\s+like|"
    r"must\s+be|probably|likely|just)\s+(?:to\s+be\s+)?(?:probably\s+|likely\s+|just\s+)?"
    r"(?:an?\s+|the\s+|some\s+)?(?:infra(?:structure)?|pipeline|build\s+system|environment|"
    r"deploy(?:ment)?|runner)\s+(?:issue|problem|failure|error|bug)",
    r"(?:the\s+)?(?:CI|pipeline|build|runner)\s+(?:is|seems?|appears?)\s+(?:broken|flaky|unstable|down)",
    # Deferral — punting to future work instead of fixing now
    r"(?:needs?|requires?)\s+(?:a\s+)?separate\s+(?:investigation|fix|ticket|PR|issue|effort|task|attention)",
    r"(?:can|will|should)\s+(?:be\s+)?(?:fixed|addressed|resolved|handled|investigated)\s+(?:later|separately|in\s+(?:a\s+)?(?:follow[- ]?up|separate|different|another))",
    r"(?:leav(?:e|ing)|left)\s+(?:this|that|it)\s+(?:for\s+now|as[- ]?is|alone)",
    # Scope limitation
    r"(?:outside|beyond|out\s+of)\s+(?:the\s+)?scope(?:\s+of)?",
    # Dismissing as flaky / known
    r"(?:it'?s|that'?s|this\s+is|just)\s+(?:an?\s+)?(?:flaky|intermittent|transient|sporadic)\s+(?:test|failure|check|build)",
    r"(?:this\s+is|that'?s|it'?s)\s+a\s+(?:known|existing|tracked)\s+(?:issue|bug|problem)",
]

# Categories aligned by index with _PATTERNS. Used by the denial to name
# *which kind* of shirking matched and by gate_signal_query.py to count
# fire frequency per category (the half-life mechanism per gate-design.md
# Operating Rule 1: query the signal log to see which patterns fire most
# and prune the dead ones).
_CATEGORIES: list[str] = [
    "pre-existing",            # pre-existing failure/issue/bug...
    "pre-existing",            # appears to be a pre-existing
    "pre-existing",            # checking if this is pre-existing
    "attribution-deflection",  # not in anything I changed
    "attribution-deflection",  # not in the files I changed
    "unrelated",               # unrelated to this/my/our
    "unrelated",               # not related to ...
    "unrelated",               # not related/relevant to our changes
    "unrelated",               # not caused by our changes
    "pre-existing",            # was already failing/broken
    "pre-existing",            # failing before our changes
    "pre-existing",            # failing prior to my
    "unrelated",               # completely different problem
    "unrelated",               # separate issue from/to/than
    "attribution-deflection",  # I didn't change/touch ...
    "attribution-deflection",  # didn't touch ...
    "unrelated",               # test failure is unrelated
    "attribution-deflection",  # not my/our problem/issue/...
    "unrelated",               # that's a separate/different/unrelated bug
    "acceptance-evasion",      # note this and move on
    "acceptance-evasion",      # just accept the errors
    "acceptance-evasion",      # accept the errors until
    "infrastructure-blame",    # CI issue/problem/...
    "infrastructure-blame",    # infra/pipeline/build/environment issue
    "infrastructure-blame",    # CI/pipeline/build/runner is broken/flaky
    "deferral",                # needs separate investigation/fix/...
    "deferral",                # will be fixed later/separately
    "deferral",                # leaving this for now / as-is
    "scope-limitation",        # outside/beyond/out of scope
    "dismissal",               # it's a flaky test/failure
    "dismissal",               # this is a known/existing/tracked issue
]

# Sanity check: categories must align with patterns. This is a startup
# assertion — if someone edits one list without the other, the hook
# fails loudly rather than silently miscategorizing.
assert len(_CATEGORIES) == len(_PATTERNS), (
    f"_CATEGORIES ({len(_CATEGORIES)}) must align with _PATTERNS ({len(_PATTERNS)})"
)

# Map each category to a one-line description used in the denial,
# pointing the agent at where the rationale lives.
_CATEGORY_RATIONALE: dict[str, str] = {
    "pre-existing": "claiming a failure pre-dates your change deflects ownership; even if true, the failure is in your delivery window now.",
    "attribution-deflection": "'I didn't touch that' deflects ownership; if the failure surfaces from your work, it's yours to either fix or escalate.",
    "unrelated": "declaring a failure unrelated assumes the boundary you're drawing is the boundary that matters.",
    "infrastructure-blame": "blaming CI / pipeline / 'works locally' externalizes a problem the outcome still requires resolved.",
    "deferral": "punting to 'later' or a 'separate ticket' is wind-down disguised as scoping.",
    "scope-limitation": "'out of scope' is a real defense sometimes — but only if the genuinely-out-of-scope work was filed; otherwise it's deferral.",
    "acceptance-evasion": "explicitly choosing to live with failures is the most direct form of the anti-pattern.",
    "dismissal": "'flaky' or 'known issue' may be true, but the burden is on you to confirm — not assume.",
    "stop-solicitation": "asking the user whether you should stop or continue is wind-down behavior; continue, verify completion, or name the concrete blocker.",
}

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]

_STOP_SOLICITATION_SYSTEM = (
    "You judge whether an AI coding agent's message is STOP-SOLICITATION: "
    "asking the user whether the agent should stop, pause, wrap, hand work back, "
    "or continue, instead of taking the next in-scope action, stating verified "
    "completion, or naming a concrete blocker. Technical/product questions about "
    "stopping conditions, worker shutdown, wrapping strings, or implementation "
    "choices are NOT stop-solicitation. Answer with ONLY one token: "
    "stop_solicitation | not_stop_solicitation | unclear"
)
_STOP_SOLICITATION_BACKSTOP_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwant\s+me\s+to\b(?=[^?]{0,500}\bor\b)(?=[^?]{0,500}\b(?:wrap|stop|pause|keep\s+going|continue)\b)",
        r"\bwant\s+any\s+of\s+those\b(?=[^?]{0,300}\bstopping\s+point\b)",
        r"\bis\s+this\s+(?:a\s+)?(?:good|clean|natural|reasonable|honest)\s+stopping\s+point\b",
        (
            r"\bwant\s+me\s+to\b"
            r"(?=[^?]{0,650}\bor\b)"
            r"(?=[^?]{0,650}\b(?:open\s+(?:a\s+)?(?:draft\s+)?pr|draft\s+pr)\b)"
            r"(?=[^?]{0,650}\bfinish\b)"
            r"(?=[^?]{0,650}\bsave\s+(?:a\s+)?memory\b)"
        ),
        r"\bshould\s+I\s+(?:continue|keep\s+going)\b(?=[^?]{0,250}\bor\b)(?=[^?]{0,250}\b(?:stop|pause|wrap|leave|end|call\s+it)\b)",
        r"\blet\s+me\s+know\s+if\s+you\s+want\s+me\s+to\s+(?:continue|keep\s+going|proceed)\b",
    )
]

# Bash commands that represent "I'm done" — trigger the PreToolUse path
FINISHING_COMMANDS = ["git commit", "gh pr create", "gh pr merge", "git push"]

# How far back in the transcript to look (characters)
TRANSCRIPT_WINDOW = 25_000

# Signatures of this hook's own block messages AND meta-discussion about the hook.
# Messages containing any of these are skipped to prevent self-referential triggers
# (e.g., the agent quoting or explaining the hook's behavior).
_HOOK_SIGNATURES = frozenset({
    "OUTCOME OWNERSHIP VIOLATION",
    "VERIFICATION REQUIRED",
    "FIX THE FAILURES NOW:",
    "RUN VERIFICATION NOW:",
    # Self-referential names: a message that mentions this hook or the rule
    # file it enforces is discussing the gate, not shirking through it.
    "outcome-ownership.md",
    "validate_no_shirking",
})

# ---------------------------------------------------------------------------
# User approval patterns — detect when the user explicitly said "go ahead"
# (The hook tells agents to "obtain explicit user approval", so we must
#  honour that approval when we see it.)
# ---------------------------------------------------------------------------
_APPROVAL_PATTERNS: list[str] = [
    r"\byes\b",
    r"\bapproved?\b",
    r"\bproceed\b",
    r"\bgo\s+ahead\b",
    r"\blgtm\b",
    r"\bthat'?s\s+(?:fine|ok(?:ay)?)\b",
]
_COMPILED_APPROVAL = [re.compile(p, re.IGNORECASE) for p in _APPROVAL_PATTERNS]


# ---------------------------------------------------------------------------
# Verification evidence (Level 3: require proof of outcome)
# ---------------------------------------------------------------------------

_CODE_MOD_TOOLS = frozenset({
    "Edit", "Write", "NotebookEdit",
    "mcp__serena__replace_symbol_body",
    "mcp__serena__insert_after_symbol",
    "mcp__serena__insert_before_symbol",
    "mcp__serena__rename_symbol",
})

# Only Edit/Write target arbitrary file paths; NotebookEdit (.ipynb) and the
# Serena symbol tools operate on code by construction, so they are always
# code-mods. For Edit/Write we inspect the path: prose/docs edits are NOT code
# modifications and must not demand a verification run (2026-06-01 false-positive:
# the gate fired on a markdown-only memory edit). This mirrors the prose/docs
# exemption in claude/rules/tdd-enforcement.md. Behavioral config (CI YAML, IaC,
# manifests) is deliberately NOT exempt there, so it stays a code-mod here too —
# the exemption is prose/docs ONLY, by file extension.
_PATH_CHECKED_TOOLS = frozenset({"Edit", "Write"})
_DOCS_EXTENSIONS = (".md", ".markdown", ".txt", ".rst", ".adoc")


def _is_docs_path(file_path: str) -> bool:
    """True iff file_path is a prose/docs file (exempt from the verification gate)."""
    if not isinstance(file_path, str) or not file_path:
        return False
    return file_path.lower().endswith(_DOCS_EXTENSIONS)


# ---------------------------------------------------------------------------
# Transcript reading
# ---------------------------------------------------------------------------

def read_recent_agent_text(transcript_path: str) -> str:
    """Extract text from recent assistant turns in the JSONL transcript."""
    path = Path(transcript_path)
    if not path.exists():
        return ""

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    tail = raw[-TRANSCRIPT_WINDOW:] if len(raw) > TRANSCRIPT_WINDOW else raw

    chunks: list[str] = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            # Non-JSON line — include as-is; pattern may appear in raw text
            chunks.append(line)
            continue

        # Support both {"type":"assistant","message":{…}} and {"role":"assistant",…}
        msg = entry.get("message", entry)
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue

        content = msg.get("content", [])
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(block.get("text", ""))
                elif isinstance(block, str):
                    chunks.append(block)

    return "\n".join(chunks)


def read_recent_messages(transcript_path: str) -> list[tuple[str, str]]:
    """Extract (role, text) pairs from recent transcript entries, in order.

    Unlike read_recent_agent_text, this preserves *both* assistant and user
    messages so we can detect user approval after a shirking phrase.
    """
    path = Path(transcript_path)
    if not path.exists():
        return []

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    tail = raw[-TRANSCRIPT_WINDOW:] if len(raw) > TRANSCRIPT_WINDOW else raw

    messages: list[tuple[str, str]] = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg = entry.get("message", entry)
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "")
        if role not in ("assistant", "user"):
            continue

        text_parts: list[str] = []
        content = msg.get("content", [])
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    text_parts.append(blk.get("text", ""))
                elif isinstance(blk, str):
                    text_parts.append(blk)

        if text_parts:
            messages.append((role, "\n".join(text_parts)))

    return messages


# ---------------------------------------------------------------------------
# Code-block stripping — avoid false positives from quoted code/patterns
# ---------------------------------------------------------------------------

_FENCED_BLOCK = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_TILDE_FENCE = re.compile(r"~~~[\s\S]*?~~~", re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`]+`")
# Quoting/example markup: content the agent is showing or that the harness
# injected, not asserting. Strip the whole tagged span (and its contents) so a
# shirking phrase quoted as an example or inside a system reminder does not fire.
_QUOTED_TAGS = re.compile(
    r"<(system-reminder|example)>[\s\S]*?</\1>",
    re.IGNORECASE,
)


def _strip_code_spans(text: str) -> str:
    """Remove fenced code blocks, inline code spans, and quoted markup.

    Both ``` and ~~~ fences are stripped, along with the contents of
    <system-reminder> and <example> tags — these hold quoted or injected
    content, not the agent's own assertions, so a shirking phrase inside
    them is not the agent shirking.
    """
    text = _FENCED_BLOCK.sub("", text)
    text = _TILDE_FENCE.sub("", text)
    text = _QUOTED_TAGS.sub("", text)
    text = _INLINE_CODE.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Negation / meta-description guard
# ---------------------------------------------------------------------------
#
# A shirking phrase that the agent *disavows* ("I will NOT claim it's
# pre-existing") or *describes* ("scan for phrases like 'not my problem'") is
# not the agent shirking. These cues, when they appear in the short window of
# text immediately before a match, mark the match as disavowal/description
# rather than assertion. The window is bounded (GUARD_WINDOW) so that a real
# shirking assertion appearing far enough after a descriptive cue still fires
# (see test_scan_for_real_match_after_window_still_flagged).

GUARD_WINDOW = 40

# Negation cues: the phrase is being denied, forbidden, or made conditional.
# Checked only in the short window immediately before a match — "I will NOT
# claim it's pre-existing" disavows; "is pre-existing" asserts. Bare "like" /
# "such as" are deliberately NOT here: "looks like a pre-existing failure" is a
# real assertion (those belong to the quoted meta-description path below).
_NEGATION_CUES = re.compile(
    r"\b(?:not|n['’]?t|avoid|never|don['’]?t|doesn['’]?t|"
    r"won['’]?t|without|if|unless|whether)\b",
    re.IGNORECASE,
)

# Clause boundaries. English negation scopes within its own clause: in
# "I do not have time, leaving this for now" the "not" negates "have time",
# and the comma ends that clause — the deferral that follows is a fresh,
# unconditional assertion. So a negation cue only disavows the matched phrase
# when NO clause break sits between the cue and the match.
_CLAUSE_BREAK = re.compile(r"[,;:—–]|--")
# For the detector-naming meta-guard (below): clause breaks AND sentence
# boundaries (. ! ?). A strong meta-cue in an EARLIER clause or sentence must
# not launder a shirk asserted later ("the hook is fine. This failure is
# unrelated." still fires) — never-suppress.
_META_SCOPE_BREAK = re.compile(r"[,;:—–.!?]|--")

# Certainty idioms opened by "without". "Without a doubt …" / "Without
# question …" are the INVERSE of disavowal — they intensify the assertion that
# follows. So a "without" cue that opens one of these does NOT guard the match
# ("Without a doubt this is a pre-existing failure" is real shirking). Matched
# only when it immediately follows the cue, so a genuine disavowal ("without
# claiming it's pre-existing") still guards.
_WITHOUT_CERTAINTY = re.compile(r"\bwithout$", re.IGNORECASE)
_CERTAINTY_TAIL = re.compile(r"^\s+(?:a\s+)?(?:doubt|question)\b", re.IGNORECASE)

# Meta-description cues: the phrase is being named as an example of the kind of
# thing to look for, not used as an assertion. Unlike negation, these are
# checked across the whole preceding text (not a fixed window) but ONLY when
# the match itself is wrapped in quotation marks — quoting is the structural
# signal that the phrase is mentioned, not used. An unquoted shirking
# assertion after a meta-cue ("scan for signs ... it was already failing")
# still fires.
_META_CUES = re.compile(
    r"\b(?:scan(?:ning)?\s+for|look(?:ing)?\s+for|check(?:ing)?\s+for|"
    r"search(?:ing)?\s+for|watch(?:ing)?\s+for|detect(?:ing|s)?|"
    r"phrases?\s+like|patterns?\s+like|signs?\s+(?:of|it)|"
    r"language\s+(?:such\s+as|like)|such\s+as\b)",
    re.IGNORECASE,
)

# Strong meta-cues NAME THE DETECTOR ITSELF (not the dismissive category words).
# When the agent is talking ABOUT this gate — "validate_no_shirking fired on my
# explanation of why a CI failure looked unrelated" — the match is meta-discussion,
# not shirking, even when UNQUOTED. An agent actually shirking does not say
# "the shirking hook fires on 'unrelated'", so detector-naming cues are safe to
# guard unquoted. Category words ("pre-existing", "unrelated") are NEVER cues here
# (that would launder a real deflection — never-suppress). Bead 858.5 / design Step 3.
_STRONG_META_CUES = re.compile(
    r"\b(?:shirk(?:ing)?|false[\s-]?positive|validate_no_shirking|the\s+hook|"
    r"this\s+(?:gate|check|hook)\s+(?:fire|match|flag|trigger|catch)\w*|"
    r"keyword\s+match|meta[\s-]?discussion|"
    # The gate's own category labels (hyphenated internal vocabulary — an agent
    # actually shirking writes "it's a CI issue", never "infrastructure-blame").
    # Plain-word labels (pre-existing, unrelated, deferral, dismissal) are NEVER
    # cues — that would launder the real deflections they name (never-suppress).
    r"infrastructure-blame|attribution-deflection|acceptance-evasion|"
    r"scope-limitation|stop-solicitation|outcome\s+ownership\s+violation|"
    # Detector verbs: classifying a phrase is gate-talk, not shirking.
    r"flagg(?:ing|ed|s)?\s+(?:it|this|that)\s+as|was\s+flagged\s+as|"
    r"re-?scan(?:ning|s|ned)?)\b",
    re.IGNORECASE,
)

# Straight and curly double-quote characters used to delimit quoted examples.
_QUOTE_CHARS = "\"“”"


def _inside_quotes(stripped: str, match_start: int) -> bool:
    """True if the match begins inside an open double-quoted span.

    Counts unbalanced double-quote characters before the match: an odd count
    means the match sits inside a quotation (a mentioned phrase), an even
    count means it is unquoted (an asserted phrase).
    """
    quote_count = sum(stripped[:match_start].count(q) for q in _QUOTE_CHARS)
    return quote_count % 2 == 1


def _negation_guards(window: str) -> bool:
    """True if a negation cue in the window actually scopes the match.

    A negation only disavows the matched phrase when it is in the SAME clause —
    i.e. no clause break (comma, semicolon, colon, em/en-dash, `--`) sits
    between the cue and the match. We test the nearest (last) cue in the
    window: if a clause break follows it, that cue belongs to an earlier clause
    and does not guard the match ("I do not have time, leaving this for now").
    """
    last = None
    for m in _NEGATION_CUES.finditer(window):
        last = m
    if last is None:
        return False
    # Text between the end of the cue and the match (the window ends at match).
    tail = window[last.end():]
    # "without a doubt" / "without question" are certainty idioms, not
    # disavowals: they intensify the assertion that follows, so this cue does
    # not guard the match. A genuine "without claiming ..." disavowal has no
    # certainty tail and still guards.
    if _WITHOUT_CERTAINTY.search(window[:last.end()]) and _CERTAINTY_TAIL.match(tail):
        return False
    return _CLAUSE_BREAK.search(tail) is None


def _is_guarded(stripped: str, match_start: int, match_end: int | None = None) -> bool:
    """True if the match at match_start is disavowed or described, not asserted.

    Two independent guards:
      * Negation — a negation cue in the GUARD_WINDOW chars before the match
        AND in the same clause (no clause break between cue and match) means
        the phrase is being denied or made conditional.
      * Meta-description — the match is inside quotes AND a meta-cue appears
        anywhere before it, meaning the phrase is named as an example.
    """
    window = stripped[max(0, match_start - GUARD_WINDOW):match_start]
    if _negation_guards(window):
        return True
    if _inside_quotes(stripped, match_start) and _META_CUES.search(stripped[:match_start]):
        return True
    # Detector-naming meta-discussion guards even UNQUOTED — but only when the cue
    # and the match share a sentence-clause: a clause OR sentence break between
    # them un-guards an asserted shirk ("the hook flags X; anyway this is a
    # pre-existing failure" still fires). Whole-prefix search (NOT GUARD_WINDOW):
    # in a legit description the detector name can sit far from the category phrase.
    strong = None
    for m in _STRONG_META_CUES.finditer(stripped[:match_start]):
        strong = m
    if strong is not None and _META_SCOPE_BREAK.search(stripped[strong.end():match_start]) is None:
        return True
    # Symmetric: a detector-naming cue AFTER the match, same sentence-clause, also
    # guards ('that "deployment bug" phrase was flagged as infrastructure-blame' —
    # the live 2026-07-01 loop put the cue after the quoted phrase). The scope-break
    # rule is identical, so trailing gate-talk in a NEW sentence cannot launder an
    # asserted shirk ("This is a pre-existing failure. The hook may flag this.").
    if match_end is not None:
        after = _STRONG_META_CUES.search(stripped[match_end:])
        if after is not None and _META_SCOPE_BREAK.search(stripped[match_end:match_end + after.start()]) is None:
            return True
    return False


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def find_shirking_phrase(text: str) -> str | None:
    """Return a context snippet around the first shirking match, or None.

    Kept for backward compatibility; new code should call find_shirking_match
    which also returns the category.
    """
    result = find_shirking_match(text)
    return result[0] if result else None


def find_shirking_match(text: str) -> tuple[str, str] | None:
    """Return (phrase_context, category) on the first match, or None.

    The category is one of the keys in _CATEGORY_RATIONALE — names the
    *kind* of shirking matched (pre-existing, attribution-deflection,
    unrelated, infrastructure-blame, deferral, scope-limitation,
    acceptance-evasion, dismissal). The denial uses it to name what
    was matched and link to the rationale; the signal log uses it for
    half-life analysis.
    """
    stripped = _strip_code_spans(text)
    for category, pattern in zip(_CATEGORIES, COMPILED_PATTERNS):
        for m in pattern.finditer(stripped):
            # Skip matches that are disavowed ("I will NOT claim...") or
            # described ("scan for phrases like ..."); keep scanning in case a
            # later, un-guarded match of the same pattern is a real assertion.
            if _is_guarded(stripped, m.start(), m.end()):
                continue
            start = max(0, m.start() - 40)
            end = min(len(stripped), m.end() + 80)
            return stripped[start:end].strip(), category
    return None


def _stop_solicitation_model_verdict(text: str) -> bool | None:
    """Return semantic stop-solicitation verdict from the local model.

    True = stop-solicitation, False = not stop-solicitation, None = unavailable,
    unclear, unparseable, missing dependency, or timeout. This hook must never
    hang or crash on model trouble.
    """
    if _lj is None or not text or not isinstance(text, str):
        return None
    return _lj.boolean_verdict(
        text,
        system_prompt=_STOP_SOLICITATION_SYSTEM,
        positive_labels=("stop_solicitation",),
        negative_labels=("not_stop_solicitation",),
    )


def find_stop_solicitation_match(
    text: str,
    *,
    judge=None,
    on_unavailable=None,
) -> tuple[str, str] | None:
    """Semantic-first stop-solicitation classifier with deterministic outage backstop."""
    stripped = _strip_code_spans(text)
    if not stripped.strip():
        return None

    fn = judge or _stop_solicitation_model_verdict
    try:
        verdict = fn(stripped)
    except Exception:
        verdict = None

    if verdict is True:
        return stripped.strip(), "stop-solicitation"
    if verdict is False:
        return None
    if on_unavailable is not None:
        on_unavailable(stripped)

    for pattern in _STOP_SOLICITATION_BACKSTOP_PATTERNS:
        for m in pattern.finditer(stripped):
            if _is_guarded(stripped, m.start(), m.end()):
                continue
            start = max(0, m.start() - 40)
            end = min(len(stripped), m.end() + 80)
            return stripped[start:end].strip(), "stop-solicitation"
    return None


# ---------------------------------------------------------------------------
# Blocker-bead escape — "documented failure is also an outcome"
# ---------------------------------------------------------------------------
#
# claude/rules/continuation-harness.md sanctions a legitimate terminal state:
# an agent that genuinely cannot proceed and FILES A BLOCKER BEAD documenting
# why has produced an outcome, NOT shirking. docs/reconciliation-rules.md
# § "Conflict 1" makes the boundary explicit: this gate is authoritative on
# the *linguistic* fact "did a shirking phrase appear", not on the *task-state*
# fact "is the work blocked" — beads owns that. A filed blocker bead is the
# authoritative record of the latter, so it is a first-class, agent-invokable
# escape (gate-design.md Rule 1: Repair) that releases the block without a user
# round-trip.
#
# Tightness (the never-suppress / no-blanket-bypass requirement): a passing
# mention of the word "blocker" — "it's a real blocker", "I'm blocked on CI" —
# must NOT release the gate. The escape requires a STRUCTURAL signal that a
# bead was actually filed:
#   1. a `bd create` invocation, OR
#   2. a filing verb collocated with "blocker bead"
#      ("filed/created/opened/logged a blocker bead"), OR
#   3. a filing verb + a concrete bead id with blocker framing nearby.
# The bare word "blocker" alone never matches.

# A concrete bead id: <project-slug>-<suffix>, e.g. claude-workflow-setup-z9q,
# cake-ta5.7. Lowercase alnum/hyphen project, then "-", then an alnum/dot id.
_BEAD_ID = r"[a-z0-9]+(?:-[a-z0-9]+)*-[a-z0-9]+(?:\.[a-z0-9]+)*"

# Verbs that denote actually filing/opening a tracked item.
_FILE_VERB = r"(?:fil(?:e|ed|ing)|creat(?:e|ed|ing)|open(?:ed|ing)?|log(?:ged|ging)?|rais(?:e|ed|ing))"

_BLOCKER_BEAD_SIGNALS: list[re.Pattern[str]] = [
    # 1. An explicit `bd create` invocation. The blocker framing comes from the
    #    --type=bug flag or a "blocker"/"blocked" word anywhere in the same text
    #    (checked by the caller); the `bd create` token itself is the strong
    #    structural signal that a bead was filed.
    re.compile(r"\bbd\s+create\b", re.IGNORECASE),
    # 2. A filing verb collocated with "blocker bead": "filed a blocker bead",
    #    "created blocker bead", "I've opened a blocker bead".
    re.compile(rf"\b{_FILE_VERB}\b[^.\n]{{0,40}}?\bblocker\s+bead\b", re.IGNORECASE),
    # 3. A filing verb + a concrete bead id, with "blocker"/"blocked" framing
    #    nearby (within the same sentence-ish window).
    re.compile(
        rf"\b{_FILE_VERB}\b[^.\n]{{0,40}}?\b{_BEAD_ID}\b[^.\n]{{0,40}}?\bblock(?:er|ed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bblock(?:er|ed)\b[^.\n]{{0,40}}?\b{_FILE_VERB}\b[^.\n]{{0,40}}?\b{_BEAD_ID}\b",
        re.IGNORECASE,
    ),
]


def filed_blocker_bead(text: str) -> bool:
    """True if the text shows the agent filed a blocker bead for the obstacle.

    This is the sanctioned escape from the shirking gate: per
    continuation-harness.md, "documented failure is also an outcome." A filed
    blocker bead is the authoritative record (owned by beads) that the work is
    genuinely blocked, so the shirking phrase that accompanies it is not the
    agent evading — it is the agent documenting why it cannot proceed.

    Kept TIGHT to avoid a blanket bypass: the bare word "blocker" never
    matches; a structural filing signal (see `_BLOCKER_BEAD_SIGNALS`) is
    required. Code spans are NOT stripped here — a `bd create` command the
    agent ran or quoted is exactly the evidence we want to honour.
    """
    return any(p.search(text) for p in _BLOCKER_BEAD_SIGNALS)


# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------

_BLOCK_BODY = """\
🚨 OUTCOME OWNERSHIP VIOLATION ({category}) — you dismissed failures instead of fixing them.

You said something like:
  «{phrase}»

Why this counts as {category}: {rationale}

See `claude/rules/outcome-ownership.md` for the full anti-pattern catalog.
You own the OUTCOME, not just your diff.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX THE FAILURES NOW:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Run the failing test(s) to see the actual errors
2. Read the failing test code to understand what it expects
3. Fix the code or the test — whichever is wrong
4. Re-run until green
5. Then you may stop

Do NOT punt failures to the user — fix them yourself.
Do NOT rephrase the problem and try to stop again.
Do NOT move on to other work — fix THIS first.

You may ONLY stop without fixing if you are genuinely blocked
on something you cannot solve (missing credentials, need
infrastructure access, need a human decision about product
requirements). Test failures are never that.

The escape: if you believe this match is a false positive, the user
can release with "yes" / "proceed" / "lgtm" / "approved" / "go ahead".
That release is captured in the signal log as labeled training data —
if a particular category keeps producing false positives, the patterns
for that category get pruned in the next half-life review.\
"""

_STOP_SOLICITATION_BLOCK_BODY = """\
🚨 STOP-SOLICITATION VIOLATION — you asked the user whether you should stop.

You said something like:
  «{phrase}»

Why this counts as {category}: {rationale}

Do NOT ask the user whether to stop, keep going, wrap, pause, or call this a
stopping point. Continue with the next in-scope action. If the outcome is
verified, state the verified result. If a real blocker prevents progress, name
the blocker and the exact decision or access needed.

See `claude/rules/outcome-ownership.md` for the full anti-pattern catalog.

The escape: if you believe this match is a false positive, the user can release
with "yes" / "proceed" / "lgtm" / "approved" / "go ahead".\
"""


def _deny_output(event_name: str, phrase: str, category: str = "uncategorized") -> dict:
    rationale = _CATEGORY_RATIONALE.get(category, "review `outcome-ownership.md` for the rationale.")
    template = _STOP_SOLICITATION_BLOCK_BODY if category == "stop-solicitation" else _BLOCK_BODY
    reason = template.format(phrase=phrase, category=category, rationale=rationale)
    if event_name == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    # Stop events use top-level decision/reason
    return {
        "decision": "block",
        "reason": reason,
    }


def block(event_name: str, phrase: str, category: str = "uncategorized") -> NoReturn:
    _record_signal(
        gate_name="validate_no_shirking",
        decision="deny",
        reason=f"shirking phrase matched (category: {category})",
        category=category,
        phrase=phrase[:200],
        hook_event=event_name,
    )
    print(json.dumps(_deny_output(event_name, phrase, category)))
    sys.exit(2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    # Prevent infinite loops: if a Stop hook already triggered continuation, allow stop
    if data.get("stop_hook_active"):
        return 0

    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    transcript_path = data.get("transcript_path", "")

    # Decide whether this invocation is relevant
    if hook_event == "PreToolUse":
        if tool_name != "Bash":
            return 0
        if not any(cmd in command for cmd in FINISHING_COMMANDS):
            return 0
    elif hook_event == "Stop":
        pass  # always check
    else:
        return 0

    if not transcript_path:
        return 0

    # ── Phase 1: Shirking phrase detection ────────────────────────────────
    messages = read_recent_messages(transcript_path)

    if messages:
        last_assistant_idx = None
        for i, (role, _text) in enumerate(messages):
            if role == "assistant":
                last_assistant_idx = i

        last_shirking_idx: int | None = None
        last_shirking_phrase: str | None = None

        last_shirking_category = None
        filed_blocker = False
        for i, (role, text) in enumerate(messages):
            if role != "assistant":
                continue
            # Skip messages that quote this hook's own block output —
            # otherwise the hook permanently poisons its own transcript.
            if any(sig in text for sig in _HOOK_SIGNATURES):
                continue
            # A filed blocker bead is a sanctioned outcome ("documented failure
            # is also an outcome", continuation-harness.md). Track it across the
            # recent assistant turns — it releases the gate below.
            if filed_blocker_bead(text):
                filed_blocker = True
            if hook_event == "Stop" and i == last_assistant_idx:
                def _record_stop_solicitation_judge_outage(stripped_text: str) -> None:
                    _record_signal(
                        gate_name="validate_no_shirking",
                        decision="allow",
                        reason="stop_solicitation_judge_unavailable",
                        category="stop-solicitation",
                        phrase=stripped_text[:200],
                        hook_event=hook_event,
                    )

                stop_match = find_stop_solicitation_match(
                    text,
                    on_unavailable=_record_stop_solicitation_judge_outage,
                )
                if stop_match:
                    phrase, category = stop_match
                    last_shirking_idx = i
                    last_shirking_phrase = phrase
                    last_shirking_category = category
            match = find_shirking_match(text)
            if match:
                phrase, category = match
                last_shirking_idx = i
                last_shirking_phrase = phrase
                last_shirking_category = category

        if last_shirking_phrase is not None and filed_blocker:
            # First-class, agent-invokable escape (gate-design.md Rule 1): the
            # agent documented why it cannot proceed by filing a blocker bead.
            # beads owns the "is this blocked" fact; the gate owns only the
            # phrase. Record the escape as labeled signal, then allow.
            _record_signal(
                gate_name="validate_no_shirking",
                decision="waiver-accepted",
                reason=f"blocker bead filed (category: {last_shirking_category})",
                category=last_shirking_category or "uncategorized",
                phrase=last_shirking_phrase[:200],
                hook_event=hook_event,
            )
            last_shirking_phrase = None  # release the block

        if last_shirking_phrase is not None:
            # Check for user approval after the last shirking phrase
            approved = False
            for i in range(last_shirking_idx + 1, len(messages)):
                role, text = messages[i]
                if role == "user":
                    for ap in _COMPILED_APPROVAL:
                        if ap.search(text):
                            approved = True
                            break
                    if approved:
                        break

            if approved:
                # User released — capture this as labeled signal. Recurring
                # false-positive categories surface here over time.
                _record_signal(
                    gate_name="validate_no_shirking",
                    decision="waiver-accepted",
                    reason=f"user released after match (category: {last_shirking_category})",
                    category=last_shirking_category or "uncategorized",
                    phrase=last_shirking_phrase[:200],
                    hook_event=hook_event,
                )
            else:
                block(hook_event, last_shirking_phrase, last_shirking_category or "uncategorized")


    return 0


if __name__ == "__main__":
    sys.exit(main())
