#!/usr/bin/env python3
"""Claude Code hook: just-in-time outcome-ownership salience at the ship boundary.

Fires as PreToolUse on the PR-ship actions (`gh pr create`, `gh pr merge`) and
re-surfaces the operative lines of `claude/rules/outcome-ownership.md` at the exact
moment the agent is declaring delivery. ADVISORY ONLY — it injects context and never
blocks (no `permissionDecision`), so a spurious match can only inject one advisory
paragraph; it can never block or break a command.

Wiring note (why a broad `Bash` matcher, not `Bash(gh pr merge:*)`):
  Claude's argument-scoped matchers are command-PREFIX matchers — `Bash(gh pr merge:*)`
  does NOT match `cd /worktree\ngh pr merge …`, the exact newline-compound shape the cake
  incident used (transcript record 602). So this hook is wired on the broad `Bash`
  matcher (like validate_no_shirking) and self-filters via `_is_ship_command`, matching
  the command TOKENS anywhere they appear rather than at a leading anchor. Same design on
  the codex surface. (The prefix-matcher blindspot also affects merge_authorization_gate
  and outcome_assertion_gate — tracked separately.)

Why this exists (binding failure, not doctrine gap):
  In cake session cc2d7508 (2026-07-16) the FULL outcome-ownership doctrine — including
  the "pre-existing is not an excuse" anti-pattern and "you are the follow-up" — was
  injected at session start (transcript record 6). ~600 records later, at the finishing
  action, the agent shipped three known test reds it self-classified as "pre-existing …
  orthogonal … pass in CI on full data" and wrote "flag the two remaining items for
  you." The doctrine reached the agent and was ignored: a SALIENCE-DECAY failure. A rule
  injected at session start has ~zero attentional weight by the time the ship action
  happens. This hook moves the operative lines from where they decay (session start) to
  where the decision is made (the ship command).

Deliberately NOT a language matcher and NOT a blocker:
  `validate_no_shirking.py` already tries to BLOCK on dismissive language and missed this
  exact paraphrase ("pre-existing dev-dataset test reds"). Chasing paraphrases with a
  regex is a losing arms race (see that file's FP-narrowing history). This hook does not
  detect shirking at all — it unconditionally re-states the standard at the ship point,
  so it is immune to phrasing. A spurious fire (e.g. an echoed literal `gh pr create`
  inside a quoted string) costs one advisory paragraph and can never block — so the
  false-positive cost is bounded to noise, deliberately traded for catching every real
  ship shape.

Scope note: `git push` is intentionally EXCLUDED. Agents push constantly; a nudge on
  every push becomes wallpaper and is tuned out. `gh pr create` / `gh pr merge` are the
  rare, high-stakes "I am declaring this delivered" moments — the salience budget is
  spent there.

Input (via stdin):
  JSON with hook_event_name, tool_name, tool_input.command
Exit codes:
  0 — always (advisory only, never blocks)
"""
from __future__ import annotations

import json
import re
import sys

# A `gh pr create` / `gh pr merge` invocation ANYWHERE in the command triggers the
# reminder. We match the command TOKENS (word-bounded), not a leading anchor, so the
# following real ship shapes all fire — the leading-anchor version missed every one:
#   cd /worktree\ngh pr merge 1750   (newline-compound — the cake-incident shape)
#   GH_TOKEN=… gh pr merge 262        (inline-auth env prefix)
#   time gh pr merge / sudo gh pr …   (wrapper prefix)
#   $(gh pr merge …) / (gh pr merge)  (subshell)
# `gh pr view/list/checks` (inspection) and `git push` never match — create|merge is
# required and git is not gh. Case-sensitive: `gh` is lowercase, so env vars like
# GH_TOKEN do not themselves trigger it.
_SHIP_RE = re.compile(r"\bgh\s+pr\s+(?:create|merge)\b")

_NUDGE = (
    "OUTCOME-OWNERSHIP CHECK (you are about to declare delivery):\n"
    "• A red test in your delivery window is YOURS — regardless of when it started. "
    "\"pre-existing\", \"unrelated\", \"orthogonal\", or \"passes in CI on full data\" "
    "is NOT grounds to ship it or hand it off.\n"
    "• You are the follow-up. Do NOT defer failing tests, or any named acceptance "
    "criterion, to the user as \"remaining items\" / \"worth your eye\" / post-merge "
    "work. If it is not done, it is continued work now — not a handoff.\n"
    "• Before you ship: is the authoritative test run GREEN and the real user-facing "
    "outcome verified? If not, that is the next action — not this command. If a red is "
    "genuinely un-greenable locally, own its verification against the real arbiter "
    "(schedule your own return), do not classify it away."
)


def _is_ship_command(command: str) -> bool:
    return _SHIP_RE.search(command) is not None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if data.get("hook_event_name") != "PreToolUse":
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command or not _is_ship_command(command):
        return 0

    # Non-blocking: inject context, no permissionDecision. The agent reads this as it
    # proceeds with the ship — it is never denied.
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": _NUDGE,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
