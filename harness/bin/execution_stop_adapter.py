#!/usr/bin/env python3
"""Task-context loader for the Stop gate.

This adapter used to decide Stop from the delegated-execution ledger. That
layer is gone; what remains is loading the task-mode context and honouring a
task-mode incident. Stop's actual teeth are downstream and untouched: contract
verification, the wakeup override, and the Beads queue-drain check.

WHY THE LEDGER LAYER WAS REMOVED (2026-08-28)

It blocked Stop in 19 of 19 threads that held an active execution, on records
that could never complete:

  - 83 executions sat in state "queued" with native_child_id null. No child was
    ever bound to them, so no result could arrive. Work dispatched to a
    background teammate that reports by mailbox registers no execution at all
    (escapement-so5i), so the ledger was recording phantoms while missing the
    real thing.
  - Every one of those threads evaluated to ("block",
    "delegated_execution_overdue"), and behind that the health gate would have
    blocked too: supervisor health had not been fresh since 2026-08-11.
  - The reaper could not clear them. execution_supervisor.plan_thread marks a
    thread "unresolved" when it has active executions and no resolvable
    repo_cwd, and any unresolved thread fails the whole pass -- so the exact
    condition it existed to clean up was the condition it refused to act on.
    12 of those 19 threads had no resolvable repo. It could only reconcile
    threads that did not need reconciling.

A gate that blocks on unfalsifiable state, whose repair mechanism cannot run,
is compliance theatre by this repo's own definition (see
claude/rules/delicate-art-of-bureaucracy.md). It was dead for 17 days before
anyone noticed, which is also the honest measure of what it was providing.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib

from task_session_mode import load_task_context, load_task_mode_incident


def decide_task_mode(
    session_id: str,
    thread_dir: pathlib.Path,
    now: dt.datetime,
    *,
    harness_root: pathlib.Path,
    transcript_path: pathlib.Path | str | None = None,
) -> tuple[dict | None, tuple[str, str] | None]:
    """Load task context; block only on an unresolved task-mode incident.

    Returning ``(session_mode, None)`` hands the decision to the Stop gate's
    own logic. Only a task-mode incident short-circuits, because that records a
    real unresolved failure in this session rather than an inference about a
    child that may never have existed.
    """
    thread_dir = pathlib.Path(thread_dir)
    session_mode_path = thread_dir / "session_mode.json"
    task_mode_incident_path = thread_dir / "task_mode_incident.json"

    session_mode = load_task_context(session_mode_path, expected_session_id=session_id)
    task_mode_incident = load_task_mode_incident(task_mode_incident_path, session_id)

    if task_mode_incident is not None:
        return session_mode, ("block", "task_mode_incident_unresolved")

    # An incident file that will not load blocks only inside a session we already
    # know is task-managed. Blocking on it unconditionally would let anyone who
    # can write garbage into a thread directory freeze an unrelated session;
    # ignoring it inside a real task session would drop genuinely unresolved
    # state. This split is the contract in harness/tests/test_task_mode_scope.py.
    if session_mode is not None and os.path.lexists(task_mode_incident_path):
        return session_mode, ("block", "task_mode_incident_unresolved")

    return session_mode, None
