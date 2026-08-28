#!/usr/bin/env python3
"""Public lifecycle application for the isolated delegation canary."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from delegation_canary_evidence import (
    CanaryFailure,
    dispatches,
    terminal_record,
    verify_candidate_plugin,
    verify_overlap,
    verify_peer_dependency,
)

UTC = dt.timezone.utc


def write_mode(thread: Path, session_id: str, repo: Path) -> None:
    thread.mkdir(parents=True, exist_ok=True)
    path = thread / "session_mode.json"
    if path.exists():
        return
    path.write_text(
        json.dumps(
            {
                "mode": "task",
                "repo_cwd": str(repo),
                "task_id": "canary-root",
                "parent_id": None,
                "entered_at": dt.datetime.now(UTC).isoformat(),
                "session_id": session_id,
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def register_dispatches(records: list[dict], ledger_path: Path, hook) -> None:
    for _index, record, item in dispatches(records):
        tool_input = item["input"]
        result = hook.pre_tool(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
                "session_id": record.get("session_id"),
                "tool_use_id": item["id"],
                "tool_input": tool_input,
            },
            None,
            ledger_path,
        )
        if result.get("reason") != "dispatch_registered":
            raise CanaryFailure("managed_completion_unresolved")


def apply_transcript(
    transcript: Path, ledger_path: Path, session_id: str, api
) -> dict:
    for _ in range(12):
        current = api["store"].load_trusted(ledger_path, session_id)
        if current is None:
            raise CanaryFailure("managed_completion_unresolved")
        events = api["adapter"].observe_transcript(transcript, current)
        if not events:
            return current

        def apply(ledger: dict) -> dict:
            now = dt.datetime.now(UTC)
            for offset, event in enumerate(events):
                api["ledger"].apply_event(
                    ledger, event, now + dt.timedelta(microseconds=offset)
                )
            return ledger

        api["store"].mutate_atomic(ledger_path, apply)
    raise CanaryFailure("managed_completion_unresolved")


def apply_results(
    ledger_path: Path, session_id: str, records: list[dict], api
) -> dict:
    current = api["store"].load_trusted(ledger_path, session_id)
    if current is None:
        raise CanaryFailure("managed_completion_unresolved")
    terminal = [item for item in current["executions"] if item["state"] == "terminal"]
    if any(terminal_record(records, execution) is None for execution in terminal):
        raise CanaryFailure("managed_completion_unresolved")
    for execution in terminal:
        captured: dict = {}

        def claim(ledger: dict) -> dict:
            value = api["ledger"].claim_result_application(
                ledger,
                execution["execution_id"],
                dt.datetime.now(UTC),
                "delegation-canary",
                60,
                attempt=1,
                generation=1,
            )
            if value is None:
                raise ValueError("result application claim unavailable")
            captured.update(value)
            return ledger

        api["store"].mutate_atomic(ledger_path, claim)

        def verify(item: dict, expected=execution) -> bool:
            return terminal_record(records, item) is not None and all(
                item.get(key) == expected.get(key)
                for key in (
                    "execution_id",
                    "dispatch_tool_use_id",
                    "native_child_id",
                    "terminal_event_id",
                )
            )

        result = api["application"].apply_verified_result(
            ledger_path,
            expected_parent=session_id,
            execution_id=execution["execution_id"],
            attempt=1,
            generation=1,
            owner=captured["owner"],
            claim_generation=captured["claim_generation"],
            verify_outcome=verify,
            apply=lambda _key: None,
            idempotency_key=execution["result_application"]["idempotency_key"],
            clock=lambda: dt.datetime.now(UTC),
        )
        if result.get("status") != "applied":
            raise CanaryFailure("managed_completion_unresolved")
    final = api["store"].load_trusted(ledger_path, session_id)
    if final is None:
        raise CanaryFailure("managed_completion_unresolved")
    return final


def load_api(candidate_root: Path) -> dict[str, object]:
    sys.path.insert(0, str(candidate_root / "harness" / "bin"))
    import claude_agent_lifecycle as adapter
    import delegation_hook as hook
    import execution_ledger as ledger
    import execution_store as store
    import result_application as application
    import would_block_stop as completion

    return {
        "adapter": adapter,
        "hook": hook,
        "ledger": ledger,
        "store": store,
        "application": application,
        "completion": completion,
    }


def verify_managed(
    records: list[dict], scratch: Path, repo: Path, version: str,
    candidate_root: Path, api,
) -> dict:
    from delegation_canary_evidence import session_and_version

    session_id, stream_version = session_and_version(records)
    if stream_version != version:
        raise CanaryFailure("host_capability_unresolved")
    verify_candidate_plugin(records, candidate_root)
    thread = scratch / "harness" / "threads" / session_id
    write_mode(thread, session_id, repo)
    ledger_path = thread / "executions.json"
    register_dispatches(records, ledger_path, api["hook"])
    transcript = scratch / "managed-stream.jsonl"
    transcript.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    ledger = apply_transcript(transcript, ledger_path, session_id, api)
    terminal = [item for item in ledger["executions"] if item["state"] == "terminal"]
    aborted = [item for item in ledger["executions"] if item["state"] == "aborted"]
    child_ids = [item["native_child_id"] for item in terminal]
    if len(child_ids) != len(set(child_ids)) or any(value is None for value in child_ids):
        raise CanaryFailure("native_child_identity_unresolved")
    if len(terminal) != 3 or len(aborted) != 1:
        raise CanaryFailure("managed_completion_unresolved")
    verify_overlap(records, terminal)
    verify_peer_dependency(records, terminal)
    ledger = apply_results(ledger_path, session_id, records, api)
    decision = api["completion"].execution_stop_decision(
        "closed", ledger, None, [], dt.datetime.now(UTC)
    )
    if decision != ("allow", "delegated_outcome_complete"):
        raise CanaryFailure("managed_completion_unresolved")
    return {
        "distinct_native_children": len(set(child_ids)),
        "overlap_proven": True,
        "peer_dependency_proven": True,
        "terminal_count": len(terminal),
        "abort_count": len(aborted),
        "completion_decision": list(decision),
    }
