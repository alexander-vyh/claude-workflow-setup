#!/usr/bin/env python3
"""Replay identity for normalized host observations."""

from __future__ import annotations

import hashlib
import json


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
    incidents: list[dict], event: dict, observation: tuple[str, str] | None
) -> None:
    """Persist one accepted normalized host observation for replay comparison."""
    if observation is None:
        return
    host_event_id, fingerprint = observation
    incidents.append(
        {
            "type": "host_event_observation",
            "execution_id": event["execution_id"],
            "attempt": event["attempt"],
            "generation": event["generation"],
            "host_event_id": host_event_id,
            "event_fingerprint": fingerprint,
        }
    )
