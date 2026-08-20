#!/usr/bin/env python3
"""Claude Code hook: surface the bead-derived oracle convention at creation time.

Fires as PreToolUse on Bash `bd create`. ADVISORY ONLY — it injects one paragraph
of context and never blocks (no `permissionDecision`), so a spurious match costs a
paragraph and can never break a command.

Why this exists (adoption failure, not doctrine gap):
  `harness/bin/derive_contract.py` already turns a fenced ```verify block inside a
  bead's acceptance_criteria into a continuation-harness contract — goal from the
  title, verification_command from the block, fail-closed on absent or trivial
  oracles. It works. Measured 2026-08-20 in a real repo: 475 open beads, 278 with
  acceptance criteria, **0** with a verify block.

  The cause was discoverability. The convention lived in
  `claude/skills/work-breakdown/SKILL.md` and `docs/reconciliation-rules.md`, and
  the injected rules listed "bead-derived contracts" in a NOT-YET-BUILT list
  (`claude/rules/continuation-harness.md`) while teaching the hand-authored
  `init_contract.py --goal --verify` path that derive_contract was built to replace.

  A rule injected at session start also has near-zero attentional weight by the time
  a bead is created, so this follows the `outcome_ownership_nudge.py` pattern:
  put the operative lines at the moment of the decision, not at session start.

Consequence of the gap: no verify block means no derivable contract, which means
"done" falls back to whatever is mechanically checkable — a green suite, clean lint
— rather than to the outcome the bead exists to produce.

Wiring note: broad `Bash` matcher with self-filtering, not `Bash(bd create:*)`.
Claude's argument-scoped matchers are command-PREFIX matchers and miss
`cd /repo\nbd create …`, the newline-compound shape. Same reasoning as
`outcome_ownership_nudge.py` and `validate_no_shirking.py`.

Portability: the input is the command string plus the bead's own acceptance text —
durable artifact state, no transcript and no host-specific runtime payload — so this
renders to the Codex surface as well as Claude.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None

# Mirrors derive_contract._VERIFY_BLOCK_RE. Only a fence tagged `verify` counts, so
# an illustrative ``` block in acceptance criteria is never mistaken for an oracle.
_VERIFY_BLOCK_RE = re.compile(r"```[ \t]*verify[ \t]*\r?\n(.*?)\r?\n```", re.S)

_SHELL_SEP_RE = re.compile(r"&&|\|\||[;\n]")

_ACCEPTANCE_FLAGS = ("--acceptance", "--acceptance-file")

_NUDGE = (
    "Escapement: this bead has no machine oracle, so no continuation-harness "
    "contract can be derived from it and 'done' will fall back to whatever is "
    "mechanically checkable (a green suite, clean lint) rather than to the outcome "
    "the bead exists to produce.\n\n"
    "Declare the oracle ONCE, inside --acceptance, as a fenced verify block:\n\n"
    "    --acceptance=\"<what a user must be able to observe>\n\n"
    "    ```verify\n"
    "    <shell command whose exit 0 proves that outcome>\n"
    "    ```\"\n\n"
    "harness/bin/derive_contract.py reads it: goal comes from the bead title, "
    "verification_command from the block. Prefer a command that exercises the real "
    "surface a reader sees over one that inspects a helper. A trivial oracle "
    "(`true`, `echo x`) is rejected, and `bd close <id>` is tracking state, not proof."
)


def _is_bd_create(command: str) -> bool:
    """True when a segment of the command actually invokes `bd create`.

    Token-position aware so a `bd create` mentioned inside a quoted commit message
    or an echo does not trip the nudge.
    """
    for segment in _SHELL_SEP_RE.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        i = 0
        # step over leading env assignments and wrappers
        while i < len(tokens) and ("=" in tokens[i] and not tokens[i].startswith("-")):
            i += 1
        while i < len(tokens) and tokens[i] in ("env", "command"):
            i += 1
        if i + 1 < len(tokens) and Path(tokens[i]).name == "bd" and tokens[i + 1] == "create":
            return True
    return False


def _acceptance_text(command: str) -> str:
    """Return whatever was passed to --acceptance, joined across segments."""
    found: list[str] = []
    for segment in _SHELL_SEP_RE.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        for index, token in enumerate(tokens):
            for flag in _ACCEPTANCE_FLAGS:
                if token == flag and index + 1 < len(tokens):
                    found.append(tokens[index + 1])
                elif token.startswith(f"{flag}="):
                    found.append(token[len(flag) + 1:])
    return "\n".join(found)


def has_verify_oracle(acceptance: str) -> bool:
    """Whether derive_contract would find an oracle here. Kept deliberately
    identical to the consumer: a nudge that disagrees with the thing it advertises
    is worse than no nudge."""
    return bool(acceptance) and _VERIFY_BLOCK_RE.search(acceptance) is not None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    event = data.get("hook_event_name") or data.get("hookEventName") or ""
    if event != "PreToolUse" or data.get("tool_name") != "Bash":
        return 0

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return 0

    if not _is_bd_create(command):
        return 0
    if has_verify_oracle(_acceptance_text(command)):
        _record_signal(
            gate_name="bead_verify_nudge",
            decision="allow",
            reason="bd create carries a derivable verify oracle",
        )
        return 0

    _record_signal(
        gate_name="bead_verify_nudge",
        decision="nudge",
        reason="bd create without a fenced verify oracle in --acceptance",
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": _NUDGE,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
