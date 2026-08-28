#!/usr/bin/env python3
"""Interpret the `bd` command a close gate is asked to judge.

Split out of `review_gate.py` because reading a shell command correctly is its
own responsibility with its own failure modes — and an adversarial review
established that those failure modes, not the decision logic, were where this
gate actually stopped working. The decision logic was right; it just was not
being handed the commands agents really run.

WHAT THE COMMAND SURFACE HAS TO SURVIVE
---------------------------------------
Every one of these was a live bypass, and none of them looks like evasion —
they are ordinary `bd` usage, which is worse than an evadable gate because the
agent never learns it dodged anything and no signal is produced:

  bd close -r "finished the work"   `-r` is `--reason`; the prose was read as
                                    the bead id, `bd show` failed, and the gate
                                    fell open
  bd close                          closes the last-touched issue; no id to
                                    parse, so nothing was gated
  bd close esc-a esc-b              only the first id was checked
  bd -C . close X                   a global flag before the subcommand meant
  bd --actor bot close X            the command was not recognised as a close
  bd done X                         documented alias for `close`
  bd update X -s closed             `-s` is `--status`
  ID=x; bd close $ID                an unexpanded variable was read as the id
  echo $(bd close X)                a substitution runs, but argv[0] was `echo`
  bd create -d "`bd close`"         and the shell expands inside double quotes

TWO LAYERS, DELIBERATELY REDUNDANT
-----------------------------------
1. Know `bd`'s grammar: global flags, close verbs, and which flags consume a
   following token.
2. Require every resolved target to be *shaped* like a bead id.

Layer 2 exists because layer 1 is a list, and lists go stale the moment `bd`
adds a flag. If anything unexpected lands in the positional slot, the answer is
"this close targets something I cannot identify" — which the gate must refuse,
not wave through. A gate that fails open on the inputs it does not understand
is only enforcing against inputs that were never a problem.

The last two were found by accident rather than by reasoning: a backticked
`bd close`, written as an *illustration* inside a `--description "..."`
argument while filing an unrelated bead, was expanded by the shell and really
closed the last-touched issue. Nothing in the gate saw a close. That is the
worst shape a bypass can take — it needs no intent, produces no signal, and
teaches nobody anything.

Deliberately not handled: `eval "bd close X"`, `sh -c "bd close X"`, and a
`bd close` inside a heredoc body. Those are conscious limits, not oversights —
covering them needs a real shell interpreter. Command substitution used to be
grouped with them; it does not belong there, because unlike `eval` it appears
constantly in ordinary command lines.
"""

from __future__ import annotations

import re

# Shell separators that begin a new command, honoured only outside quotes.
# A regex split cannot do this: a `;` inside a quoted waiver reason would cut
# the command in half, the fragment holding `bd close` would stop looking like
# a close, and the gate would silently allow it. Punctuation in prose must not
# be a bypass.
_SEPARATORS = ("||", "&&", ";", "|", "\n")

# Command substitution runs a real command, so it is a segment boundary too.
# Crucially it is honoured INSIDE double quotes, because that is where the
# shell expands it — which is how this was found: a backticked `bd close`
# written as an illustration inside a `--description "..."` argument really
# closed the last-touched issue, with the gate never seeing a close at all.
# Single quotes still protect their contents, matching the shell.
_SUBST_OPEN = "$("
_SUBST_BACKTICK = "`"

# Process substitution runs a command too, and `<(bd close X)` bypassed this
# gate entirely until escapement-843m. It is listed separately from `$(` because
# the QUOTING RULES DIFFER, and following the shell exactly is what keeps the
# false-positive direction closed: the shell expands `$(...)` and backticks
# inside double quotes, but does NOT perform process substitution there. So
# `"<(bd close x)"` is literal text an agent may write in a bead description,
# while `"$(bd close x)"` really runs.
_PROC_SUBST_OPENERS = ("<(", ">(")

_ENV_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
_TOKEN_RE = re.compile(r"(?:'[^']*'|\"[^\"]*\"|\S)+")

# Shell punctuation that may cling to a bare word, e.g. `$(bd close abc)`.
_STRIP_CHARS = "'\"()`;&${}"

#: A bead id: lowercase word segments, a short alphanumeric suffix, optional
#: dot-separated child indices. `escapement-iw8s`, `escapement-mol-4ef`,
#: `escapement-858.4`, `cake-4cq.1.1`.
#:
#: Anchored, unlike the loose scanner used to find ids *inside prose*. Here the
#: question is "is this token a bead id", and the answer must be no for
#: `finished the work here`, `$ID`, and `closed`.
BEAD_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\.\d+)*$")

# `bd` global flags that consume the following token.
_GLOBAL_VALUE_FLAGS = {
    "--actor", "--db", "-C", "--directory", "--dolt-auto-commit",
}
# `bd` global flags that stand alone.
_GLOBAL_BOOL_FLAGS = {
    "--global", "--ignore-schema-skew", "--json", "--profile", "-q",
    "--quiet", "--readonly", "--sandbox",
}

#: Subcommands that close. `done` is a documented alias for `close`.
_CLOSE_VERBS = {"close", "done"}

# Subcommand flags that consume the following token. `-r` and `-s` are the
# short forms whose absence made prose and the word "closed" parse as bead ids.
_VALUE_FLAGS = {
    "-r", "--reason", "--reason-file", "--session",
    "-s", "--status",
    "-a", "--assignee", "-p", "--priority", "-t", "--type", "-e", "--estimate",
    "-l", "--labels", "-d", "--description",
    "--notes", "--append-notes", "--design", "--owner", "--parent",
    "--due", "--defer", "--metadata", "--set-metadata", "--unset-metadata",
    "--external-ref", "--id",
}

#: Flags meaning "do not actually close anything".
_NON_CLOSING_FLAGS = {"-h", "--help", "--dry-run"}

# Redirection operators. `bd close X >/dev/null` used to put `/dev/null` in the
# positional slot, fail the bead-id shape check, and get refused as "a bead the
# gate cannot identify" — while the denial's first remedy was "name the bead
# explicitly", which is what the agent had already done (escapement-c94e).
#
# THE SPLIT BELOW IS LOAD-BEARING, and it is the difference between a friction
# fix and a new bypass. A BARE operator takes the NEXT token as its target;
# an operator with its target ATTACHED (`>/dev/null`, `2>&1`) takes nothing.
# Consuming the next token unconditionally — the obvious version of this rule —
# swallows a following bead in a multi-bead close, so `bd close A 2>&1 B` would
# check only A and let B close UNREVIEWED. That is a silent allow, the one
# direction this module must never move in; over-refusal is survivable, this is
# not.
#
# One regex, split on whether anything FOLLOWS the operator inside the token.
# That, not the operator's spelling, is what decides. `2>` is bare and takes the
# next token; `2>&1` is the same operator family with its target attached and
# takes nothing. A set of literal spellings got this wrong for `2>` and `&>`.
_REDIRECT_RE = re.compile(
    r"^(?P<op>&>>|&>|\d*(?:>>|>&|>|<<<|<<|<&|<))(?P<rest>.*)$"
)


def segments(command: str) -> list[str]:
    """Split a command line into independently-executed segments.

    Quote-aware: separators inside `'...'` or `"..."` are literal text, not
    command boundaries. A backslash escapes the next character outside single
    quotes, matching shell behaviour closely enough for this purpose.

    Command substitutions are the exception to the double-quote rule, because
    they are the exception in the shell: `"$(bd close x)"` runs, and so does
    `` "`bd close x`" ``. Their contents therefore become segments of their own
    even inside double quotes. A closing `)` only ends a substitution that was
    opened — treating every `)` as a boundary would split a waiver reason
    containing an ordinary parenthesis, stranding the `REVIEW_WAIVER=` prefix
    in a different segment and quietly turning a valid waiver into an unwaived
    close.
    """
    text = command or ""
    out: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    index = 0

    def flush() -> None:
        nonlocal current
        out.append("".join(current))
        current = []

    while index < len(text):
        char = text[index]

        if quote == "'":
            current.append(char)
            if char == "'":
                quote = None
            index += 1
            continue

        # Checked before the double-quote branch: the shell expands these there.
        if text.startswith(_SUBST_OPEN, index):
            flush()
            depth += 1
            index += len(_SUBST_OPEN)
            continue
        if char == _SUBST_BACKTICK:
            flush()
            index += 1
            continue
        if char == ")" and depth > 0:
            flush()
            depth -= 1
            index += 1
            continue

        # Process substitution, unlike `$(`, is NOT expanded inside double
        # quotes — so this check sits after the quote branch would have claimed
        # the character, and only runs when we are outside quotes entirely.
        if quote is None and any(
            text.startswith(opener, index) for opener in _PROC_SUBST_OPENERS
        ):
            flush()
            depth += 1
            index += 2
            continue

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
            flush()
            index += len(separator)
            continue

        current.append(char)
        index += 1

    flush()
    return [segment.strip() for segment in out if segment.strip()]


def _strip_env_prefix(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """Peel leading `VAR=value` assignments off a segment's token list."""
    env: dict[str, str] = {}
    for index, token in enumerate(tokens):
        match = _ENV_ASSIGN_RE.match(token)
        if not match:
            return env, tokens[index:]
        name, raw = match.groups()
        env[name] = raw.strip().strip("'\"")
    return env, []


def _clean(token: str) -> str:
    return token.strip(_STRIP_CHARS).strip()


def _bd_segments(command: str) -> list[tuple[dict[str, str], list[str]]]:
    """Return (env, argv) for each segment that invokes `bd`."""
    found = []
    for segment in segments(command):
        tokens = _TOKEN_RE.findall(segment)
        env, argv = _strip_env_prefix(tokens)
        if argv and _clean(argv[0]) == "bd":
            found.append((env, argv))
    return found


def _skip_global_flags(tokens: list[str]) -> list[str]:
    """Drop `bd`'s global flags so the subcommand can be found."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            break
        name = token.split("=", 1)[0]
        if name in _GLOBAL_VALUE_FLAGS and "=" not in token:
            index += 2
            continue
        if name in _GLOBAL_VALUE_FLAGS or name in _GLOBAL_BOOL_FLAGS:
            index += 1
            continue
        break
    return tokens[index:]


def _positionals(tokens: list[str]) -> list[str]:
    """Every bare word that is not a flag, a flag's value, or a redirection."""
    out: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if "=" not in token and token.split("=", 1)[0] in _VALUE_FLAGS:
                skip_next = True
            continue
        redirect = _REDIRECT_RE.match(token)
        if redirect:
            # Bare (`>`, `2>`, `&>`): the target is the NEXT token, consume it.
            # Attached (`>/dev/null`, `2>&1`): the target rides along, consume
            # only this token — see the note above on why the difference is a
            # bypass rather than a nicety.
            skip_next = not redirect.group("rest")
            continue
        cleaned = _clean(token)
        if cleaned:
            out.append(cleaned)
    return out


def _sets_status_closed(tokens: list[str]) -> bool:
    """True when the argv sets status to closed, in any flag form."""
    for index, token in enumerate(tokens):
        name, _, inline = token.partition("=")
        if name not in ("--status", "-s"):
            continue
        if inline:
            if _clean(inline) == "closed":
                return True
        elif index + 1 < len(tokens) and _clean(tokens[index + 1]) == "closed":
            return True
    return False


def close_targets(command: str) -> list[str] | None:
    """Resolve which beads a command closes.

    Returns:
        None  — the command does not close anything.
        []    — it closes something we cannot identify (a bare `bd close`, an
                unexpanded `$ID`, prose captured from an unknown flag). The
                caller must refuse: an unidentifiable close cannot be checked
                against a review, and treating it as "not a close" is the
                bypass this return value exists to prevent.
        [ids] — the beads being closed. `bd close` accepts several.
    """
    resolved: list[str] = []
    closing = False

    for _env, argv in _bd_segments(command):
        rest = _skip_global_flags(argv[1:])
        if not rest:
            continue
        verb = _clean(rest[0])
        args = rest[1:]

        if any(token.split("=", 1)[0] in _NON_CLOSING_FLAGS for token in args):
            continue

        if verb in _CLOSE_VERBS:
            closing = True
        elif verb == "update" and _sets_status_closed(args):
            closing = True
        else:
            continue

        for candidate in _positionals(args):
            if BEAD_ID_RE.match(candidate):
                if candidate not in resolved:
                    resolved.append(candidate)
            else:
                # Something occupies the positional slot but is not a bead id.
                # Never ignore it: that is how `-r "finished the work"` used to
                # sail through.
                return []

    if not closing:
        return None
    return resolved


def waiver_reason(command: str, var_name: str) -> str | None:
    """Return the waiver reason set as an env prefix on a `bd` segment.

    Only a genuine leading assignment counts. Text that merely mentions the
    variable — inside `--reason`, a comment, or an echoed denial message — is
    not a waiver. That distinction is what stopped the gate's own denial text,
    which contains the literal placeholder, from satisfying the gate.
    """
    for env, _argv in _bd_segments(command):
        if var_name in env:
            return env[var_name]
    return None


def writes_reserved_metadata(command: str, key: str) -> bool:
    """True when a command sets the reserved review-record metadata key directly.

    Recording a review must go through the recording CLI, which is what stamps
    independence from an observed reviewer dispatch. A hand-written
    `bd update <id> --set-metadata escapement_review={...}` would let an
    implementer mint `independent: true` for itself with no reviewer involved.
    """
    for _env, argv in _bd_segments(command):
        for index, token in enumerate(argv):
            name, _, inline = token.partition("=")
            if name not in ("--set-metadata", "--metadata"):
                continue
            value = inline or (argv[index + 1] if index + 1 < len(argv) else "")
            if _clean(value).lstrip("'\"").startswith(key):
                return True
    return False
