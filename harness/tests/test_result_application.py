#!/usr/bin/env python3
"""Behavioral proof that result application is fenced and independently verified."""

from __future__ import annotations

import copy
import datetime as dt
import pathlib
import sys

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger as ledger_api  # noqa: E402
import result_application  # noqa: E402


def at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def terminal_ledger(
    execution_id: str = "exec-alpha", digest: str = "sha256:same"
) -> dict:
    ledger = ledger_api.new_ledger("parent-7")
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": "parent-7",
            "bead_id": "escapement-e3ai.2",
            "execution_id": execution_id,
            "host": "codex",
            "agent_name": "worker",
            "dispatch_tool_use_id": f"call-{execution_id}",
            "watchdog_id": f"watch-{execution_id}",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-09T20:00:00Z"),
    )
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_terminal",
            "parent_session_id": "parent-7",
            "execution_id": execution_id,
            "attempt": 1,
            "generation": 1,
            "terminal_event_id": f"terminal-{execution_id}",
            "terminal_reason": "completed",
            "result_digest": digest,
        },
        at("2026-08-09T20:05:00Z"),
    )
    return ledger


def item(ledger: dict, execution_id: str = "exec-alpha") -> dict:
    return next(
        entry for entry in ledger["executions"] if entry["execution_id"] == execution_id
    )


def claim(
    ledger: dict, owner: str = "applier-a", when: str = "2026-08-09T20:06:00Z"
) -> dict:
    claimed = ledger_api.claim_result_application(
        ledger,
        "exec-alpha",
        at(when),
        owner,
        30,
        attempt=1,
        generation=1,
    )
    assert claimed is not None
    return claimed


def apply_claim(
    ledger: dict,
    claimed: dict,
    verify_outcome,
    apply,
    when: str = "2026-08-09T20:06:01Z",
) -> dict:
    return result_application.apply_verified_result(
        ledger,
        "exec-alpha",
        attempt=1,
        generation=1,
        owner=claimed["owner"],
        claim_generation=claimed["claim_generation"],
        verify_outcome=verify_outcome,
        apply=apply,
        idempotency_key="execution:exec-alpha:attempt:1:generation:1",
        now=at(when),
    )


def test_terminal_and_digest_do_not_directly_prove_application() -> None:
    ledger = terminal_ledger()
    assert item(ledger)["state"] == "terminal"
    assert item(ledger)["result_digest"] == "sha256:same"
    assert item(ledger)["result_application"] == {
        "state": "unapplied",
        "claim": None,
        "claim_generation": 0,
        "idempotency_key": "execution:exec-alpha:attempt:1:generation:1",
        "applied_at": None,
    }


def test_claim_alone_is_applying_not_applied() -> None:
    ledger = terminal_ledger()
    claimed = claim(ledger)
    assert claimed["claim_generation"] == 1
    assert item(ledger)["result_application"]["state"] == "applying"
    assert item(ledger)["result_application"]["applied_at"] is None


def test_missing_outcome_proof_does_not_apply_or_mutate_business_state() -> None:
    ledger = terminal_ledger()
    claimed = claim(ledger)
    external_calls: list[str] = []
    result = apply_claim(
        ledger, claimed, lambda _execution: None, external_calls.append
    )
    assert result == {"status": "verification_unresolved"}
    assert external_calls == []
    assert item(ledger)["result_application"]["state"] == "applying"
    assert item(ledger)["result_application"]["applied_at"] is None


def test_negative_post_apply_verification_cannot_mark_applied() -> None:
    ledger = terminal_ledger()
    claimed = claim(ledger)
    external_calls: list[str] = []
    result = apply_claim(
        ledger, claimed, lambda _execution: False, external_calls.append
    )
    assert result == {"status": "outcome_not_observed"}
    assert external_calls == ["execution:exec-alpha:attempt:1:generation:1"]
    assert item(ledger)["result_application"]["state"] == "applying"


def test_verifier_exception_fails_closed_before_external_application() -> None:
    ledger = terminal_ledger()
    claimed = claim(ledger)
    external_calls: list[str] = []

    def broken_verifier(_execution: dict) -> bool:
        raise RuntimeError("outcome service unavailable")

    result = apply_claim(ledger, claimed, broken_verifier, external_calls.append)
    assert result == {
        "status": "verification_error",
        "error": "outcome service unavailable",
    }
    assert external_calls == []
    assert item(ledger)["result_application"]["state"] == "applying"


def test_current_claim_applies_once_then_requires_positive_business_proof() -> None:
    ledger = terminal_ledger()
    claimed = claim(ledger)
    externally_applied: set[str] = set()

    def verify(_execution: dict) -> bool:
        return "execution:exec-alpha:attempt:1:generation:1" in externally_applied

    def apply(key: str) -> None:
        externally_applied.add(key)

    result = apply_claim(ledger, claimed, verify, apply)
    assert result == {
        "status": "applied",
        "idempotency_key": "execution:exec-alpha:attempt:1:generation:1",
    }
    assert externally_applied == {"execution:exec-alpha:attempt:1:generation:1"}
    assert item(ledger)["result_application"]["state"] == "applied"
    assert item(ledger)["result_application"]["applied_at"] == "2026-08-09T20:06:01Z"


def test_expired_claim_takeover_fences_stale_owner() -> None:
    ledger = terminal_ledger()
    stale = claim(ledger)
    current = claim(ledger, "applier-b", "2026-08-09T20:06:30Z")
    assert current["claim_generation"] == 2
    verifier_calls: list[str] = []
    before_stale_call = copy.deepcopy(ledger)
    stale_result = apply_claim(
        ledger,
        stale,
        lambda _execution: verifier_calls.append("stale") or True,
        lambda _key: None,
        "2026-08-09T20:06:31Z",
    )
    assert stale_result == {"status": "stale_claim"}
    assert verifier_calls == []
    assert ledger == before_stale_call

    current_verifier_calls: list[str] = []
    current_result = apply_claim(
        ledger,
        current,
        lambda _execution: current_verifier_calls.append("current") or True,
        lambda _key: pytest.fail("already-observed outcome must not be applied again"),
        "2026-08-09T20:06:31Z",
    )
    assert current_result["status"] == "applied"
    assert current_verifier_calls == ["current"]


def test_expired_claim_takeover_cannot_launder_missing_business_proof() -> None:
    ledger = terminal_ledger()
    claim(ledger, "applier-a", "2026-08-09T20:06:00Z")
    takeover = claim(ledger, "applier-b", "2026-08-09T20:06:30Z")
    apply_calls: list[str] = []

    result = apply_claim(
        ledger,
        takeover,
        lambda _execution: None,
        apply_calls.append,
        "2026-08-09T20:06:31Z",
    )

    assert result == {"status": "verification_unresolved"}
    assert apply_calls == []
    application = item(ledger)["result_application"]
    assert application["state"] == "applying"
    assert application["claim"]["owner"] == "applier-b"
    assert application["claim_generation"] == 2
    assert application["applied_at"] is None


def test_crash_after_external_effect_recovers_without_duplicate_application() -> None:
    class SimulatedProcessDeath(BaseException):
        pass

    ledger = terminal_ledger()
    first = claim(ledger)
    external_applications: list[str] = []

    def verify(_execution: dict) -> bool:
        return bool(external_applications)

    def apply_then_die(key: str) -> None:
        external_applications.append(key)
        raise SimulatedProcessDeath()

    with pytest.raises(SimulatedProcessDeath):
        apply_claim(ledger, first, verify, apply_then_die)
    assert external_applications == ["execution:exec-alpha:attempt:1:generation:1"]
    assert item(ledger)["result_application"]["state"] == "applying"

    takeover = claim(ledger, "applier-b", "2026-08-09T20:06:30Z")
    takeover_verifications: list[str] = []

    def verify_takeover(execution: dict) -> bool:
        takeover_verifications.append(execution["execution_id"])
        return verify(execution)

    result = apply_claim(
        ledger,
        takeover,
        verify_takeover,
        lambda _key: pytest.fail(
            "takeover must observe the existing effect before retry"
        ),
        "2026-08-09T20:06:31Z",
    )
    assert result["status"] == "applied"
    assert takeover_verifications == ["exec-alpha"]
    assert external_applications == ["execution:exec-alpha:attempt:1:generation:1"]


def test_equal_digests_on_distinct_executions_have_distinct_application_identity() -> (
    None
):
    ledger = terminal_ledger("exec-alpha", "sha256:identical")
    second = terminal_ledger("exec-beta", "sha256:identical")["executions"][0]
    ledger["executions"].append(second)
    keys: list[str] = []

    for execution_id, owner, expected_key in [
        ("exec-alpha", "a", "execution:exec-alpha:attempt:1:generation:1"),
        ("exec-beta", "b", "execution:exec-beta:attempt:1:generation:1"),
    ]:
        claimed = ledger_api.claim_result_application(
            ledger,
            execution_id,
            at("2026-08-09T20:06:00Z"),
            owner,
            30,
            attempt=1,
            generation=1,
        )
        assert claimed is not None
        observed: set[str] = set()
        result_application.apply_verified_result(
            ledger,
            execution_id,
            attempt=1,
            generation=1,
            owner=owner,
            claim_generation=claimed["claim_generation"],
            verify_outcome=lambda _execution, seen=observed: bool(seen),
            apply=lambda key, seen=observed: (keys.append(key), seen.add(key)),
            idempotency_key=expected_key,
            now=at("2026-08-09T20:06:01Z"),
        )
    assert keys == [
        "execution:exec-alpha:attempt:1:generation:1",
        "execution:exec-beta:attempt:1:generation:1",
    ]
