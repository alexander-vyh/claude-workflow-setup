"""Reading Codex's `apply_patch` PreToolUse payload.

Codex writes files through `apply_patch`, not Write/Edit, so any gate that
matches on Write/Edit is silently inert there. Every such gate needs the same
two answers — which file, and how much longer does it get — and the format is
Codex's, not ours, so it gets one owner here rather than a copy per gate.

The payload shape is CAPTURED, not assumed. See
`tests/fixtures/codex_apply_patch_pretooluse.json` for provenance: the patch
text arrives as ``tool_input["command"]`` and paths inside it are relative to
``cwd``. An earlier gate in this repo guessed ``tool_input["input"]`` and would
have been dead on arrival with its own tests passing.

Everything here fails open — an unreadable payload returns None, never an
exception into a live session.
"""

from __future__ import annotations

import os
import re

PATCH_TARGET = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$")


def first_target(command: str, cwd: str) -> tuple[str, str, int, int] | None:
    """The first file a patch touches: ``(kind, abs_path, added, removed)``.

    Only the first, because a gate reports on one file, and a patch touching
    several is better judged per-file on the next edit than by a summed number
    that matches nothing on disk.

    Returns None when there is no parseable target, so callers fail open.
    """
    if not command or "*** " not in command:
        return None
    added = removed = 0
    target: str | None = None
    kind = ""
    for line in command.splitlines():
        match = PATCH_TARGET.match(line)
        if match:
            if target is not None:
                break  # second target — report only the first
            kind, target = match.group(1), match.group(2).strip()
            continue
        if target is None:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    if not target:
        return None
    path = target if os.path.isabs(target) else os.path.join(cwd or "", target)
    return kind, path, added, removed
