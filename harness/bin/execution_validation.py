#!/usr/bin/env python3
"""Trusted shape and cross-state validation for execution ledgers."""

from __future__ import annotations

import datetime as dt
from typing import Any

UTC = dt.timezone.utc
EXECUTION_STATES = {"queued", "running", "terminal", "cancelled", "unknown"}
RECONCILE_STATES = {None, "start", "idle", "hard"}
APPLICATION_STATES = {"unapplied", "applying", "applied"}

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
_RECOVERY_CLAIM_KEYS = {
    "owner",
    "execution_id",
    "attempt",
    "generation",
    "claimed_at",
    "expires_at",
}
_APPLICATION_CLAIM_KEYS = _RECOVERY_CLAIM_KEYS | {"claim_generation"}


def _parse(value: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _valid_timestamp(value: Any, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    try:
        _parse(value)
    except (TypeError, ValueError):
        return False
    return True


def _integer(value: Any, *, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _application_key(execution_id: str, attempt: int, generation: int) -> str:
    return f"execution:{execution_id}:attempt:{attempt}:generation:{generation}"


def _valid_claim(claim: Any, required: set[str]) -> bool:
    if claim is None:
        return True
    if not isinstance(claim, dict) or set(claim) != required:
        return False
    if not all(_nonempty_text(claim.get(key)) for key in ("owner", "execution_id")):
        return False
    if not all(
        _integer(claim.get(key), minimum=1)
        for key in required & {"attempt", "generation", "claim_generation"}
    ):
        return False
    return _valid_timestamp(claim.get("claimed_at")) and _valid_timestamp(
        claim.get("expires_at")
    )


def _claim_matches_execution(claim: dict, item: dict) -> bool:
    return (
        claim["execution_id"] == item["execution_id"]
        and claim["attempt"] == item["attempt"]
        and claim["generation"] == item["generation"]
    )


def _valid_application(application: Any, item: dict) -> bool:
    if not isinstance(application, dict) or set(application) != _APPLICATION_KEYS:
        return False
    if application.get("state") not in APPLICATION_STATES:
        return False
    if not _integer(application.get("claim_generation"), minimum=0):
        return False
    if application.get("idempotency_key") != _application_key(
        item["execution_id"], item["attempt"], item["generation"]
    ):
        return False
    if not _valid_timestamp(application.get("applied_at"), nullable=True):
        return False
    claim = application.get("claim")
    if not _valid_claim(claim, _APPLICATION_CLAIM_KEYS):
        return False
    if claim is not None and (
        not _claim_matches_execution(claim, item)
        or claim["claim_generation"] != application["claim_generation"]
    ):
        return False
    state = application["state"]
    if state == "applying" and claim is None:
        return False
    if state != "applying" and claim is not None:
        return False
    if (state == "applied") != (application["applied_at"] is not None):
        return False
    return True


def _valid_terminal_state(item: dict) -> bool:
    state = item["state"]
    terminal_fields = ("terminal_at", "terminal_reason", "terminal_event_id")
    if state == "terminal":
        return (
            _nonempty_text(item["native_child_id"])
            and all(_nonempty_text(item[key]) for key in terminal_fields)
            and _nonempty_text(item["result_digest"])
        )
    if state == "cancelled":
        return (
            _nonempty_text(item["native_child_id"])
            and all(_nonempty_text(item[key]) for key in terminal_fields)
            and item["result_digest"] is None
        )
    return all(item[key] is None for key in (*terminal_fields, "result_digest"))


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
    if not all(_nonempty_text(item.get(key)) for key in required_text):
        return False
    if item["native_child_id"] is not None and not _nonempty_text(
        item["native_child_id"]
    ):
        return False
    if not all(_integer(item.get(key), minimum=1) for key in ("attempt", "generation")):
        return False
    if not _integer(item.get("recovery_count"), minimum=0):
        return False
    if (
        item.get("state") not in EXECUTION_STATES
        or item.get("reconcile_due") not in RECONCILE_STATES
    ):
        return False
    if not all(
        _valid_timestamp(item.get(key))
        for key in ("queued_at", "start_deadline", "idle_deadline", "hard_deadline")
    ):
        return False
    if not all(
        _valid_timestamp(item.get(key), nullable=True)
        for key in ("started_at", "last_activity_at", "terminal_at")
    ):
        return False
    if any(
        item[key] is not None and not isinstance(item[key], str)
        for key in (
            "last_activity_kind",
            "terminal_reason",
            "terminal_event_id",
            "result_digest",
        )
    ):
        return False
    recovery_claim = item.get("recovery_claim")
    if not _valid_claim(recovery_claim, _RECOVERY_CLAIM_KEYS):
        return False
    if recovery_claim is not None and not _claim_matches_execution(
        recovery_claim, item
    ):
        return False
    if not _valid_terminal_state(item):
        return False
    if not _valid_application(item.get("result_application"), item):
        return False
    if (
        item["state"] != "terminal"
        and item["result_application"]["state"] != "unapplied"
    ):
        return False
    return True


def is_valid_ledger(value: Any) -> bool:
    """Return whether a ledger is shape-valid and cross-state consistent."""
    required_keys = {
        "version",
        "parent_session_id",
        "updated_at",
        "executions",
        "incidents",
    }
    if not isinstance(value, dict) or set(value) != required_keys:
        return False
    if not _integer(value.get("version"), minimum=1) or value["version"] != 1:
        return False
    if not _nonempty_text(value.get("parent_session_id")):
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
    if not isinstance(incidents, list) or not all(
        isinstance(item, dict) for item in incidents
    ):
        return False
    return True
