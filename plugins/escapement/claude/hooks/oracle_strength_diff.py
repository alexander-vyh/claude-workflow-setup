#!/usr/bin/env python3
"""Per-function oracle-strength diff for test-file changes.

This module classifies a single test-file change into NONE / WARN by comparing
the *per-function* assertion sets of the old and new sources, rather than
file-aggregate counts (the refuted prior behavior, which nets out delete+add
swaps).

Design contract (see .research/flask4045-control-failure-20260619/09-test-oracle-brief.md):

  - NONE : no oracle weakening (or a strengthening).
  - WARN : a plausible oracle downgrade (advisory; surface, do not block).

There is intentionally NO BLOCK tier. The 2026-06-20 corpus replay proved that
the one signal we hoped was block-safe — a negative-control assertion removed
without re-add — false-fires on legitimate red->green TDD (a placeholder
negative control is correctly dropped once the feature it placeheld is built),
mechanically indistinguishable from a genuine restriction-coverage drop. The
consuming gate therefore emits `ask`, never `deny`; the human/agent adjudicates
the feature-built-vs-coverage-dropped question this module cannot.

Two properties are load-bearing:

  1. Per-FUNCTION comparison. Functions are matched by name; a function that
     lost strong assertions, or disappeared entirely, is a WARN-level signal
     unless the lost coverage reappears or the change is a net strengthening.
  2. Never raises / never escalates on parse failure. A source that cannot be
     confidently parsed degrades toward NONE/WARN — robustness, not coercion.

`Level.BLOCK` is retained in the enum for API stability but is never returned.

The parsing + line-classification layer lives in the sibling module
``oracle_strength_parse`` (kept separate so each file stays under the repo's
500-line complexity gate). Its public helpers are re-exported here so callers
continue to import ``lang_for`` / ``extract_test_functions`` /
``is_negative_control`` from ``oracle_strength_diff``.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


def _load_sibling(name: str):
    """Import a sibling module beside this file, by path if necessary.

    This module is sometimes loaded by path (tests via spec_from_file_location,
    hook runners) where the hooks dir is not on sys.path, so a plain
    ``import oracle_strength_parse`` would fail. Try the normal import first;
    fall back to loading the sibling .py directly.
    """
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        path = Path(__file__).resolve().parent / f"{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module


_parse = _load_sibling("oracle_strength_parse")
_ParseError = _parse.ParseError
extract_test_functions = _parse.extract_test_functions
is_negative_control = _parse.is_negative_control
_is_strong = _parse.is_strong
lang_for = _parse.lang_for
_normalize = _parse.normalize

__all__ = [
    "Level",
    "Finding",
    "evaluate",
    "extract_test_functions",
    "is_negative_control",
    "lang_for",
]


class Level(Enum):
    NONE = 0
    WARN = 1
    BLOCK = 2


@dataclass
class Finding:
    """Result of an oracle-strength comparison for one file change."""

    level: Level
    reasons: list[str] = field(default_factory=list)
    path: str = ""


# --------------------------------------------------------------------------- #
# Core comparison
# --------------------------------------------------------------------------- #


def _all_assertions(funcs: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for lines in funcs.values():
        out.extend(lines)
    return out


def _negative_controls(assertions: list[str]) -> set[str]:
    return {a for a in assertions if is_negative_control(a)}


def _neg_control_subject(line: str) -> str:
    """A coarse 'subject' key for a negative control, used to decide whether an
    equivalent control was re-added elsewhere.

    The subject is the set of quoted-string and identifier tokens in the line,
    minus the negation operators themselves. Two negative controls are
    considered equivalent if their subject token sets overlap meaningfully.
    """
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", line)
    if quoted:
        return "|".join(sorted(t.lower() for t in quoted))
    # Fall back to identifier tokens, dropping common negation keywords.
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line.lower())
    drop = {
        "not", "in", "to", "no", "assert", "assertnotin", "assertnotequal",
        "refute", "expect", "self", "raises", "pytest", "tothrow",
    }
    keep = [t for t in toks if t not in drop]
    return "|".join(sorted(set(keep)))


def _neg_control_readded(removed: str, new_assertions: list[str]) -> bool:
    """True if an equivalent negative control to `removed` exists in the new side.

    Equivalence is shape (still a negative control) + subject overlap. An exact
    normalized-line match also counts.
    """
    removed_norm = _normalize(removed)
    new_negs = _negative_controls(new_assertions)
    if removed_norm in {_normalize(n) for n in new_negs}:
        return True
    removed_subject = set(_neg_control_subject(removed).split("|")) - {""}
    if not removed_subject:
        # No identifiable subject; treat any surviving negative control of the
        # same broad kind as a re-add to avoid over-blocking.
        return bool(new_negs)
    for cand in new_negs:
        cand_subject = set(_neg_control_subject(cand).split("|")) - {""}
        if removed_subject & cand_subject:
            return True
    return False


def evaluate(old_src: str, new_src: str, path: str) -> Finding:
    """Classify a test-file change into NONE / WARN (advisory only).

    There is no BLOCK tier: the consuming gate emits `ask`, never `deny`
    (decision log below). Parse ambiguity degrades toward NONE/WARN and never
    raises — robustness, not a coercive gate.
    """
    lang = lang_for(path)
    reasons: list[str] = []

    try:
        old_funcs = extract_test_functions(old_src, lang)
    except _ParseError as exc:
        old_funcs = {}
        reasons.append(f"old side not confidently parsed ({exc}); advisory only")
    try:
        new_funcs = extract_test_functions(new_src, lang)
    except _ParseError as exc:
        new_funcs = {}
        reasons.append(f"new side not confidently parsed ({exc}); advisory only")

    old_assertions = _all_assertions(old_funcs)
    new_assertions = _all_assertions(new_funcs)

    # --- Advisory signals only; there is NO BLOCK tier -------------------- #
    # Decision 2026-06-20 (corpus-validated, see .research/flask4045-control-
    # failure-20260619/): the negative-control-removal signal is NOT a safe
    # hard-block. The replay proved it false-fires on legitimate red->green TDD
    # — sifiaops removed `not_to include('processing_started_at')` precisely
    # *because* the feature it placeheld got built, which is mechanically
    # identical to a genuine restriction-coverage drop (dwslack). The gate that
    # consumes this Finding emits `ask`, never `deny`. WARN surfaces; the human
    # / agent adjudicates the feature-built-vs-coverage-dropped question this
    # module structurally cannot.
    warn_reasons: list[str] = []

    removed_unreadded_negs: list[str] = []
    for neg in _negative_controls(old_assertions):
        if _neg_control_readded(neg, new_assertions):
            continue
        removed_unreadded_negs.append(neg)
    if removed_unreadded_negs:
        warn_reasons.append(
            "negative-control / security assertion removed without re-add "
            "(advisory — confirm the restriction is still enforced or now "
            "genuinely obsolete): " + "; ".join(removed_unreadded_negs[:3])
        )

    # --- WARN signals: per-function strength drops ------------------------ #
    warn_reasons.extend(
        _per_function_warnings(old_funcs, new_funcs, new_assertions)
    )

    if warn_reasons:
        reasons.extend(warn_reasons)
        return Finding(Level.WARN, reasons, path)

    return Finding(Level.NONE, reasons, path)


def _per_function_warnings(
    old_funcs: dict[str, list[str]],
    new_funcs: dict[str, list[str]],
    new_assertions_all: list[str],
) -> list[str]:
    """Detect per-function oracle weakening that warrants WARN.

    A function present in old is a downgrade signal if, in new, it lost strong
    assertions (in-place weakening) or disappeared entirely — UNLESS the lost
    strong coverage reappears somewhere in the new source (a delete+re-add /
    refactor) or the function was net-strengthened.
    """
    warnings: list[str] = []
    new_strong_all = {_normalize(a) for a in new_assertions_all if _is_strong(a)}

    for name, old_lines in old_funcs.items():
        old_strong = {_normalize(a) for a in old_lines if _is_strong(a)}
        if not old_strong:
            # No strong oracle to lose; weak/empty functions don't trigger WARN.
            continue

        new_lines = new_funcs.get(name)
        if new_lines is None:
            # Function disappeared. If ALL of its strong assertions reappear
            # elsewhere in new, this is a refactor/move, not a downgrade.
            if old_strong <= new_strong_all:
                continue
            lost = old_strong - new_strong_all
            warnings.append(
                f"test function '{name}' removed; strong assertions not "
                f"found elsewhere: {sorted(lost)[:2]}"
            )
            continue

        new_strong = {_normalize(a) for a in new_lines if _is_strong(a)}
        if new_strong >= old_strong:
            # Same or strengthened in place.
            continue
        lost = old_strong - new_strong
        # The lost strong assertions might have moved to another function.
        if lost <= new_strong_all:
            continue
        truly_lost = lost - new_strong_all
        warnings.append(
            f"test function '{name}' lost strong assertion(s): "
            f"{sorted(truly_lost)[:2]}"
        )

    return warnings
