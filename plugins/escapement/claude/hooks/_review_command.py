#!/usr/bin/env python3
"""Interpret the shell command a close gate is asked to judge.

Split out of `review_gate.py` because reading a shell command correctly is its
own responsibility with its own failure modes, and because those failure modes
are where a gate quietly stops working. Three real defects lived in the naive
version this replaces, all found by attacking it rather than by testing it:

  1. `REVIEW_WAIVER=` was matched anywhere in the command string, so
     `bd close x --reason "... REVIEW_WAIVER='...'"` counted as a waiver. The
     self-defeating case: the gate's own denial text contains
     `REVIEW_WAIVER="<>=20-char rationale>"`, and that placeholder clears the
     20-character substance bar — so an agent that pasted the denial into its
     close reason silently satisfied the gate it had just been refused by.
     A waiver is now only honoured as a real environment-assignment prefix on
     the segment that actually runs `bd`.

  2. `bd update --status closed <id>` resolved its target to the literal
     string `"closed"`, because the parser skipped flags but not their
     separate value tokens. The gate then looked for a review of a bead named
     "closed" and denied with a nonsense id.

  3. `git commit -m "bd close x"` and `echo "bd close x"` counted as closes,
     because the match was a bare substring search over the whole command.
     Commands are now segmented on shell separators and only a segment that
     actually invokes `bd` is considered.

Deliberately not handled: a `bd close` appearing inside a heredoc body still
matches, since the body arrives as its own line. Denying a close that is not
happening is a false positive, but a cheap and rare one with a waiver escape,
whereas parsing heredocs properly needs a real shell parser.
"""

from __future__ import annotations

import re

# Shell separators that begin a new command, honoured only outside quotes.
# A regex split cannot do this: a `;` inside a quoted waiver reason would cut
# the command in half, the fragment holding `bd close` would stop looking like
# a close, and the gate would silently allow it. Punctuation in prose must not
# be a bypass.
_SEPARATORS = ("||", "&&", ";", "|", "\n")

# A leading environment assignment, e.g. `FOO=bar` or `FOO="a b"`.
_ENV_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)

# Tokeniser that keeps quoted runs together.
_TOKEN_RE = re.compile(r"(?:'[^']*'|\"[^\"]*\"|\S)+")

# `bd update` flags that take a *separate* value token. Without this the value
# is mistaken for the positional bead id.
_VALUE_FLAGS = {
    "--status", "--reason", "--assignee", "-a", "--priority", "-p",
    "--notes", "--append-notes", "--design", "--type", "-t", "--owner",
    "--labels", "-l", "--estimate", "-e", "--actor", "--db", "--set-metadata",
    "--unset-metadata", "--metadata", "--parent", "--due", "--defer",
}

# Characters shell syntax may leave attached to a bare word, e.g. the leading
# `$(` and trailing `)` in `$(bd close abc)`. No bead id contains any of them,
# so stripping is safe on both ends.
_STRIP_CHARS = "'\"()`;&${}"


def segments(command: str) -> list[str]:
    """Split a command line into independently-executed segments.

    Quote-aware: separators inside `'...'` or `"..."` are literal text, not
    command boundaries. A backslash escapes the next character outside single
    quotes, matching shell behaviour closely enough for this purpose.
    """
    text = command or ""
    out: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]

        if quote:
            current.append(char)
            if char == "\\" and quote == '"' and index + 1 < len(text):
                current.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue

        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue

        if char == "\\" and index + 1 < len(text):
            current.extend((char, text[index + 1]))
            index += 2
            continue

        separator = next(
            (sep for sep in _SEPARATORS if text.startswith(sep, index)), None
        )
        if separator:
            out.append("".join(current))
            current = []
            index += len(separator)
            continue

        current.append(char)
        index += 1

    out.append("".join(current))
    return [segment.strip() for segment in out if segment.strip()]


def _strip_env_prefix(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """Peel leading `VAR=value` assignments off a segment's token list."""
    env: dict[str, str] = {}
    index = 0
    for index, token in enumerate(tokens):  # noqa: B007 - index used after loop
        match = _ENV_ASSIGN_RE.match(token)
        if not match:
            return env, tokens[index:]
        name, raw = match.groups()
        env[name] = raw.strip().strip("'\"")
    return env, []


def _is_bd_invocation(argv: list[str]) -> bool:
    """True when the first word actually invokes `bd` (not `mybd`, not a quote)."""
    return bool(argv) and argv[0].strip(_STRIP_CHARS) == "bd"


def _bd_segments(command: str) -> list[tuple[dict[str, str], list[str]]]:
    """Return (env, argv) for each segment that invokes `bd`."""
    found = []
    for segment in segments(command):
        tokens = _TOKEN_RE.findall(segment)
        env, argv = _strip_env_prefix(tokens)
        if _is_bd_invocation(argv):
            found.append((env, argv))
    return found


def close_target(command: str) -> str | None:
    """Return the bead id this command closes, or None if it closes nothing.

    Recognises `bd close <id>` and `bd update <id> --status closed` in any flag
    order, skipping the values of flags that take a separate token.
    """
    for env, argv in _bd_segments(command):
        del env
        if len(argv) < 2:
            continue
        verb = argv[1].strip(_STRIP_CHARS)
        rest = argv[2:]

        if verb == "close":
            target = _first_positional(rest)
            if target:
                return target
        elif verb == "update" and _sets_status_closed(rest):
            target = _first_positional(rest)
            if target:
                return target
    return None


def _first_positional(tokens: list[str]) -> str | None:
    """First bare word that is not a flag or a flag's value."""
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            # `--flag=value` carries its value; `--flag value` consumes the next
            # token, which is exactly how `--status closed` used to be mistaken
            # for the bead id.
            if "=" not in token and token.split("=", 1)[0] in _VALUE_FLAGS:
                skip_next = True
            continue
        candidate = token.strip(_STRIP_CHARS).strip()
        if candidate:
            return candidate
    return None


def _sets_status_closed(tokens: list[str]) -> bool:
    """True when the argv sets `--status closed` in either flag form."""
    for index, token in enumerate(tokens):
        if token.startswith("--status="):
            if token.split("=", 1)[1].strip(_STRIP_CHARS) == "closed":
                return True
        elif token == "--status" and index + 1 < len(tokens):
            if tokens[index + 1].strip(_STRIP_CHARS) == "closed":
                return True
    return False


def waiver_reason(command: str, var_name: str) -> str | None:
    """Return the waiver reason set as an env prefix on a `bd` segment.

    Only a genuine leading assignment counts. Text that merely mentions the
    variable — inside `--reason`, a comment, or an echoed denial message — is
    not a waiver, which is what stopped the gate's own denial text from
    satisfying it.
    """
    for env, _argv in _bd_segments(command):
        if var_name in env:
            return env[var_name]
    return None
