#!/usr/bin/env python3
"""Independently verify and apply a fenced delegated-execution result."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any


def _iso(now: dt.datetime) -> str:
    if (
        not isinstance(now, dt.datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError("datetime must be timezone-aware")
    return now.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("claim expiry must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def _execution(ledger: dict, execution_id: str) -> dict | None:
    matches = [
        item
        for item in ledger.get("executions", [])
        if item.get("execution_id") == execution_id
    ]
    return matches[0] if len(matches) == 1 else None


def _current_claim(
    execution: dict | None,
    *,
    attempt: int,
    generation: int,
    owner: str,
    claim_generation: int,
    now: dt.datetime,
) -> bool:
    if (
        execution is None
        or execution.get("attempt") != attempt
        or execution.get("generation") != generation
    ):
        return False
    application = execution.get("result_application")
    if not isinstance(application, dict) or application.get("state") != "applying":
        return False
    claim = application.get("claim")
    if not isinstance(claim, dict):
        return False
    return (
        claim.get("owner") == owner
        and claim.get("execution_id") == execution.get("execution_id")
        and claim.get("attempt") == attempt
        and claim.get("generation") == generation
        and claim.get("claim_generation") == claim_generation
        and claim_generation == application.get("claim_generation")
        and now.astimezone(dt.timezone.utc) < _parse(claim["expires_at"])
    )


def _verify(verify_outcome: Callable[[dict], Any], execution: dict) -> tuple[str, str]:
    try:
        outcome = verify_outcome(execution)
    except (
        Exception
    ) as exc:  # verifier outages fail closed; process-death BaseException propagates
        return "error", str(exc)
    if outcome is True:
        return "observed", ""
    if outcome is False:
        return "negative", ""
    return "unresolved", ""


def apply_verified_result(
    ledger: dict,
    execution_id: str,
    *,
    attempt: int,
    generation: int,
    owner: str,
    claim_generation: int,
    verify_outcome: Callable[[dict], Any],
    apply: Callable[[str], Any],
    idempotency_key: str,
    now: dt.datetime,
) -> dict:
    """Apply only through a live fenced claim and independently observed outcome.

    `verify_outcome` is the business oracle.  A terminal state or result digest
    is never substituted for it.  When mutation is needed, `apply` receives the
    execution-scoped stable idempotency key; the outcome is checked again before
    durable completion.
    """
    timestamp = _iso(now)
    execution = _execution(ledger, execution_id)
    if not _current_claim(
        execution,
        attempt=attempt,
        generation=generation,
        owner=owner,
        claim_generation=claim_generation,
        now=now,
    ):
        return {"status": "stale_claim"}
    application = execution["result_application"]
    if application.get("idempotency_key") != idempotency_key:
        raise ValueError("idempotency_key does not match the execution generation")

    verification_status, verification_error = _verify(verify_outcome, execution)
    if verification_status == "error":
        return {"status": "verification_error", "error": verification_error}
    if verification_status == "unresolved":
        return {"status": "verification_unresolved"}

    if verification_status != "observed":
        try:
            apply(idempotency_key)
        except Exception as exc:  # BaseException models process death and must escape
            return {"status": "application_error", "error": str(exc)}
        verification_status, verification_error = _verify(verify_outcome, execution)
        if verification_status == "error":
            return {"status": "verification_error", "error": verification_error}
        if verification_status != "observed":
            if verification_status == "unresolved":
                return {"status": "verification_unresolved"}
            return {"status": "outcome_not_observed"}

    # External callbacks are allowed to take time or touch durable state.  Fence
    # again before persisting completion so an expired/stolen claim cannot win.
    execution = _execution(ledger, execution_id)
    if not _current_claim(
        execution,
        attempt=attempt,
        generation=generation,
        owner=owner,
        claim_generation=claim_generation,
        now=now,
    ):
        return {"status": "stale_claim"}
    application = execution["result_application"]
    application["state"] = "applied"
    application["applied_at"] = timestamp
    application["claim"] = None
    ledger["updated_at"] = timestamp
    return {"status": "applied", "idempotency_key": idempotency_key}
