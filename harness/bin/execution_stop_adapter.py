#!/usr/bin/env python3
"""Filesystem/Beads adapter for the pure delegated Stop policy."""

from __future__ import annotations

import datetime as dt
import os
import pathlib

from beads_task_state import task_root_status
from execution_expectation import (
    ledger_covers_expectations,
    load_expectation,
    load_incident,
)
import execution_store
import schedule_store
import supervisor_health
from task_session_mode import (
    load_task_context,
    load_task_mode_incident,
    transcript_has_successful_exact_claim,
)
from thread_identity import supervisor_health_path
from would_block_stop import execution_stop_decision


def decide_task_mode(
    session_id: str,
    thread_dir: pathlib.Path,
    now: dt.datetime,
    *,
    harness_root: pathlib.Path,
    transcript_path: pathlib.Path | str | None = None,
) -> tuple[dict | None, tuple[str, str] | None]:
    """Load task context and decide managed execution state when it exists."""
    thread_dir = pathlib.Path(thread_dir)
    ledger_path = pathlib.Path(thread_dir) / "executions.json"
    expectation_path = thread_dir / "execution_expectation.json"
    incident_path = thread_dir / "execution_incident.json"
    session_mode_path = thread_dir / "session_mode.json"
    task_mode_incident_path = thread_dir / "task_mode_incident.json"
    session_mode = load_task_context(session_mode_path, expected_session_id=session_id)
    task_mode_incident = load_task_mode_incident(task_mode_incident_path, session_id)
    expectation = load_expectation(expectation_path, session_id)
    incident = load_incident(incident_path, session_id)
    ledger = None
    if not ledger_path.is_symlink():
        ledger = execution_store.load_trusted(ledger_path, expected_parent=session_id)
    transcript_claim = transcript_has_successful_exact_claim(
        transcript_path, session_id
    )
    if session_mode is None:
        trusted_managed_evidence = any(
            (
                task_mode_incident is not None,
                incident is not None,
                expectation is not None and bool(expectation["expectations"]),
                ledger is not None and bool(ledger.get("executions")),
                transcript_claim,
            )
        )
        if trusted_managed_evidence:
            return None, ("block", "delegated_execution_unresolved")
        return None, None
    if task_mode_incident is not None or (
        os.path.lexists(task_mode_incident_path) and task_mode_incident is None
    ):
        return session_mode, ("block", "delegated_execution_unresolved")
    if os.path.lexists(expectation_path) and expectation is None:
        return session_mode, ("block", "delegated_execution_unresolved")
    if os.path.lexists(incident_path) and incident is None:
        return session_mode, ("block", "delegated_execution_unresolved")
    if incident is not None:
        return session_mode, ("block", "delegated_execution_unresolved")
    if not os.path.lexists(ledger_path):
        if expectation is not None:
            return session_mode, ("block", "delegated_execution_unresolved")
        return session_mode, None
    if ledger_path.is_symlink():
        return session_mode, ("block", "delegated_execution_unresolved")
    if ledger is None:
        return session_mode, ("block", "delegated_execution_unresolved")
    if expectation is not None and not ledger_covers_expectations(expectation, ledger):
        return session_mode, ("block", "delegated_execution_unresolved")

    scheduled = schedule_store.load(pathlib.Path(thread_dir) / "scheduled.json")
    health = supervisor_health.load_trusted(supervisor_health_path(harness_root))
    return session_mode, execution_stop_decision(
        task_root_status(session_mode), ledger, health, scheduled, now
    )
