#!/usr/bin/env python3
"""Behavioral oracle for the durable delegated-execution state machine.

The literals in this file are independently hand-authored.  In particular,
deadline expectations are not built with production constants/helpers, and
generation tests address executions by semantic attempt identity rather than a
result digest.
"""

from __future__ import annotations

import copy
import datetime as dt
import pathlib
import sys

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger as ledger_api  # noqa: E402

UTC = dt.timezone.utc


def at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def dispatch_event(execution_id: str = "exec-alpha") -> dict:
    return {
        "kind": "dispatch_registered",
        "parent_session_id": "parent-7",
        "bead_id": "escapement-e3ai.2",
        "execution_id": execution_id,
        "host": "codex",
        "agent_name": "worker",
        "dispatch_tool_use_id": "call-44",
        "watchdog_id": f"watch-{execution_id}",
        "attempt": 1,
        "generation": 1,
    }


def registered(execution_id: str = "exec-alpha") -> dict:
    ledger = ledger_api.new_ledger("parent-7")
    ledger_api.register_execution(
        ledger, dispatch_event(execution_id), at("2026-08-09T20:00:00Z")
    )
    return ledger


def started() -> dict:
    ledger = registered()
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_bound",
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "native_child_id": "child-native-1",
        },
        at("2026-08-09T20:00:10Z"),
    )
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_started",
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "native_child_id": "child-native-1",
        },
        at("2026-08-09T20:00:20Z"),
    )
    return ledger


def execution(ledger: dict, execution_id: str = "exec-alpha") -> dict:
    return next(
        item for item in ledger["executions"] if item["execution_id"] == execution_id
    )


def test_registers_a_queued_attempt_with_literal_deadlines() -> None:
    ledger = registered()
    assert ledger["updated_at"] == "2026-08-09T20:00:00Z"
    assert execution(ledger) == {
        "bead_id": "escapement-e3ai.2",
        "execution_id": "exec-alpha",
        "host": "codex",
        "agent_name": "worker",
        "native_child_id": None,
        "dispatch_tool_use_id": "call-44",
        "attempt": 1,
        "generation": 1,
        "state": "queued",
        "queued_at": "2026-08-09T20:00:00Z",
        "started_at": None,
        "last_activity_at": None,
        "last_activity_kind": None,
        "start_deadline": "2026-08-09T20:02:00Z",
        "idle_deadline": "2026-08-09T20:15:00Z",
        "hard_deadline": "2026-08-09T22:00:00Z",
        "reconcile_due": None,
        "terminal_at": None,
        "terminal_reason": None,
        "terminal_event_id": None,
        "result_digest": None,
        "watchdog_id": "watch-exec-alpha",
        "recovery_count": 0,
        "recovery_claim": None,
        "result_application": {
            "state": "unapplied",
            "claim": None,
            "claim_generation": 0,
            "idempotency_key": "execution:exec-alpha:attempt:1:generation:1",
            "applied_at": None,
        },
    }


def test_binds_and_starts_only_the_matching_native_child() -> None:
    item = execution(started())
    assert item["native_child_id"] == "child-native-1"
    assert item["state"] == "running"
    assert item["started_at"] == "2026-08-09T20:00:20Z"
    assert item["last_activity_at"] == "2026-08-09T20:00:20Z"
    assert item["last_activity_kind"] == "child_started"
    assert item["idle_deadline"] == "2026-08-09T20:15:20Z"


def test_completed_activity_renews_idle_but_never_hard_deadline() -> None:
    ledger = started()
    ledger_api.apply_event(
        ledger,
        {
            "kind": "activity_completed",
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "native_child_id": "child-native-1",
            "activity_kind": "tool_completed",
        },
        at("2026-08-09T21:59:40Z"),
    )
    item = execution(ledger)
    assert item["last_activity_at"] == "2026-08-09T21:59:40Z"
    assert item["last_activity_kind"] == "tool_completed"
    assert item["idle_deadline"] == "2026-08-09T22:14:40Z"
    assert item["hard_deadline"] == "2026-08-09T22:00:00Z"


def test_completed_activity_requires_explicit_bound_native_identity() -> None:
    ledger = started()
    before = copy.deepcopy(ledger)
    with pytest.raises(ValueError, match="native child"):
        ledger_api.apply_event(
            ledger,
            {
                "kind": "activity_completed",
                "parent_session_id": "parent-7",
                "execution_id": "exec-alpha",
                "attempt": 1,
                "generation": 1,
                "activity_kind": "tool_completed",
            },
            at("2026-08-09T20:01:00Z"),
        )
    assert ledger == before


def test_completed_activity_rejects_unbound_or_mismatched_native_identity() -> None:
    unbound = registered()
    unbound_item = execution(unbound)
    unbound_item["state"] = "running"
    unbound_item["started_at"] = "2026-08-09T20:00:20Z"
    unbound_item["last_activity_at"] = "2026-08-09T20:00:20Z"
    unbound_item["last_activity_kind"] = "child_started"
    before_unbound = copy.deepcopy(unbound)
    with pytest.raises(ValueError, match="native child"):
        ledger_api.apply_event(
            unbound,
            {
                "kind": "activity_completed",
                "parent_session_id": "parent-7",
                "execution_id": "exec-alpha",
                "attempt": 1,
                "generation": 1,
                "native_child_id": "unbound-child",
                "activity_kind": "tool_completed",
            },
            at("2026-08-09T20:01:00Z"),
        )
    assert unbound == before_unbound

    bound = started()
    before_bound = copy.deepcopy(bound)
    with pytest.raises(ValueError, match="native child"):
        ledger_api.apply_event(
            bound,
            {
                "kind": "activity_completed",
                "parent_session_id": "parent-7",
                "execution_id": "exec-alpha",
                "attempt": 1,
                "generation": 1,
                "native_child_id": "different-native-child",
                "activity_kind": "tool_completed",
            },
            at("2026-08-09T20:01:00Z"),
        )
    assert bound == before_bound


@pytest.mark.parametrize("event_child", [None, "unbound-child"])
def test_terminal_evidence_cannot_create_result_without_prior_native_binding(
    event_child: str | None,
) -> None:
    ledger = registered()
    event = {
        "kind": "child_terminal",
        "parent_session_id": "parent-7",
        "execution_id": "exec-alpha",
        "attempt": 1,
        "generation": 1,
        "terminal_event_id": "terminal-unbound",
        "terminal_reason": "completed",
        "result_digest": "sha256:must-not-apply",
    }
    if event_child is not None:
        event["native_child_id"] = event_child
    before = copy.deepcopy(ledger)
    with pytest.raises(ValueError, match="native child"):
        ledger_api.apply_event(ledger, event, at("2026-08-09T20:05:00Z"))
    assert ledger == before
    assert (
        ledger_api.claim_result_application(
            ledger,
            "exec-alpha",
            at("2026-08-09T20:06:00Z"),
            "applier",
            30,
            attempt=1,
            generation=1,
        )
        is None
    )


@pytest.mark.parametrize("event_child", [None, "different-native-child"])
def test_terminal_evidence_requires_exact_bound_native_identity(
    event_child: str | None,
) -> None:
    ledger = registered()
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_bound",
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "native_child_id": "child-native-1",
        },
        at("2026-08-09T20:00:10Z"),
    )
    event = {
        "kind": "child_terminal",
        "parent_session_id": "parent-7",
        "execution_id": "exec-alpha",
        "attempt": 1,
        "generation": 1,
        "terminal_event_id": "terminal-wrong-child",
        "terminal_reason": "completed",
        "result_digest": "sha256:must-not-apply",
    }
    if event_child is not None:
        event["native_child_id"] = event_child
    before = copy.deepcopy(ledger)
    with pytest.raises(ValueError, match="native child"):
        ledger_api.apply_event(ledger, event, at("2026-08-09T20:05:00Z"))
    assert ledger == before


@pytest.mark.parametrize(
    ("setup", "event_child"),
    [
        ("bound", None),
        ("unbound", "unbound-child"),
        ("bound", "different-native-child"),
    ],
)
def test_cancellation_evidence_requires_exact_prior_native_binding(
    setup: str, event_child: str | None
) -> None:
    ledger = started() if setup == "bound" else registered()
    event = {
        "kind": "child_cancelled",
        "parent_session_id": "parent-7",
        "execution_id": "exec-alpha",
        "attempt": 1,
        "generation": 1,
        "terminal_event_id": "cancelled-wrong-child",
        "terminal_reason": "supervisor_cancelled",
    }
    if event_child is not None:
        event["native_child_id"] = event_child
    before = copy.deepcopy(ledger)

    with pytest.raises(ValueError, match="native child"):
        ledger_api.apply_event(ledger, event, at("2026-08-09T20:05:00Z"))

    assert ledger == before


def test_cancellation_evidence_accepts_the_exact_bound_native_child() -> None:
    ledger = started()
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_cancelled",
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "native_child_id": "child-native-1",
            "terminal_event_id": "cancelled-900",
            "terminal_reason": "supervisor_cancelled",
        },
        at("2026-08-09T20:05:00Z"),
    )

    item = execution(ledger)
    assert item["state"] == "cancelled"
    assert item["native_child_id"] == "child-native-1"
    assert item["terminal_at"] == "2026-08-09T20:05:00Z"
    assert item["terminal_reason"] == "supervisor_cancelled"
    assert item["terminal_event_id"] == "cancelled-900"
    assert item["result_digest"] is None
    assert item["result_application"]["state"] == "unapplied"
    assert (
        ledger_api.claim_result_application(
            ledger,
            "exec-alpha",
            at("2026-08-09T20:06:00Z"),
            "applier",
            30,
            attempt=1,
            generation=1,
        )
        is None
    )


@pytest.mark.parametrize(
    "kind", ["tool_started", "status_polled", "semantic_annotation"]
)
def test_nonsemantic_events_do_not_renew_activity(kind: str) -> None:
    ledger = started()
    before = copy.deepcopy(execution(ledger))
    ledger_api.apply_event(
        ledger,
        {
            "kind": kind,
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "native_child_id": "child-native-1",
        },
        at("2026-08-09T21:59:59Z"),
    )
    item = execution(ledger)
    assert item["last_activity_at"] == before["last_activity_at"]
    assert item["last_activity_kind"] == before["last_activity_kind"]
    assert item["idle_deadline"] == before["idle_deadline"]
    assert item["hard_deadline"] == before["hard_deadline"]
    ledger_api.reconcile_deadlines(ledger, at("2026-08-09T22:00:00Z"))
    assert item["reconcile_due"] == "hard"


def test_terminal_event_is_idempotent_by_terminal_event_identity() -> None:
    ledger = started()
    event = {
        "kind": "child_terminal",
        "parent_session_id": "parent-7",
        "execution_id": "exec-alpha",
        "attempt": 1,
        "generation": 1,
        "native_child_id": "child-native-1",
        "terminal_event_id": "terminal-900",
        "terminal_reason": "completed",
        "result_digest": "sha256:result-a",
    }
    ledger_api.apply_event(ledger, event, at("2026-08-09T20:04:00Z"))
    first = copy.deepcopy(ledger)
    ledger_api.apply_event(ledger, event, at("2026-08-09T20:09:00Z"))
    assert ledger == first
    item = execution(ledger)
    assert item["state"] == "terminal"
    assert item["terminal_at"] == "2026-08-09T20:04:00Z"
    assert item["terminal_event_id"] == "terminal-900"
    assert item["result_digest"] == "sha256:result-a"
    assert item["result_application"]["state"] == "unapplied"


def test_old_generation_terminal_is_evidence_not_active_result() -> None:
    ledger = registered()
    ledger_api.reconcile_deadlines(ledger, at("2026-08-09T20:02:00Z"))
    first = ledger_api.claim_recovery(
        ledger, "exec-alpha", at("2026-08-09T20:02:01Z"), "supervisor-a", 30
    )
    assert first is not None and first["generation"] == 1
    second = ledger_api.claim_recovery(
        ledger, "exec-alpha", at("2026-08-09T20:02:31Z"), "supervisor-b", 30
    )
    assert second is not None and second["generation"] == 2
    active_before_late_event = copy.deepcopy(execution(ledger))

    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_terminal",
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "terminal_event_id": "late-generation-one",
            "terminal_reason": "completed",
            "result_digest": "sha256:stale",
        },
        at("2026-08-09T20:03:00Z"),
    )
    item = execution(ledger)
    assert item == active_before_late_event
    assert ledger["incidents"][-1] == {
        "type": "old_generation_event",
        "execution_id": "exec-alpha",
        "event_kind": "child_terminal",
        "event_id": "late-generation-one",
        "event_attempt": 1,
        "event_generation": 1,
        "active_attempt": 1,
        "active_generation": 2,
        "recorded_at": "2026-08-09T20:03:00Z",
    }


def test_start_deadline_sets_sticky_reconcile_without_terminal_state() -> None:
    ledger = registered()
    due = ledger_api.reconcile_deadlines(ledger, at("2026-08-09T20:02:00Z"))
    assert [item["execution_id"] for item in due] == ["exec-alpha"]
    assert execution(ledger)["reconcile_due"] == "start"
    assert execution(ledger)["state"] == "queued"


def test_idle_deadline_sets_sticky_reconcile_without_terminal_state() -> None:
    ledger = started()
    ledger_api.reconcile_deadlines(ledger, at("2026-08-09T20:15:20Z"))
    assert execution(ledger)["reconcile_due"] == "idle"
    assert execution(ledger)["state"] == "running"
    ledger_api.apply_event(
        ledger,
        {
            "kind": "activity_completed",
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "native_child_id": "child-native-1",
            "activity_kind": "checkpoint",
        },
        at("2026-08-09T20:15:21Z"),
    )
    assert execution(ledger)["reconcile_due"] == "idle"


def test_hard_deadline_wins_even_after_recent_completed_activity() -> None:
    ledger = started()
    ledger_api.apply_event(
        ledger,
        {
            "kind": "activity_completed",
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "native_child_id": "child-native-1",
            "activity_kind": "checkpoint",
        },
        at("2026-08-09T21:59:50Z"),
    )
    ledger_api.reconcile_deadlines(ledger, at("2026-08-09T22:00:00Z"))
    assert execution(ledger)["reconcile_due"] == "hard"
    assert execution(ledger)["state"] == "running"


def test_recovery_claim_is_exclusive_and_expiry_advances_generation() -> None:
    ledger = registered()
    original_hard_deadline = execution(ledger)["hard_deadline"]
    ledger_api.reconcile_deadlines(ledger, at("2026-08-09T20:02:00Z"))
    claim = ledger_api.claim_recovery(
        ledger, "exec-alpha", at("2026-08-09T20:02:01Z"), "supervisor-a", 30
    )
    assert claim == {
        "owner": "supervisor-a",
        "execution_id": "exec-alpha",
        "attempt": 1,
        "generation": 1,
        "claimed_at": "2026-08-09T20:02:01Z",
        "expires_at": "2026-08-09T20:02:31Z",
    }
    assert (
        ledger_api.claim_recovery(
            ledger, "exec-alpha", at("2026-08-09T20:02:30Z"), "supervisor-b", 30
        )
        is None
    )

    takeover = ledger_api.claim_recovery(
        ledger, "exec-alpha", at("2026-08-09T20:02:31Z"), "supervisor-b", 45
    )
    assert takeover is not None
    assert takeover["generation"] == 2
    item = execution(ledger)
    assert item["generation"] == 2
    assert item["recovery_count"] == 1
    assert item["hard_deadline"] == original_hard_deadline
    assert item["native_child_id"] is None
    assert (
        item["result_application"]["idempotency_key"]
        == "execution:exec-alpha:attempt:1:generation:2"
    )


def test_stale_generation_cannot_claim_application_or_mutate_current_result() -> None:
    ledger = registered()
    ledger_api.reconcile_deadlines(ledger, at("2026-08-09T20:02:00Z"))
    ledger_api.claim_recovery(ledger, "exec-alpha", at("2026-08-09T20:02:01Z"), "a", 1)
    ledger_api.claim_recovery(ledger, "exec-alpha", at("2026-08-09T20:02:02Z"), "b", 30)
    before = copy.deepcopy(ledger)
    with pytest.raises(ValueError, match="generation"):
        ledger_api.claim_result_application(
            ledger,
            "exec-alpha",
            at("2026-08-09T20:04:00Z"),
            "stale-owner",
            30,
            attempt=1,
            generation=1,
        )
    assert ledger == before


@pytest.mark.parametrize(
    "event_patch, message",
    [
        ({"kind": "made_up_event"}, "event kind"),
        ({"attempt": 9}, "attempt"),
        ({"generation": 0}, "generation"),
        ({"parent_session_id": "foreign-parent"}, "parent"),
    ],
)
def test_unknown_or_invalid_event_identity_is_rejected(
    event_patch: dict, message: str
) -> None:
    ledger = registered()
    event = {
        "kind": "child_bound",
        "parent_session_id": "parent-7",
        "execution_id": "exec-alpha",
        "attempt": 1,
        "generation": 1,
        "native_child_id": "child-native-1",
    }
    event.update(event_patch)
    with pytest.raises(ValueError, match=message):
        ledger_api.apply_event(ledger, event, at("2026-08-09T20:00:10Z"))


def test_naive_time_is_rejected_instead_of_becoming_local_time() -> None:
    with pytest.raises(ValueError, match="timezone"):
        ledger_api.register_execution(
            ledger_api.new_ledger("parent-7"),
            dispatch_event(),
            dt.datetime(2026, 8, 9, 20, 0),
        )
