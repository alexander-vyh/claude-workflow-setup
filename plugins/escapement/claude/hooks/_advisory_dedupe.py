#!/usr/bin/env python3
"""Has this session already been told this exact thing?

An advisory gate that re-reports an unchanged finding on every turn is not an
advisory -- it trains the reader to skip it, which costs the one time it matters.
Measured over the signal corpus before this shipped: of 11,399 fires across the
two oracle-downgrade gates, 9,843 (86%) were unchanged repeats, with runs up to
82 consecutive identical messages inside a single session.

These gates are stateless by construction: they re-scan the working tree on every
Stop or finishing command, and an uncommitted weakened assertion looks new every
time. This module gives them a one-line memory.

Fail-open everywhere: any error means report. A duplicated advisory is a nuisance;
a missed one is the failure the gate exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
from typing import Any

_SESSION_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _state_path(
    gate_name: str, session_id: str, suffix: str = "last"
) -> pathlib.Path | None:
    if not session_id or not _SESSION_RE.fullmatch(session_id):
        return None
    if not gate_name or not _SESSION_RE.fullmatch(gate_name):
        return None
    root = os.environ.get("HARNESS_ROOT") or os.path.join(
        os.path.expanduser("~"), ".claude", "harness"
    )
    return pathlib.Path(root) / "threads" / session_id / f"{gate_name}.{suffix}"


def already_reported(gate_name: str, session_id: str, finding: Any) -> bool:
    """True when ``finding`` is exactly what this session was last told by this gate.

    Records the finding as the new high-water mark when it differs, so the next
    identical call is suppressed and the next *different* one is not.
    """
    try:
        digest = hashlib.sha256(
            json.dumps(finding, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError):
        return False
    try:
        state = _state_path(str(gate_name), str(session_id))
        if state is None:
            return False
        if state.exists() and state.read_text(encoding="utf-8").strip() == digest:
            return True
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(digest, encoding="utf-8")
        return False
    except OSError:
        return False


def clear(gate_name: str, session_id: str) -> None:
    """Forget what this gate last told this session.

    Call this on the gate's *clean* path -- the turn where it found nothing. Without
    it the stored digest outlives the condition it describes, so ``warn -> fix ->
    reintroduce the identical weakening`` is silently suppressed the second time.
    Suppressing a repeat is the point; suppressing a genuine reoccurrence is the
    false negative this module must not create.
    """
    for suffix in ("last", "seen"):
        try:
            state = _state_path(str(gate_name), str(session_id), suffix)
            if state is not None:
                state.unlink(missing_ok=True)
        except OSError:
            continue


# A session that triggers this many distinct findings from one gate is pathological;
# the observed maximum across 155 sessions in the signal corpus was ~10. Past the cap
# we stop remembering and report, which is the fail-open direction.
_MAX_REMEMBERED = 256


def already_reported_any(gate_name: str, session_id: str, finding: Any) -> bool:
    """True when this gate has told this session about ``finding`` at any point.

    ``already_reported`` remembers only the most recent finding, which is right
    when a gate re-scans one changing condition. It is wrong when a gate fires
    per item and gives the same advice for every one: tdd_gate warned about impl
    file A, then B, then A again, and last-value memory let A through a second
    time because B had displaced it. Measured over the corpus, that alternation
    is the difference between suppressing 41% and 81% of its repeats.

    Use this where the advice does not depend on which item triggered it. Use
    ``already_reported`` where a changed finding is genuinely new information.
    ``clear`` resets both.
    """
    try:
        digest = hashlib.sha256(
            json.dumps(finding, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError):
        return False
    try:
        state = _state_path(str(gate_name), str(session_id), "seen")
        if state is None:
            return False
        seen: list[str] = []
        if state.exists():
            seen = state.read_text(encoding="utf-8").split()
            if digest in seen:
                return True
            if len(seen) >= _MAX_REMEMBERED:
                return False
        state.parent.mkdir(parents=True, exist_ok=True)
        with state.open("a", encoding="utf-8") as fh:
            fh.write(digest + "\n")
        return False
    except OSError:
        return False
