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
import pathlib
import time
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


#: How long a recorded dispatch stays usable. The recording CLI runs as a
#: separate process moments after the reviewer returns, so minutes are enough;
#: a day is generous. Without a bound, the ledger accumulated every dispatch
#: from every session forever, and a reviewer run last week could vouch for
#: work written today.
MAX_DISPATCH_AGE_SECONDS = 24 * 60 * 60


def has_dispatch(bead_id: str, session_id: str | None = None) -> bool:
    """True when a recent isolated review of this bead was observed.

    Prefers the caller's own session ledger. Falls back to scanning recent
    ledgers only because the recording CLI runs as a subprocess that may have
    no resolvable session id — but the scan is bounded by
    `MAX_DISPATCH_AGE_SECONDS`, so a stale dispatch cannot vouch for new work.
    The close path independently re-checks the work fingerprint, so this is a
    corroborating signal, never the only one.
    """
    if session_id:
        return _contains(ledger_path(session_id), bead_id)

    if not LEDGER_DIR.is_dir():
        return False
    try:
        candidates = sorted(LEDGER_DIR.glob("*.json"))
    except OSError:
        return False

    now = time.time()
    for path in candidates:
        try:
            if now - path.stat().st_mtime > MAX_DISPATCH_AGE_SECONDS:
                continue
        except OSError:
            continue
        if _contains(path, bead_id):
            return True
    return False


def _contains(path: pathlib.Path, bead_id: str) -> bool:
    if not path.exists():
        return False
    for entry in _load(path):
        if isinstance(entry, dict) and bead_id in (entry.get("beads") or []):
            return True
    return False
