#!/usr/bin/env python3
"""Trusted, process-safe persistence for delegated execution ledgers."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import pathlib
import stat
import tempfile
from collections.abc import Callable

from execution_validation import is_valid_ledger
from trusted_source import is_trusted_file


def load_trusted(path: pathlib.Path, expected_parent: str) -> dict | None:
    """Load a trusted valid ledger for exactly one parent session."""
    path = pathlib.Path(path)
    if not is_trusted_file(path):
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not is_valid_ledger(value) or value["parent_session_id"] != expected_parent:
        return None
    return value


def mutate_atomic(path: pathlib.Path, mutation: Callable[[dict], dict]) -> dict:
    """Serialize and durably replace one trusted ledger under a stable lock."""
    path = pathlib.Path(path)
    lock_path = path.with_name(f".{path.name}.lock")
    lock_fd = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        lock_stat = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise ValueError("ledger lock is not a regular file")
        os.chmod(lock_path, 0o600)
        with os.fdopen(lock_fd, "r+") as lock_file:
            lock_fd = -1
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            if not is_trusted_file(path):
                raise ValueError("ledger is not a trusted source")
            try:
                current = json.loads(path.read_text())
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("ledger JSON is malformed") from exc
            if not is_valid_ledger(current):
                raise ValueError("ledger does not match the execution schema")
            updated = mutation(copy.deepcopy(current))
            if not is_valid_ledger(updated):
                raise ValueError("mutation produced an invalid execution ledger")

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
