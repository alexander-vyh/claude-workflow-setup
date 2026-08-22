#!/usr/bin/env python3
"""Claude Code hook: prevent linked bead closes from leaving tasks.md stale.

Fires as PreToolUse on Bash commands containing `bd close`.

If the bead being closed has a `spec_id` that points at an OpenSpec
`tasks.md#anchor`, this hook requires the referenced OpenSpec task or task
section to already be checked. This closes the reverse-link gap: Beads may be
the operational task graph, but OpenSpec remains a visible planning artifact
whose task list must not silently drift after linked bead closure.

Scope:
  - Only `bd close <id>` is checked.
  - Only `spec_id` values whose path ends in `tasks.md` are checked.
  - Non-task spec IDs are allowed; they are handled by existing spec-id gates.

Exit contract:
  0 + no stdout — allow
  0 + permissionDecision=deny JSON — deny
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, NoReturn

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None


_SUBPROCESS_TIMEOUT = 5
_SPEC_ID_KEYS = ("spec_id", "specID", "spec-id", "specId")


def slugify(value: str) -> str:
    """Return a GitHub-ish markdown anchor slug for headings/task text."""
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"\*\*([^*]*)\*\*", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def extract_close_target(command: str) -> str | None:
    """Return the issue id being closed by a `bd close` command."""
    match = re.search(r"\bbd\s+close\b(.*)", command, re.DOTALL)
    if not match:
        return None
    tokens = re.findall(r"(?:'[^']*'|\"[^\"]*\"|\S)+", match.group(1))
    for token in tokens:
        if token.startswith("-"):
            continue
        return token.strip("'\"")
    return None


def _bd_json(args: list[str]) -> object | None:
    try:
        result = subprocess.run(
            ["bd", *args, "--json"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def get_issue(issue_id: str) -> dict | None:
    """Fetch a single bead record, returning None on bd failure."""
    data = _bd_json(["show", issue_id])
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) else None


def extract_spec_id(issue: dict) -> str | None:
    """Pull a spec_id from known bd output shapes."""
    for key in _SPEC_ID_KEYS:
        value = issue.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for container_key in ("metadata", "extra"):
        container = issue.get(container_key)
        if isinstance(container, str):
            try:
                container = json.loads(container)
            except (json.JSONDecodeError, ValueError):
                container = {}
        if isinstance(container, dict):
            for key in _SPEC_ID_KEYS:
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    return None


def _checkbox_match(line: str) -> re.Match[str] | None:
    return re.match(r"^\s*(?:[-*]|\d+\.)\s+\[([ xX])\]\s+(.*\S)\s*$", line)


def _heading_match(line: str) -> re.Match[str] | None:
    return re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)


def _checkbox_state(line: str) -> tuple[bool, str] | None:
    match = _checkbox_match(line)
    if not match:
        return None
    checked = match.group(1).lower() == "x"
    text = match.group(2).strip()
    return checked, text


def _section_lines(lines: list[str], start_index: int, heading_level: int) -> list[str]:
    out: list[str] = []
    for line in lines[start_index + 1:]:
        match = _heading_match(line)
        if match and len(match.group(1)) <= heading_level:
            break
        out.append(line)
    return out


def _check_section(lines: list[str], anchor: str) -> tuple[bool, str]:
    for idx, line in enumerate(lines):
        match = _heading_match(line)
        if not match:
            continue
        heading_text = match.group(2).strip()
        if slugify(heading_text) != anchor:
            continue

        checkboxes = []
        for section_line in _section_lines(lines, idx, len(match.group(1))):
            state = _checkbox_state(section_line)
            if state:
                checkboxes.append(state)

        if not checkboxes:
            return False, (
                f"OpenSpec anchor '#{anchor}' resolves to section '{heading_text}', "
                "but that section contains no checkbox tasks to reconcile."
            )

        unchecked = [text for checked, text in checkboxes if not checked]
        if unchecked:
            preview = "; ".join(unchecked[:3])
            return False, (
                f"OpenSpec task section '#{anchor}' still has unchecked task(s): "
                f"{preview}"
            )
        return True, ""

    return False, f"OpenSpec tasks.md anchor '#{anchor}' does not match any heading."


def _check_item(lines: list[str], anchor: str) -> tuple[bool, str]:
    for line in lines:
        state = _checkbox_state(line)
        if not state:
            continue
        checked, text = state
        if slugify(text) != anchor:
            continue
        if checked:
            return True, ""
        return False, f"OpenSpec task '{text}' is still unchecked."

    return False, f"OpenSpec tasks.md anchor '#{anchor}' does not match any checkbox task."


def check_tasks_spec_id(spec_id: str, project_dir: Path) -> tuple[bool, str]:
    """Validate completion state for an OpenSpec `tasks.md#anchor` spec_id."""
    if "#" in spec_id:
        path_part, anchor = spec_id.split("#", 1)
        anchor = slugify(anchor)
    else:
        path_part, anchor = spec_id, ""

    if not path_part.endswith("tasks.md"):
        return True, ""

    if not anchor:
        return False, (
            f"OpenSpec task spec_id '{spec_id}' points at tasks.md without an "
            "anchor, so the close cannot be reconciled to a specific task."
        )

    tasks_path = (project_dir / path_part).resolve()
    if not tasks_path.is_file():
        return False, f"OpenSpec tasks file '{path_part}' does not exist."

    try:
        lines = tasks_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False, f"OpenSpec tasks file '{path_part}' exists but cannot be read."

    section_ok, section_reason = _check_section(lines, anchor)
    if section_ok:
        return True, ""
    if "does not match any heading" not in section_reason:
        return False, section_reason

    item_ok, item_reason = _check_item(lines, anchor)
    if item_ok:
        return True, ""
    return False, f"{section_reason} {item_reason}"


def evaluate(
    command: str,
    project_dir: Path,
    issue_reader: Callable[[str], Optional[dict]] | None = None,
) -> tuple[str, str]:
    """Return (`allow`|`deny`, message) for a bd close command."""
    if issue_reader is None:
        issue_reader = get_issue

    issue_id = extract_close_target(command)
    if not issue_id:
        return "allow", ""

    issue = issue_reader(issue_id)
    if issue is None:
        return "allow", ""

    spec_id = extract_spec_id(issue)
    if not spec_id:
        return "allow", ""

    ok, reason = check_tasks_spec_id(spec_id, project_dir)
    if ok:
        return "allow", ""

    return "deny", (
        f"Bead {issue_id} is linked to OpenSpec task `{spec_id}`, but the "
        f"linked OpenSpec task is not complete: {reason}\n"
        "Update the referenced `tasks.md` checkbox to `[x]` after verifying "
        "the outcome, or correct the bead's spec_id before closing it."
    )


def _deny(message: str) -> NoReturn:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }
    }))
    sys.exit(0)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    if hook_event != "PreToolUse":
        return 0
    if data.get("tool_name", "") != "Bash":
        return 0

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if "bd close" not in command:
        return 0

    project_dir = Path(
        data.get("cwd", "")
        or data.get("workingDirectory", "")
        or os.getcwd()
    )
    decision, message = evaluate(command, project_dir)
    if decision == "deny":
        issue_id = extract_close_target(command)
        _record_signal(
            gate_name="openspec_task_reconciliation_gate",
            decision="deny",
            reason=message,
            issue_id=issue_id,
        )
        _deny(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
