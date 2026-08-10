#!/usr/bin/env python3
"""
continuation-harness Stop gate.

Pure function that decides whether a Claude Code (or other agent CLI) session
may stop, based on observable filesystem state. No prose pattern matching.
No LLM judgment. State-only.

Decision rules:
  ("allow", "verification_passed")  -- contract.last_run.exit_code == expected_exit AND timestamp within current turn
  ("allow", "wakeup_registered")    -- scheduled.json has at least one future-dated entry
  ("allow", "user_released")        -- recent user message matches explicit-stop set
  ("block", "no_completion_or_resumption_proof") -- none of the above; contract EXISTS (committed task, unverified)
  ("allow", "conversational")       -- no contract = no committed task in flight = free to stop (teeth: a declared contract, ready bd work, and validate_no_shirking still block)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import sys
from typing import Optional, Tuple

from thread_identity import (  # noqa: E402
    InvalidActorIdentity as InvalidActorIdentity,
    resolve_thread_dir,
    sanitize_session_id as sanitize_session_id,
)

try:
    from verify_integrity import is_suppressed_verification
except ImportError:  # pragma: no cover — fail-open: never crash the Stop gate
    def is_suppressed_verification(_command):
        return None

# State root: where contracts / wakeups / incidents live. Standard per-user
# location, independent of where the harness CODE is installed or invoked from,
# so concurrent sessions in any repo share one state dir and NOTHING is ever
# written into a project working tree. Override with CONTINUATION_HARNESS_HOME
# (or legacy HARNESS_ROOT). NO author-specific / repo-specific path is baked in.
DEFAULT_HARNESS_ROOT = pathlib.Path(
    os.environ.get(
        "CONTINUATION_HARNESS_HOME",
        pathlib.Path.home() / ".claude" / "harness",
    )
)


def harness_home() -> pathlib.Path:
    """The state root (env-overridable). Single source of truth for all tools."""
    return pathlib.Path(os.environ.get("HARNESS_ROOT", DEFAULT_HARNESS_ROOT))

EXPLICIT_STOP_SET = frozenset({
    "stop",
    "stop here",
    "end here",
    "that's enough",
    "thats enough",
    "done for now",
    "we're done",
    "were done",
    "okay stop",
    "ok stop",
    "halt",
})

# "Current turn" window. last_run older than this is treated as stale so an
# old passing run can't be reused indefinitely. 5 minutes is the default —
# long enough for legitimate verification runs, short enough that a passing
# run from yesterday doesn't unlock today's Stop.
CURRENT_TURN_WINDOW_SECONDS = 300


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_iso(s: str) -> Optional[_dt.datetime]:
    if not isinstance(s, str):
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return _dt.datetime.fromisoformat(s)
    except (ValueError, AttributeError):
        return None


def _load_json(path: pathlib.Path):
    if not path.exists():
        return None
    try:
        with path.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def resolve_watermark(
    thread_dir: pathlib.Path,
    contract: Optional[dict] = None,
) -> Optional[_dt.datetime]:
    """Session-start watermark for scope filtering (bead 858.1), or None.

    A DERIVED signal (gate-design Rule 3 / derive-not-assert) — never agent-asserted.
    Priority:
      1. contract.json#created_at  (system-stamped at init_contract; the lean common case)
      2. {thread_dir}/scope_watermark.json#watermark  (SessionStart fallback, contract-less)
      3. None  -> caller MUST degrade to advisory-allow, never a hard block on unscoped
         backlog, and never substitute now() (a now-watermark filters out every real
         session-fresh bead and re-creates the premature-stop bug).

    A malformed/absent timestamp at one source falls through to the next.
    """
    if contract is None:
        contract = _load_json(thread_dir / "contract.json")
    if isinstance(contract, dict):
        ts = _parse_iso(contract.get("created_at", ""))
        if ts is not None:
            return ts
    watermark = _load_json(thread_dir / "scope_watermark.json")
    if isinstance(watermark, dict):
        ts = _parse_iso(watermark.get("watermark", ""))
        if ts is not None:
            return ts
    return None


def _verification_passed_this_turn(contract: Optional[dict]) -> bool:
    if not isinstance(contract, dict):
        return False
    last = contract.get("last_run")
    if not isinstance(last, dict):
        return False
    if last.get("exit_code") != contract.get("expected_exit", 0):
        return False
    if is_suppressed_verification(contract.get("verification_command", "")):
        # A green reached by gutting the check (|| true, bare true, --no-verify,
        # SKIP=, ...) is not a real pass (move 1b, escapement-e9v.2).
        return False
    ts = _parse_iso(last.get("timestamp", ""))
    if ts is None:
        return False
    age = (_now() - ts).total_seconds()
    return 0 <= age <= CURRENT_TURN_WINDOW_SECONDS


def _suppressed_green(contract: Optional[dict]) -> Optional[str]:
    """The contract WOULD pass (fresh, exit==expected) but its verify command is
    self-neutering. Returns the suppression reason so the block can explain that
    the verify COMMAND — not a missing run — is the problem (move 1b)."""
    if not isinstance(contract, dict):
        return None
    last = contract.get("last_run")
    if not isinstance(last, dict):
        return None
    if last.get("exit_code") != contract.get("expected_exit", 0):
        return None
    ts = _parse_iso(last.get("timestamp", ""))
    if ts is None:
        return None
    if not (0 <= (_now() - ts).total_seconds() <= CURRENT_TURN_WINDOW_SECONDS):
        return None
    return is_suppressed_verification(contract.get("verification_command", ""))


def _wakeup_registered(scheduled: Optional[list]) -> bool:
    if not isinstance(scheduled, list):
        return False
    now = _now()
    for entry in scheduled:
        if not isinstance(entry, dict):
            continue
        wake_at = _parse_iso(entry.get("wake_at", ""))
        if wake_at is not None and wake_at > now:
            return True
    return False


def _user_released(recent_user_message: Optional[str]) -> bool:
    if not isinstance(recent_user_message, str):
        return False
    normalized = recent_user_message.strip().lower().rstrip(".!?")
    return normalized in EXPLICIT_STOP_SET


def would_block_stop(thread_state: dict) -> Tuple[str, str]:
    """
    Decide whether a Stop event for this thread should be blocked.

    thread_state keys:
      contract:               dict | None    (parsed contract.json)
      scheduled:              list | None    (parsed scheduled.json — an array)
      recent_user_message:    str  | None    (most recent user message text)

    Returns (decision, reason) where decision is "allow" or "block".
    """
    contract = thread_state.get("contract")
    scheduled = thread_state.get("scheduled")
    recent_user_message = thread_state.get("recent_user_message")

    if _verification_passed_this_turn(contract):
        return ("allow", "verification_passed")
    if _wakeup_registered(scheduled):
        return ("allow", "wakeup_registered")
    if _user_released(recent_user_message):
        return ("allow", "user_released")
    if _suppressed_green(contract):
        # Fresh exit-0, but the verify command was gutted (|| true, bare true,
        # --no-verify, ...). Distinct reason so the block explains the COMMAND is
        # the problem — not a missing run — instead of looping the agent on a
        # generic "unverified" message (move 1b, escapement-e9v.2).
        return ("block", "verification_suppressed")
    if contract is None:
        # No contract = no committed task in flight = conversational. Stopping is
        # free (no magic word needed). This deliberately relaxes the old "no
        # contract → block" rule, which nagged every conversational turn. Teeth
        # remain: a DECLARED-but-unverified contract still blocks below, ready bd
        # work still blocks in task mode, and (move 1b) a suppressed-green
        # contract blocks above with a distinct reason.
        return ("allow", "conversational")
    # Contract PRESENT but not verified: either a declared dict that didn't pass,
    # OR a malformed/unreadable contract.json surfaced as a non-dict marker by
    # load_thread_state. Both fail SAFE → block (a corrupt contract must NOT read
    # as "no contract → allow"; that would let a work session sneak out).
    return ("block", "no_completion_or_resumption_proof")


def thread_dir_for_session(
    session_id: Optional[str],
    harness_root: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """Resolve the per-session thread directory.

    Canonical function of the explicit override, session id, harness root, and
    the optional CLAUDE_AGENT_ID environment identity. Resolution priority:

      1. HARNESS_THREAD_DIR env override (explicit; for tests / special cases)
      2. actor + session        -> threads/{session_id}/agents/{actor-key}
      3. parent session         -> threads/{session_id}
      4. no session             -> threads/current

    A present invalid actor identity raises InvalidActorIdentity instead of
    falling back to the parent's state.
    """
    if harness_root is None:
        harness_root = pathlib.Path(
            os.environ.get("HARNESS_ROOT", DEFAULT_HARNESS_ROOT)
        )
    return resolve_thread_dir(session_id, harness_root)


def load_thread_state(
    thread_dir: pathlib.Path,
    recent_user_message: Optional[str] = None,
) -> dict:
    """Load thread state from filesystem. Convenience for Stop-hook adapter.

    A contract.json that EXISTS but is unparseable is surfaced as a non-dict
    marker (not None) so the gate fails SAFE (blocks) on a corrupt contract,
    rather than treating it as 'no contract' and allowing a conversational stop.
    """
    contract_path = thread_dir / "contract.json"
    contract = _load_json(contract_path)
    if contract is None and contract_path.exists():
        contract = "__unreadable_contract__"  # present-but-corrupt → fail safe (block)
    return {
        "contract": contract,
        "scheduled": _load_json(thread_dir / "scheduled.json"),
        "recent_user_message": recent_user_message,
    }


def _cli_main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: would_block_stop.py <thread_dir> [recent_user_message]",
            file=sys.stderr,
        )
        return 2
    thread_dir = pathlib.Path(argv[1])
    recent = argv[2] if len(argv) > 2 else None
    state = load_thread_state(thread_dir, recent)
    decision, reason = would_block_stop(state)
    print(json.dumps({"decision": decision, "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main(sys.argv))
