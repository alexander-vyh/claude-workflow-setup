#!/usr/bin/env python3
"""Trust guard for flat files whose contents the harness will shell-execute.

The continuation harness auto-executes command strings sourced from local config
files (`scheduled.json` polled by the launchd waker; `contract.json` run by
`verify`). Those commands are intentionally shell-shaped (pipes, `&&`, jq `-q`
filters) — converting them to an argv vector would break the feature, so the
real defence is not argv but *provenance*: refuse to trust a file another local
user could have rewritten before we hand its contents to a shell.

This is the StrictModes pattern (cf. OpenSSH refusing a group-writable
`~/.ssh/config`): a file is trusted only when it AND its directory are owned by
us (or root) and are not writable by group or other. The injection vector these
sites have is "poisoned flat file," not "interpolated argument," so a perms/owner
check is the matched control. (It is deliberately NOT used for bead-sourced
commands, where the risk is malicious *content* in a correctly-permissioned file
— a perms check would pass and give false assurance there.)

POSIX-only semantics; on platforms without `os.geteuid` the checks no-op (return
trusted) rather than block, since the mode bits are not meaningful there.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Union

_PathLike = Union[str, "os.PathLike[str]", Path]

#: group- or other-writable bits — the tamper surface we reject.
_WRITABLE_BY_OTHERS = stat.S_IWGRP | stat.S_IWOTH


class UntrustedSource(Exception):
    """Raised when a command-source file fails the trust check."""


def _has_geteuid() -> bool:
    return hasattr(os, "geteuid")


def _owner_ok(st: os.stat_result) -> bool:
    euid = os.geteuid()  # caller-guarded by _has_geteuid()
    return st.st_uid in (euid, 0)


def _mode_ok(st: os.stat_result) -> bool:
    return not (st.st_mode & _WRITABLE_BY_OTHERS)


def _dir_ok(d: Path) -> bool:
    try:
        st = os.stat(d)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    if not _owner_ok(st):
        return False
    # A world-writable directory lets an attacker replace the file wholesale,
    # UNLESS the sticky bit is set (the /tmp model: only the owner may unlink).
    if st.st_mode & _WRITABLE_BY_OTHERS and not (st.st_mode & stat.S_ISVTX):
        return False
    return True


def is_trusted_file(path: _PathLike) -> bool:
    """Return True iff `path` is safe to read as a command source.

    Safe = an existing regular file, owned by us (or root), not writable by
    group/other, whose containing directory is likewise owned and not loosely
    writable. No-ops to True on non-POSIX platforms.
    """
    p = Path(path)
    if not _has_geteuid():
        return p.is_file()
    try:
        st = os.stat(p)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    if not _owner_ok(st) or not _mode_ok(st):
        return False
    return _dir_ok(p.parent)


def assert_trusted_file(path: _PathLike) -> None:
    """Raise UntrustedSource (with an actionable message) if `path` is untrusted."""
    if not is_trusted_file(path):
        raise UntrustedSource(
            f"refusing to execute commands from untrusted source: {path} — "
            f"the file or its directory is missing, not owned by you, or "
            f"writable by group/other. Fix with: "
            f"chmod go-w {path} && chmod go-w $(dirname {path})"
        )
