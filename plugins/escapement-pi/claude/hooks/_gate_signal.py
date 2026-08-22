"""Shared signal-capture for Claude Code gate hooks.

Per `claude/rules/gate-design.md` Rule 2: every gate must produce
persistent signal. This module is the canonical implementation —
all gates in `claude/hooks/` call `record()` at every decision point
so the corpus of gate-firings is uniform, queryable, and amenable to
the half-life review that Operating Rule 1 of the bureaucracy
principle requires.

## API

    from _gate_signal import record
    record(gate_name="spec_id_enforcement",
           decision="deny",
           reason="placeholder value 'none'",
           command="bd create --type=task ...")

## Storage shape

Each call appends one JSON line to `.beads/.gate-signal.jsonl`:

    {"ts": "2026-05-26T22:42:21Z",
     "gate": "spec_id_enforcement",
     "decision": "deny",
     "reason": "placeholder value 'none'",
     "session_id": "67b9768d-...",
     "extras": {"command": "bd create ..."}}

## Failure behavior

The gate's primary job is enforcement; logging is secondary. If persistence
fails, `record()` returns ``False`` without raising so callers that need an
operator-visible diagnostic can emit one without changing the gate decision.

## Querying

See `claude/bin/gate_signal_query.py` for the canonical reader.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SIGNAL_FILENAME = ".gate-signal.jsonl"
# The dedicated waiver corpus, distinct from the unified signal store. Per
# gate-design.md "Standard waiver convention": waiver reasons accumulate in
# .beads/.gate-waivers.jsonl, "keyed by gate and decision", so the user can
# grep ONE file for the full reasoned-exception corpus instead of filtering
# the high-volume signal store. Every accepted waiver is written to BOTH
# stores: the unified store keeps the complete decision timeline; the waiver
# store is the labeled training corpus the half-life review reads.
_WAIVER_FILENAME = ".gate-waivers.jsonl"
_BEADS_DIR_ENV = "BEADS_DIR"

# Fallback sink for when no `.beads/` is locatable (a repo or worktree without
# beads). Without this, the primary path resolves to None and the entry is
# silently dropped — making `_gate_signal` a single point of failure for the
# whole gate-learning loop (docs/reconciliation-rules.md Conflict 2). When the
# primary `.beads/` signal path is unresolvable, record() writes here instead
# so signal is preserved, not lost. The directory defaults to user-level
# `~/.claude/harness/` and is overridable via GATE_SIGNAL_FALLBACK_DIR (used by
# tests to redirect away from the real store; also a deliberate operator hook).
_FALLBACK_FILENAME = "gate-signal-fallback.jsonl"
_FALLBACK_DIR_ENV = "GATE_SIGNAL_FALLBACK_DIR"
_DEFAULT_FALLBACK_DIR = Path("~/.claude/harness")


def _resolve_beads_dir() -> Path | None:
    """Find the .beads directory.

    Prefers the BEADS_DIR env var (set in cake-style multi-worktree
    setups); otherwise walks up from CWD looking for .beads/.
    Returns None if no .beads/ is locatable.
    """
    beads_dir_env = os.environ.get(_BEADS_DIR_ENV)
    if beads_dir_env:
        candidate = Path(beads_dir_env)
        if candidate.is_dir():
            return candidate

    cwd = Path(os.getcwd()).resolve()
    for parent in [cwd, *cwd.parents]:
        beads = parent / ".beads"
        if beads.is_dir():
            return beads

    return None


def _resolve_signal_path() -> Path | None:
    """Return the unified signal file path, or None if no .beads/ exists.

    Kept as a named helper so existing callers/tests that assert the
    signal-store location continue to resolve it unambiguously.
    """
    beads = _resolve_beads_dir()
    return beads / _SIGNAL_FILENAME if beads is not None else None


def _resolve_waiver_path() -> Path | None:
    """Return the dedicated waiver corpus path, or None if no .beads/ exists."""
    beads = _resolve_beads_dir()
    return beads / _WAIVER_FILENAME if beads is not None else None


def _resolve_fallback_path(create: bool = True) -> Path | None:
    """Return the user-level fallback signal path.

    Used only when the primary `.beads/` signal path is unresolvable, so the
    gate-learning loop is not a single point of failure (a no-beads context
    would otherwise drop every gate decision silently). The directory is
    GATE_SIGNAL_FALLBACK_DIR if set, else `~/.claude/harness/`; it is created
    if absent. Returns None only if even the directory cannot be prepared —
    record() then degrades to its existing fail-soft no-op. Never raises.

    Readers pass create=False: resolving a path to read from should not have
    the side effect of creating a directory.
    """
    try:
        env_dir = os.environ.get(_FALLBACK_DIR_ENV)
        base = Path(env_dir) if env_dir else _DEFAULT_FALLBACK_DIR
        base = base.expanduser()
        if create:
            base.mkdir(parents=True, exist_ok=True)
        return base / _FALLBACK_FILENAME
    except Exception:
        return None


def signal_sources() -> list[Path]:
    """Return every existing signal file a reader must union.

    record() writes to `.beads/.gate-signal.jsonl` when a beads dir is
    locatable and to the user-level fallback when it is not, so a reader
    consulting only one of them silently under-reports. This is not an edge
    case: a linked worktree without its own `.beads/` sends an entire
    session's gate history to the fallback, where every `.beads/`-only reader
    is blind to it.

    Path resolution lives here because this module owns the write side; a
    reader re-deriving it is how the two drifted apart in the first place.
    """
    sources: list[Path] = []
    seen: set[Path] = set()
    for candidate in (_resolve_signal_path(), _resolve_fallback_path(create=False)):
        if candidate is None or not candidate.is_file():
            continue
        try:
            key = candidate.resolve()
        except OSError:
            key = candidate
        if key in seen:
            continue
        seen.add(key)
        sources.append(candidate)
    return sources


def record(
    gate_name: str,
    decision: str,
    reason: str = "",
    event_type: str = "signal",
    **extras: Any,
) -> bool:
    """Append one signal record to .beads/.gate-signal.jsonl.

    When ``event_type == "waiver"`` the same record is ALSO appended to the
    dedicated waiver corpus (.beads/.gate-waivers.jsonl) so the documented
    standard waiver convention (gate-design.md) is real and greppable: the
    waiver store is the labeled training data the half-life review reads,
    while the unified signal store still carries the complete timeline.

    Args:
        gate_name: stable identifier for the gate (e.g.
            'spec_id_enforcement'). Used by query tools to group
            decisions per gate.
        decision: one of 'allow', 'deny', 'ask', 'allow-with-warning',
            'waiver-accepted', or any other shape the gate uses.
        reason: human-readable rationale. For waivers, the user's
            captured reason text — this is the labeled training data
            future revisions read.
        event_type: 'signal' (default) writes only the unified store;
            'waiver' additionally writes the dedicated waiver corpus.
        **extras: any additional fields the specific gate wants to
            preserve (command excerpt, matched-pattern, target file,
            etc.). Stored under the 'extras' key.

    Returns ``True`` when every available target was written and ``False``
    when no target was available or persistence failed. Never raises.
    """
    try:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "gate": gate_name,
            "decision": decision,
            "reason": reason,
            "event_type": event_type,
        }
        session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if session_id:
            entry["session_id"] = session_id
        if extras:
            entry["extras"] = extras

        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))

        # Primary signal sink: the `.beads/` store when locatable, else the
        # user-level fallback so signal is never silently dropped in a
        # no-beads context (docs/reconciliation-rules.md Conflict 2).
        signal_target = _resolve_signal_path()
        if signal_target is None:
            signal_target = _resolve_fallback_path()

        targets: list[Path | None] = [signal_target]
        # The dedicated waiver corpus lives under `.beads/` only — when beads
        # is absent the waiver's full record is still preserved via the
        # primary fallback above; there is no separate fallback waiver file.
        if event_type == "waiver":
            targets.append(_resolve_waiver_path())

        available_targets = [target for target in targets if target is not None]
        if not available_targets:
            return False
        for target in available_targets:
            with open(target, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:
        return False  # signal capture must never block a gate decision
