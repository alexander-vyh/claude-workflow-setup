#!/usr/bin/env python3
"""Translate Claude Agent completion events into delegated-execution evidence.

Split from `delegation_hook` because registration and completion are different
jobs: registration answers a permission question at dispatch time, completion
answers no question at all and only writes evidence.

Everything here is constrained by the real host payloads escapement-g27c
captured (Claude Code 2.1.248) at
`harness/tests/fixtures/agent_dispatch_hook_payloads.json`:

- Agent PostToolUse is the ONLY event carrying the dispatch's `tool_use_id` and
  the child's `agentId` together, so it is the binding event.
- The order of PostToolUse and SubagentStop is dispatch-mode dependent, so
  binding must be order-tolerant.
- `tool_use_id` is absent from the subagent events; `agent_id` is the only join.
- A backgrounded PostToolUse is a launch receipt, not a verdict.

No handler here raises into the host. An observation failure leaves the
execution active, where the deadline and recovery paths already cover it;
taking the session down instead would be a worse answer to a worse problem.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import pathlib

from execution_cancellation import cancel_failed_dispatch
from execution_ledger import apply_event
from execution_store import mutate_atomic

UTC = dt.timezone.utc


def _iso_now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _terminal_event_id(route: str, key: str) -> str:
    """Name which observation route supplied the verdict, deterministically.

    Deterministic so a re-delivered event dedupes through `apply_event`'s own
    idempotency check, and route-identifying so the ledger records whether a
    verdict came from the Agent result or from SubagentStop. Only one route
    ever terminalizes a given dispatch — which one depends on the dispatch
    mode — and both handlers refuse an already-terminal execution first.
    """
    return f"{route}:{key}"


def _event(ledger: dict, item: dict, kind: str, child_id: str, **extra) -> dict:
    return {
        "kind": kind,
        "parent_session_id": ledger["parent_session_id"],
        "execution_id": item["execution_id"],
        "attempt": item["attempt"],
        "generation": item["generation"],
        "native_child_id": child_id,
        **extra,
    }


def _by_dispatch(ledger: dict, tool_use_id: str) -> dict:
    matches = [
        item
        for item in ledger.get("executions", [])
        if item.get("dispatch_tool_use_id") == tool_use_id
    ]
    if len(matches) != 1:
        raise ValueError("dispatch tool identity is unresolved")
    return matches[0]


def _by_child(ledger: dict, child_id: str) -> dict | None:
    """Resolve by native child id, the only join the subagent events carry.

    Returns None rather than raising when nothing is bound yet: under a
    foreground dispatch SubagentStop fires BEFORE the PostToolUse that binds
    the child, and that ordering is normal, not an error.
    """
    matches = [
        item
        for item in ledger.get("executions", [])
        if item.get("native_child_id") == child_id
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _reply_text(tool_response: dict) -> str:
    """Read the child's reply only from the named reply field.

    `tool_response.prompt` and `.description` echo what the DISPATCHER asked
    for and are populated before the child has produced anything, so a
    substring search over the payload would happily digest the parent's own
    instructions and call it the child's result.
    """
    content = tool_response.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        entry["text"]
        for entry in content
        if isinstance(entry, dict)
        and entry.get("type") == "text"
        and isinstance(entry.get("text"), str)
    )


def post_tool(payload: dict, ledger_path) -> dict:
    """Bind the native child at Agent PostToolUse, and terminalize if it proves it.

    Captured in escapement-g27c: a BACKGROUNDED dispatch's PostToolUse is a
    launch receipt (`status: async_launched`, `content: null`, single-digit
    `duration_ms`). It proves identity and liveness but NOT completion, and
    reading a verdict out of it would fabricate one. A FOREGROUND dispatch's
    PostToolUse arrives after the child is done (`status: completed`, with the
    reply in `content`), so it proves termination too.
    """
    if (
        not isinstance(payload, dict)
        or payload.get("hook_event_name") != "PostToolUse"
        or payload.get("tool_name") != "Agent"
    ):
        return {"status": "ignored", "reason": "unmanaged_native_agent"}
    session_id = payload.get("session_id")
    tool_use_id = payload.get("tool_use_id")
    tool_response = payload.get("tool_response")
    if not isinstance(tool_response, dict):
        return {"status": "unresolved", "reason": "agent_result_unreadable"}
    child_id = tool_response.get("agentId")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(tool_use_id, str)
        or not tool_use_id
        or not isinstance(child_id, str)
        or not child_id
    ):
        return {"status": "unresolved", "reason": "native_child_identity_unverified"}

    completed = tool_response.get("status") == "completed"
    outcome: dict[str, str] = {}

    def observe(current: dict) -> dict:
        if current.get("parent_session_id") != session_id:
            raise ValueError("parent session does not match ledger")
        item = _by_dispatch(current, tool_use_id)
        apply_event(current, _event(current, item, "child_bound", child_id), _iso_now())
        if item["state"] == "queued":
            apply_event(
                current, _event(current, item, "child_started", child_id), _iso_now()
            )
        if completed and item["state"] not in {"terminal", "cancelled"}:
            apply_event(
                current,
                _event(
                    current,
                    item,
                    "child_terminal",
                    child_id,
                    terminal_event_id=_terminal_event_id(
                        "agent-result", tool_use_id
                    ),
                    terminal_reason="agent_result_completed",
                    result_digest=_digest(_reply_text(tool_response)),
                ),
                _iso_now(),
            )
        outcome["status"] = "terminal" if item["state"] == "terminal" else "bound"
        outcome["execution_id"] = item["execution_id"]
        return current

    try:
        mutate_atomic(pathlib.Path(ledger_path), observe)
    except (OSError, ValueError, KeyError):
        return {"status": "unresolved", "reason": "execution_identity_unresolved"}
    return outcome


def subagent_stop(payload: dict, ledger_path) -> dict:
    """Terminalize a bound child at SubagentStop, the single-code-path verdict.

    `SubagentStop.last_assistant_message` carries the child's final text under
    BOTH dispatch modes, which Agent PostToolUse does not. SubagentStop has no
    `tool_use_id`, so `agent_id` is the only join back to the dispatch — which
    means this can only act on a child PostToolUse has already bound. Under a
    foreground dispatch it has not yet, and that is fine: PostToolUse
    terminalizes that case itself.
    """
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "SubagentStop":
        return {"status": "ignored", "reason": "unmanaged_native_agent"}
    session_id = payload.get("session_id")
    child_id = payload.get("agent_id")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(child_id, str)
        or not child_id
    ):
        return {"status": "unresolved", "reason": "native_child_identity_unverified"}
    message = payload.get("last_assistant_message")
    verdict = message if isinstance(message, str) else ""
    outcome: dict[str, str] = {}

    def observe(current: dict) -> dict:
        if current.get("parent_session_id") != session_id:
            raise ValueError("parent session does not match ledger")
        item = _by_child(current, child_id)
        if item is None:
            outcome["status"] = "unmatched"
            outcome["reason"] = "native_child_not_bound"
            return current
        if item["state"] in {"terminal", "cancelled"}:
            outcome["status"] = "already_terminal"
            outcome["execution_id"] = item["execution_id"]
            return current
        if item["state"] == "queued":
            apply_event(
                current, _event(current, item, "child_started", child_id), _iso_now()
            )
        apply_event(
            current,
            _event(
                current,
                item,
                "child_terminal",
                child_id,
                terminal_event_id=_terminal_event_id("subagent-stop", child_id),
                terminal_reason="subagent_stop",
                result_digest=_digest(verdict),
            ),
            _iso_now(),
        )
        outcome["status"] = "terminal"
        outcome["execution_id"] = item["execution_id"]
        return current

    try:
        mutate_atomic(pathlib.Path(ledger_path), observe)
    except (OSError, ValueError, KeyError):
        return {"status": "unresolved", "reason": "execution_identity_unresolved"}
    return outcome


def post_tool_failure(payload: dict, ledger_path) -> dict:
    """Release an execution whose dispatch the host itself rejected.

    A rejected dispatch fires PostToolUseFailure and NO subagent event, so the
    execution registered at PreToolUse would otherwise sit queued until its
    two-hour hard deadline — waiting for a child that was never created. The
    host's own error is positive evidence, so this needs no deadline and no
    operator rationale.
    """
    if (
        not isinstance(payload, dict)
        or payload.get("hook_event_name") != "PostToolUseFailure"
        or payload.get("tool_name") != "Agent"
    ):
        return {"status": "ignored", "reason": "unmanaged_native_agent"}
    session_id = payload.get("session_id")
    tool_use_id = payload.get("tool_use_id")
    error = payload.get("error")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(tool_use_id, str)
        or not tool_use_id
    ):
        return {"status": "unresolved", "reason": "dispatch_evidence_unresolved"}
    outcome: dict[str, str] = {}

    def observe(current: dict) -> dict:
        if current.get("parent_session_id") != session_id:
            raise ValueError("parent session does not match ledger")
        item = _by_dispatch(current, tool_use_id)
        if item["state"] in {"terminal", "cancelled"}:
            outcome["status"] = "already_terminal"
            outcome["execution_id"] = item["execution_id"]
            return current
        cancel_failed_dispatch(
            current,
            item["execution_id"],
            _iso_now(),
            error=error if isinstance(error, str) else "",
        )
        outcome["status"] = "cancelled"
        outcome["execution_id"] = item["execution_id"]
        return current

    try:
        mutate_atomic(pathlib.Path(ledger_path), observe)
    except (OSError, ValueError, KeyError):
        return {"status": "unresolved", "reason": "execution_identity_unresolved"}
    return outcome
