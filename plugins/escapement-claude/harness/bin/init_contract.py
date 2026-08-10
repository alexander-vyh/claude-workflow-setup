#!/usr/bin/env python3
"""
Helper for agents to scaffold a contract.json in the active thread directory.

Usage:
  init_contract.py --goal "..." --verify "shell command" [--expected-exit N] [--source agent-declared|bead-derived|user-authored]

Writes to the exact HARNESS_THREAD_DIR override, actor-owned session directory,
or legacy parent session directory resolved by thread_identity.py.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from would_block_stop import (  # noqa: E402
    InvalidActorIdentity,
    thread_dir_for_session,
    sanitize_session_id,
    harness_home,
)

# Commands that exit 0 regardless of system state — i.e. not oracles at all.
# An exact whole-segment match against this set (case-insensitive) is trivial.
_TRIVIAL_EXACT = frozenset({"", "true", ":", "exit", "exit 0", "pwd", "cd", "cd .", "cd ./"})
# Commands whose first word always exits 0 no matter the arguments.
_TRIVIAL_FIRST_WORD = frozenset({"echo", "printf"})
# Shell separators that compose multiple commands.
_SEGMENT_RE = re.compile(r"&&|\|\||;|\||\n")


def _oracle_segments(command: str) -> list[str]:
    return [seg.strip() for seg in _SEGMENT_RE.split(command) if seg.strip()]


def _segment_is_trivial(segment: str) -> bool:
    low = segment.strip().lower()
    if low in _TRIVIAL_EXACT:
        return True
    first = low.split()[0] if low.split() else ""
    return first in _TRIVIAL_FIRST_WORD


def is_trivial_oracle(command: str) -> "str | None":
    """Return a human-readable reason if `command` is a trivial (always-0) oracle,
    else None.

    Implements gate-design Rule 3 (validate the value, not its presence): an oracle
    like `true` / `:` / `echo done` / `exit 0` unlocks the Stop gate with no proof of
    outcome. It catches the *named null patterns* — it is NOT a general triviality
    prover (deciding whether an arbitrary command always exits 0 is undecidable). A
    command is rejected only when EVERY composed segment is a known no-op, so a real
    check in any segment (e.g. `true && pytest`) is allowed.
    """
    raw = (command or "").strip()
    if raw == "":
        return "empty oracle — --verify must run a command whose exit code proves the outcome"
    segments = _oracle_segments(raw)
    if not segments:
        return "no executable command found in --verify"
    if all(_segment_is_trivial(seg) for seg in segments):
        return (
            f"trivial oracle {command!r}: every segment is a no-op that exits 0 "
            "regardless of state, so it proves nothing. Provide a command whose exit "
            "code actually demonstrates the outcome — e.g. a test run (pytest ...), a "
            "state assertion (bd close <id>, gh pr view ... -q '.state==\"OPEN\"'), or "
            "a file/data check."
        )
    return None


def build_contract(
    goal: str,
    verify: str,
    *,
    source: str = "agent-declared",
    expected_exit: int = 0,
    session_id: "str | None" = None,
    thread_id: "str | None" = None,
) -> dict:
    """Construct a contract dict in the canonical shape (single source of truth).

    Both the hand-authoring path (`main`/init_contract) and the bead-derivation
    path (derive_contract.py) build their contract here, so a derived contract is
    byte-for-byte the same shape as a hand-authored one and the Stop gate / verify
    treat them identically. Does NOT screen the oracle or touch the filesystem —
    callers run `is_trivial_oracle` and write the file (so a rejected oracle leaves
    nothing behind).
    """
    return {
        "goal": goal,
        "verification_command": verify,
        "expected_exit": expected_exit,
        "source": source,
        "thread_id": thread_id or sanitize_session_id(session_id) or f"current-{uuid.uuid4().hex[:8]}",
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "last_run": None,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", required=True, help="One-sentence outcome.")
    parser.add_argument("--verify", required=True, help="Shell command whose exit code is the oracle.")
    parser.add_argument("--expected-exit", type=int, default=0)
    parser.add_argument("--source", choices=["agent-declared", "bead-derived", "user-authored"], default="agent-declared")
    parser.add_argument("--thread-id", default=None)
    args = parser.parse_args(argv)

    # Gate-design Rule 3: screen the oracle BEFORE any filesystem work, so a trivial
    # --verify leaves no contract behind. A no-op oracle would unlock the Stop gate
    # with zero proof of outcome.
    trivial_reason = is_trivial_oracle(args.verify)
    if trivial_reason is not None:
        print(f"refusing to write contract: {trivial_reason}", file=sys.stderr)
        return 2

    harness_root = harness_home()
    # Resolve the per-session thread dir the same way the Stop hook and verify do,
    # so a session's contract lands where its own Stop hook will look for it.
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    try:
        thread_dir = thread_dir_for_session(session_id, harness_root)
    except InvalidActorIdentity as exc:
        print(f"refusing to write contract: invalid actor identity: {exc}", file=sys.stderr)
        return 2
    thread_dir.mkdir(parents=True, exist_ok=True)

    contract = build_contract(
        args.goal,
        args.verify,
        source=args.source,
        expected_exit=args.expected_exit,
        session_id=session_id,
        thread_id=args.thread_id,
    )

    out = thread_dir / "contract.json"
    with out.open("w") as f:
        json.dump(contract, f, indent=2)

    print(f"contract written to {out}")
    print(json.dumps(contract, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
