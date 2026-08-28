#!/usr/bin/env python3
"""Canonical `.beads/.gate-signal.jsonl` writer for harness-local gates.

`harness/bin` is host-neutral state code and cannot import
`claude/hooks/_gate_signal`, so this module owns the mirrored line shape for
every harness gate. The half-life toolchain (`claude/bin/gate_signal_*`) and
the running launchd monitor read ONLY `.gate-signal.jsonl`: a decision or a
waiver reason logged anywhere else is invisible to half-life review.

Writes are best-effort. A gate must never fail because its signal did not land.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

GATE = "continuation-harness"


def beads_dir() -> pathlib.Path | None:
    """Resolve the `.beads` directory from BEADS_DIR, else by walking up."""
    env = os.environ.get("BEADS_DIR")
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env)
    cwd = pathlib.Path(os.getcwd()).resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".beads").is_dir():
            return parent / ".beads"
    return None


def record(
    decision: str,
    reason: str,
    session_id: str,
    notes: str = "",
    **extras: object,
) -> None:
    """Append one canonical signal line for a harness gate decision."""
    try:
        beads = beads_dir()
        if beads is None:
            return
        payload: dict[str, object] = {
            key: value for key, value in extras.items() if value is not None
        }
        if notes:
            payload["notes"] = notes
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "gate": GATE,
            "decision": decision,
            "reason": reason,
            "session_id": session_id,
            "extras": payload,
        }
        with (beads / ".gate-signal.jsonl").open("a") as handle:
            handle.write(json.dumps(line) + "\n")
    except OSError:
        pass
