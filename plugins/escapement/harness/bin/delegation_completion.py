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

from execution_ledger import apply_event
from execution_store import load_trusted, mutate_atomic

UTC = dt.timezone.utc


class _LifecycleAdapterFailure(RuntimeError):
    pass


class _LifecycleGenerationFailure(RuntimeError):
    pass


_FOREGROUND_TOP_KEYS = {
    "cwd",
    "duration_ms",
    "hook_event_name",
    "permission_mode",
    "prompt_id",
    "session_id",
    "tool_input",
    "tool_name",
    "tool_response",
    "tool_use_id",
    "transcript_path",
}
_FOREGROUND_INPUT_KEYS = {
    "description",
    "name",
    "prompt",
    "run_in_background",
    "subagent_type",
}
_FOREGROUND_RESPONSE_KEYS = {
    "agentId",
    "agentType",
    "content",
    "harnessNoteCount",
    "harnessSectionHash",
    "harnessTailCount",
    "prompt",
    "resolvedModel",
    "status",
    "totalDurationMs",
    "totalTokens",
    "totalToolUseCount",
    "usage",
}


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


def _event(
    ledger: dict, item: dict, kind: str, child_id: str | None, **extra
) -> dict:
    event = {
        "kind": kind,
        "parent_session_id": ledger["parent_session_id"],
        "execution_id": item["execution_id"],
        "attempt": item["attempt"],
        "generation": item["generation"],
        **extra,
    }
    if child_id is not None:
        event["native_child_id"] = child_id
    return event


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


def _foreground_result(payload: object) -> tuple[str, str, str, str, str] | None:
    """Return exact captured foreground identity and verdict, else unresolved."""
    if not isinstance(payload, dict) or set(payload) != _FOREGROUND_TOP_KEYS:
        return None
    tool_input = payload.get("tool_input")
    tool_response = payload.get("tool_response")
    if (
        not isinstance(tool_input, dict)
        or set(tool_input) != _FOREGROUND_INPUT_KEYS
        or not isinstance(tool_response, dict)
        or set(tool_response) != _FOREGROUND_RESPONSE_KEYS
    ):
        return None
    content = tool_response.get("content")
    if (
        payload.get("hook_event_name") != "PostToolUse"
        or payload.get("tool_name") != "Agent"
        or tool_input.get("run_in_background") is not False
        or tool_response.get("status") != "completed"
        or tool_response.get("agentType") != tool_input.get("subagent_type")
        or not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or set(content[0]) != {"type", "text"}
        or content[0].get("type") != "text"
    ):
        return None
    values = (
        payload.get("session_id"),
        payload.get("tool_use_id"),
        tool_input.get("name"),
        tool_response.get("agentId"),
        content[0].get("text"),
    )
    if not all(isinstance(value, str) and value for value in values):
        return None
    session_id, tool_use_id, name, child_id, reply = values
    return session_id, tool_use_id, name, child_id, reply


def _post_tool_async(payload: dict, ledger_path) -> dict:
    """Apply only capture-proven asynchronous PostToolUse evidence."""
    try:
        from claude_agent_lifecycle import observe_post_tool
    except Exception:
        return {
            "status": "unresolved",
            "reason": "lifecycle_observation_adapter_failed",
        }

    path = pathlib.Path(ledger_path)
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session_id, str) or not session_id:
        return {
            "status": "unresolved",
            "reason": "native_child_identity_unverified",
        }
    try:
        current = load_trusted(path, session_id)
    except (OSError, ValueError):
        current = None
    if current is None:
        return {
            "status": "unresolved",
            "reason": "lifecycle_observation_persistence_failed",
        }
    try:
        observed = observe_post_tool(payload, current)
    except Exception:
        return {
            "status": "unresolved",
            "reason": "lifecycle_observation_adapter_failed",
        }
    if not isinstance(observed.get("events"), list) or not observed["events"]:
        return {
            "status": "unresolved",
            "reason": "native_child_identity_unverified",
        }

    def apply(current: dict) -> dict:
        nonlocal observed
        try:
            observed = observe_post_tool(payload, current)
        except Exception as exc:
            raise _LifecycleAdapterFailure from exc
        events = observed.get("events")
        if not isinstance(events, list) or not events:
            return current
        for event in events:
            apply_event(current, event, _iso_now())
        return current

    try:
        mutate_atomic(path, apply)
    except _LifecycleAdapterFailure:
        return {
            "status": "unresolved",
            "reason": "lifecycle_observation_adapter_failed",
        }
    except (OSError, ValueError):
        return {
            "status": "unresolved",
            "reason": "lifecycle_observation_persistence_failed",
        }
    if observed.get("status") == "observed":
        return observed
    return {
        "status": "unresolved",
        "reason": "native_child_identity_unverified",
    }


def post_tool(payload: dict, ledger_path) -> dict:
    """Bind the native child at Agent PostToolUse, and terminalize if it proves it.

    Captured in escapement-g27c: a BACKGROUNDED dispatch's PostToolUse is a
    launch receipt (`status: async_launched`, `content: null`, single-digit
    `duration_ms`). It proves identity and liveness but NOT completion, and
    reading a verdict out of it would fabricate one. A FOREGROUND dispatch's
    PostToolUse arrives after the child is done (`status: completed`, with the
    reply in `content`), so it proves termination too.
    """
    foreground = _foreground_result(payload)
    if foreground is None:
        return _post_tool_async(payload, ledger_path)
    session_id, tool_use_id, name, child_id, reply = foreground
    outcome: dict[str, str] = {}

    def observe(current: dict) -> dict:
        if current.get("parent_session_id") != session_id:
            raise ValueError("parent session does not match ledger")
        item = _by_dispatch(current, tool_use_id)
        if item.get("agent_name") != name:
            raise ValueError("agent identity does not match registered dispatch")
        if item.get("attempt") != 1 or item.get("generation") != 1:
            raise _LifecycleGenerationFailure
        terminal_event_id = _terminal_event_id("agent-result", tool_use_id)
        terminal = _event(
            current,
            item,
            "child_terminal",
            child_id,
            host_event_id=f"claude:{terminal_event_id}",
            terminal_event_id=terminal_event_id,
            terminal_reason="agent_result_completed",
            result_digest=_digest(reply),
        )
        if item["state"] == "terminal":
            apply_event(current, terminal, _iso_now())
            outcome["status"] = "terminal"
            outcome["execution_id"] = item["execution_id"]
            return current
        if item["state"] in {"cancelled", "aborted"}:
            raise ValueError("execution already has different terminal evidence")
        apply_event(current, _event(current, item, "child_bound", child_id), _iso_now())
        if item["state"] == "queued":
            apply_event(
                current, _event(current, item, "child_started", child_id), _iso_now()
            )
        apply_event(current, terminal, _iso_now())
        outcome["status"] = "terminal"
        outcome["execution_id"] = item["execution_id"]
        return current

    try:
        mutate_atomic(pathlib.Path(ledger_path), observe)
    except _LifecycleGenerationFailure:
        return {
            "status": "unresolved",
            "reason": "lifecycle_generation_unverified",
        }
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
    if not isinstance(message, str) or not message.strip():
        return {"status": "unresolved", "reason": "child_result_unverified"}
    verdict = message
    outcome: dict[str, str] = {}

    def observe(current: dict) -> dict:
        if current.get("parent_session_id") != session_id:
            raise ValueError("parent session does not match ledger")
        item = _by_child(current, child_id)
        if item is None:
            outcome["status"] = "unmatched"
            outcome["reason"] = "native_child_not_bound"
            return current
        terminal_event_id = _terminal_event_id("subagent-stop", child_id)
        terminal = _event(
            current,
            item,
            "child_terminal",
            child_id,
            host_event_id=f"claude:{terminal_event_id}",
            terminal_event_id=terminal_event_id,
            terminal_reason="subagent_stop",
            result_digest=_digest(verdict),
        )
        if item["state"] == "terminal":
            apply_event(current, terminal, _iso_now())
            outcome["status"] = "already_terminal"
            outcome["execution_id"] = item["execution_id"]
            return current
        if item["state"] in {"cancelled", "aborted"}:
            outcome["status"] = "already_terminal"
            outcome["execution_id"] = item["execution_id"]
            return current
        if item["state"] == "queued":
            apply_event(
                current, _event(current, item, "child_started", child_id), _iso_now()
            )
        apply_event(current, terminal, _iso_now())
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
        if item.get("attempt") != 1 or item.get("generation") != 1:
            raise _LifecycleGenerationFailure
        host_error = error if isinstance(error, str) else ""
        host_event_id = f"claude:dispatch-failed:{tool_use_id}"
        existing = next(
            (
                incident
                for incident in current.get("incidents", [])
                if incident.get("type") == "dispatch_failed"
                and incident.get("execution_id") == item["execution_id"]
                and incident.get("attempt") == item["attempt"]
                and incident.get("generation") == item["generation"]
            ),
            None,
        )
        state_before = existing["state_before"] if existing else item["state"]
        apply_event(
            current,
            _event(
                current,
                item,
                "dispatch_aborted",
                None,
                host_event_id=host_event_id,
                terminal_reason="dispatch_failed",
                host_error=host_error,
            ),
            _iso_now(),
        )
        item = _by_dispatch(current, tool_use_id)
        if existing is None:
            current.setdefault("incidents", []).append(
                {
                    "type": "dispatch_failed",
                    "execution_id": item["execution_id"],
                    "attempt": item["attempt"],
                    "generation": item["generation"],
                    "host_error": host_error,
                    "state_before": state_before,
                    "recorded_at": item["terminal_at"],
                }
            )
        outcome["status"] = "aborted"
        outcome["execution_id"] = item["execution_id"]
        return current

    try:
        mutate_atomic(pathlib.Path(ledger_path), observe)
    except _LifecycleGenerationFailure:
        return {
            "status": "unresolved",
            "reason": "lifecycle_generation_unverified",
        }
    except (OSError, ValueError, KeyError):
        return {"status": "unresolved", "reason": "execution_identity_unresolved"}
    return outcome
