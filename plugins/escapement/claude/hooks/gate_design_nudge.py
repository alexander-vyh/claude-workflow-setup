#!/usr/bin/env python3
"""Claude Code hook: nudge toward the `gate-design` skill when editing a gate.

The mechanical half of the gate-design rule->skill conversion (option D+B):
the full gate-design manual lives in the on-demand `gate-design` skill, a
3-rule checklist stays resident as a stub rule, and THIS PreToolUse nudge
catches the file-edit trigger a resident checklist might miss.

When you Write/Edit — or, on Codex, apply_patch — a gate-ish file — a hook `.py`, a file with `gate` in its
name, or `settings.template.json` (gate wiring) — it injects a one-line
systemMessage reminding you to load the `gate-design` skill before building the
gate, so the deny gets an escape path, the gate emits signal, and any required
value is validated (not merely present).

This is a NUDGE, never a gate: it emits only `systemMessage` and exits 0. It
NEVER returns permissionDecision, so it can never block an edit.

Scope is deliberately tight (gate-ish paths only) so it is signal, not noise —
ordinary edits pass silently. No cooldown in v1: the message is one line and
becomes a no-op once the skill is loaded; add a cooldown later if it proves
noisy during heavy gate work.

Exit codes:
  0 — always (advisory only, never blocks)
"""

from __future__ import annotations

import json
import os
import sys


def _patch_target(command: str, cwd: str) -> str | None:
    """The file a Codex apply_patch touches, or None if it cannot be read."""
    hooks_dir = os.path.dirname(os.path.abspath(__file__))
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    try:
        from _codex_patch import first_target  # type: ignore
    except ImportError:
        return None  # advisory hook: never break a session over a missing helper
    parsed = first_target(command, cwd)
    return parsed[1] if parsed else None


_NUDGE = (
    "You're editing a gate-ish file. Load the `gate-design` skill and satisfy "
    "its 3 rules before shipping: (1) an escape path IN the denial, "
    "(2) persistent signal via _gate_signal.record(...), (3) validate value "
    "not presence. The skill has the reference designs and anti-patterns."
)


def _is_gate_path(file_path: str) -> bool:
    """True if this path looks like gate authoring: a hook .py, a *gate* file,
    or the settings template that wires gates."""
    if not file_path:
        return False
    name = os.path.basename(file_path)
    norm = file_path.replace(os.sep, "/")

    if name == "settings.template.json":
        return True
    # A Python file inside a hooks/ directory is almost certainly a gate/hook.
    if name.endswith(".py") and "/hooks/" in norm:
        return True
    # Any file whose name carries "gate" (foo_gate.py, gate-design.md, …).
    if "gate" in name.lower():
        return True
    return False


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "apply_patch"):
        return 0
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0

    if tool_name == "apply_patch":
        # Codex authors gates through apply_patch, so matching Write/Edit alone
        # left the host that most needs this nudge without it.
        target = _patch_target(tool_input.get("command", "") or "",
                               str(data.get("cwd") or ""))
        if target is None:
            return 0  # unparseable — stay silent rather than guess
        file_path = target
    else:
        file_path = tool_input.get("file_path", "")

    if not _is_gate_path(file_path):
        return 0  # ordinary edit — stay silent (anti-noise)

    json.dump({"systemMessage": _NUDGE}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
