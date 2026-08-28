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


def find_dispatch(
    bead_id: str,
    session_id: str | None = None,
    fingerprint: str | None = None,
) -> dict | None:
    """Return the corroborating dispatch entry, or None.

    Returns the entry rather than a bool because the caller needs two things
    out of it that a bool throws away: the reviewer's actual `subagent_type`
    (so the recorded `reviewer` field names the agent that really ran, instead
    of whatever the implementer typed after `--reviewer`), and the captured
    verdict, if the host let us observe one.

    `fingerprint` is the caller's current work fingerprint. Requiring the
    dispatch to carry the same one is what binds review→record: a reviewer that
    read state A cannot corroborate a verdict recorded at state B.

    Prefers the caller's own session ledger; falls back to scanning recent,
    trusted ledgers because the recording CLI runs as a subprocess that may
    have no resolvable session id.

    SELECTION AMONG SEVERAL MATCHES. Newest-wins alone is wrong, and mutation
    testing is what surfaced it: two reviewers dispatched in parallel against
    the same bead and the same tree state produce two entries whose relative
    order is arbitrary, so "dispatch two reviewers and record whichever one
    liked it" would have become the next bypass. A blocking verdict therefore
    outranks a clean one regardless of timing — a reviewer that said BLOCK is
    not overruled by a second opinion that arrived a second later. Only among
    equally-blocking entries does the newest win, which is what makes a
    re-review after repair count.
    """
    matches: list[dict] = []
    for path in _candidate_paths(session_id):
        matches.extend(
            e for e in _load(path) if _entry_corroborates(e, bead_id, fingerprint)
        )
    if not matches:
        return None
    return max(matches, key=lambda e: (bool(e.get("blocking")), e.get("at") or 0))


def _candidate_paths(session_id: str | None) -> list[Path]:
    if session_id:
        return [ledger_path(session_id)]
    if not _dir_is_trusted(LEDGER_DIR):
        return []
    try:
        candidates = sorted(LEDGER_DIR.glob("*.json"))
    except OSError:
        return []
    now = time.time()
    fresh: list[Path] = []
    for path in candidates:
        try:
            if now - path.stat().st_mtime <= MAX_DISPATCH_AGE_SECONDS:
                fresh.append(path)
        except OSError:
            continue
    return fresh


def has_dispatch(
    bead_id: str,
    session_id: str | None = None,
    fingerprint: str | None = None,
) -> bool:
    """True when an isolated reviewer read THIS bead at THIS state of the work."""
    return find_dispatch(bead_id, session_id, fingerprint) is not None


def record_agent_id(
    session_id: str,
    bead_ids: list[str],
    subagent_type: str,
    agent_id: str,
) -> bool:
    """Bind the native child identifier to its dispatch, claiming any verdict.

    `PostToolUse` carries both `tool_input` (which names the bead) and
    `tool_response.agentId` (the child's identity). `SubagentStop` carries the
    identity and the verdict but NOT the bead, and the two events share no
    `tool_use_id`. So `agent_id` is the only join key, and this is where it is
    established.

    The claim step exists because the event ORDER is mode-dependent: a
    background dispatch fires PostToolUse first, a foreground one fires
    SubagentStop first (escapement-g27c pinned both). A verdict that arrived
    early was stashed by `record_subagent_verdict`; it is collected here.
    """
    if not agent_id or subagent_type not in INDEPENDENT_REVIEWER_TYPES:
        return False

    def apply(state: dict) -> bool:
        entry = _newest_open(state.get("dispatches", []), bead_ids, subagent_type)
        if entry is None:
            return False
        entry["agent_id"] = agent_id
        pending = (state.get("pending_verdicts") or {}).pop(agent_id, None)
        if pending:
            _attach_verdict(entry, pending)
        return True

    return _mutate(session_id, apply)


def record_subagent_verdict(session_id: str, agent_id: str, verdict: str) -> bool:
    """Attach a `SubagentStop` verdict to its dispatch, or stash it.

    Returns True when it landed on a dispatch entry, False when it was held for
    one that has not been bound yet. False is not an error — it is the ordinary
    foreground case, and dropping the verdict there would silently lose every
    review dispatched in the foreground.
    """
    if not agent_id or not verdict:
        return False

    def apply(state: dict) -> bool:
        for entry in state.get("dispatches", []):
            if isinstance(entry, dict) and entry.get("agent_id") == agent_id:
                _attach_verdict(entry, verdict)
                return True
        state.setdefault("pending_verdicts", {})[agent_id] = verdict
        return False

    return _mutate(session_id, apply)


def _newest_open(entries: list, bead_ids: list[str], subagent_type: str) -> dict | None:
    """The most recent dispatch for this bead and reviewer with no verdict yet."""
    open_entries = [
        e for e in entries
        if isinstance(e, dict)
        and not e.get("verdict")
        and e.get("subagent_type") == subagent_type
        and any(b in (e.get("beads") or []) for b in bead_ids)
    ]
    if not open_entries:
        return None
    return max(open_entries, key=lambda e: e.get("at") or 0)


def _attach_verdict(entry: dict, verdict: str) -> None:
    module = _verdict_module()
    entry["verdict"] = module.clip_verdict(verdict)
    entry["verdict_digest"] = module.verdict_digest(verdict)
    entry["blocking"] = module.classify_blocking(verdict)
    entry["verdict_at"] = time.time()


def _mutate(session_id: str, apply) -> bool:
    """Read the session ledger, apply a change, write it back."""
    path = ledger_path(session_id)
    state = _load_state(path)
    result = apply(state)
    try:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        os.chmod(LEDGER_DIR, _DIR_MODE)
        path.write_text(json.dumps(state))
        os.chmod(path, _FILE_MODE)
    except OSError:
        return False
    return result


def _load_state(path: Path) -> dict:
    """Whole trusted ledger document, including any stashed verdicts."""
    if not is_trusted_ledger(path):
        return {"dispatches": [], "pending_verdicts": {}}
    try:
        parsed = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"dispatches": [], "pending_verdicts": {}}
    if not isinstance(parsed, dict):
        return {"dispatches": [], "pending_verdicts": {}}
    entries = parsed.get("dispatches")
    pending = parsed.get("pending_verdicts")
    return {
        "dispatches": entries if isinstance(entries, list) else [],
        "pending_verdicts": pending if isinstance(pending, dict) else {},
    }


def record_verdict(
    session_id: str,
    bead_ids: list[str],
    subagent_type: str,
    verdict: str,
) -> bool:
    """Attach a reviewer's returned text to its own dispatch entry.

    Called from `Agent` **PostToolUse**, which is the only moment the subagent's
    output exists. The PreToolUse entry it updates was written before the
    reviewer emitted a token, which is precisely why a dispatch on its own was
    never evidence that a review happened.

    Binds to the newest entry for this bead and reviewer type that has no
    verdict yet, so two reviews in one session do not overwrite each other.
    Returns True when an entry was updated.
    """
    if not verdict or subagent_type not in INDEPENDENT_REVIEWER_TYPES:
        return False
    path = ledger_path(session_id)
    entries = _load(path)

    open_entries = [
        e for e in entries
        if isinstance(e, dict)
        and not e.get("verdict")
        and e.get("subagent_type") == subagent_type
        and any(b in (e.get("beads") or []) for b in bead_ids)
    ]
    if not open_entries:
        return False

    target = max(open_entries, key=lambda e: e.get("at") or 0)
    target["verdict"] = _verdict_module().clip_verdict(verdict)
    target["verdict_digest"] = _verdict_module().verdict_digest(verdict)
    target["blocking"] = _verdict_module().classify_blocking(verdict)
    target["verdict_at"] = time.time()

    try:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        os.chmod(LEDGER_DIR, _DIR_MODE)
        path.write_text(json.dumps({"dispatches": entries}))
        os.chmod(path, _FILE_MODE)
    except OSError:
        return False
    return True


def _verdict_module():
    """Imported lazily so a missing sibling degrades capture, not the gate.

    `review_gate` is a `PreToolUse` hook on `bd close`; if this module failed to
    import, every close in the repository would stop. Verdict capture is the
    newer, more optional half — it must never be able to take the load-bearing
    half down with it.
    """
    import _review_verdict

    return _review_verdict
