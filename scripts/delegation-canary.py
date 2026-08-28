#!/usr/bin/env python3
"""Prove the installed Claude delegation lifecycle from structured records."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
UTC = dt.timezone.utc
DEPENDENCY_RE = re.compile(r"\bDEPENDENCY-[A-Za-z0-9-]+\b")
class CanaryFailure(Exception):
    """One externally visible canary invariant was not proven."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
def _host_version(claude_bin: Path, env: dict[str, str], timeout: int) -> str:
    result = subprocess.run(
        [str(claude_bin), "--version"],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", result.stdout)
    if result.returncode != 0 or match is None:
        raise CanaryFailure("host_capability_unresolved")
    return match.group(1)
def _records(stdout: str) -> list[dict]:
    records: list[dict] = []
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records
def _run_claude(
    claude_bin: Path, candidate_root: Path, repo: Path, config: Path,
    harness: Path, session_id: str, prompt: str, env: dict[str, str], timeout: int,
) -> list[dict]:
    run_env = {
        **env,
        "HARNESS_ROOT": str(harness),
        "CONTINUATION_HARNESS_HOME": str(harness),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DISABLE_AUTOUPDATER": "1",
    }
    result = subprocess.run(
        [
            str(claude_bin),
            "--print",
            "--verbose",
            "--output-format", "stream-json",
            "--forward-subagent-text",
            "--include-hook-events",
            "--no-session-persistence",
            "--permission-mode", "bypassPermissions",
            "--dangerously-skip-permissions",
            "--setting-sources", "",
            "--settings", str(config / "settings.json"),
            "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}',
            "--plugin-dir", str(candidate_root),
            "--session-id", session_id,
            prompt,
        ],
        cwd=repo,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    (config / f"{session_id}.stdout.jsonl").write_text(result.stdout, encoding="utf-8")
    (config / f"{session_id}.stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise CanaryFailure("native_agent_first_attempt_failed")
    records = _records(result.stdout)
    if not records:
        raise CanaryFailure("host_capability_unresolved")
    return records
def _tool_content(record: dict) -> dict | None:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list) or len(content) != 1:
        return None
    item = content[0]
    return item if isinstance(item, dict) else None
def _dispatches(records: list[dict]) -> list[tuple[int, dict, dict]]:
    found = []
    for index, record in enumerate(records):
        item = _tool_content(record)
        tool_input = item.get("input") if isinstance(item, dict) else None
        if (
            isinstance(item, dict)
            and item.get("type") == "tool_use"
            and item.get("name") == "Agent"
            and isinstance(item.get("id"), str)
            and isinstance(tool_input, dict)
            and tool_input.get("run_in_background") is True
        ):
            found.append((index, record, item))
    return found
def _session_and_version(records: list[dict]) -> tuple[str, str]:
    init = next(
        (
            record
            for record in records
            if record.get("type") == "system" and record.get("subtype") == "init"
        ),
        None,
    )
    if not isinstance(init, dict):
        raise CanaryFailure("host_capability_unresolved")
    session_id = init.get("session_id")
    version = init.get("claude_code_version")
    if not isinstance(session_id, str) or not isinstance(version, str):
        raise CanaryFailure("host_capability_unresolved")
    return session_id, version
def _root_result(records: list[dict]) -> str:
    results = [
        record.get("result")
        for record in records
        if record.get("type") == "result"
        and record.get("subtype") == "success"
        and isinstance(record.get("result"), str)
    ]
    return results[-1] if results else ""
def _spawn_witness(records: list[dict], tool_id: str) -> tuple[str, int, int] | None:
    starts = [
        (index, record)
        for index, record in enumerate(records)
        if record.get("type") == "system"
        and record.get("subtype") == "task_started"
        and record.get("tool_use_id") == tool_id
        and isinstance(record.get("task_id"), str)
        and record.get("is_backgrounded") is True
        and record.get("task_type") == "local_agent"
    ]
    terminals = [
        (index, record)
        for index, record in enumerate(records)
        if record.get("type") == "system"
        and record.get("subtype") == "task_notification"
        and record.get("tool_use_id") == tool_id
        and record.get("status") == "completed"
    ]
    if len(starts) != 1 or len(terminals) != 1:
        return None
    child_id = starts[0][1]["task_id"]
    if terminals[0][1].get("task_id") != child_id:
        return None
    return child_id, starts[0][0], terminals[0][0]
def _write_mode(thread: Path, session_id: str, repo: Path) -> None:
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
def _register_dispatches(records: list[dict], ledger_path: Path, hook) -> None:
    for _index, record, item in _dispatches(records):
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
def _apply_transcript(transcript: Path, ledger_path: Path, session_id: str, api) -> dict:
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
def _apply_results(ledger_path: Path, session_id: str, terminal_ids: set[str], api) -> dict:
    current = api["store"].load_trusted(ledger_path, session_id)
    if current is None:
        raise CanaryFailure("managed_completion_unresolved")
    for execution in current["executions"]:
        if execution["state"] != "terminal":
            continue
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
        result = api["application"].apply_verified_result(
            ledger_path, expected_parent=session_id,
            execution_id=execution["execution_id"], attempt=1, generation=1,
            owner=captured["owner"], claim_generation=captured["claim_generation"],
            verify_outcome=lambda item: item.get("terminal_event_id") in terminal_ids,
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
def _load_api(candidate_root: Path) -> dict[str, object]:
    sys.path.insert(0, str(candidate_root / "harness" / "bin"))
    import claude_agent_lifecycle as adapter
    import delegation_hook as hook
    import execution_ledger as ledger
    import execution_store as store
    import result_application as application
    import would_block_stop as completion
    return {"adapter": adapter, "hook": hook, "ledger": ledger, "store": store,
            "application": application, "completion": completion}
def _verify_unmanaged(records: list[dict], harness: Path, version: str) -> dict:
    _session, stream_version = _session_and_version(records)
    dispatches = _dispatches(records)
    if stream_version != version or len(dispatches) < 1:
        raise CanaryFailure("native_agent_first_attempt_failed")
    if _spawn_witness(records, dispatches[0][2]["id"]) is None:
        raise CanaryFailure("native_agent_first_attempt_failed")
    if "UNMANAGED_OK" not in _root_result(records):
        raise CanaryFailure("native_agent_first_attempt_failed")
    state_created = any(harness.rglob("executions.json"))
    if state_created:
        raise CanaryFailure("unmanaged_state_created")
    return {"first_attempt": True, "escapement_state_created": False}
def _peer_tokens(records: list[dict], terminal: list[dict], conclusion: str) -> set[str]:
    by_name = {item["agent_name"]: item for item in terminal}
    sender = by_name.get("canary-child-1")
    recipient = by_name.get("canary-child-2")
    if sender is None or recipient is None:
        return set()
    recipient_summary = next(
        (
            record.get("summary", "")
            for record in records
            if record.get("type") == "system"
            and record.get("subtype") == "task_notification"
            and record.get("tool_use_id") == recipient["dispatch_tool_use_id"]
            and record.get("task_id") == recipient["native_child_id"]
            and record.get("status") == "completed"
        ),
        "",
    )
    requests: dict[str, set[str]] = {}
    for record in records:
        if record.get("parent_tool_use_id") != sender["dispatch_tool_use_id"]:
            continue
        item = _tool_content(record)
        if not isinstance(item, dict):
            continue
        if item.get("type") == "tool_use" and item.get("name") == "SendMessage":
            tool_input = item.get("input")
            if not isinstance(tool_input, dict) or tool_input.get("recipient") != "canary-child-2":
                continue
            body = tool_input.get("message")
            tokens = set(DEPENDENCY_RE.findall(body)) if isinstance(body, str) else set()
            if isinstance(item.get("id"), str) and len(tokens) == 1:
                requests[item["id"]] = tokens
            continue
        tool_id = item.get("tool_use_id")
        if item.get("type") != "tool_result" or tool_id not in requests:
            continue
        content = item.get("content")
        text = content[0].get("text") if isinstance(content, list) and content else None
        try:
            acknowledgement = json.loads(text) if isinstance(text, str) else None
        except json.JSONDecodeError:
            acknowledgement = None
        pin = acknowledgement.get("pin") if isinstance(acknowledgement, dict) else None
        if (
            isinstance(acknowledgement, dict)
            and acknowledgement.get("success") is True
            and isinstance(pin, dict)
            and pin.get("id") == recipient["native_child_id"]
            and pin.get("name") == recipient["agent_name"]
        ):
            return {
                token
                for token in requests[tool_id]
                if token in recipient_summary and token in conclusion
            }
    return set()
def _verify_managed(
    records: list[dict], scratch: Path, repo: Path, version: str, api
) -> dict:
    session_id, stream_version = _session_and_version(records)
    if stream_version != version:
        raise CanaryFailure("host_capability_unresolved")
    thread = scratch / "harness" / "threads" / session_id
    _write_mode(thread, session_id, repo)
    ledger_path = thread / "executions.json"
    _register_dispatches(records, ledger_path, api["hook"])
    transcript = scratch / "managed-stream.jsonl"
    transcript.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    ledger = _apply_transcript(transcript, ledger_path, session_id, api)
    terminal = [item for item in ledger["executions"] if item["state"] == "terminal"]
    aborted = [item for item in ledger["executions"] if item["state"] == "aborted"]
    child_ids = [item["native_child_id"] for item in terminal]
    if len(child_ids) != len(set(child_ids)) or any(value is None for value in child_ids):
        raise CanaryFailure("native_child_identity_unresolved")
    intervals = []
    dispatch_by_tool = {item[2]["id"]: item[0] for item in _dispatches(records)}
    for item in terminal:
        witness = _spawn_witness(records, item["dispatch_tool_use_id"])
        if witness is None:
            raise CanaryFailure("managed_completion_unresolved")
        intervals.append((dispatch_by_tool[item["dispatch_tool_use_id"]], witness[2]))
    if len(intervals) == 3 and max(start for start, _end in intervals) >= min(
        end for _start, end in intervals
    ):
        raise CanaryFailure("children_do_not_overlap")
    if not _peer_tokens(records, terminal, _root_result(records)):
        raise CanaryFailure("peer_dependency_unproven")
    if len(terminal) != 3 or len(aborted) != 1:
        raise CanaryFailure("managed_completion_unresolved")
    terminal_ids = {
        record["uuid"]
        for record in records
        if record.get("type") == "system"
        and record.get("subtype") == "task_notification"
        and record.get("status") == "completed"
        and isinstance(record.get("uuid"), str)
    }
    ledger = _apply_results(ledger_path, session_id, terminal_ids, api)
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
def _prepare_scratch(root: Path) -> tuple[Path, Path, Path]:
    config = root / "config"
    harness = root / "harness"
    repo = root / "repo"
    for path in (config, harness, repo, repo / ".beads"):
        path.mkdir(parents=True, exist_ok=True)
    settings = config / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    settings.chmod(0o600)
    (repo / ".beads" / "config.yaml").write_text(
        "issue-prefix: canary\n", encoding="utf-8"
    )
    return config, harness, repo
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--expected-version")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args(argv)
    try:
        source_plugin = args.source_root / "plugins" / "escapement-claude"
        if _files(source_plugin) != _files(args.candidate_root):
            raise CanaryFailure("installed_surface_drift")
        config, harness, repo = _prepare_scratch(args.scratch_root)
        environment = os.environ.copy()
        host_version = _host_version(Path(args.claude_bin), environment, args.timeout)
        if args.expected_version and host_version != args.expected_version:
            raise CanaryFailure("host_capability_unresolved")
        unmanaged_session = str(uuid.uuid4())
        unmanaged = _run_claude(
            Path(args.claude_bin),
            args.candidate_root,
            repo,
            config,
            harness,
            unmanaged_session,
            "Use Agent exactly once in the background on the first attempt. Name it "
            "canary-unmanaged, use subagent_type general-purpose, ask it to return "
            "UNMANAGED_CHILD_OK, wait for its terminal notification, then answer "
            "UNMANAGED_OK. Do not call any other tool.",
            environment,
            args.timeout,
        )
        unmanaged_result = _verify_unmanaged(unmanaged, harness, host_version)

        managed_session = str(uuid.uuid4())
        _write_mode(
            harness / "threads" / managed_session, managed_session, repo
        )
        dependency = f"DEPENDENCY-{uuid.uuid4().hex[:12].upper()}"
        managed = _run_claude(
            Path(args.claude_bin),
            args.candidate_root,
            repo,
            config,
            harness,
            managed_session,
            "Launch exactly three Agent calls in the background before waiting for any "
            "result. Name them canary-child-1, canary-child-2, canary-child-3 and use "
            "subagent_type general-purpose. Tell child 1 to sleep 8 seconds with Bash, "
            f"SendMessage the exact token {dependency} to canary-child-2, then return "
            "it. Tell child 2 to sleep 12 seconds, then return the dependency token it "
            "received by peer message. Tell child 3 to sleep 12 seconds and return its "
            "name. Wait for all "
            "three terminal notifications. Then attempt one background Agent named "
            "xncx-no-spawn with subagent_type nonexistent-agent-type-xyz so the native "
            "dispatch aborts before binding. Conclude with CANARY_COMPLETE and include "
            "the exact dependency token delivered by child 1. Do not call other tools.",
            environment,
            args.timeout,
        )
        managed_result = _verify_managed(
            managed, args.scratch_root, repo, host_version, _load_api(args.candidate_root)
        )
        output = {"status": "pass", "host_version": host_version,
                  "unmanaged": unmanaged_result, "managed": managed_result}
        print(json.dumps(output, sort_keys=True))
        return 0
    except (CanaryFailure, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        reason = exc.reason if isinstance(exc, CanaryFailure) else "canary_runtime_failed"
        print(json.dumps({"status": "fail", "reason": reason}, sort_keys=True))
        return 1
if __name__ == "__main__":
    raise SystemExit(main())
