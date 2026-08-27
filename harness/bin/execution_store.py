#!/usr/bin/env python3
"""Trusted, process-safe persistence for delegated execution ledgers."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import pathlib
import stat
import tempfile
from collections.abc import Callable

from execution_validation import is_valid_ledger
from trusted_source import is_trusted_file


def inspect_host_event(
    incidents: list[object], event: dict
) -> tuple[tuple[str, str] | None, bool]:
    """Return a normalized observation and whether it is an identical replay."""
    host_event_id = event.get("host_event_id")
    if host_event_id is None:
        return None, False
    if not isinstance(host_event_id, str) or not host_event_id:
        raise ValueError("host event identity must be a non-empty string")
    try:
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("host event semantics are not serializable") from exc
    observation = host_event_id, hashlib.sha256(canonical.encode()).hexdigest()
    for incident in incidents:
        if not isinstance(incident, dict) or incident.get("type") != "host_event_observation":
            continue
        if incident.get("host_event_id") != host_event_id:
            continue
        if incident.get("event_fingerprint") != observation[1]:
            raise ValueError("host event replay has conflicting identity or semantics")
        return observation, True
    return observation, False


def record_host_event(
    incidents: list[dict], item: dict, observation: tuple[str, str] | None
) -> None:
    """Persist one accepted normalized host observation for replay comparison."""
    if observation is None:
        return
    host_event_id, fingerprint = observation
    incidents.append(
        {
            "type": "host_event_observation",
            "execution_id": item["execution_id"],
            "attempt": item["attempt"],
            "generation": item["generation"],
            "host_event_id": host_event_id,
            "event_fingerprint": fingerprint,
        }
    )


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


def mutate_atomic(
    path: pathlib.Path,
    mutation: Callable[[dict], dict],
    initializer: Callable[[], dict] | None = None,
) -> dict:
    """Serialize initialize-or-mutate under one stable path lock."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
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
            if not is_trusted_file(lock_path):
                raise ValueError("ledger lock is not a trusted source")
            if is_trusted_file(path):
                try:
                    current = json.loads(path.read_text())
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError("ledger JSON is malformed") from exc
                if not is_valid_ledger(current):
                    raise ValueError("ledger does not match the execution schema")
            elif os.path.lexists(path) or initializer is None:
                raise ValueError("ledger is not a trusted source")
            else:
                current = initializer()
                if not is_valid_ledger(current):
                    raise ValueError("initializer produced an invalid execution ledger")
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


def initialize_or_mutate_atomic(
    path: pathlib.Path,
    initializer: Callable[[], dict],
    mutation: Callable[[dict], dict],
) -> dict:
    """Initialize an absent ledger or mutate an existing one under one lock."""
    return mutate_atomic(path, mutation, initializer)
