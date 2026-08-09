#!/usr/bin/env python3
"""Durable state machine for native delegated-execution attempts.

This ledger is deliberately not a task graph.  Beads remains authoritative for
work state and verified closure; this module owns only native attempt identity,
activity/deadline evidence, and fenced recovery/result-application claims.
"""

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
from typing import Any

from trusted_source import is_trusted_file

UTC = dt.timezone.utc
START_SECONDS = 2 * 60
IDLE_SECONDS = 15 * 60
HARD_SECONDS = 2 * 60 * 60

EXECUTION_STATES = {"queued", "running", "terminal", "cancelled", "unknown"}
RECONCILE_STATES = {None, "start", "idle", "hard"}
APPLICATION_STATES = {"unapplied", "applying", "applied"}
EVENT_KINDS = {
    "child_bound",
    "child_started",
    "activity_completed",
    "child_terminal",
    "child_cancelled",
    "snapshot_reconciled",
    "tool_started",
    "status_polled",
    "semantic_annotation",
}
ACTIVITY_KINDS = {
    "tool_completed",
    "assistant_nonempty",
    "checkpoint",
    "terminal_event",
}

_TOP_LEVEL_KEYS = {
    "version",
    "parent_session_id",
    "updated_at",
    "executions",
    "incidents",
}
_EXECUTION_KEYS = {
    "bead_id",
    "execution_id",
    "host",
    "agent_name",
    "native_child_id",
    "dispatch_tool_use_id",
    "attempt",
    "generation",
    "state",
    "queued_at",
    "started_at",
    "last_activity_at",
    "last_activity_kind",
    "start_deadline",
    "idle_deadline",
    "hard_deadline",
    "reconcile_due",
    "terminal_at",
    "terminal_reason",
    "terminal_event_id",
    "result_digest",
    "watchdog_id",
    "recovery_count",
    "recovery_claim",
    "result_application",
}
_APPLICATION_KEYS = {
    "state",
    "claim",
    "claim_generation",
    "idempotency_key",
    "applied_at",
}


def _iso(now: dt.datetime) -> str:
    if (
        not isinstance(now, dt.datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError("datetime must be timezone-aware")
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _after(now: dt.datetime, seconds: int) -> str:
    return _iso(now + dt.timedelta(seconds=seconds))


def _required_text(mapping: dict, name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _positive_int(mapping: dict, name: str) -> int:
    value = mapping.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _application_key(execution_id: str, attempt: int, generation: int) -> str:
    return f"execution:{execution_id}:attempt:{attempt}:generation:{generation}"


def _new_application(execution_id: str, attempt: int, generation: int) -> dict:
    return {
        "state": "unapplied",
        "claim": None,
        "claim_generation": 0,
        "idempotency_key": _application_key(execution_id, attempt, generation),
        "applied_at": None,
    }


def new_ledger(parent_session_id: str) -> dict:
    """Return a new empty per-parent execution ledger."""
    if not isinstance(parent_session_id, str) or not parent_session_id:
        raise ValueError("parent_session_id must be a non-empty string")
    return {
        "version": 1,
        "parent_session_id": parent_session_id,
        "updated_at": None,
        "executions": [],
        "incidents": [],
    }


def register_execution(ledger: dict, event: dict, now: dt.datetime) -> dict:
    """Register a generation-one native attempt in queued state."""
    timestamp = _iso(now)
    if event.get("kind") != "dispatch_registered":
        raise ValueError("event kind must be dispatch_registered")
    if event.get("parent_session_id") != ledger.get("parent_session_id"):
        raise ValueError("parent session does not match ledger")
    execution_id = _required_text(event, "execution_id")
    attempt = _positive_int(event, "attempt")
    generation = _positive_int(event, "generation")
    if generation != 1:
        raise ValueError("initial generation must be 1")
    if any(
        item.get("execution_id") == execution_id
        for item in ledger.get("executions", [])
    ):
        raise ValueError("execution_id is already registered")

    item = {
        "bead_id": _required_text(event, "bead_id"),
        "execution_id": execution_id,
        "host": _required_text(event, "host"),
        "agent_name": _required_text(event, "agent_name"),
        "native_child_id": None,
        "dispatch_tool_use_id": _required_text(event, "dispatch_tool_use_id"),
        "attempt": attempt,
        "generation": generation,
        "state": "queued",
        "queued_at": timestamp,
        "started_at": None,
        "last_activity_at": None,
        "last_activity_kind": None,
        "start_deadline": _after(now, START_SECONDS),
        "idle_deadline": _after(now, IDLE_SECONDS),
        "hard_deadline": _after(now, HARD_SECONDS),
        "reconcile_due": None,
        "terminal_at": None,
        "terminal_reason": None,
        "terminal_event_id": None,
        "result_digest": None,
        "watchdog_id": _required_text(event, "watchdog_id"),
        "recovery_count": 0,
        "recovery_claim": None,
        "result_application": _new_application(execution_id, attempt, generation),
    }
    ledger.setdefault("executions", []).append(item)
    ledger.setdefault("incidents", [])
    ledger["updated_at"] = timestamp
    return ledger


def _find(ledger: dict, execution_id: str) -> dict:
    matches = [
        item
        for item in ledger.get("executions", [])
        if item.get("execution_id") == execution_id
    ]
    if len(matches) != 1:
        raise ValueError("execution identity is unresolved")
    return matches[0]


def _validate_event_identity(ledger: dict, event: dict, item: dict) -> tuple[int, int]:
    if event.get("parent_session_id") != ledger.get("parent_session_id"):
        raise ValueError("parent session does not match ledger")
    attempt = _positive_int(event, "attempt")
    generation = _positive_int(event, "generation")
    if attempt != item["attempt"]:
        raise ValueError("attempt does not match active execution")
    return attempt, generation


def _record_old_generation(
    ledger: dict, item: dict, event: dict, now: dt.datetime
) -> None:
    event_id = event.get("terminal_event_id")
    incident = {
        "type": "old_generation_event",
        "execution_id": item["execution_id"],
        "event_kind": event["kind"],
        "event_id": event_id,
        "event_attempt": event["attempt"],
        "event_generation": event["generation"],
        "active_attempt": item["attempt"],
        "active_generation": item["generation"],
        "recorded_at": _iso(now),
    }
    existing = ledger.setdefault("incidents", [])
    if not any(
        evidence.get("type") == incident["type"]
        and evidence.get("execution_id") == incident["execution_id"]
        and evidence.get("event_id") == incident["event_id"]
        and evidence.get("event_generation") == incident["event_generation"]
        for evidence in existing
    ):
        existing.append(incident)
        ledger["updated_at"] = incident["recorded_at"]


def apply_event(ledger: dict, event: dict, now: dt.datetime) -> dict:
    """Apply one normalized host event, rejecting ambiguous identity."""
    timestamp = _iso(now)
    kind = event.get("kind")
    if kind not in EVENT_KINDS:
        raise ValueError("unknown event kind")
    item = _find(ledger, _required_text(event, "execution_id"))
    _attempt, generation = _validate_event_identity(ledger, event, item)
    if generation != item["generation"]:
        if generation < item["generation"] and kind in {
            "child_terminal",
            "child_cancelled",
        }:
            _record_old_generation(ledger, item, event, now)
            return ledger
        raise ValueError("generation does not match active execution")

    event_child = event.get("native_child_id")
    if event_child is not None and (
        not isinstance(event_child, str) or not event_child
    ):
        raise ValueError("native_child_id must be a non-empty string")
    if (
        item["native_child_id"]
        and event_child
        and event_child != item["native_child_id"]
    ):
        raise ValueError("native child identity does not match active execution")

    if kind == "child_bound":
        child_id = _required_text(event, "native_child_id")
        if item["native_child_id"] not in (None, child_id):
            raise ValueError("native child identity is already bound")
        item["native_child_id"] = child_id
    elif kind == "child_started":
        if item["native_child_id"] is None or event_child != item["native_child_id"]:
            raise ValueError("child_started requires the bound native child identity")
        if item["state"] not in {"queued", "running"}:
            raise ValueError("terminal execution cannot start")
        item["state"] = "running"
        item["started_at"] = item["started_at"] or timestamp
        item["last_activity_at"] = timestamp
        item["last_activity_kind"] = "child_started"
        item["idle_deadline"] = _after(now, IDLE_SECONDS)
    elif kind == "activity_completed":
        if item["state"] != "running":
            raise ValueError("completed activity requires a running execution")
        activity_kind = event.get("activity_kind")
        if activity_kind not in ACTIVITY_KINDS:
            raise ValueError("activity_kind is not accepted deterministic activity")
        item["last_activity_at"] = timestamp
        item["last_activity_kind"] = activity_kind
        item["idle_deadline"] = _after(now, IDLE_SECONDS)
    elif kind == "child_terminal":
        terminal_event_id = _required_text(event, "terminal_event_id")
        if (
            item["state"] == "terminal"
            and item["terminal_event_id"] == terminal_event_id
        ):
            return ledger
        if item["state"] in {"terminal", "cancelled"}:
            raise ValueError("execution already has different terminal evidence")
        item["state"] = "terminal"
        item["terminal_at"] = timestamp
        item["terminal_reason"] = _required_text(event, "terminal_reason")
        item["terminal_event_id"] = terminal_event_id
        item["result_digest"] = _required_text(event, "result_digest")
        item["last_activity_at"] = timestamp
        item["last_activity_kind"] = "terminal_event"
        item["idle_deadline"] = _after(now, IDLE_SECONDS)
    elif kind == "child_cancelled":
        terminal_event_id = _required_text(event, "terminal_event_id")
        if (
            item["state"] == "cancelled"
            and item["terminal_event_id"] == terminal_event_id
        ):
            return ledger
        if item["state"] in {"terminal", "cancelled"}:
            raise ValueError("execution already has different terminal evidence")
        item["state"] = "cancelled"
        item["terminal_at"] = timestamp
        item["terminal_reason"] = _required_text(event, "terminal_reason")
        item["terminal_event_id"] = terminal_event_id
        item["last_activity_at"] = timestamp
        item["last_activity_kind"] = "terminal_event"
    # snapshot_reconciled and the three diagnostic kinds are recognized but
    # intentionally cannot renew activity or deadlines.
    ledger["updated_at"] = timestamp
    return ledger


def reconcile_deadlines(ledger: dict, now: dt.datetime) -> list[dict]:
    """Set sticky reconciliation reasons without asserting native termination."""
    timestamp = _iso(now)
    current = now.astimezone(UTC)
    due: list[dict] = []
    changed = False
    for item in ledger.get("executions", []):
        if item.get("reconcile_due") in {"start", "idle", "hard"}:
            due.append(item)
            continue
        if item.get("state") in {"terminal", "cancelled"}:
            continue
        reason = None
        if current >= _parse(item["hard_deadline"]):
            reason = "hard"
        elif item.get("state") == "queued" and current >= _parse(
            item["start_deadline"]
        ):
            reason = "start"
        elif item.get("state") in {"running", "unknown"} and current >= _parse(
            item["idle_deadline"]
        ):
            reason = "idle"
        if reason:
            item["reconcile_due"] = reason
            due.append(item)
            changed = True
    if changed:
        ledger["updated_at"] = timestamp
    return due


def _claim_expired(claim: dict | None, now: dt.datetime) -> bool:
    return claim is not None and now.astimezone(UTC) >= _parse(claim["expires_at"])


def claim_recovery(
    ledger: dict,
    execution_id: str,
    now: dt.datetime,
    owner: str,
    ttl_seconds: int,
) -> dict | None:
    """Claim due recovery; expired takeover advances and fences generation."""
    timestamp = _iso(now)
    if (
        not isinstance(owner, str)
        or not owner
        or not isinstance(ttl_seconds, int)
        or ttl_seconds < 1
    ):
        raise ValueError("owner and positive ttl_seconds are required")
    item = _find(ledger, execution_id)
    if item["reconcile_due"] is None or item["state"] in {"terminal", "cancelled"}:
        return None
    existing = item["recovery_claim"]
    if existing is not None and not _claim_expired(existing, now):
        return None
    if existing is not None:
        item["generation"] += 1
        item["recovery_count"] += 1
        item["state"] = "queued"
        item["native_child_id"] = None
        item["queued_at"] = timestamp
        item["started_at"] = None
        item["last_activity_at"] = None
        item["last_activity_kind"] = None
        item["start_deadline"] = _after(now, START_SECONDS)
        item["idle_deadline"] = _after(now, IDLE_SECONDS)
        item["terminal_at"] = None
        item["terminal_reason"] = None
        item["terminal_event_id"] = None
        item["result_digest"] = None
        item["result_application"] = _new_application(
            execution_id, item["attempt"], item["generation"]
        )
    claim = {
        "owner": owner,
        "execution_id": execution_id,
        "attempt": item["attempt"],
        "generation": item["generation"],
        "claimed_at": timestamp,
        "expires_at": _after(now, ttl_seconds),
    }
    item["recovery_claim"] = claim
    ledger["updated_at"] = timestamp
    return copy.deepcopy(claim)


def claim_result_application(
    ledger: dict,
    execution_id: str,
    now: dt.datetime,
    owner: str,
    ttl_seconds: int,
    *,
    attempt: int,
    generation: int,
) -> dict | None:
    """Claim independently verified result application for an exact generation."""
    timestamp = _iso(now)
    if (
        not isinstance(owner, str)
        or not owner
        or not isinstance(ttl_seconds, int)
        or ttl_seconds < 1
    ):
        raise ValueError("owner and positive ttl_seconds are required")
    item = _find(ledger, execution_id)
    if attempt != item["attempt"]:
        raise ValueError("attempt does not match active execution")
    if generation != item["generation"]:
        raise ValueError("generation does not match active execution")
    if item["state"] != "terminal" or not item["result_digest"]:
        return None
    application = item["result_application"]
    if application["state"] == "applied":
        return None
    existing = application["claim"]
    if existing is not None and not _claim_expired(existing, now):
        return None
    application["claim_generation"] += 1
    claim = {
        "owner": owner,
        "execution_id": execution_id,
        "attempt": attempt,
        "generation": generation,
        "claim_generation": application["claim_generation"],
        "claimed_at": timestamp,
        "expires_at": _after(now, ttl_seconds),
    }
    application["state"] = "applying"
    application["claim"] = claim
    ledger["updated_at"] = timestamp
    return copy.deepcopy(claim)


def _valid_timestamp(value: Any, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    try:
        _parse(value)
    except (TypeError, ValueError):
        return False
    return True


def _valid_claim(claim: Any, required: set[str]) -> bool:
    if claim is None:
        return True
    if not isinstance(claim, dict) or set(claim) != required:
        return False
    if not all(
        isinstance(claim.get(key), str) and claim[key]
        for key in ("owner", "execution_id")
    ):
        return False
    if not all(
        isinstance(claim.get(key), int) and claim[key] >= 1
        for key in required & {"attempt", "generation", "claim_generation"}
    ):
        return False
    return _valid_timestamp(claim.get("claimed_at")) and _valid_timestamp(
        claim.get("expires_at")
    )


def _valid_execution(item: Any) -> bool:
    if not isinstance(item, dict) or set(item) != _EXECUTION_KEYS:
        return False
    required_text = (
        "bead_id",
        "execution_id",
        "host",
        "agent_name",
        "dispatch_tool_use_id",
        "watchdog_id",
    )
    if not all(isinstance(item.get(key), str) and item[key] for key in required_text):
        return False
    if item["native_child_id"] is not None and not isinstance(
        item["native_child_id"], str
    ):
        return False
    if not all(
        isinstance(item.get(key), int) and item[key] >= 1
        for key in ("attempt", "generation")
    ):
        return False
    if not isinstance(item.get("recovery_count"), int) or item["recovery_count"] < 0:
        return False
    if (
        item.get("state") not in EXECUTION_STATES
        or item.get("reconcile_due") not in RECONCILE_STATES
    ):
        return False
    for key in ("queued_at", "start_deadline", "idle_deadline", "hard_deadline"):
        if not _valid_timestamp(item.get(key)):
            return False
    for key in ("started_at", "last_activity_at", "terminal_at"):
        if not _valid_timestamp(item.get(key), nullable=True):
            return False
    for key in (
        "last_activity_kind",
        "terminal_reason",
        "terminal_event_id",
        "result_digest",
    ):
        if item[key] is not None and not isinstance(item[key], str):
            return False
    recovery_keys = {
        "owner",
        "execution_id",
        "attempt",
        "generation",
        "claimed_at",
        "expires_at",
    }
    if not _valid_claim(item.get("recovery_claim"), recovery_keys):
        return False
    recovery_claim = item["recovery_claim"]
    if recovery_claim is not None and (
        recovery_claim["execution_id"] != item["execution_id"]
        or recovery_claim["attempt"] != item["attempt"]
        or recovery_claim["generation"] != item["generation"]
    ):
        return False
    application = item.get("result_application")
    if not isinstance(application, dict) or set(application) != _APPLICATION_KEYS:
        return False
    if application.get("state") not in APPLICATION_STATES:
        return False
    if (
        not isinstance(application.get("claim_generation"), int)
        or application["claim_generation"] < 0
    ):
        return False
    if (
        not isinstance(application.get("idempotency_key"), str)
        or not application["idempotency_key"]
    ):
        return False
    if application["idempotency_key"] != _application_key(
        item["execution_id"], item["attempt"], item["generation"]
    ):
        return False
    if not _valid_timestamp(application.get("applied_at"), nullable=True):
        return False
    application_claim_keys = recovery_keys | {"claim_generation"}
    application_claim = application.get("claim")
    if not _valid_claim(application_claim, application_claim_keys):
        return False
    if application_claim is not None and (
        application_claim["execution_id"] != item["execution_id"]
        or application_claim["attempt"] != item["attempt"]
        or application_claim["generation"] != item["generation"]
        or application_claim["claim_generation"] != application["claim_generation"]
    ):
        return False
    if application["state"] == "applying" and application_claim is None:
        return False
    if application["state"] != "applying" and application_claim is not None:
        return False
    if (application["state"] == "applied") != (application["applied_at"] is not None):
        return False
    return True


def _valid_ledger(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _TOP_LEVEL_KEYS:
        return False
    if value.get("version") != 1:
        return False
    if (
        not isinstance(value.get("parent_session_id"), str)
        or not value["parent_session_id"]
    ):
        return False
    if not _valid_timestamp(value.get("updated_at"), nullable=True):
        return False
    executions = value.get("executions")
    incidents = value.get("incidents")
    if not isinstance(executions, list) or not all(
        _valid_execution(item) for item in executions
    ):
        return False
    if len({item["execution_id"] for item in executions}) != len(executions):
        return False
    return isinstance(incidents, list) and all(
        isinstance(item, dict) for item in incidents
    )


def load_trusted(path: pathlib.Path, expected_parent: str) -> dict | None:
    """Load a trusted, schema-valid ledger for exactly one parent session."""
    path = pathlib.Path(path)
    if not is_trusted_file(path):
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not _valid_ledger(value) or value["parent_session_id"] != expected_parent:
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
            if not _valid_ledger(current):
                raise ValueError("ledger does not match the execution schema")
            updated = mutation(copy.deepcopy(current))
            if not _valid_ledger(updated):
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
