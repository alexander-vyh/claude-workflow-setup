"""Detector for magic-number / business-constant documentation echoes.

The implementation-echo gate already catches OPAQUE GENERATED literals (UUIDs,
Salesforce ids, hex tokens) shared between source and tests. It does NOT model
the *business-constant* echo: a test that asserts a formatted number which
merely repeats a value living in a source DESCRIPTION string. Canonical case
(the one that motivated this module):

    # source (a metric description string)
    PCT_AUTOMATED = "all-history snapshot reads ~91% because ..."
    # test
    assert "91%" in describe("dw_access_provisioning_monthly", "pct_automated")

That test is green by construction and proves nothing about the computed
percentage — it re-asserts documentation prose. This detector flags it.

Precision design (scope: numeric-ish string tokens)
---------------------------------------------------
A token is flagged only when ALL hold:
  1. It is a FORMATTED number — it carries a significance marker (%, currency
     symbol, a unit suffix x/k/m/b, or a thousands comma). Bare integers and
     years ("91", "2024") have no marker and are never flagged — that kills the
     largest false-positive class (legitimate `assert score == 91`).
  2. It appears as a SUBSTRING of a SOURCE file's string literal (the number
     lives in prose), AND
  3. It appears as a WHOLE asserted string literal in a TEST file.

The asymmetry (substring in source, whole-literal in test) matches the echo
shape: the number is embedded in a description sentence but asserted as a bare
token. A legitimate constant assertion uses bare numerics on both sides and is
never a string literal, so it cannot match. The `# oracle:` override (now
substance-barred) remains the escape for a genuine display-value contract.

This module deliberately has no dependency on the gate module (it carries its
own compact string-literal scanner) so it can be unit-tested standalone.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# A formatted numeric token: digits PLUS at least one significance marker.
# Order matters — currency is tried before the bare-unit branch so "$5M" is
# captured whole rather than leaving a dangling "5M".
_NUMBER = r"""
    (?<![\w])                                      # not mid-word
    (?:
        [$€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:bn|mm|[kKmMbB]))?  # currency (+opt unit)
      | \d[\d,]*(?:\.\d+)?\s?%                                # percent
      | \d+(?:\.\d+)?\s?(?:bn|mm|[xXkKmMbB])                  # unit / multiplier
      | \d{1,3}(?:,\d{3})+(?:\.\d+)?                          # thousands separator
    )
    (?![\w])                                       # not followed by a word char
"""
_FIND_RE = re.compile(_NUMBER, re.VERBOSE)
_FULL_RE = re.compile(r"\A\s*" + _NUMBER + r"\s*\Z", re.VERBOSE)

# Compact string-literal scanner (single/double quoted, escape-aware).
_STRING_RE = re.compile(r"""(?P<q>['"])(?P<v>(?:\\.|(?!(?P=q)).)*?)(?P=q)""", re.DOTALL)


class Finding(NamedTuple):
    filepath: str          # the test file carrying the echoed assertion
    token: str             # the formatted number, e.g. "91%"
    sources: list[str]     # source files whose description strings contain it


def _normalize(token: str) -> str:
    return re.sub(r"\s+", "", token)


def is_formatted_number(value: str) -> bool:
    """True if the whole value is a single formatted number (marker required)."""
    return bool(_FULL_RE.match(value or ""))


def formatted_numbers_in(text: str) -> set[str]:
    """All formatted numbers appearing anywhere in `text`, normalized."""
    return {_normalize(m.group()) for m in _FIND_RE.finditer(text or "")}


def _string_literal_values(text: str) -> list[str]:
    return [m.group("v") for m in _STRING_RE.finditer(text or "")]


def find_magic_number_echoes(
    source_files: dict[str, str],
    test_files: dict[str, str],
) -> list[Finding]:
    """Flag formatted numbers asserted in a test that also live inside a source
    description string. See module docstring for the precision rationale.
    """
    # token -> source files whose string literals contain it (in prose)
    source_tokens: dict[str, set[str]] = {}
    for path, text in source_files.items():
        for value in _string_literal_values(text):
            for token in formatted_numbers_in(value):
                source_tokens.setdefault(token, set()).add(path)
    if not source_tokens:
        return []

    findings: list[Finding] = []
    for path, text in test_files.items():
        # tokens asserted as a WHOLE string literal in this test
        asserted = {
            _normalize(value.strip())
            for value in _string_literal_values(text)
            if is_formatted_number(value.strip())
        }
        for token in sorted(asserted & set(source_tokens)):
            findings.append(Finding(path, token, sorted(source_tokens[token])))
    return findings
