#!/usr/bin/env python3
"""Parsing + line-classification layer for oracle_strength_diff.

Cohesive responsibility: turn a test source (Python via ast, ruby/js/ts via an
indentation heuristic) into ``{test_function_name -> [assertion lines]}``, and
classify a single assertion line as strong / negative-control / any-assertion.

Extracted from oracle_strength_diff.py to keep each module under the repo's
500-line complexity gate. The comparison/policy layer (Level, Finding,
evaluate, per-function downgrade logic) stays in oracle_strength_diff.py and
imports from here.

Fragment tolerance is load-bearing: real edits arrive as class-method *slices*
that start indented or as truncated ``def`` lines. A naive ``ast.parse`` fails
on those and the consumer fail-opens, so the discriminator never runs and a
verdict is granted by the crash rather than earned. ``_parse_python_tolerant``
recovers the common fragment shapes before deferring to the heuristic parser.
"""

from __future__ import annotations

import ast
import re
import textwrap


class ParseError(Exception):
    """Raised when a source cannot be confidently parsed into functions."""


# --------------------------------------------------------------------------- #
# Language detection
# --------------------------------------------------------------------------- #

_EXT_LANG = {
    ".py": "py",
    ".rb": "rb",
    ".js": "js",
    ".jsx": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".ts": "ts",
    ".tsx": "ts",
}


def lang_for(path: str) -> str:
    """Return py|rb|js|ts|unknown for a file path based on its extension."""
    lower = path.lower()
    for ext, lang in _EXT_LANG.items():
        if lower.endswith(ext):
            return lang
    return "unknown"


# --------------------------------------------------------------------------- #
# Assertion classification
# --------------------------------------------------------------------------- #

# A "strong" assertion makes a concrete claim about a value or behavior: an
# equality/relational/membership comparison, a raises/throws expectation, or a
# rich matcher. Bare truthiness (assert x, toBeTruthy) is intentionally NOT
# strong, so weakening a strong assertion to truthiness registers as a drop.
_STRONG_ASSERTION_RE = re.compile(
    r"("
    r"\bassert\s+.+(?:==|!=|<=|>=|<|>| in | not in ).+|"
    r"\bassert(?:Equal|NotEqual|AlmostEqual|In|NotIn|Regex|Match|Raises|"
    r"Greater|Less|Is|IsNot|IsInstance)\w*\s*\(|"
    r"\bpytest\.raises\s*\(|"
    r"\bself\.assertRaises\w*\s*\(|"
    r"\bexpect\s*\(.+\)\.(?:not\.)?(?:toBe|toEqual|toStrictEqual|toContain|"
    r"toContainEqual|toMatch|toMatchObject|toHaveProperty|toThrow|toHaveLength|"
    r"toBeInstanceOf|toBeNull|toBeUndefined)\s*\(|"
    r"\bexpect\s*\(.+\)\.(?:not_)?to\b|"
    r"\b(?:should|must)\b.+\b(?:eq|eql|include|match|raise_error|be_)\b|"
    r"\bassert_response\b|"
    r"\b(?:refute|refute_equal|refute_includes)\b"
    r")",
    re.IGNORECASE,
)

# Negative controls / security assertions: a predicate that something is NOT
# present, NOT permitted, or raises an error. Removal of one of these without a
# re-add anywhere in the change is the corpus's highest-signal downgrade.
_NEGATIVE_CONTROL_RE = re.compile(
    r"("
    # "X not in Y" (python / generic)
    r"\bnot\s+in\b|"
    # python unittest negative asserts
    r"\bassert(?:Not(?:In|Equal|AlmostEqual)|NotIn|False|IsNone|IsNot)\w*\s*\(|"
    r"\bassertNot\w*\s*\(|"
    # raises / throws an error
    r"\bpytest\.raises\s*\(|\bself\.assertRaises\w*\s*\(|\bassertRaises\w*\s*\(|"
    r"\.toThrow\s*\(|\braise_error\b|\bto\s+raise_error\b|"
    # rspec / minitest negatives
    r"\bnot_to\b|\bto_not\b|\brefute\w*\b|\bnot\.to\b|"
    # explicit error status codes
    r"\bstatus(?:_code)?\s*[=:]=?\s*40[0-9]\b|"
    r"\bassert_response\s+:(?:bad_request|unauthorized|forbidden|not_found)\b"
    r")",
    re.IGNORECASE,
)

# A general assertion line (used to detect that a function still has *some*
# oracle even after a strong one was dropped — informs WARN vs NONE).
# This MUST be a superset of every downstream classifier (is_strong,
# is_negative_control): a line either of those would flag must reach the
# assertion set, or the signal is silently dropped before comparison. In
# particular, context-manager raise forms (`with pytest.raises(...)`,
# `with self.assertRaises(...)`) are negative-control assertions and must be
# collected.
_ANY_ASSERTION_RE = re.compile(
    r"(\bassert\b|\bassert\w+\s*\(|\bexpect\s*\(|\bshould\b|\bmust\b|"
    r"\brefute\w*\b|\.to\b|\.not_to\b|\bassert_response\b|"
    r"\bpytest\.raises\s*\(|\bassertRaises\w*\s*\(|\.toThrow\s*\(|"
    r"\braise_error\b)",
    re.IGNORECASE,
)


def is_negative_control(assertion_line: str) -> bool:
    """True if the line is a negative-control / security assertion."""
    return bool(_NEGATIVE_CONTROL_RE.search(assertion_line))


def is_strong(assertion_line: str) -> bool:
    return bool(_STRONG_ASSERTION_RE.search(assertion_line))


def is_assertion(line: str) -> bool:
    return bool(_ANY_ASSERTION_RE.search(line))


def normalize(line: str) -> str:
    """Collapse whitespace so set comparison is robust to reformatting."""
    return re.sub(r"\s+", " ", line.strip())


# --------------------------------------------------------------------------- #
# Test-function extraction
# --------------------------------------------------------------------------- #


def extract_test_functions(src: str, lang: str) -> dict[str, list[str]]:
    """Parse `src` into {test_function_name -> [assertion lines]}.

    Python is parsed with `ast` for accuracy; ruby/js/ts use a heuristic block
    parser keyed on `def test_` / `it`/`describe` / `test(`. Raises ParseError
    when the source cannot be confidently parsed so callers can fail-open.
    """
    if lang == "py":
        return _extract_python(src)
    if lang in ("rb", "js", "ts"):
        return _extract_heuristic(src, lang)
    raise ParseError(f"unsupported language: {lang}")


def _extract_python(src: str) -> dict[str, list[str]]:
    """Parse Python source into {test_fn -> [assertion lines]}.

    Real edits are often *fragments*: a class-method slice that starts indented
    (`unexpected indent`) or a truncated `def` with no body. A naive ast.parse
    fails on those and the caller fail-opens, so the discriminator never runs
    and a NONE/WARN verdict is granted by the crash rather than earned. To make
    those verdicts earned we try a recovery cascade before giving up (see
    _parse_python_tolerant); only if every transform fails do we fall back to
    the indentation heuristic parser (which itself raises ParseError on genuine
    garbage).
    """
    tree = _parse_python_tolerant(src)
    if tree is None:
        return _extract_heuristic(src, "py")

    funcs: dict[str, list[str]] = {}
    src_lines = src.splitlines()

    def _walk(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name.startswith("test"):
                    name = f"{prefix}{child.name}"
                    funcs[name] = _assertions_in_range(
                        src_lines, child.lineno, _end_lineno(child)
                    )
            elif isinstance(child, ast.ClassDef):
                # Recurse into test classes to capture their test methods,
                # namespacing by class so identically named methods don't clash.
                _walk(child, prefix=f"{child.name}.")

    _walk(tree)
    # ast parsed but found no test function in a non-empty fragment -> nothing to
    # compare. Defer to the heuristic parser, which may recover a bare
    # `def test_` / loose asserts ast dropped.
    if not funcs and src.strip():
        try:
            return _extract_heuristic(src, "py")
        except ParseError:
            return {}
    return funcs


def _parse_python_tolerant(src: str) -> "ast.AST | None":
    """Return an ast tree for `src`, recovering common edit-fragment shapes.

    Returns None (not a raise) when no transform yields a parseable tree, so the
    caller can fall through to the heuristic parser.
    """
    if not src.strip():
        return ast.parse("")
    candidates = (
        src,
        textwrap.dedent(src),
        # Wrap a bare indented method fragment in a class so the leading indent
        # is legal. dedent first so the wrapper indent is consistent.
        "class _OSDWrap:\n" + _reindent(textwrap.dedent(src), 4),
    )
    for candidate in candidates:
        try:
            return ast.parse(candidate)
        except SyntaxError:
            continue
    return None


def _reindent(src: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(
        (pad + line if line.strip() else line) for line in src.splitlines()
    )


def _end_lineno(node: ast.AST) -> int:
    end = getattr(node, "end_lineno", None)
    if end is not None:
        return end
    # Fallback: walk children for the max lineno.
    return max(
        (getattr(n, "lineno", 0) for n in ast.walk(node)),
        default=getattr(node, "lineno", 0),
    )


def _assertions_in_range(src_lines: list[str], start: int, end: int) -> list[str]:
    out: list[str] = []
    for raw in src_lines[start - 1 : end]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if is_assertion(line):
            out.append(normalize(line))
    return out


# Heuristic block openers for ruby / js / ts spec styles (and Python fragments
# the ast path could not accept).
_BLOCK_OPENER_RE = re.compile(
    r"^\s*("
    r"def\s+(test_\w+)|"  # ruby/minitest/python-like def test_
    r"(?:it|describe|context|test|specify)\s*[(\s]\s*['\"]([^'\"]+)['\"]"  # rspec/jest
    r")",
)


def _extract_heuristic(src: str, lang: str) -> dict[str, list[str]]:
    """Indentation/brace heuristic block parser for rb/js/ts (and py fragments).

    Returns {block_name -> [assertion lines]}. Raises ParseError when the source
    is non-empty but no test blocks are found, so the caller fails-open.
    """
    lines = src.splitlines()
    if not lines:
        return {}

    funcs: dict[str, list[str]] = {}
    # Stack of (name, indent) for currently open blocks. Assertions are
    # attributed to the nearest enclosing named block.
    stack: list[tuple[str, int]] = []

    def _current() -> str | None:
        return stack[-1][0] if stack else None

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        indent = len(raw) - len(raw.lstrip())

        # Close blocks whose body has dedented past their opener (ruby `end`,
        # or any dedent for brace languages is approximated by indent level).
        if lang == "rb" and stripped == "end":
            if stack:
                stack.pop()
            continue
        while stack and indent <= stack[-1][1] and not _BLOCK_OPENER_RE.match(raw):
            stack.pop()

        m = _BLOCK_OPENER_RE.match(raw)
        if m:
            name = m.group(2) or m.group(3) or stripped[:60]
            # Build a path-ish name so nested describe/it don't collide.
            full = f"{_current()} > {name}" if _current() else name
            stack.append((full, indent))
            funcs.setdefault(full, [])
            continue

        if is_assertion(stripped):
            owner = _current()
            if owner is None:
                # Assertion outside any recognized block — ambiguous structure.
                # Attribute to a synthetic top-level bucket so it still counts
                # toward negative-control detection but flags ambiguity.
                owner = "<toplevel>"
                funcs.setdefault(owner, [])
            funcs[owner].append(normalize(stripped))

    # Non-empty source but no recognizable test blocks => not confidently
    # parsed. Fail-open (caller degrades toward NONE/WARN).
    named_blocks = [k for k in funcs if k != "<toplevel>"]
    if not named_blocks and any(s.strip() for s in lines):
        raise ParseError("heuristic parser found no test blocks")
    return funcs
