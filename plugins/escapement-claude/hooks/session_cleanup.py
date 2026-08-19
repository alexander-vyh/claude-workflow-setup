#!/usr/bin/env python3
"""Claude Code hook: clean up stale temp files on session start.

Runs on the SessionStart event. Scans /tmp for state files left behind by
other Claude Code hooks (context burn detector, agent team tracker, review
gate) and deletes any that are older than 24 hours based on mtime.

Always exits 0 — cleanup failures must never block a session from starting.

Input (via stdin):
  JSON with hook_event_name (SessionStart)
Exit codes:
  0 — always (silent success or swallowed failure)
"""

import sys
import time
from pathlib import Path

# Maximum age in seconds (24 hours)
_MAX_AGE_SECONDS = 24 * 60 * 60

# Patterns to clean: (directory, prefix_or_glob)
_CLEANUP_TARGETS = [
    # context_burn_detector.py state files
    (Path("/tmp"), "context_burn_"),
    # agent team tracker files
    (Path("/tmp"), "agent-team-tracker-"),
]

# Separate directory-based cleanup
_CLEANUP_DIRS = [
    # review gate JSON files
    Path("/tmp/claude-review-gate"),
]


def _is_stale(path: Path, now: float) -> bool:
    """Return True if the file's mtime is older than _MAX_AGE_SECONDS."""
    try:
        return (now - path.stat().st_mtime) > _MAX_AGE_SECONDS
    except OSError:
        return False


def _cleanup_prefix(directory: Path, prefix: str, now: float) -> None:
    """Delete files in directory matching the given prefix if stale."""
    try:
        if not directory.is_dir():
            return
        for entry in directory.iterdir():
            if entry.name.startswith(prefix) and entry.is_file():
                if _is_stale(entry, now):
                    try:
                        entry.unlink()
                    except OSError:
                        pass
    except OSError:
        pass


def _cleanup_dir(directory: Path, now: float) -> None:
    """Delete stale files inside a cleanup directory."""
    try:
        if not directory.is_dir():
            return
        for entry in directory.iterdir():
            if entry.is_file() and _is_stale(entry, now):
                try:
                    entry.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def main() -> int:
    # Consume stdin (hook protocol requires it) but we don't need the payload
    try:
        sys.stdin.read()
    except Exception:
        pass

    now = time.time()

    for directory, prefix in _CLEANUP_TARGETS:
        _cleanup_prefix(directory, prefix, now)

    for directory in _CLEANUP_DIRS:
        _cleanup_dir(directory, now)

    return 0


if __name__ == "__main__":
    sys.exit(main())
