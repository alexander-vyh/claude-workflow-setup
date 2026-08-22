"""Shared detector: does a Bash command actually INVOKE `gh pr <verb>`?

One authority for "is this a PR-ship command", used by the blocking/asking ship gates
(merge_authorization_gate, outcome_assertion_gate) so codex and claude can't drift on the
question — the exact divergence an adversarial review of PR #119 flagged, and the reason
the deployed merge-authorization gate silently missed the cake-incident command
(`cd /wt\ngh pr merge 1750`, session cc2d7508 record 602: not a prefix of `gh pr merge`,
so the `Bash(gh pr merge:*)` matcher never fired and the leading-anchor `(^|[;&|])` regex
would not have matched it either).

`is_gh_pr_command` matches `gh pr <verb>` at a shell COMMAND POSITION: start of string,
or after a command separator (`; & | newline ` (`), optionally preceded by leading
env-assignments (`FOO=bar `) and a small set of exec-wrappers (`sudo`, `time`, …). It
therefore fires on every real invocation shape agents use:

    gh pr merge …                      # bare
    cd /wt && gh pr merge …            # && / || compound
    cd /wt<newline>gh pr merge …       # newline compound  (the cake shape)
    GH_TOKEN=… gh pr merge …           # inline-auth env prefix
    time gh pr merge … / sudo gh …     # exec wrapper
    $(gh pr merge …) / (gh pr merge)   # subshell / command substitution

and deliberately does NOT fire on a `gh pr merge` token that is not in command position
— inside a quoted `echo`/`printf` string, a `git commit -m "…"` message, or a comment —
because a *blocking* gate must never deny a command that does not actually invoke `gh`.
(The advisory outcome_ownership_nudge intentionally casts wider; a blocking gate must
not. Different guarantees, one deliberate difference — not drift.)
"""
from __future__ import annotations

import re
from functools import lru_cache

# Exec-wrappers that run the FOLLOWING command (so `time gh pr merge` really does merge).
# `echo`/`printf` are deliberately absent — they consume `gh pr merge` as literal args.
_WRAPPERS = r"(?:sudo|time|env|command|nice|nohup|xargs|stdbuf|setsid|doas)"

# A shell command position: start-of-string or immediately after a separator, then any
# leading env-assignments and exec-wrappers before the command word.
_CMD_POS = (
    r"(?:^|[\n;|&`(])"          # start, or a shell command separator / subshell open
    r"\s*"
    r"(?:\w+=\S*\s+)*"          # leading env-assignments: FOO=bar
    r"(?:" + _WRAPPERS + r"\s+)*"  # exec-wrappers: time / sudo / env / …
)


# The verb must be a COMPLETE token: followed by whitespace, end-of-string, or a shell
# separator — never a bare \b, which would match `gh pr merge-conflict-report` at the
# merge/hyphen boundary (a different subcommand, not a merge).
_VERB_END = r"(?=[\s;&|)`]|$)"


@lru_cache(maxsize=None)
def _pattern(verbs: tuple[str, ...]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(v) for v in verbs)
    # Case-sensitive: `gh` is lowercase, so env vars like GH_TOKEN never self-trigger.
    return re.compile(_CMD_POS + r"gh\s+pr\s+(?:" + alternation + r")" + _VERB_END)


def is_gh_pr_command(command: str, *verbs: str) -> bool:
    """True iff `command` invokes `gh pr <verb>` (for any given verb) at command position.

    verbs: one or more of "create", "merge", … (matched literally). Raises no error on
    empty/None command — returns False (fail-safe for hook self-filtering)."""
    if not command or not isinstance(command, str) or not verbs:
        return False
    return _pattern(tuple(verbs)).search(command) is not None
