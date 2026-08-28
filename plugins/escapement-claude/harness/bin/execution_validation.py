#!/usr/bin/env python3
"""Trusted shape and cross-state validation for execution ledgers."""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from execution_incident_validation import validate_incidents

UTC = dt.timezone.utc
EXECUTION_STATES = {"queued", "running", "terminal", "cancelled", "aborted", "unknown"}
RECONCILE_STATES = {None, "start", "idle", "hard"}
APPLICATION_STATES = {"unapplied", "applying", "applied"}
RUNNING_ACTIVITY_KINDS = {
    "child_started",
    "tool_completed",
    "assistant_nonempty",
    "checkpoint",
    "terminal_event",
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
        # A cancellation records that no result will arrive, so it does NOT
        # require a bound native child: the failure this recovers from is the
        # child that dies before the host ever reports its identity. Requiring
        # one here is what made an unreported execution permanently
        # unterminalizable. `terminal` still requires it — a real result digest
        # implies a real child that produced it — and a cancelled execution
        # still carries no result_digest, so it can never be mistaken for one.
        if not (
            all(_nonempty_text(item[key]) for key in terminal_fields)
            and item["result_digest"] is None
        ):
            return False
        if item["native_child_id"] is not None:
            return True
        return (
            item["terminal_reason"] == "unreported_child_cancelled"
            and item["terminal_event_id"]
            == (
                f"unreported-cancel:{item['execution_id']}:"
                f"{item['attempt']}:{item['generation']}"
            )
        )
    if state == "aborted":
        return (
            item["native_child_id"] is None
            and item["started_at"] is None
            and item["last_activity_at"] is None
            and item["last_activity_kind"] is None
            and all(_nonempty_text(item[key]) for key in terminal_fields)
            and item["result_digest"] is None
        )
    return all(item[key] is None for key in (*terminal_fields, "result_digest"))


def _valid_running_state(item: dict) -> bool:
    if item["state"] != "running":
        return True
    if (
        not _nonempty_text(item["native_child_id"])
        or not _valid_timestamp(item["started_at"])
        or not _valid_timestamp(item["last_activity_at"])
        or item["last_activity_kind"] not in RUNNING_ACTIVITY_KINDS
    ):
        return False
    started_at = _parse(item["started_at"])
    last_activity_at = _parse(item["last_activity_at"])
    idle_deadline = _parse(item["idle_deadline"])
    return (
        started_at <= last_activity_at
        and idle_deadline == last_activity_at + dt.timedelta(minutes=15)
    )


def _legacy_resolved_evidence(item: object) -> bool:
    if not isinstance(item, dict) or item.get("state") not in {"terminal", "cancelled"}:
        return False
    terminal_fields = ("terminal_at", "terminal_reason", "terminal_event_id")
    if not _nonempty_text(item.get("native_child_id")) or not all(
        _nonempty_text(item.get(field)) for field in terminal_fields
    ):
        return False
    if item["state"] == "terminal":
        return _nonempty_text(item.get("result_digest"))
    return item.get("result_digest") is None


def normalize_legacy_resolved_ledger(value: Any) -> Any:
    """Clear only pre-resolution residue from version-one terminal evidence."""
    if not isinstance(value, dict) or value.get("version") != 1:
        return value
    executions = value.get("executions")
    if not isinstance(executions, list):
        return value
    resolved = [
        item
        for item in executions
        if isinstance(item, dict) and item.get("state") in {"terminal", "cancelled"}
    ]
    if not all(_legacy_resolved_evidence(item) for item in resolved):
        return value
    normalized = copy.deepcopy(value)
    for item in normalized["executions"]:
        if item.get("state") in {"terminal", "cancelled"}:
            for field in (
                "start_deadline",
                "idle_deadline",
                "hard_deadline",
                "reconcile_due",
                "recovery_claim",
            ):
                item[field] = None
    return normalized


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
    if not _valid_timestamp(item.get("queued_at")):
        return False
    deadline_fields = ("start_deadline", "idle_deadline", "hard_deadline")
    resolved = item["state"] in {"terminal", "cancelled", "aborted"}
    if not all(
        _valid_timestamp(item.get(key), nullable=resolved) for key in deadline_fields
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
    if resolved and (
        item["reconcile_due"] is not None
        or recovery_claim is not None
        or any(item[field] is not None for field in deadline_fields)
    ):
        return False
    if not _valid_running_state(item):
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
    executions_by_id = {item["execution_id"]: item for item in executions}
    return validate_incidents(incidents, executions_by_id)
