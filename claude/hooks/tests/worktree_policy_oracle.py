"""Independent shell-aware oracle for forbidden direct worktree creation.

The business invariant is semantic: active policy and missing-bundle repair
must not contain an executable direct Git/Beads creation command. References
passed as quoted arguments to search, print, or audit commands are not
execution policy and must remain valid negative controls.
"""

from __future__ import annotations

import html
import re
import shlex
from pathlib import Path


_FENCED_CODE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_HTML_CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_SEPARATOR_CHARS = frozenset(";&|\n")
_SEPARATOR_SENTINELS = {
    separator: chr(0xE000 + index)
    for index, separator in enumerate(sorted(_SHELL_SEPARATOR_CHARS))
}
_SENTINEL_RESTORE = str.maketrans(
    {sentinel: separator for separator, sentinel in _SEPARATOR_SENTINELS.items()}
)
_GIT_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--git-common-dir", "--namespace"}
)
_BD_VALUE_OPTIONS = frozenset(("-C", "--directory"))


def _clean_html_code(raw: str) -> str:
    return html.unescape(_HTML_TAG_RE.sub("", raw)).strip()


def _shell_looking_line(line: str) -> str | None:
    stripped = line.strip().removeprefix("$").strip()
    prefixes = ("git ", "bd ", "cd ", "env ", "command ")
    return stripped if stripped.startswith(prefixes) else None


def extract_code_candidates(text: str) -> list[str]:
    """Extract executable-looking Markdown, HTML, and plain shell candidates."""
    candidates: list[str] = []
    remaining = text
    extractors = (
        (_FENCED_CODE_RE, lambda match: match.group(1).strip()),
        (_HTML_CODE_RE, lambda match: _clean_html_code(match.group(1))),
        (_INLINE_CODE_RE, lambda match: match.group(1).strip()),
    )
    for pattern, extract in extractors:
        candidates.extend(
            candidate
            for match in pattern.finditer(remaining)
            if (candidate := extract(match))
        )
        remaining = pattern.sub(
            lambda match: "\n" * match.group(0).count("\n") or " ",
            remaining,
        )
    candidates.extend(
        candidate
        for line in remaining.splitlines()
        if (candidate := _shell_looking_line(line)) is not None
    )
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _protect_quoted_or_escaped_separators(command: str) -> str:
    protected: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == "'":
                quote = None
                protected.append(char)
            else:
                protected.append(_SEPARATOR_SENTINELS.get(char, char))
            index += 1
            continue
        if char == "\\":
            if index + 1 < len(command):
                escaped = command[index + 1]
                if escaped in _SHELL_SEPARATOR_CHARS:
                    protected.append(_SEPARATOR_SENTINELS[escaped])
                else:
                    protected.extend((char, escaped))
                index += 2
            else:
                protected.append(char)
                index += 1
            continue
        if char == quote:
            quote = None
            protected.append(char)
        elif char in {"'", '"'} and quote is None:
            quote = char
            protected.append(char)
        elif quote is not None and char in _SHELL_SEPARATOR_CHARS:
            protected.append(_SEPARATOR_SENTINELS[char])
        else:
            protected.append(char)
        index += 1
    return "".join(protected)


def _shell_segments(command: str) -> list[list[str]]:
    try:
        protected = _protect_quoted_or_escaped_separators(command)
        lexer = shlex.shlex(protected, posix=True, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
    except ValueError:
        return []

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in _SHELL_SEPARATOR_CHARS for char in token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token.translate(_SENTINEL_RESTORE))
    return [segment for segment in segments if segment]


def _command_index(tokens: list[str]) -> int | None:
    index = 0
    while index < len(tokens) and _ENV_ASSIGNMENT_RE.match(tokens[index]):
        index += 1
    while index < len(tokens):
        if tokens[index] == "env":
            index += 1
            while index < len(tokens):
                token = tokens[index]
                if _ENV_ASSIGNMENT_RE.match(token):
                    index += 1
                elif token in {"-u", "--unset"} and index + 1 < len(tokens):
                    index += 2
                elif token.startswith("-"):
                    index += 1
                else:
                    break
            continue
        if tokens[index] == "command":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            continue
        break
    return index if index < len(tokens) else None


def _skip_selectors(
    tokens: list[str],
    index: int,
    value_options: frozenset[str],
) -> int:
    while index < len(tokens):
        token = tokens[index]
        if token in value_options:
            index += 2
        elif any(token.startswith(option + "=") for option in value_options):
            index += 1
        elif token.startswith("-C") and token != "-C":
            index += 1
        elif token.startswith("-"):
            index += 1
        else:
            break
    return index


def _is_direct_creation(tokens: list[str]) -> bool:
    index = _command_index(tokens)
    if index is None:
        return False
    executable = Path(tokens[index]).name
    index += 1
    if executable == "git":
        index = _skip_selectors(tokens, index, _GIT_VALUE_OPTIONS)
        return tokens[index : index + 2] == ["worktree", "add"]
    if executable == "bd":
        index = _skip_selectors(tokens, index, _BD_VALUE_OPTIONS)
        return tokens[index : index + 2] == ["worktree", "create"]
    return False


def direct_creation_commands(text: str) -> list[str]:
    """Return code candidates containing executable direct creation commands."""
    return [
        candidate
        for candidate in extract_code_candidates(text)
        if any(_is_direct_creation(segment) for segment in _shell_segments(candidate))
    ]
