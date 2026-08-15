#!/usr/bin/env python3
"""Route a fired wakeup: cheap shell POLL vs fresh-session HANDOFF vs same-session resume.

The waker (launchd firing layer) calls `dispatch()` on each due `scheduled.json` entry
and enacts the returned action. The HARD INVARIANT (from the GCP-wait analysis): a wait
must NEVER reload a large conversation — not while polling, and not on the eventual wake.

Wakeup intents
--------------
* kind="check"  — "wake me WHEN condition X is true" (poll-until-ready; GCP job / PR merge /
  bead state). Each poll runs `command` as a tiny shell job — NO Claude:
    - exit 0  (condition MET)     -> action "handoff"  (wake a FRESH session, cheap model)
    - exit !=0 (NOT ready yet)    -> action "reschedule" (poll again later; NO Claude)
    - runner error                -> action "reschedule" (transient; self-heals or hits deadline)
    - past `deadline`             -> action "handoff" with a "did not complete" note (escalate
                                     once rather than poll forever)
* kind="resume" (or missing, back-compat) — same-session `claude --resume` (full context).
  ONLY for immediate continuation (e.g. the wind-down rung) where the in-flight context is
  small and still relevant. NEVER for long waits.

Actions the waker enacts
------------------------
* "handoff"     -> `claude -p "<self-contained prompt>" --model <model>` in a FRESH session.
                   NO `--resume`, NO history reload. Prompt must stand alone (PR#, bead IDs,
                   expected terminal state, one short handoff note). Defaults to a CHEAP model.
* "reschedule"  -> rewrite this entry's wake_at to now + poll_interval. NO Claude spawned.
* "resume"      -> `claude --resume <id> -p "<prompt>"` (same session; small-context case only).
* "noop"        -> nothing (malformed / no command / unknown kind).

This module is PURE (runner + clock injected); it decides, it does not spawn `claude` or
rewrite files — those belong to the waker so the dangerous part stays isolated.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from typing import Callable, Optional, Tuple

DEFAULT_POLL_INTERVAL = 300          # seconds between cheap polls
MIN_POLL_INTERVAL = 60               # avoid tight poll loops
MAX_POLL_INTERVAL = 86400            # one day; longer waits should use a fresh wakeup
DEFAULT_HANDOFF_MODEL = "haiku"      # #5: housekeeping/polling waers run cheap, not Opus 1M


def _default_runner(
    command: str, *, cwd: Optional[str | os.PathLike[str]] = None
) -> Tuple[int, str]:
    """Run a shell command; return (exit_code, combined_output). Waker-only; tests inject."""
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=cwd,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _parse_iso(s) -> Optional[_dt.datetime]:
    if not isinstance(s, str) or not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _past_deadline(entry: dict, now: Optional[_dt.datetime]) -> bool:
    dl = _parse_iso(entry.get("deadline", ""))
    if dl is None:
        return False
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    if dl.tzinfo is None:
        dl = dl.replace(tzinfo=_dt.timezone.utc)
    return now >= dl


def _poll_interval(entry: dict) -> int:
    try:
        interval = int(entry.get("poll_interval", DEFAULT_POLL_INTERVAL))
    except (TypeError, ValueError):
        return DEFAULT_POLL_INTERVAL
    if interval < MIN_POLL_INTERVAL or interval > MAX_POLL_INTERVAL:
        return DEFAULT_POLL_INTERVAL
    return interval


def _handoff(entry: dict, *, reason: str, note: str = "") -> dict:
    prompt = entry.get("escalate_prompt") or entry.get("prompt") or "Awaited condition met; re-engage."
    if note:
        prompt = note + "\n\n" + prompt
    return {
        "action": "handoff",
        "prompt": prompt,
        "model": entry.get("model") or DEFAULT_HANDOFF_MODEL,
        "reason": reason,
    }


def dispatch(
    entry: dict,
    *,
    run_cmd: Optional[Callable[[str], Tuple[int, str]]] = None,
    now: Optional[_dt.datetime] = None,
) -> dict:
    """Decide what a due wakeup should do. Pure; fail-safe.

    A check NEVER yields a same-session "resume" (that would reload the big context).
    Malformed entries / missing commands → "noop" (never a blind resume, never a spawn).
    """
    if not isinstance(entry, dict):
        return {"action": "noop", "reason": "malformed_entry"}

    kind = entry.get("kind") or "resume"

    if kind == "resume":
        return {"action": "resume", "prompt": entry.get("prompt", "")}

    if kind == "check":
        command = entry.get("command")
        if not command or not isinstance(command, str):
            return {"action": "noop", "reason": "check_no_command"}
        runner = run_cmd or _default_runner
        try:
            code, _out = runner(command)
            ran = True
        except Exception:
            code, ran = None, False

        if ran and code == 0:
            # condition met → wake a FRESH session (cheap), never --resume.
            return _handoff(entry, reason="condition_met")

        # not ready (clean non-zero) or a transient runner error → keep polling cheaply,
        # UNLESS past the deadline, in which case escalate ONCE so we don't poll forever.
        if _past_deadline(entry, now):
            return _handoff(
                entry, reason="poll_deadline",
                note="NOTE: the awaited condition did not complete before its deadline — "
                     "investigate rather than assume success.",
            )
        return {
            "action": "reschedule",
            "reason": "not_ready" if ran else "poll_runner_error",
            "poll_interval": _poll_interval(entry),
        }

    return {"action": "noop", "reason": "unknown_kind"}
