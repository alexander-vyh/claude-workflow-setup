#!/usr/bin/env python3
"""Behavioral proof that result application is fenced and independently verified."""

from __future__ import annotations

import copy
import datetime as dt
import json
import multiprocessing
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
            "kind": "child_bound",
            "parent_session_id": "parent-7",
            "execution_id": execution_id,
            "attempt": 1,
            "generation": 1,
            "native_child_id": f"native-{execution_id}",
        },
        at("2026-08-09T20:00:10Z"),
    )
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_terminal",
            "parent_session_id": "parent-7",
            "execution_id": execution_id,
            "attempt": 1,
            "generation": 1,
            "native_child_id": f"native-{execution_id}",
            "terminal_event_id": f"terminal-{execution_id}",
            "terminal_reason": "completed",
            "result_digest": digest,
            "host_event_id": f"host:terminal:{execution_id}",
        },
        at("2026-08-09T20:05:00Z"),
    )
    return ledger


def running_ledger(activity_kind: str | None = "tool_completed") -> dict:
    ledger = ledger_api.new_ledger("parent-7")
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": "parent-7",
            "bead_id": "escapement-e3ai.2",
            "execution_id": "exec-alpha",
            "host": "codex",
            "agent_name": "worker",
            "dispatch_tool_use_id": "call-exec-alpha",
            "watchdog_id": "watch-exec-alpha",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-09T20:00:00Z"),
    )
    ledger_api.apply_event(
        ledger,
        {
            "kind": "child_bound",
            "parent_session_id": "parent-7",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "native_child_id": "native-exec-alpha",
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
            "native_child_id": "native-exec-alpha",
        },
        at("2026-08-09T20:00:20Z"),
    )
    if activity_kind is not None:
        ledger_api.apply_event(
            ledger,
            {
                "kind": "activity_completed",
                "parent_session_id": "parent-7",
                "execution_id": "exec-alpha",
                "attempt": 1,
                "generation": 1,
                "native_child_id": "native-exec-alpha",
                "activity_kind": activity_kind,
            },
            at("2026-08-09T20:04:00Z"),
        )
    return ledger


def persist(tmp_path: pathlib.Path, ledger: dict) -> pathlib.Path:
    path = tmp_path / "executions.json"
    path.write_text(json.dumps(ledger))
    path.chmod(0o600)
    return path


def loaded(path: pathlib.Path) -> dict:
    ledger = ledger_api.load_trusted(path, "parent-7")
    assert ledger is not None
    return ledger


def item(ledger: dict, execution_id: str = "exec-alpha") -> dict:
    return next(
        entry for entry in ledger["executions"] if entry["execution_id"] == execution_id
    )


def claim(
    path: pathlib.Path,
    owner: str = "applier-a",
    when: str = "2026-08-09T20:06:00Z",
    execution_id: str = "exec-alpha",
) -> dict:
    captured: dict = {}

    def mutation(ledger: dict) -> dict:
        claimed = ledger_api.claim_result_application(
            ledger,
            execution_id,
            at(when),
            owner,
            30,
            attempt=1,
            generation=1,
        )
        assert claimed is not None
        captured.update(claimed)
        return ledger

    ledger_api.mutate_atomic(path, mutation)
    claimed = captured
    assert claimed is not None
    return claimed


def apply_claim(
    path: pathlib.Path,
    claimed: dict,
    verify_outcome,
    apply,
    when: str = "2026-08-09T20:06:01Z",
    execution_id: str = "exec-alpha",
    clock=None,
) -> dict:
    return result_application.apply_verified_result(
        path,
        expected_parent="parent-7",
        execution_id=execution_id,
        attempt=1,
        generation=1,
        owner=claimed["owner"],
        claim_generation=claimed["claim_generation"],
        verify_outcome=verify_outcome,
        apply=apply,
        idempotency_key=f"execution:{execution_id}:attempt:1:generation:1",
        clock=clock or (lambda: at(when)),
    )


def _take_over_in_process(path: pathlib.Path, owner: str = "applier-b") -> None:
    def mutation(ledger: dict) -> dict:
        claimed = ledger_api.claim_result_application(
            ledger,
            "exec-alpha",
            at("2026-08-09T20:06:30Z"),
            owner,
            30,
            attempt=1,
            generation=1,
        )
        if claimed is None:
            raise RuntimeError("durable takeover was not acquired")
        return ledger

    ledger_api.mutate_atomic(path, mutation)


def test_terminal_and_digest_do_not_directly_prove_application(tmp_path) -> None:
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


def test_claim_alone_is_applying_not_applied(tmp_path) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    ledger = loaded(path)
    assert claimed["claim_generation"] == 1
    assert item(ledger)["result_application"]["state"] == "applying"
    assert item(ledger)["result_application"]["applied_at"] is None


def test_missing_outcome_proof_does_not_apply_or_mutate_business_state(
    tmp_path,
) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    external_calls: list[str] = []
    result = apply_claim(path, claimed, lambda _execution: None, external_calls.append)
    ledger = loaded(path)
    assert result == {"status": "verification_unresolved"}
    assert external_calls == []
    assert item(ledger)["result_application"]["state"] == "applying"
    assert item(ledger)["result_application"]["applied_at"] is None


def test_negative_post_apply_verification_cannot_mark_applied(tmp_path) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    external_calls: list[str] = []
    result = apply_claim(path, claimed, lambda _execution: False, external_calls.append)
    ledger = loaded(path)
    assert result == {"status": "outcome_not_observed"}
    assert external_calls == ["execution:exec-alpha:attempt:1:generation:1"]
    assert item(ledger)["result_application"]["state"] == "applying"


def test_verifier_exception_fails_closed_before_external_application(tmp_path) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    external_calls: list[str] = []

    def broken_verifier(_execution: dict) -> bool:
        raise RuntimeError("outcome service unavailable")

    result = apply_claim(path, claimed, broken_verifier, external_calls.append)
    ledger = loaded(path)
    assert result == {
        "status": "verification_error",
        "error": "outcome service unavailable",
    }
    assert external_calls == []
    assert item(ledger)["result_application"]["state"] == "applying"


def test_current_claim_applies_once_then_requires_positive_business_proof(
    tmp_path,
) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    externally_applied: set[str] = set()

    def verify(_execution: dict) -> bool:
        return "execution:exec-alpha:attempt:1:generation:1" in externally_applied

    def apply(key: str) -> None:
        externally_applied.add(key)

    result = apply_claim(path, claimed, verify, apply)
    ledger = loaded(path)
    assert result == {
        "status": "applied",
        "idempotency_key": "execution:exec-alpha:attempt:1:generation:1",
    }
    assert externally_applied == {"execution:exec-alpha:attempt:1:generation:1"}
    assert item(ledger)["result_application"]["state"] == "applied"
    assert item(ledger)["result_application"]["applied_at"] == "2026-08-09T20:06:01Z"


def test_negative_verification_cannot_apply_after_claim_expires(tmp_path) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    current_time = [at("2026-08-09T20:06:01Z")]
    verifier_calls: list[str] = []
    apply_calls: list[str] = []

    def verify(_execution: dict) -> bool:
        verifier_calls.append("negative")
        current_time[0] = at("2026-08-09T20:06:31Z")
        return False

    result = apply_claim(
        path,
        claimed,
        verify,
        apply_calls.append,
        clock=lambda: current_time[0],
    )

    assert result == {"status": "stale_claim"}
    assert verifier_calls == ["negative"]
    assert apply_calls == []
    application = item(loaded(path))["result_application"]
    assert application["state"] == "applying"
    assert application["claim"]["owner"] == "applier-a"
    assert application["applied_at"] is None


def test_negative_verification_cannot_apply_after_durable_takeover(tmp_path) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    verifier_calls: list[str] = []
    apply_calls: list[str] = []
    process_exit_codes: list[int | None] = []

    def verify(_execution: dict) -> bool:
        verifier_calls.append("negative")
        if len(verifier_calls) == 1:
            process = multiprocessing.get_context("fork").Process(
                target=_take_over_in_process,
                args=(path,),
            )
            process.start()
            process.join(3)
            process_exit_codes.append(process.exitcode)
        return False

    result = apply_claim(
        path,
        claimed,
        verify,
        apply_calls.append,
        clock=lambda: at("2026-08-09T20:06:01Z"),
    )

    assert process_exit_codes == [0], "verifier must run outside the ledger flock"
    assert result == {"status": "stale_claim"}
    assert verifier_calls == ["negative"]
    assert apply_calls == []
    application = item(loaded(path))["result_application"]
    assert application["state"] == "applying"
    assert application["claim"]["owner"] == "applier-b"
    assert application["claim_generation"] == 2
    assert application["applied_at"] is None


def test_negative_verification_cannot_apply_after_same_owner_takeover(
    tmp_path,
) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    verifier_calls: list[str] = []
    apply_calls: list[str] = []
    process_exit_codes: list[int | None] = []

    def verify(_execution: dict) -> bool:
        verifier_calls.append("negative")
        if len(verifier_calls) == 1:
            process = multiprocessing.get_context("fork").Process(
                target=_take_over_in_process,
                args=(path, "applier-a"),
            )
            process.start()
            process.join(3)
            process_exit_codes.append(process.exitcode)
        return False

    result = apply_claim(
        path,
        claimed,
        verify,
        apply_calls.append,
        clock=lambda: at("2026-08-09T20:06:01Z"),
    )

    assert process_exit_codes == [0]
    assert result == {"status": "stale_claim"}
    assert verifier_calls == ["negative"]
    assert apply_calls == []
    application = item(loaded(path))["result_application"]
    assert application["state"] == "applying"
    assert application["claim"]["owner"] == "applier-a"
    assert application["claim_generation"] == 2
    assert application["claim"]["claim_generation"] == 2
    assert application["applied_at"] is None


def test_completion_rechecks_fresh_time_after_verifier_crosses_claim_expiry(
    tmp_path,
) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    current_time = [at("2026-08-09T20:06:01Z")]

    def verify(_execution: dict) -> bool:
        current_time[0] = at("2026-08-09T20:06:31Z")
        return True

    result = apply_claim(
        path,
        claimed,
        verify,
        lambda _key: pytest.fail("already-observed outcome must not be applied"),
        clock=lambda: current_time[0],
    )

    assert result == {"status": "stale_claim"}
    application = item(loaded(path))["result_application"]
    assert application["state"] == "applying"
    assert application["claim"]["owner"] == "applier-a"
    assert application["applied_at"] is None


def test_completion_rechecks_fresh_time_after_apply_crosses_claim_expiry(
    tmp_path,
) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    current_time = [at("2026-08-09T20:06:01Z")]
    effects: list[str] = []

    def verify(_execution: dict) -> bool:
        return bool(effects)

    def apply(key: str) -> None:
        effects.append(key)
        current_time[0] = at("2026-08-09T20:06:31Z")

    result = apply_claim(path, claimed, verify, apply, clock=lambda: current_time[0])

    assert effects == ["execution:exec-alpha:attempt:1:generation:1"]
    assert result == {"status": "stale_claim"}
    application = item(loaded(path))["result_application"]
    assert application["state"] == "applying"
    assert application["claim"]["owner"] == "applier-a"
    assert application["applied_at"] is None


def test_completion_reloads_durable_takeover_after_external_callback(tmp_path) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    process_exit_codes: list[int | None] = []
    current_time = [at("2026-08-09T20:06:01Z")]

    def verify(_execution: dict) -> bool:
        process = multiprocessing.get_context("fork").Process(
            target=_take_over_in_process,
            args=(path,),
        )
        process.start()
        process.join(3)
        process_exit_codes.append(process.exitcode)
        current_time[0] = at("2026-08-09T20:06:31Z")
        return True

    result = apply_claim(
        path,
        claimed,
        verify,
        lambda _key: pytest.fail("already-observed outcome must not be applied"),
        clock=lambda: current_time[0],
    )

    assert process_exit_codes == [0], "callbacks must run outside the ledger flock"
    assert result == {"status": "stale_claim"}
    application = item(loaded(path))["result_application"]
    assert application["state"] == "applying"
    assert application["claim"]["owner"] == "applier-b"
    assert application["claim_generation"] == 2
    assert application["applied_at"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "applying_on_running",
        "applying_without_result",
        "terminal_without_native_child",
        "terminal_without_terminal_time",
        "running_with_terminal_evidence",
        "applied_without_result",
    ],
)
def test_invalid_durable_cross_state_fails_before_external_callbacks(
    tmp_path, mutation
) -> None:
    path = persist(tmp_path, terminal_ledger())
    claimed = claim(path)
    invalid = loaded(path)
    execution = item(invalid)
    if mutation == "applying_on_running":
        execution["state"] = "running"
        execution["terminal_at"] = None
        execution["terminal_reason"] = None
        execution["terminal_event_id"] = None
    elif mutation == "applying_without_result":
        execution["result_digest"] = None
    elif mutation == "terminal_without_native_child":
        execution["native_child_id"] = None
    elif mutation == "terminal_without_terminal_time":
        execution["terminal_at"] = None
    elif mutation == "running_with_terminal_evidence":
        execution["state"] = "running"
        execution["result_application"] = {
            "state": "unapplied",
            "claim": None,
            "claim_generation": 0,
            "idempotency_key": "execution:exec-alpha:attempt:1:generation:1",
            "applied_at": None,
        }
    elif mutation == "applied_without_result":
        execution["result_digest"] = None
        execution["result_application"]["state"] = "applied"
        execution["result_application"]["claim"] = None
        execution["result_application"]["applied_at"] = "2026-08-09T20:07:00Z"
    path.write_text(json.dumps(invalid))
    path.chmod(0o600)
    before = json.loads(path.read_text())
    callback_calls: list[str] = []

    result = apply_claim(
        path,
        claimed,
        lambda _execution: callback_calls.append("verify") or True,
        lambda _key: callback_calls.append("apply"),
    )

    assert result == {"status": "unresolved_ledger"}
    assert callback_calls == []
    assert json.loads(path.read_text()) == before


@pytest.mark.parametrize("activity_kind", [None, "checkpoint", "tool_completed"])
def test_valid_running_execution_is_resolved_but_never_applicable(
    tmp_path, activity_kind
) -> None:
    path = persist(tmp_path, running_ledger(activity_kind))
    before = loaded(path)
    callback_calls: list[str] = []

    result = result_application.apply_verified_result(
        path,
        expected_parent="parent-7",
        execution_id="exec-alpha",
        attempt=1,
        generation=1,
        owner="not-a-claimant",
        claim_generation=1,
        verify_outcome=lambda _execution: callback_calls.append("verify") or True,
        apply=lambda _key: callback_calls.append("apply"),
        idempotency_key="execution:exec-alpha:attempt:1:generation:1",
        clock=lambda: at("2026-08-09T20:06:01Z"),
    )

    assert result == {"status": "stale_claim"}
    assert callback_calls == []
    assert loaded(path) == before


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("native_child_id", None),
        ("native_child_id", ""),
        ("started_at", None),
        ("last_activity_at", None),
        ("last_activity_kind", None),
        ("last_activity_kind", "status_polled"),
        ("last_activity_kind", "tool_started"),
        ("last_activity_kind", "semantic_annotation"),
        ("started_at", "2026-08-09T20:04:01Z"),
        ("idle_deadline", "2026-08-09T20:18:59Z"),
        ("idle_deadline", "2026-08-09T20:19:01Z"),
    ],
)
def test_invalid_running_state_fails_public_gate_before_callbacks(
    tmp_path, field, invalid_value
) -> None:
    ledger = running_ledger()
    item(ledger)[field] = invalid_value
    path = persist(tmp_path, ledger)
    before = json.loads(path.read_text())
    callback_calls: list[str] = []

    result = result_application.apply_verified_result(
        path,
        expected_parent="parent-7",
        execution_id="exec-alpha",
        attempt=1,
        generation=1,
        owner="not-a-claimant",
        claim_generation=1,
        verify_outcome=lambda _execution: callback_calls.append("verify") or True,
        apply=lambda _key: callback_calls.append("apply"),
        idempotency_key="execution:exec-alpha:attempt:1:generation:1",
        clock=lambda: at("2026-08-09T20:06:01Z"),
    )

    assert result == {"status": "unresolved_ledger"}
    assert callback_calls == []
    assert json.loads(path.read_text()) == before


def test_expired_claim_takeover_fences_stale_owner(tmp_path) -> None:
    path = persist(tmp_path, terminal_ledger())
    stale = claim(path)
    current = claim(path, "applier-b", "2026-08-09T20:06:30Z")
    assert current["claim_generation"] == 2
    verifier_calls: list[str] = []
    before_stale_call = copy.deepcopy(loaded(path))
    stale_result = apply_claim(
        path,
        stale,
        lambda _execution: verifier_calls.append("stale") or True,
        lambda _key: None,
        "2026-08-09T20:06:31Z",
    )
    assert stale_result == {"status": "stale_claim"}
    assert verifier_calls == []
    assert loaded(path) == before_stale_call

    current_verifier_calls: list[str] = []
    current_result = apply_claim(
        path,
        current,
        lambda _execution: current_verifier_calls.append("current") or True,
        lambda _key: pytest.fail("already-observed outcome must not be applied again"),
        "2026-08-09T20:06:31Z",
    )
    assert current_result["status"] == "applied"
    assert current_verifier_calls == ["current"]


def test_expired_claim_takeover_cannot_launder_missing_business_proof(tmp_path) -> None:
    path = persist(tmp_path, terminal_ledger())
    claim(path, "applier-a", "2026-08-09T20:06:00Z")
    takeover = claim(path, "applier-b", "2026-08-09T20:06:30Z")
    apply_calls: list[str] = []

    result = apply_claim(
        path,
        takeover,
        lambda _execution: None,
        apply_calls.append,
        "2026-08-09T20:06:31Z",
    )

    assert result == {"status": "verification_unresolved"}
    assert apply_calls == []
    application = item(loaded(path))["result_application"]
    assert application["state"] == "applying"
    assert application["claim"]["owner"] == "applier-b"
    assert application["claim_generation"] == 2
    assert application["applied_at"] is None


def test_crash_after_external_effect_recovers_without_duplicate_application(
    tmp_path,
) -> None:
    class SimulatedProcessDeath(BaseException):
        pass

    path = persist(tmp_path, terminal_ledger())
    first = claim(path)
    outcome_path = tmp_path / "business-outcome.txt"

    def verify(_execution: dict) -> bool:
        return outcome_path.exists()

    def apply_then_die(key: str) -> None:
        with outcome_path.open("a") as effect_log:
            effect_log.write(f"{key}\n")
        raise SimulatedProcessDeath()

    with pytest.raises(SimulatedProcessDeath):
        apply_claim(path, first, verify, apply_then_die)
    assert outcome_path.read_text().splitlines() == [
        "execution:exec-alpha:attempt:1:generation:1"
    ]
    stale_snapshot = loaded(path)
    assert item(stale_snapshot)["result_application"]["state"] == "applying"

    takeover = claim(path, "applier-b", "2026-08-09T20:06:30Z")
    stale_result = apply_claim(
        path,
        first,
        lambda _execution: True,
        lambda _key: pytest.fail("stale snapshot must not apply"),
        "2026-08-09T20:06:31Z",
    )
    assert stale_result == {"status": "stale_claim"}
    assert item(loaded(path))["result_application"]["claim"]["owner"] == "applier-b"
    takeover_verifications: list[str] = []

    def verify_takeover(execution: dict) -> bool:
        takeover_verifications.append(execution["execution_id"])
        return verify(execution)

    result = apply_claim(
        path,
        takeover,
        verify_takeover,
        lambda _key: pytest.fail(
            "takeover must observe the existing effect before retry"
        ),
        "2026-08-09T20:06:31Z",
    )
    assert result["status"] == "applied"
    assert takeover_verifications == ["exec-alpha"]
    assert outcome_path.read_text().splitlines() == [
        "execution:exec-alpha:attempt:1:generation:1"
    ]
    assert item(loaded(path))["result_application"]["state"] == "applied"


def test_equal_digests_on_distinct_executions_have_distinct_application_identity(
    tmp_path,
) -> None:
    ledger = terminal_ledger("exec-alpha", "sha256:identical")
    second = terminal_ledger("exec-beta", "sha256:identical")["executions"][0]
    ledger["executions"].append(second)
    path = persist(tmp_path, ledger)
    keys: list[str] = []

    for execution_id, owner, expected_key in [
        ("exec-alpha", "a", "execution:exec-alpha:attempt:1:generation:1"),
        ("exec-beta", "b", "execution:exec-beta:attempt:1:generation:1"),
    ]:
        claimed = claim(path, owner, execution_id=execution_id)
        assert claimed is not None
        observed: set[str] = set()
        result_application.apply_verified_result(
            path,
            expected_parent="parent-7",
            execution_id=execution_id,
            attempt=1,
            generation=1,
            owner=owner,
            claim_generation=claimed["claim_generation"],
            verify_outcome=lambda _execution, seen=observed: bool(seen),
            apply=lambda key, seen=observed: (keys.append(key), seen.add(key)),
            idempotency_key=expected_key,
            clock=lambda: at("2026-08-09T20:06:01Z"),
        )
    assert keys == [
        "execution:exec-alpha:attempt:1:generation:1",
        "execution:exec-beta:attempt:1:generation:1",
    ]
