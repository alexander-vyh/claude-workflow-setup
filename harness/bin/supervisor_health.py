#!/usr/bin/env python3
"""Trusted durable health for the delegated-work supervisor."""

from __future__ import annotations

import copy
import datetime as dt
import fcntl
import json
import os
import pathlib
import stat
import tempfile
import uuid
from collections.abc import Callable

from trusted_source import is_trusted_file

UTC = dt.timezone.utc
_HEALTH_KEYS = {
    "reconcile_started_at",
    "last_successful_reconcile_started_at",
    "last_successful_reconcile_at",
    "completed_generation",
    "installation_id",
    "counts",
}
_COUNT_KEYS = {"successful_passes", "threads", "recoveries"}


def _iso(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_valid(record: object) -> bool:
    """Return whether a health snapshot has the complete trusted wire shape."""
    if not isinstance(record, dict) or set(record) != _HEALTH_KEYS:
        return False
    if (
        record["reconcile_started_at"] is not None
        and _parse(record["reconcile_started_at"]) is None
    ):
        return False
    if (
        record["last_successful_reconcile_started_at"] is not None
        and _parse(record["last_successful_reconcile_started_at"]) is None
    ):
        return False
    if (
        record["last_successful_reconcile_at"] is not None
        and _parse(record["last_successful_reconcile_at"]) is None
    ):
        return False
    successful_start = _parse(record["last_successful_reconcile_started_at"])
    successful_completion = _parse(record["last_successful_reconcile_at"])
    if (successful_start is None) != (successful_completion is None):
        return False
    if (
        successful_start is not None
        and successful_completion is not None
        and successful_start > successful_completion
    ):
        return False
    if not _nonnegative_integer(record["completed_generation"]):
        return False
    if not isinstance(record["installation_id"], str) or not record["installation_id"]:
        return False
    counts = record["counts"]
    return (
        isinstance(counts, dict)
        and set(counts) == _COUNT_KEYS
        and all(_nonnegative_integer(counts[key]) for key in _COUNT_KEYS)
    )


def _normalize_storage(record: object) -> dict | None:
    """Normalize prior health snapshots to the complete consumer wire shape."""
    if not isinstance(record, dict):
        return None
    legacy_keys = _HEALTH_KEYS - {"last_successful_reconcile_started_at"}
    record_keys = set(record)
    if record_keys != _HEALTH_KEYS and record_keys != legacy_keys:
        return None
    counts = record.get("counts")
    if not isinstance(counts, dict) or not set(counts) <= _COUNT_KEYS:
        return None
    if not all(_nonnegative_integer(value) for value in counts.values()):
        return None
    normalized = copy.deepcopy(record)
    if record_keys == legacy_keys:
        # Old records did not carry the successful pass start. Migrate only
        # when their diagnostic pass start is chronologically usable as that
        # proof; otherwise ambiguity remains fail-closed.
        successful_completion = _parse(record["last_successful_reconcile_at"])
        legacy_start = _parse(record["reconcile_started_at"])
        if successful_completion is None:
            normalized["last_successful_reconcile_started_at"] = None
        elif legacy_start is not None and legacy_start <= successful_completion:
            normalized["last_successful_reconcile_started_at"] = record[
                "reconcile_started_at"
            ]
        else:
            return None
    normalized["counts"] = {key: counts.get(key, 0) for key in _COUNT_KEYS}
    return normalized if is_valid(normalized) else None


def load_trusted(path: pathlib.Path) -> dict | None:
    """Load one valid owner-controlled health snapshot."""
    path = pathlib.Path(path)
    if path.is_symlink() or not is_trusted_file(path):
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return record if is_valid(record) else None


def is_fresh_successful(
    record: dict | None,
    now: dt.datetime,
    max_age_seconds: int,
) -> bool:
    """Whether a completed useful pass is recent enough to authorize a pause."""
    if not is_valid(record) or max_age_seconds <= 0:
        return False
    completed = _parse(record["last_successful_reconcile_at"])
    started = _parse(record["last_successful_reconcile_started_at"])
    reference_now = now.astimezone(UTC)
    if (
        completed is None
        or started is None
        or completed > reference_now
        or started > reference_now
    ):
        return False
    age = (reference_now - completed).total_seconds()
    return (
        0 <= age <= max_age_seconds
        and record["completed_generation"] > 0
        and record["counts"]["successful_passes"] > 0
    )


def _new() -> dict:
    installation_id = os.environ.get("ESCAPEMENT_INSTALLATION_ID") or uuid.uuid4().hex
    return {
        "reconcile_started_at": None,
        "last_successful_reconcile_started_at": None,
        "last_successful_reconcile_at": None,
        "completed_generation": 0,
        "installation_id": installation_id,
        "counts": {"successful_passes": 0, "threads": 0, "recoveries": 0},
    }


def mutate(path: pathlib.Path, mutation: Callable[[dict], dict]) -> dict:
    """Durably mutate supervisor health under its stable path lock."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    lock_fd = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ValueError("health lock is not a regular file")
        os.chmod(lock_path, 0o600)
        with os.fdopen(lock_fd, "r+") as lock_file:
            lock_fd = -1
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            if not is_trusted_file(lock_path):
                raise ValueError("health lock is untrusted")
            if path.is_symlink():
                raise ValueError("supervisor health is untrusted")
            if is_trusted_file(path):
                try:
                    current = json.loads(path.read_text())
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError("supervisor health is malformed") from exc
                current = _normalize_storage(current)
                if current is None:
                    raise ValueError("supervisor health is invalid")
            elif os.path.lexists(path):
                raise ValueError("supervisor health is untrusted")
            else:
                current = _new()
            updated = mutation(copy.deepcopy(current))
            if not is_valid(updated):
                raise ValueError("health mutation produced invalid state")

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


def mark_started(path: pathlib.Path, now: dt.datetime) -> dict:
    def update(current: dict) -> dict:
        current["reconcile_started_at"] = _iso(now)
        return current

    return mutate(path, update)


def mark_success(
    path: pathlib.Path,
    now: dt.datetime,
    *,
    threads: int,
    recoveries: int,
    started_at: dt.datetime | None = None,
) -> dict:
    def update(current: dict) -> dict:
        previous = current["counts"]
        successful_start = started_at
        if successful_start is None:
            successful_start = _parse(current["reconcile_started_at"])
        if successful_start is None:
            raise ValueError("successful supervisor pass has no start timestamp")
        current["last_successful_reconcile_started_at"] = _iso(successful_start)
        current["last_successful_reconcile_at"] = _iso(now)
        current["completed_generation"] += 1
        current["counts"] = {
            "successful_passes": previous["successful_passes"] + 1,
            "threads": threads,
            "recoveries": recoveries,
        }
        return current

    return mutate(path, update)
