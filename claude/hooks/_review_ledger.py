#!/usr/bin/env python3
"""Session-local ledger of isolated reviewer dispatches (Claude-only).

This is the *corroborating* half of the independent-review gate, and it is
deliberately not the load-bearing half. Claude fires a `PreToolUse` event for
the `Agent` tool, so a hook can observe that a structurally isolated reviewer —
one with no shared conversation history — was actually dispatched, and for
which bead. Codex exposes no such event, so on that host this ledger is simply
never written and the gate falls back to the host-neutral evidence in Beads.

Kept separate from `_review_record.py` because the two answer different
questions with different lifetimes: the Beads record is durable evidence that
travels with the work, while this is ephemeral proof-of-dispatch that only has
to survive between the reviewer running and its verdict being recorded.

Everything here fails open. A hook that raised on an unreadable temp file would
block a close for a reason unrelated to review discipline.
"""

from __future__ import annotations

import json
from pathlib import Path

LEDGER_DIR = Path("/tmp/claude-review-gate")


def ledger_path(session_id: str) -> Path:
    return LEDGER_DIR / f"{session_id}.json"


def _load(path: Path) -> list:
    try:
        parsed = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(parsed, dict):
        entries = parsed.get("dispatches")
        return entries if isinstance(entries, list) else []
    return []


def record_dispatch(
    session_id: str,
    beads: list[str],
    subagent_type: str,
    fingerprint: str | None,
) -> None:
    """Append an isolated-reviewer dispatch to this session's ledger."""
    path = ledger_path(session_id)
    entries = _load(path) if path.exists() else []
    entries.append({
        "beads": beads,
        "subagent_type": subagent_type,
        "fingerprint": fingerprint,
    })
    try:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"dispatches": entries}))
    except OSError:
        pass


def has_dispatch(bead_id: str) -> bool:
    """True when any session ledger holds an isolated review of this bead.

    Scanned across every ledger file rather than keyed to one session id: the
    recording CLI runs as a subprocess and may have no resolvable session id,
    and a reviewer dispatched in one session is legitimately recorded from the
    same session's shell. Cross-session reuse is safe because the close path
    independently re-checks the work fingerprint — an old dispatch cannot
    vouch for work that has changed since.
    """
    if not LEDGER_DIR.is_dir():
        return False
    try:
        candidates = sorted(LEDGER_DIR.glob("*.json"))
    except OSError:
        return False
    for path in candidates:
        for entry in _load(path):
            if isinstance(entry, dict) and bead_id in (entry.get("beads") or []):
                return True
    return False
