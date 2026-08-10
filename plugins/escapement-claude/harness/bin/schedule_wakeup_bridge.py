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
import pathlib
import sys
from typing import Optional

# Sibling import — works in the repo tree and when installed to ~/.claude/harness/bin.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from would_block_stop import (  # noqa: E402
    InvalidActorIdentity,
    thread_dir_for_session,
    sanitize_session_id,
    harness_home,
    _parse_iso,
)

CREATED_BY = "ScheduleWakeup"

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

    sched_path = thread_dir / "scheduled.json"
    existing = []
    if sched_path.exists():
        try:
            existing = json.loads(sched_path.read_text())
        except (OSError, json.JSONDecodeError):
            existing = []

    merged = merge_entries(existing, entry, now)

    thread_dir.mkdir(parents=True, exist_ok=True)
    sched_path.write_text(json.dumps(merged, indent=2))
    return sched_path


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
    try:
        entries = json.loads(sched_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    remaining = [
        e for e in entries
        if not (isinstance(e, dict) and e.get("created_by") == created_by)
    ]
    try:
        sched_path.write_text(json.dumps(remaining, indent=2))
    except OSError:
        pass
    return remaining


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
    except InvalidActorIdentity as exc:
        print(f"schedule wakeup denied: invalid actor identity: {exc}", file=sys.stderr)
        return 2
    except Exception:
        return 0  # never break the agent's tool flow
    return 0


if __name__ == "__main__":
    sys.exit(main())
