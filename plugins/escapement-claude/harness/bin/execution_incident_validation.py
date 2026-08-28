#!/usr/bin/env python3
"""Cross-validate durable execution incidents against their executions."""

from __future__ import annotations

from typing import Any

_HOST_OBSERVATION_KEYS = {
    "type", "execution_id", "attempt", "generation", "host_event_id",
    "event_fingerprint",
}
_UNREPORTED_CANCELLATION_KEYS = {
    "type", "execution_id", "attempt", "generation", "actor", "reason",
    "overdue_basis", "state_before", "native_child_id", "recorded_at",
}
_DISPATCH_FAILURE_KEYS = {
    "type", "execution_id", "attempt", "generation", "host_error",
    "state_before", "recorded_at",
}
CANCEL_REASON_MIN_CHARS = 20
CANCEL_REASON_PLACEHOLDERS = frozenset(
    {
        "none", "tbd", "todo", "wip", "n/a", "na", "fixme", "xxx",
        "unknown", "dead", "stuck", "?", "??", "???",
    }
)


def _integer(value: Any, *, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def substantive_cancellation_reason(reason: object) -> str:
    """Return the canonical manual-cancellation rationale, else raise."""
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    stripped = reason.strip()
    tokens = [token.strip("-–—.,;:()[]\"'") for token in stripped.lower().split()]
    if tokens and all(
        token in CANCEL_REASON_PLACEHOLDERS or not token for token in tokens
    ):
        raise ValueError(f"reason {stripped!r} is a placeholder, not a rationale")
    if len(stripped) < CANCEL_REASON_MIN_CHARS:
        raise ValueError(
            f"reason must be at least {CANCEL_REASON_MIN_CHARS} characters and say "
            f"why no result will arrive; got {len(stripped)}"
        )
    return stripped


def _valid_host_observation(incident: dict, executions: dict[str, dict]) -> bool:
    if set(incident) != _HOST_OBSERVATION_KEYS:
        return False
    execution = executions.get(incident.get("execution_id"))
    fingerprint = incident.get("event_fingerprint")
    return (
        execution is not None
        and _integer(incident.get("attempt"), minimum=1)
        and incident["attempt"] == execution["attempt"]
        and _integer(incident.get("generation"), minimum=1)
        and _nonempty_text(incident.get("host_event_id"))
        and isinstance(fingerprint, str)
        and len(fingerprint) == 64
        and set(fingerprint) <= set("0123456789abcdef")
    )


def _valid_unreported_cancellation(incident: dict, execution: dict) -> bool:
    if set(incident) != _UNREPORTED_CANCELLATION_KEYS:
        return False
    try:
        reason = substantive_cancellation_reason(incident.get("reason"))
    except ValueError:
        return False
    bound = execution["native_child_id"] is not None
    expected_event_id = (
        f"unreported-cancel:{execution['execution_id']}:"
        f"{execution['attempt']}:{execution['generation']}"
    )
    return (
        incident.get("type") == "unreported_child_cancelled"
        and incident.get("execution_id") == execution["execution_id"]
        and incident.get("attempt") == execution["attempt"]
        and incident.get("generation") == execution["generation"]
        and _nonempty_text(incident.get("actor"))
        and incident["actor"] == incident["actor"].strip()
        and incident.get("reason") == reason
        and execution.get("terminal_event_id") == expected_event_id
        and incident.get("overdue_basis") in ({"hard"} if bound else {"start", "hard"})
        and incident.get("state_before") == ("running" if bound else "queued")
        and incident.get("native_child_id") == execution["native_child_id"]
        and incident.get("recorded_at") == execution["terminal_at"]
    )


def _valid_dispatch_failure(incident: dict, execution: dict) -> bool:
    expected_event_id = (
        f"{execution['host']}:dispatch-failed:{execution['dispatch_tool_use_id']}"
    )
    return (
        set(incident) == _DISPATCH_FAILURE_KEYS
        and incident.get("type") == "dispatch_failed"
        and incident.get("execution_id") == execution["execution_id"]
        and incident.get("attempt") == execution["attempt"]
        and incident.get("generation") == execution["generation"]
        and isinstance(incident.get("host_error"), str)
        and incident.get("state_before") == "queued"
        and incident.get("recorded_at") == execution["terminal_at"]
        and execution.get("state") == "aborted"
        and execution.get("terminal_reason") == "dispatch_failed"
        and execution.get("terminal_event_id") == expected_event_id
    )


def _one_matching(incidents: list[dict], execution: dict, validator) -> bool:
    matches = [
        incident
        for incident in incidents
        if incident.get("execution_id") == execution["execution_id"]
    ]
    return len(matches) == 1 and validator(matches[0], execution)


def validate_incidents(incidents: list[dict], executions: dict[str, dict]) -> bool:
    """Return whether typed incidents carry exact durable audit linkage."""
    observations = [
        item for item in incidents if item.get("type") == "host_event_observation"
    ]
    if not all(_valid_host_observation(item, executions) for item in observations):
        return False
    if len({item["host_event_id"] for item in observations}) != len(observations):
        return False

    cancellations = [
        item for item in incidents if item.get("type") == "unreported_child_cancelled"
    ]
    cancelled = [
        item
        for item in executions.values()
        if item["state"] == "cancelled"
        and item["terminal_reason"] == "unreported_child_cancelled"
    ]
    if len(cancellations) != len(cancelled) or not all(
        _one_matching(cancellations, execution, _valid_unreported_cancellation)
        for execution in cancelled
    ):
        return False

    failures = [item for item in incidents if item.get("type") == "dispatch_failed"]
    aborted = [
        item
        for item in executions.values()
        if item["state"] == "aborted" and item["terminal_reason"] == "dispatch_failed"
    ]
    return len(failures) == len(aborted) and all(
        _one_matching(failures, execution, _valid_dispatch_failure)
        for execution in aborted
    )
