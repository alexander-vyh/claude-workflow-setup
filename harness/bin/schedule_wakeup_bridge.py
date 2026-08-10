#!/usr/bin/env python3
"""PostToolUse:ScheduleWakeup bridge for the continuation-harness.

Bead: escapement-0wg.

The problem
-----------
continuation-harness.md documents three Stop-permission paths; path 2 is
"register a wakeup via the ScheduleWakeup tool". But the Stop gate
(would_block_stop) reads ``{thread_dir}/scheduled.json``, while the ScheduleWakeup
*tool* persists inside the Claude Code runtime and never writes that file. So
path 2 was dead: a legitimately-blocked wait-turn still got
``no_completion_or_resumption_proof`` every time. Only verify-pass (path 1) or
user-release (path 3) actually released the gate.

The fix
-------
This module is a PostToolUse hook matched on the ScheduleWakeup tool. After a
ScheduleWakeup call succeeds, it translates the call into a schema-conforming
entry (harness/schemas/scheduled.schema.json) in *that session's* thread dir —
the exact file the gate already reads. ``wake_at = now + clamp(delaySeconds,
60, 3600)`` mirrors the tool's own clamping so the registered time matches the
real wakeup.

Self-pruning (part 2 — stale fallbacks)
---------------------------------------
Two mechanisms keep a stale wakeup from "releasing" the gate forever or replaying
a finished task list:

* On every write the bridge drops past-dated entries and deduplicates its own
  ``created_by == "ScheduleWakeup"`` entries (latest wins) so repeated calls
  don't accumulate.
* ``prune_thread()`` cancels a thread's pending ScheduleWakeup wakeups; the
  ``verify`` script calls it on a passing run, so once tracked work completes the
  wakeup is cancelled and cannot replay (on the harness side). Entries registered
  by other processes (supervisor / adapter fallbacks) are preserved.

  `wakeup_waker.py` reads this — now correctly pruned — scheduled.json as its
  source of truth. Its default mode is dry-run; unattended firing requires the
  daemon/launchd shell to invoke it with `--fire`.

Fail-open: any malformed payload / IO error exits 0 without writing, so the
bridge never blocks or corrupts the agent's tool flow.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import sys
from typing import Optional

# Sibling import — works in the repo tree and when installed to ~/.claude/harness/bin.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from would_block_stop import (  # noqa: E402
    thread_dir_for_session,
    sanitize_session_id,
    harness_home,
    _parse_iso,
)
import execution_store  # noqa: E402
import schedule_store  # noqa: E402
import supervisor_health  # noqa: E402

CREATED_BY = "ScheduleWakeup"
MANAGED_CREATED_BY = "execution-supervisor"

# Mirror the ScheduleWakeup tool's documented clamp so the registered wake_at
# matches when the runtime will actually fire.
_DELAY_FLOOR = 60
_DELAY_CEIL = 3600


def _clamp_delay(value) -> Optional[int]:
    """Return delaySeconds clamped to [60, 3600], or None if not a usable number."""
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if not isinstance(value, (int, float)):
        return None
    return max(_DELAY_FLOOR, min(_DELAY_CEIL, int(value)))


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def merge_entries(existing: list, new_entry: dict, now: _dt.datetime) -> list:
    """Combine existing scheduled entries with a new one.

    - drops past-dated entries (already fired / stale) regardless of creator;
    - removes prior ScheduleWakeup entries so the latest call supersedes them
      (no unbounded accumulation);
    - preserves future entries from other creators (supervisor/adapter fallbacks).
    """
    kept: list = []
    for e in existing if isinstance(existing, list) else []:
        if not isinstance(e, dict):
            continue
        wa = _parse_iso(e.get("wake_at", ""))
        if wa is None or wa <= now:
            continue  # prune stale / unparseable
        if e.get("created_by") == CREATED_BY:
            continue  # superseded by new_entry
        kept.append(e)
    kept.append(new_entry)
    return kept


def _write_entries(
    sched_path: pathlib.Path,
    build,
) -> list | None:
    lock_file = schedule_store.try_lock(sched_path, blocking=False)
    if lock_file is None:
        return None
    try:
        existing = schedule_store.load(sched_path)
        if existing is None:
            return None
        updated = build(existing)
        schedule_store.write_durable(sched_path, updated)
        return updated
    finally:
        lock_file.close()


def _managed_entry(
    execution: dict,
    trigger_at: _dt.datetime,
    registered_at: _dt.datetime,
    prompt: str,
    health: dict,
) -> dict | None:
    required_text = ("parent_session_id", "watchdog_id", "execution_id")
    if not all(
        isinstance(execution.get(key), str) and execution[key] for key in required_text
    ):
        return None
    if not all(
        isinstance(execution.get(key), int)
        and not isinstance(execution[key], bool)
        and execution[key] >= 1
        for key in ("attempt", "generation")
    ):
        return None
    return {
        "wake_at": trigger_at.astimezone(_dt.timezone.utc).isoformat(),
        "registered_at": registered_at.astimezone(_dt.timezone.utc).isoformat(),
        "prompt": prompt,
        "thread_id": execution["parent_session_id"],
        "created_by": MANAGED_CREATED_BY,
        "crash_count": 0,
        "supervisor_installation_id": health["installation_id"],
        "supervisor_generation": health["completed_generation"],
        **{key: execution[key] for key in required_text},
        "attempt": execution["attempt"],
        "generation": execution["generation"],
    }


def _persist_managed_wakeups(
    executions: list[dict],
    thread_dir: pathlib.Path,
    trigger_at: _dt.datetime,
    *,
    prompt: str,
) -> list[dict] | None:
    health = supervisor_health.load_trusted(
        pathlib.Path(thread_dir).parent.parent / "supervisor-health.json"
    )
    if health is None or health["completed_generation"] < 1:
        return None
    if not executions:
        return None
    sched_path = pathlib.Path(thread_dir) / "scheduled.json"
    managed: list[dict] = []

    def build(existing: list) -> list:
        # This timestamp is sampled only after the stable schedule lock is held.
        # A supervisor pass that began earlier cannot qualify; one that begins
        # later cannot inspect the schedule until this durable replace finishes.
        registered_at = _now()
        entries = [
            _managed_entry(execution, trigger_at, registered_at, prompt, health)
            for execution in executions
        ]
        if any(entry is None for entry in entries):
            raise ValueError("managed execution identity is invalid")
        managed.extend(entry for entry in entries if entry is not None)
        kept = []
        for entry in existing:
            if not isinstance(entry, dict):
                continue
            wake_at = _parse_iso(entry.get("wake_at", ""))
            if wake_at is None or wake_at <= registered_at:
                continue
            if entry.get("created_by") in {CREATED_BY, MANAGED_CREATED_BY}:
                continue
            kept.append(entry)
        return [*kept, *managed]

    return managed if _write_entries(sched_path, build) is not None else None


def persist_managed_wakeup(
    execution: dict,
    thread_dir: pathlib.Path,
    trigger_at: _dt.datetime,
) -> dict | None:
    """Persist one exact execution proof against the current health generation."""
    entries = _persist_managed_wakeups(
        [execution],
        pathlib.Path(thread_dir),
        trigger_at,
        prompt="reconcile delegated execution",
    )
    return entries[0] if entries else None


def parse_and_register(
    payload: dict,
    *,
    now: Optional[_dt.datetime] = None,
    harness_root: Optional[pathlib.Path] = None,
) -> Optional[pathlib.Path]:
    """Translate a ScheduleWakeup PostToolUse payload into a scheduled.json entry.

    Returns the path written, or None if this payload is not a usable
    ScheduleWakeup call (the no-op / fail-open case).
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "ScheduleWakeup":
        return None

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None

    delay = _clamp_delay(tool_input.get("delaySeconds"))
    if delay is None:
        # Without a real delay we cannot compute a wake_at — do NOT fabricate one
        # (that would forge a resumption proof the agent never declared).
        return None

    now = now or _now()
    if harness_root is None:
        harness_root = harness_home()

    session_id = payload.get("session_id")
    thread_dir = thread_dir_for_session(session_id, harness_root)
    thread_id = sanitize_session_id(session_id) or "current"

    prompt = tool_input.get("prompt") or tool_input.get("reason") or "scheduled wakeup"

    entry = {
        "wake_at": (now + _dt.timedelta(seconds=delay)).isoformat(),
        "prompt": str(prompt),
        "thread_id": thread_id,
        "created_by": CREATED_BY,
        "crash_count": 0,
    }

    ledger_path = thread_dir / "executions.json"
    if os.path.lexists(ledger_path):
        if ledger_path.is_symlink():
            return None
        ledger = execution_store.load_trusted(ledger_path, expected_parent=thread_id)
        if ledger is None:
            return None
        active = [
            {
                "parent_session_id": ledger["parent_session_id"],
                **{
                    key: execution[key]
                    for key in ("watchdog_id", "execution_id", "attempt", "generation")
                },
            }
            for execution in ledger["executions"]
            if execution["state"] not in {"terminal", "cancelled"}
        ]
        if not active:
            return None
        if (
            _persist_managed_wakeups(
                active,
                thread_dir,
                now + _dt.timedelta(seconds=delay),
                prompt=str(prompt),
            )
            is not None
        ):
            return thread_dir / "scheduled.json"
        return None

    sched_path = thread_dir / "scheduled.json"
    merged = _write_entries(
        sched_path,
        lambda existing: merge_entries(existing, entry, now),
    )
    return sched_path if merged is not None else None


def prune_thread(
    thread_dir: pathlib.Path,
    *,
    created_by: str = CREATED_BY,
) -> list:
    """Cancel a thread's pending wakeups registered by ``created_by``.

    Called when tracked work completes (verify passes) so a finished task list
    cannot be replayed by its own ScheduleWakeup wakeup. Entries from other
    creators are preserved. Returns the remaining entries (also written back).
    Fail-open: missing/unreadable file -> nothing to do.
    """
    sched_path = pathlib.Path(thread_dir) / "scheduled.json"
    if not sched_path.exists():
        return []
    creators = {created_by}
    if created_by == CREATED_BY:
        creators.add(MANAGED_CREATED_BY)

    def build(entries: list) -> list:
        return [
            entry
            for entry in entries
            if not (
                isinstance(entry, dict) and entry.get("created_by") in creators
            )
        ]

    try:
        return _write_entries(sched_path, build) or []
    except (OSError, ValueError):
        return []


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # CLI: --prune <thread_dir>  (used by verify on a passing run)
    if argv and argv[0] == "--prune":
        if len(argv) >= 2:
            prune_thread(pathlib.Path(argv[1]))
        return 0

    # Hook mode: read the PostToolUse payload from stdin.
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open
    try:
        parse_and_register(payload)
    except Exception:
        return 0  # never break the agent's tool flow
    return 0


if __name__ == "__main__":
    sys.exit(main())
