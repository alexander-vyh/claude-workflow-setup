#!/usr/bin/env python3
"""SessionStart hook — write a per-session scope watermark (bead 858.8).

For contract-less sessions (no contract.created_at), the session-scope gate has
no derived watermark and degrades to advisory-allow, leaving the session
unscoped. This hook stamps {thread_dir}/scope_watermark.json at SessionStart so
`would_block_stop.resolve_watermark` can scope the implicit Stop-path to
session-fresh bd work even without a contract.

FIRST-WRITE-WINS: a resumed/compacted session keeps its ORIGINAL start. A later
watermark would mis-classify earlier session-fresh work as backlog (premature
stop), so we never overwrite an existing file.

The watermark is a system-stamped timestamp (derive-not-assert / gate-design
Rule 3) — never agent free-text.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from would_block_stop import InvalidActorIdentity, thread_dir_for_session, harness_home  # noqa: E402
from thread_identity import state_identity  # noqa: E402
import session_isolation  # noqa: E402  (per-session isolation steer, bead e9v.4)

HARNESS_ROOT = harness_home()


def write_session_watermark(thread_dir: pathlib.Path, session_id: str, ts: str) -> None:
    """Write {thread_dir}/scope_watermark.json first-write-wins. Best-effort."""
    try:
        thread_dir.mkdir(parents=True, exist_ok=True)
        target = thread_dir / "scope_watermark.json"
        if target.exists():
            return  # first-write-wins: preserve the original session start
        target.write_text(json.dumps({"watermark": ts, "session_id": session_id}))
    except OSError:
        pass  # never fail SessionStart on a logging-grade write


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    # Key off the SAME session id the Stop hook + init_contract resolve, so the
    # watermark lands in the thread dir the Stop hook reads. SessionStart payloads
    # may omit session_id (the existing SessionStart hooks read only cwd), so fall
    # back to CLAUDE_CODE_SESSION_ID — what init_contract keys on (E-3 robustness,
    # flagged by the 858 panel's adversary + implementer). thread_dir_for_session
    # also honors HARNESS_THREAD_DIR (rung 1) and actor-scopes subagents.
    session_id = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    try:
        thread_dir = thread_dir_for_session(session_id, HARNESS_ROOT)
        checkout_id = state_identity(session_id)
    except InvalidActorIdentity as exc:
        print(f"session watermark denied: invalid actor identity: {exc}", file=sys.stderr)
        return 2
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_session_watermark(thread_dir, session_id, ts)

    # bead e9v.4: stamp this session's checkout identity and, if a live concurrent
    # session already shares this non-isolated checkout, surface the worktree steer
    # PROACTIVELY at session start (before the work entangles). Best-effort: a
    # failure here must never disrupt SessionStart.
    try:
        cwd = payload.get("cwd") or os.getcwd()
        now = _dt.datetime.now(_dt.timezone.utc)
        session_isolation.write_checkout(thread_dir, checkout_id, cwd, now)
        steer = session_isolation.isolation_steer_for_thread(
            HARNESS_ROOT, checkout_id, thread_dir, now
        )
        if steer:
            print("continuation-harness [isolation]:" + steer)
    except Exception:  # noqa: BLE001 — never let isolation bookkeeping disrupt SessionStart
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
