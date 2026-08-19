#!/usr/bin/env python3
"""Claude Code hook: enforce named agents.

Enforcement:
  - HARD BLOCK: Agent calls without `name` — anonymous agents cannot be
    addressed via SendMessage and are never acceptable.

Input (via stdin):
  JSON with hook_event_name, tool_name, tool_input
Exit codes:
  0 — allow or deny (deny is signaled via permissionDecision JSON, not exit code)
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Shared signal capture per claude/rules/gate-design.md Rule 2.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None

_LOG_FILE = Path.home() / ".claude" / "hooks" / "agent-dispatch.log"


def _log(msg: str) -> None:
    try:
        with open(_LOG_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except OSError:
        pass


# Placeholder reasons that do not satisfy the waiver substance threshold.
# Mirrors spec_id_enforcement._PLACEHOLDER_VALUES so the corpus is uniform.
_PLACEHOLDER_REASONS = {
    "none", "tbd", "todo", "wip", "n/a", "na", "fixme", "xxx", "?", "??", "???",
}
_WAIVER_MIN_LEN = 20
_WAIVER_KEY = "enforce_named_agents_waiver"


def _validate_waiver(reason: str) -> tuple[bool, str]:
    """Validate a waiver reason per gate-design.md Rule 3 (value, not presence).

    Rejects: empty/whitespace, placeholder tokens (tbd/n/a/todo/wip/?...),
    and reasons under the substance threshold. Returns (is_valid, error)
    where error is empty on valid.
    """
    cleaned = (reason or "").strip()
    if not cleaned:
        return False, "waiver reason is empty — supply a real justification"
    if cleaned.lower() in _PLACEHOLDER_REASONS:
        return False, (
            f"waiver reason '{cleaned}' is a placeholder, not a real "
            "justification"
        )
    if len(cleaned) < _WAIVER_MIN_LEN:
        return False, (
            f"waiver reason is too short ({len(cleaned)} chars); a real "
            f"justification needs at least {_WAIVER_MIN_LEN} characters"
        )
    return True, ""


_BLOCK_NO_NAME = """\
🚫 AGENT BLOCKED — missing `name` parameter.

Anonymous agents cannot be addressed via SendMessage.
Every agent MUST have a `name`.

Example:
  Agent(name="researcher", description="...", prompt="...")
  Agent(name="qa-tester", description="...", prompt="...")

There is almost never a reason to dispatch an anonymous agent. The
two legitimate cases — a one-off lookup, or an explicit user-requested
anonymous probe — are still served by giving the agent a name (even
something throwaway like name="oneoff" or name="probe"). The cost of
naming is one keyword arg; the cost of leaving it off is that the
agent cannot be addressed, paired, or coordinated with.

ESCAPE — if you genuinely must dispatch an unnamed agent, add a waiver
field to the SAME Agent call:
  Agent(enforce_named_agents_waiver="<why this must be anonymous>", ...)
The reason must be real free-text (>=20 chars; tbd/n/a/todo/wip/? are
rejected) and is logged to the gate-signal corpus. You do NOT need to
disable this gate.\
"""


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_name = data.get("tool_name", "")
    _log(f"CALLED tool_name={tool_name!r}")

    if tool_name != "Agent":
        return 0

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0

    agent_name = (tool_input.get("name") or "").strip()
    _log(f"AGENT name={agent_name!r} desc={tool_input.get('description', '')!r}")

    # HARD BLOCK: no name — unless a valid waiver is supplied (escape path,
    # gate-design.md Rule 1). The waiver reason is validated for substance
    # (Rule 3) and persisted to the signal corpus (Rule 2).
    if not agent_name:
        waiver_raw = tool_input.get(_WAIVER_KEY)
        if waiver_raw is not None:
            valid, error = _validate_waiver(str(waiver_raw))
            if valid:
                _log("ALLOWED — no name but valid waiver supplied")
                _record_signal(
                    gate_name="enforce_named_agents",
                    decision="waiver-accepted",
                    reason=str(waiver_raw).strip(),
                    event_type="waiver",
                )
                return 0
            # Invalid waiver — fall through to deny, with the reason why.
            _log(f"BLOCKED — no name, invalid waiver: {error}")
            _record_signal(
                gate_name="enforce_named_agents",
                decision="deny",
                reason=f"invalid waiver: {error}",
            )
            result = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"🚫 AGENT BLOCKED — waiver rejected: {error}\n\n"
                        f"{_BLOCK_NO_NAME}"
                    ),
                }
            }
            json.dump(result, sys.stdout)
            return 0  # canonical deny: JSON permissionDecision only, exit 0

        _log("BLOCKED — no name")
        _record_signal(
            gate_name="enforce_named_agents",
            decision="deny",
            reason="agent dispatched without name parameter",
        )
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _BLOCK_NO_NAME,
            }
        }
        json.dump(result, sys.stdout)
        return 0  # canonical deny: JSON permissionDecision only, exit 0

    _log("ALLOWED — named agent")
    _record_signal(
        gate_name="enforce_named_agents",
        decision="allow",
        reason="named agent",
        name=agent_name,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
