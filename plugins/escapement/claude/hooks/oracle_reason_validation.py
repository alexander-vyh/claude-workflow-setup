"""Substance bar for the implementation-echo gate's `# oracle:` override.

The echo gate lets a per-file `# oracle: <reason>` comment exempt a flagged
test file (gate-design.md Rule 1: every gate needs a first-class escape).
Originally the override was *presence-validated* — any non-empty reason
exempted the file. That is mock bureaucracy by construction (Rule 3,
Wiesche et al. 2013): an agent under pressure learns the shortest passing
string, and a *circular* reason — one whose only specific referents are the
file's own asserted literals ("the field-name literals ARE the oracle") —
launders the file past the gate while naming no independent source of truth.

This module is the value-not-presence check. A reason is rejected when it is:
  - "too-short"   : under the substance length floor
  - "placeholder" : a known null pattern (n/a, tbd, ...)
  - "circular"    : every specific referent it names is an asserted literal of
                    the file it would exempt (or it names no referent at all)

An accepted reason (returns None) must contain at least one *external*
referent — a word that is neither generic/oracle-vocabulary boilerplate nor
one of the file's own asserted literals. A single external referent is enough
to keep the escape path usable for legitimate overrides (Rule 1 / Flexibility).

This is a deterministic floor, not a semantic judge: it cannot tell a vague
hand-wave ("see the code") from a real independent oracle. It catches the
*obvious* circular/placeholder cases and forces the author to articulate an
external referent; the captured reason corpus + half-life review catch the
rest. The module deliberately has no dependency on the gate (no circular
import) — callers pass in the file's asserted literals.
"""

from __future__ import annotations

import re
from typing import Iterable

# Substance length floor for an override reason (gate-design.md standard
# waiver convention: minimum 20 characters).
MIN_REASON_LEN = 20

# Null patterns: a reason made up entirely of these tokens names nothing.
PLACEHOLDERS = frozenset(
    {
        "none", "tbd", "todo", "wip", "n/a", "na", "fixme", "xxx",
        "placeholder", "?", "??", "???", "-", "--", "...",
    }
)

# Generic English + test/oracle descriptor vocabulary. A reason built ONLY
# from these words (plus the file's own asserted literals) names no
# independent source of truth — it is self-referential. These are not
# domain words: source/oracle nouns like "salesforce", "upstream", "schema",
# "contract", "finance" are deliberately ABSENT so they count as external
# referents that rescue a reason.
STOPWORDS = frozenset(
    {
        # function words
        "a", "an", "the", "this", "that", "these", "those", "is", "are",
        "was", "were", "be", "been", "being", "am", "of", "in", "on", "at",
        "to", "for", "from", "by", "with", "as", "and", "or", "but", "not",
        "no", "it", "its", "they", "them", "their", "we", "our", "you",
        "your", "here", "there", "where", "when", "which", "who", "whom",
        "whose", "what", "why", "how", "all", "any", "each", "both", "more",
        "most", "some", "such", "only", "own", "same", "so", "than", "too",
        "very", "ie", "eg", "etc", "vs", "do", "does", "did", "has", "have",
        "had", "can", "could", "will", "would", "should", "may", "might",
        "must", "about", "above", "below", "over", "under", "into", "onto",
        "per", "via", "within", "across", "against", "between", "still",
        # test / oracle descriptor vocabulary
        "oracle", "oracles", "test", "tests", "tested", "testing", "assert",
        "asserts", "asserted", "assertion", "assertions", "field", "fields",
        "name", "names", "named", "column", "columns", "row", "rows",
        "literal", "literals", "string", "strings", "value", "values",
        "constant", "constants", "identifier", "identifiers", "ids", "key",
        "keys", "self", "documenting", "documented", "expected", "actual",
    }
)

_WORD_RE = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def asserted_tokens(literals: Iterable[str]) -> set[str]:
    """Tokenize the file's asserted string literals into a referent set.

    Each literal (e.g. ``"pct_automated"`` → value ``pct_automated``) becomes
    one or more lowercase identifier tokens. These are the referents a reason
    must go *beyond* to count as independent.
    """
    out: set[str] = set()
    for lit in literals:
        out.update(_tokens(lit))
    return out


def significant_tokens(reason: str) -> list[str]:
    """The reason's content words: length >= 3 and not generic/oracle boilerplate."""
    return [t for t in _tokens(reason) if len(t) >= 3 and t not in STOPWORDS]


def _is_placeholder(stripped: str) -> bool:
    toks = stripped.lower().split()
    return bool(toks) and all(t in PLACEHOLDERS for t in toks)


def validate_oracle_reason(reason: str, asserted: set[str]) -> str | None:
    """Return a rejection category, or None if the reason clears the bar.

    Args:
        reason: the captured `# oracle:` reason text.
        asserted: the asserted-literal token set of the file this reason would
            exempt (from `asserted_tokens`).

    Returns:
        "too-short" | "placeholder" | "circular" if rejected; None if accepted
        (the reason names at least one external, independent referent).
    """
    stripped = reason.strip()
    if len(stripped) < MIN_REASON_LEN:
        return "too-short"
    if _is_placeholder(stripped):
        return "placeholder"
    sig = significant_tokens(stripped)
    external = [t for t in sig if t not in asserted]
    if not external:
        return "circular"
    return None


def partition_overrides(
    overrides: dict[str, list[str]],
    asserted_by_file: dict[str, set[str]],
) -> tuple[set[str], dict[str, list[tuple[str, str]]]]:
    """Split raw `# oracle:` overrides into honored vs rejected.

    Args:
        overrides: {test_file: [reason, ...]} as captured from the files.
        asserted_by_file: {test_file: asserted-literal token set} for each file.

    Returns:
        (valid_files, rejected) where
          - valid_files: files with at least one reason that clears the bar
            (these are genuinely exempt);
          - rejected: {file: [(reason, category), ...]} for files whose reasons
            ALL failed — these are NOT exempt and their issues remain.
    """
    valid: set[str] = set()
    rejected: dict[str, list[tuple[str, str]]] = {}
    for filepath, reasons in overrides.items():
        asserted = asserted_by_file.get(filepath, set())
        failures: list[tuple[str, str]] = []
        accepted = False
        for reason in reasons:
            category = validate_oracle_reason(reason, asserted)
            if category is None:
                accepted = True
            else:
                failures.append((reason, category))
        if accepted:
            valid.add(filepath)
        else:
            rejected[filepath] = failures
    return valid, rejected
