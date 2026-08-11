#!/usr/bin/env python3
"""Shared locking and durable persistence for per-thread wake schedules."""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import stat
import tempfile

from trusted_source import is_trusted_file


def try_lock(path: pathlib.Path, *, blocking: bool = False):
    """Open the stable schedule lock, returning None on nonblocking contention."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".json.lock")
    lock_fd = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ValueError("schedule lock is not a regular file")
        os.chmod(lock_path, 0o600)
        lock_file = os.fdopen(lock_fd, "a+")
        lock_fd = -1
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(lock_file, flags)
        except BlockingIOError:
            lock_file.close()
            return None
        if not is_trusted_file(lock_path):
            lock_file.close()
            raise ValueError("schedule lock is untrusted")
        return lock_file
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)


def load(path: pathlib.Path, *, absent: list | None = None) -> list | None:
    """Load a trusted list schedule; distinguish absence from invalid state."""
    path = pathlib.Path(path)
    if not os.path.lexists(path):
        return [] if absent is None else list(absent)
    if path.is_symlink() or not is_trusted_file(path):
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) else None


def write_durable(path: pathlib.Path, entries: list) -> None:
    """Replace a schedule durably while its caller holds the stable lock."""
    path = pathlib.Path(path)
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
            json.dump(entries, temporary, indent=2)
            temporary.write("\n")
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
