#!/usr/bin/env python3
"""Session-local ledger of isolated reviewer dispatches (Claude-only).

This is the *corroborating* half of the independent-review gate. Claude fires a
`PreToolUse` event for the `Agent` tool, so a hook can observe that a
structurally isolated reviewer — one with no shared conversation history — was
actually dispatched, and for which bead. Codex exposes no such event, so there
this ledger is never written and the gate falls back to the host-neutral
evidence in Beads.

TWO DEFECTS AN ADVERSARIAL REVIEW FOUND HERE, BOTH OF WHICH MADE THE
CORROBORATION WORTHLESS
-----------------------------------------------------------------------

1. THE DISPATCH FINGERPRINT WAS WRITTEN AND NEVER READ.
   `record_dispatch` has always stored the fingerprint at dispatch time — the
   correct anchor for "what the reviewer actually looked at" — and
   `has_dispatch` simply ignored it. The record's own fingerprint is stamped
   when the *implementer* runs the recording CLI, at a moment of its choosing.
   So: dispatch a real reviewer at state A, rewrite everything, record at state
   B, close at B — allowed, with the reviewer having read code that no longer
   exists. The fingerprint bound record→close and never review→close, so the
   stale-review hole survived inside the layer built to close it. This module's
   own docstring used to assert the opposite.

   `has_dispatch` now takes the current fingerprint and requires a dispatch
   that matches it. A reviewer that read different code does not corroborate.

2. THE LEDGER WAS FORGEABLE.
   It lived in world-writable `/tmp` with no ownership or mode check, followed
   symlinks, and re-validated nothing on read — `record_dispatch` only writes
   allowlisted `subagent_type`s, but nothing checked that when reading it back.
   One shell redirect into `/tmp/claude-review-gate/x.json` produced
   `independent: true` with no `Agent` dispatch at all. That made the
   Claude-only corroboration — the single thing Codex cannot do, and the reason
   the manifest claims Claude enforces more — worth precisely nothing.

   The directory is now created `0700`, and a ledger file is read only if it is
   a regular file (never a symlink), owned by us, not group/other-writable, in
   a directory that is likewise owned and not loosely writable. `subagent_type`
   is re-validated against the allowlist on read.

Everything still fails *closed* on a trust failure and *open* on an I/O error:
an unreadable temp file must not block a close, but an untrusted one must not
vouch for a review either.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

from _review_record import INDEPENDENT_REVIEWER_TYPES

LEDGER_DIR = Path("/tmp/claude-review-gate")

#: How long a recorded dispatch stays usable. The recording CLI runs moments
#: after the reviewer returns, so minutes are enough; a day is generous.
#: Unbounded, a reviewer run last week could vouch for work written today.
MAX_DISPATCH_AGE_SECONDS = 24 * 60 * 60

_DIR_MODE = 0o700
_FILE_MODE = 0o600


def ledger_path(session_id: str) -> Path:
    return LEDGER_DIR / f"{session_id}.json"


# ---------------------------------------------------------------------------
# Trust
# ---------------------------------------------------------------------------

def _owned_by_us(st: os.stat_result) -> bool:
    return st.st_uid in (os.geteuid(), 0)


def _not_loosely_writable(st: os.stat_result) -> bool:
    return not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def _dir_is_trusted(path: Path) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and _owned_by_us(st) and _not_loosely_writable(st)


def is_trusted_ledger(path: Path) -> bool:
    """True when a ledger file may be believed.

    Same model as `harness/bin/trusted_source.py`, kept local rather than
    imported so the hook has no cross-tree dependency. A symlink is refused
    outright: following one would let an attacker aim the ledger at a file
    they control while the check inspects the target.
    """
    if not hasattr(os, "geteuid"):  # pragma: no cover - non-POSIX
        return path.is_file()
    if path.is_symlink():
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    if not _owned_by_us(st) or not _not_loosely_writable(st):
        return False
    return _dir_is_trusted(path.parent)


def _load(path: Path) -> list:
    """Return a trusted file's dispatch entries, or [] if it may not be believed."""
    if not is_trusted_ledger(path):
        return []
    try:
        parsed = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(parsed, dict):
        entries = parsed.get("dispatches")
        return entries if isinstance(entries, list) else []
    return []


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def record_dispatch(
    session_id: str,
    beads: list[str],
    subagent_type: str,
    fingerprint: str | None,
) -> None:
    """Append an isolated-reviewer dispatch to this session's ledger."""
    path = ledger_path(session_id)
    entries = _load(path)
    entries.append({
        "beads": beads,
        "subagent_type": subagent_type,
        "fingerprint": fingerprint,
        "at": time.time(),
    })
    try:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        # mkdir's mode is ignored when the directory already exists, and the
        # directory may predate this version, so tighten it every time.
        os.chmod(LEDGER_DIR, _DIR_MODE)
        path.write_text(json.dumps({"dispatches": entries}))
        os.chmod(path, _FILE_MODE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def _entry_corroborates(entry: object, bead_id: str, fingerprint: str | None) -> bool:
    if not isinstance(entry, dict):
        return False
    if bead_id not in (entry.get("beads") or []):
        return False
    # Re-validate on read. Writing only allowlisted types is not a guarantee
    # about a file we did not necessarily write.
    if (entry.get("subagent_type") or "") not in INDEPENDENT_REVIEWER_TYPES:
        return False
    if fingerprint is None:
        # Staleness is not checkable here (no work tree). Corroboration still
        # requires a dispatch, it just cannot be pinned to a tree state.
        return True
    return entry.get("fingerprint") == fingerprint


def has_dispatch(
    bead_id: str,
    session_id: str | None = None,
    fingerprint: str | None = None,
) -> bool:
    """True when an isolated reviewer read THIS bead at THIS state of the work.

    `fingerprint` is the caller's current work fingerprint. Requiring the
    dispatch to carry the same one is what binds review→record: a reviewer that
    read state A cannot corroborate a verdict recorded at state B.

    Prefers the caller's own session ledger; falls back to scanning recent,
    trusted ledgers because the recording CLI runs as a subprocess that may
    have no resolvable session id.
    """
    if session_id:
        return any(
            _entry_corroborates(e, bead_id, fingerprint)
            for e in _load(ledger_path(session_id))
        )

    if not _dir_is_trusted(LEDGER_DIR):
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
        if any(_entry_corroborates(e, bead_id, fingerprint) for e in _load(path)):
            return True
    return False
