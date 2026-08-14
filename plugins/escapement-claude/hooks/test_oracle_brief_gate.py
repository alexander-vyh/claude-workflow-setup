#!/usr/bin/env python3
"""Public hook entrypoint for Test Oracle Brief edit and landing gates."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover - exercised by isolated-hook integration test
    _record_signal = None

from test_oracle_brief_landing import landing_context, landing_stage
from oracle_brief_rapid import (
    RAPID_OBSERVED_FIELD,
    RAPID_REVIEW_FIELDS,
    RAPID_SECTION_FIELDS,
)
from test_oracle_brief_policy import (
    BRIEF_RELATIVE_PATH,
    REQUIRED_SECTIONS,
    brief_status,
    classify_edit_target,
)


GATED_EDIT_TOOLS = frozenset(
    {
        "Write",
        "Edit",
        "NotebookEdit",
        "mcp__serena__replace_symbol_body",
        "mcp__serena__insert_after_symbol",
        "mcp__serena__insert_before_symbol",
    }
)
FILE_PATH_KEYS = ("file_path", "relative_path", "notebook_path")


def allow() -> int:
    return 0


def ask(reason: str) -> int:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


def deny(reason: str) -> int:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


def record_decision_signal(
    data: dict,
    *,
    decision: str,
    reason: str,
    category: str,
    target: str,
    **extras,
) -> None:
    signal_extras = {
        "tool": data.get("tool_name", ""),
        "target": target,
        "category": category,
        **extras,
    }
    if "rapid-brief" not in category:
        signal_extras.pop("stage", None)
    if _record_signal is None:
        persisted = False
    else:
        try:
            persisted = _record_signal(
                gate_name="test_oracle_brief_gate",
                decision=decision,
                reason=reason,
                **signal_extras,
            )
        except Exception:  # pragma: no cover - third-party replacement safety
            persisted = False
    if not persisted:
        print(
            "test_oracle_brief_gate: gate signal unavailable; decision preserved",
            file=sys.stderr,
        )


def block_message(
    reason: str,
    repo_root: Path,
    files: list[str],
    *,
    ask_decision: bool,
) -> str:
    sample_files = "\n".join(f"  - {name}" for name in files[:8])
    if len(files) > 8:
        sample_files += f"\n  - ... {len(files) - 8} more"
    message = (
        f"{reason}\n\n"
        "Before editing or landing behavior-bearing code/tests, create:\n"
        f"  {repo_root / BRIEF_RELATIVE_PATH}\n\n"
        "Full form headings (default):\n"
        + "\n".join(f"  - {section}" for section in REQUIRED_SECTIONS)
        + "\n\nRapid form (only when every protected field is explicitly clear):\n"
        + "\n".join(
            f"  - {section}: {', '.join(fields)}"
            for section, fields in RAPID_SECTION_FIELDS.items()
        )
        + "\n  - Review stage also requires: "
        + ", ".join(RAPID_REVIEW_FIELDS)
        + f"\n  - Final stage also requires: {RAPID_OBSERVED_FIELD}"
        + "\n\nRelevant changed/target files:\n"
        + (sample_files if sample_files else "  - unknown")
    )
    if ask_decision:
        message += (
            "\n\nThis ask decision is recorded. This hook cannot observe or record "
            "a later host approval or rejection."
        )
    return message


def _path_from(data: dict) -> str | None:
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return None
    return next(
        (
            tool_input[path_key]
            for path_key in FILE_PATH_KEYS
            if isinstance(tool_input.get(path_key), str) and tool_input[path_key].strip()
        ),
        None,
    )


def handle_edit_gate(data: dict) -> int:
    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    tool_name = data.get("tool_name", "")
    if hook_event != "PreToolUse" or tool_name not in GATED_EDIT_TOOLS:
        return allow()
    raw_path = _path_from(data)
    if raw_path is None:
        return allow()
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else None
    repo_root, target, relevant = classify_edit_target(raw_path, cwd)
    if repo_root is None or target is None or not relevant:
        return allow()

    ok, reason, category = brief_status(repo_root, stage="edit")
    if ok:
        record_decision_signal(
            data,
            decision="allow",
            reason="oracle brief present and valid",
            category=category,
            target=target,
            stage="edit",
        )
        return allow()

    reason = reason or "Test Oracle Brief is missing required explanatory content."
    record_decision_signal(
        data,
        decision="ask",
        reason=reason,
        category=category,
        target=target,
        stage="edit",
    )
    return ask(block_message(reason, repo_root, [target], ask_decision=True))


def _command_from(data: dict) -> str:
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else ""


def handle_bash_landing_gate(data: dict) -> int:
    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    tool_name = data.get("tool_name", "")
    if hook_event != "PreToolUse" or tool_name != "Bash":
        return allow()
    command = _command_from(data)
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    context = landing_context(command, cwd)
    if context is None:
        return allow()
    repo_root, relevant = context
    stage = landing_stage(command)
    if stage is None:
        return allow()

    ok, reason, category = brief_status(repo_root, stage=stage)
    target = relevant[0]
    if ok:
        record_decision_signal(
            data,
            decision="allow",
            reason="oracle brief present and valid",
            category=category,
            target=target,
            surface="landing-command",
            stage=stage,
            file_count=len(relevant),
        )
        return allow()

    reason = reason or "Test Oracle Brief is missing required explanatory content."
    record_decision_signal(
        data,
        decision="deny",
        reason=reason,
        category=category,
        target=target,
        surface="landing-command",
        stage=stage,
        file_count=len(relevant),
    )
    return deny(block_message(reason, repo_root, relevant, ask_decision=False))


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return allow()
    edit_result = handle_edit_gate(data)
    if edit_result != 0:
        return edit_result
    return handle_bash_landing_gate(data)


if __name__ == "__main__":
    raise SystemExit(main())
