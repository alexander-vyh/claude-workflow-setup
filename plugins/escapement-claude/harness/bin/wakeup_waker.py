#!/usr/bin/env python3
"""Continuation-harness waker — the firing layer that was missing.

Periodically (via launchd; the daemon shell) reads each session's `scheduled.json`,
fires DUE entries through `wakeup_dispatch.dispatch()`, and enacts the result:

  - reschedule  -> re-arm the entry (wake_at += poll_interval). NO Claude spawned —
                   this is the cheap GCP/PR poll that may repeat for hours at ~zero cost.
  - handoff     -> spawn a FRESH, cheap-model session (`claude -p ... --model ...`) and
                   PRUNE the entry. NO `--resume` of the big context, ever.
  - resume      -> same-session `claude --resume` (small-context case) and PRUNE.
  - noop        -> drop the (malformed/done) entry.

PRUNE-AFTER-FIRE is the fix for the observed 25× resume / 45× block storms: a one-shot
wake never survives in the schedule to re-fire; only a not-ready poll is re-armed.

`plan()` is PURE and unit-tested. `main()` is the thin imperative shell (read/write
files, spawn subprocesses); it DEFAULTS TO DRY-RUN so loading it can't surprise anyone —
spawning requires --fire, and even then this module only emits/loads what the daemon runs.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import os
import pathlib
import sys
import tempfile
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import wakeup_dispatch as wd  # noqa: E402
import execution_supervisor as es  # noqa: E402
import trusted_source as ts  # noqa: E402

HARNESS_ROOT = pathlib.Path(
    os.environ.get(
        "CONTINUATION_HARNESS_HOME", pathlib.Path.home() / ".claude" / "harness"
    )
)


def plan(
    entries: List[dict],
    now: _dt.datetime,
    run_cmd: Optional[Callable[[str], Tuple[int, str]]] = None,
) -> Tuple[List[dict], List[dict]]:
    """Pure planner. Returns (kept_entries, spawns).

    kept_entries: the new scheduled.json contents (not-due untouched; not-ready polls
    re-armed; fired one-shots PRUNED; malformed dropped).
    spawns: descriptors the waker shell should enact ({"type": "handoff"|"resume", ...}).
    """
    n = now if now.tzinfo else now.replace(tzinfo=_dt.timezone.utc)
    kept: List[dict] = []
    spawns: List[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue  # malformed → drop
        wa = wd._parse_iso(e.get("wake_at", ""))
        if wa is None:
            continue  # bad/absent wake_at → drop (fail-safe, never fire)
        if wa.tzinfo is None:
            wa = wa.replace(tzinfo=_dt.timezone.utc)
        if wa > n:
            kept.append(e)  # not due → untouched
            continue

        action = wd.dispatch(e, run_cmd=run_cmd, now=n)
        kind = action.get("action")
        if kind == "reschedule":
            interval = int(action.get("poll_interval", wd.DEFAULT_POLL_INTERVAL))
            rearmed = dict(e)
            rearmed["wake_at"] = (n + _dt.timedelta(seconds=interval)).isoformat()
            kept.append(rearmed)  # re-armed; NO spawn
        elif kind == "handoff":
            spawns.append(
                {
                    "type": "handoff",
                    "thread_id": e.get("thread_id"),
                    "model": action.get("model"),
                    "prompt": action.get("prompt"),
                    "reason": action.get("reason"),
                    "_entry": e,
                }
            )  # PRUNED (not kept)
        elif kind == "resume":
            spawns.append(
                {
                    "type": "resume",
                    "thread_id": e.get("thread_id"),
                    "prompt": action.get("prompt"),
                    "_entry": e,
                }
            )  # PRUNED
        # noop → dropped
    return kept, spawns


# --------------------------------------------------------------------------
# Thin imperative shell (the daemon). DRY-RUN by default.
# --------------------------------------------------------------------------


def _spawn(spawn: dict) -> list:
    """Build the claude argv for a spawn. handoff = FRESH session (no --resume)."""
    if spawn["type"] == "handoff":
        argv = ["claude", "-p", spawn["prompt"]]
        if spawn.get("model"):
            argv += ["--model", spawn["model"]]
        return argv
    # resume = same session, small-context case only
    return [
        "claude",
        "--resume",
        spawn.get("thread_id") or "",
        "-p",
        spawn.get("prompt", ""),
    ]


def _load(path: pathlib.Path):
    try:
        with path.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _try_lock(path: pathlib.Path):
    lock_file = path.with_suffix(".json.lock").open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def _public_spawn(spawn: dict) -> dict:
    return {k: v for k, v in spawn.items() if k != "_entry"}


def _dry_run_runner(command: str) -> Tuple[int, str]:
    """Represent a check poll without executing its shell command."""
    return 1, "dry-run: command not executed"


def _write_schedule_durable(path: pathlib.Path, entries: list) -> None:
    """Replace a schedule durably while the caller holds its stable lock."""
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            json.dump(entries, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_name is not None:
            pathlib.Path(temporary_name).unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Continuation-harness waker (poll/handoff)."
    )
    ap.add_argument(
        "--fire",
        action="store_true",
        help="actually spawn handoffs/resumes and rewrite schedules (default: dry-run)",
    )
    ap.add_argument("--threads-root", default=str(HARNESS_ROOT / "threads"))
    args = ap.parse_args(argv)

    now = _dt.datetime.now(_dt.timezone.utc)
    root = pathlib.Path(args.threads_root)
    total_spawns = []
    exit_code = 0
    scheduled_ok = True
    for sched in root.glob("*/scheduled.json"):
        # Trust boundary: a check entry's `command` is shell-executed by the
        # launchd-detached waker. Refuse any schedule another local user could
        # have rewritten (wrong owner, or group/world-writable file or dir).
        if not ts.is_trusted_file(sched):
            scheduled_ok = False
            print(
                f"skipped untrusted schedule (unsafe ownership/permissions): {sched}",
                file=sys.stderr,
            )
            continue
        lock_file = None
        if args.fire:
            lock_file = _try_lock(sched)
            if lock_file is None:
                scheduled_ok = False
                print(f"skipped locked schedule: {sched}", file=sys.stderr)
                continue
        try:
            entries = _load(sched)
            if not isinstance(entries, list):
                scheduled_ok = False
                continue
            kept, spawns = plan(
                entries, now, run_cmd=None if args.fire else _dry_run_runner
            )
            if args.fire:
                repo_cwd = None
                if spawns:
                    repo_cwd = es.session_repo_cwd(sched.parent, sched.parent.name)
                    if repo_cwd is None:
                        scheduled_ok = False
                        exit_code = 1
                        kept.extend(spawn["_entry"] for spawn in spawns)
                        spawns = []
                        print(
                            f"scheduled spawn lacks trusted repository context: {sched}",
                            file=sys.stderr,
                        )
                spawned = []
                for s in spawns:
                    try:
                        es.launch_in_repo(_spawn(s), repo_cwd)
                    except OSError as exc:
                        scheduled_ok = False
                        exit_code = 1
                        kept.append(s["_entry"])
                        print(f"spawn failed for {sched}: {exc}", file=sys.stderr)
                    else:
                        spawned.append(_public_spawn(s))
                _write_schedule_durable(sched, kept)
                total_spawns += spawned
            else:
                total_spawns += [_public_spawn(s) for s in spawns]
        finally:
            if lock_file is not None:
                lock_file.close()
    if args.fire:
        if not scheduled_ok:
            exit_code = 1
        reconcile_now = _dt.datetime.now(_dt.timezone.utc)

        def inspect_scheduled():
            if not scheduled_ok:
                raise RuntimeError("scheduled-work inspection was incomplete")
            return {"status": "ok"}

        try:
            result = es.reconcile_all(
                root,
                reconcile_now,
                f"wakeup-waker:{os.getpid()}",
                es.launch_recovery,
                inspect_scheduled=inspect_scheduled,
                completion_clock=lambda: _dt.datetime.now(_dt.timezone.utc),
            )
            if result["status"] != "ok":
                exit_code = 1
        except (OSError, RuntimeError, ValueError) as exc:
            exit_code = 1
            print(f"execution reconciliation incomplete: {exc}", file=sys.stderr)
    for s in total_spawns:
        print(json.dumps({"would_spawn" if not args.fire else "spawned": s}))
    print(
        f"{'FIRED' if args.fire else 'DRY-RUN'}: {len(total_spawns)} spawn(s) planned"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
