#!/usr/bin/env python3
"""Lock-protected, trust-checked, atomic mutation of a harness JSON file.

This is plumbing, not policy. It was extracted from execution_expectation.py
when the delegated-execution ledger was removed: the ledger's policy went away,
but ``task_session_mode`` still needs a way to write a small JSON record without
a torn write, a symlink swap, or a mutation that lands invalid.

Guarantees, in the order they are enforced:

* an exclusive flock on a sibling ``.<name>.lock``, so concurrent hooks in the
  same thread directory serialize;
* both the lock and the target must pass ``is_trusted_file`` -- correct owner and
  mode, no symlink -- so another user cannot substitute either;
* the caller's validator runs on the loaded state *and* on the mutation result,
  so an invalid write raises instead of persisting;
* the replacement is a same-directory temp file, fsynced, then ``os.replace``,
  with the directory fsynced after -- a reader sees the old file or the new one,
  never a partial one.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import pathlib
import stat
import tempfile
from collections.abc import Callable

from trusted_source import is_trusted_file


def mutate_trusted_atomic(
    path: pathlib.Path,
    initializer: Callable[[], dict],
    mutation: Callable[[dict], dict],
    validator: Callable[[object], bool],
    initial_validator: Callable[[object], bool] | None = None,
) -> dict:
    """Mutate the JSON at ``path`` atomically, returning the persisted value."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    lock_fd = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        lock_stat = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise ValueError("lock is not a regular file")
        os.chmod(lock_path, 0o600)
        with os.fdopen(lock_fd, "r+") as lock_file:
            lock_fd = -1
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            if not is_trusted_file(lock_path):
                raise ValueError("lock is untrusted")
            if is_trusted_file(path):
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError) as exc:
                    raise ValueError("JSON is malformed") from exc
            elif os.path.lexists(path):
                raise ValueError("path is untrusted")
            else:
                current = initializer()
            if not (initial_validator or validator)(current):
                raise ValueError("state is invalid")
            updated = mutation(copy.deepcopy(current))
            if not validator(updated):
                raise ValueError("mutation is invalid")

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
                    json.dump(updated, temporary, sort_keys=True, separators=(",", ":"))
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
            return updated
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)
