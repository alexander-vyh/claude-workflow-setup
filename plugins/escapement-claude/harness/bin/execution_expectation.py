#!/usr/bin/env python3
"""Trusted dispatch expectations that distinguish normal ledger absence."""

from __future__ import annotations

import copy
import datetime as dt
import fcntl
import json
import os
import pathlib
import stat
import tempfile
from collections.abc import Callable

from trusted_source import is_trusted_file


UTC = dt.timezone.utc


def _iso(now: dt.datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("expectation timestamp must be timezone-aware")
    return now.astimezone(UTC).isoformat()


def _valid_expectation(value: object, expected_parent: str) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("version") != 1 or value.get("parent_session_id") != expected_parent:
        return False
    items = value.get("expectations")
    if not isinstance(items, list):
        return False
    required = ("tool_use_id", "task_id", "agent_name", "host", "expected_at")
    return all(
        isinstance(item, dict)
        and all(isinstance(item.get(key), str) and item[key] for key in required)
        for item in items
    )


def _valid_incident_container(value: object, expected_parent: str) -> bool:
    if not isinstance(value, dict):
        return False
    incidents = value.get("incidents")
    return (
        value.get("version") == 1
        and value.get("parent_session_id") == expected_parent
        and isinstance(incidents, list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("reason"), str)
            and bool(item["reason"])
            and (
                item.get("tool_use_id") is None
                or (
                    isinstance(item.get("tool_use_id"), str)
                    and bool(item["tool_use_id"])
                )
            )
            and isinstance(item.get("recorded_at"), str)
            and bool(item["recorded_at"])
            for item in incidents
        )
    )


def _valid_incident(value: object, expected_parent: str) -> bool:
    return (
        _valid_incident_container(value, expected_parent)
        and bool(value["incidents"])
    )


def _load(path: pathlib.Path, validator: Callable[[object], bool]) -> dict | None:
    path = pathlib.Path(path)
    if path.is_symlink() or not is_trusted_file(path):
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if validator(value) else None


def load_expectation(path: pathlib.Path, expected_parent: str) -> dict | None:
    return _load(path, lambda value: _valid_expectation(value, expected_parent))


def load_incident(path: pathlib.Path, expected_parent: str) -> dict | None:
    return _load(path, lambda value: _valid_incident(value, expected_parent))


def mutate_trusted_atomic(
    path: pathlib.Path,
    initializer: Callable[[], dict],
    mutation: Callable[[dict], dict],
    validator: Callable[[object], bool],
    initial_validator: Callable[[object], bool] | None = None,
) -> dict:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    lock_fd = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        lock_stat = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise ValueError("expectation lock is not a regular file")
        os.chmod(lock_path, 0o600)
        with os.fdopen(lock_fd, "r+") as lock_file:
            lock_fd = -1
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            if not is_trusted_file(lock_path):
                raise ValueError("expectation lock is untrusted")
            if is_trusted_file(path):
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError) as exc:
                    raise ValueError("expectation JSON is malformed") from exc
            elif os.path.lexists(path):
                raise ValueError("expectation path is untrusted")
            else:
                current = initializer()
            if not (initial_validator or validator)(current):
                raise ValueError("expectation state is invalid")
            updated = mutation(copy.deepcopy(current))
            if not validator(updated):
                raise ValueError("expectation mutation is invalid")

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


def record_expectation(
    path: pathlib.Path,
    *,
    parent_session_id: str,
    task_id: str,
    tool_use_id: str,
    agent_name: str,
    host: str,
    now: dt.datetime,
) -> dict:
    item = {
        "tool_use_id": tool_use_id,
        "task_id": task_id,
        "agent_name": agent_name,
        "host": host,
        "expected_at": _iso(now),
    }

    def initialize() -> dict:
        return {
            "version": 1,
            "parent_session_id": parent_session_id,
            "expectations": [],
        }

    def append(current: dict) -> dict:
        matches = [
            existing
            for existing in current["expectations"]
            if existing.get("tool_use_id") == tool_use_id
        ]
        if len(matches) > 1:
            raise ValueError("dispatch expectation identity is ambiguous")
        if matches and any(
            matches[0].get(key) != item[key]
            for key in ("task_id", "agent_name", "host")
        ):
            raise ValueError("dispatch expectation identity conflicts")
        if not matches:
            current["expectations"].append(item)
        return current

    return mutate_trusted_atomic(
        path,
        initialize,
        append,
        lambda value: _valid_expectation(value, parent_session_id),
    )


def record_incident(
    path: pathlib.Path,
    *,
    parent_session_id: str,
    tool_use_id: str | None,
    reason: str,
    now: dt.datetime,
) -> dict:
    item = {
        "tool_use_id": tool_use_id,
        "reason": reason,
        "recorded_at": _iso(now),
    }

    def initialize() -> dict:
        return {
            "version": 1,
            "parent_session_id": parent_session_id,
            "incidents": [],
        }

    def append(current: dict) -> dict:
        current["incidents"].append(item)
        return current

    return mutate_trusted_atomic(
        path,
        initialize,
        append,
        lambda current: _valid_incident(current, parent_session_id),
        lambda current: _valid_incident_container(current, parent_session_id),
    )


def ledger_covers_expectations(expectation: dict, ledger: dict) -> bool:
    observed = {
        (
            item.get("dispatch_tool_use_id"),
            item.get("bead_id"),
            item.get("agent_name"),
            item.get("host"),
        )
        for item in ledger.get("executions", [])
        if isinstance(item, dict)
    }
    expected = {
        (
            item.get("tool_use_id"),
            item.get("task_id"),
            item.get("agent_name"),
            item.get("host"),
        )
        for item in expectation.get("expectations", [])
        if isinstance(item, dict)
    }
    return bool(expected) and expected <= observed
