#!/usr/bin/env python3
"""PreToolUse guard for explicit-path edits in a managed primary checkout.

The independent oracle is filesystem shape. A primary checkout has a .git
directory and a sibling .beads directory. A linked worktree has .git as a file,
so explicit edits there remain allowed.

Arbitrary process effects are outside this hook's hard-enforcement boundary.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).parent))
from _worktree_cli import bundled_cli_prefix

try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None


PATH_KEY_BY_TOOL = {
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
    "MultiEdit": "file_path",
}
GATED_EDIT_TOOLS = frozenset(PATH_KEY_BY_TOOL)


def _emit_deny(reason: str) -> NoReturn:
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
    sys.exit(0)


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def _primary_checkout_root_for(path: Path) -> Path | None:
    """Return the primary beads checkout containing path, if one exists."""
    resolved = _safe_resolve(path)
    start = resolved if resolved.is_dir() else resolved.parent
    for directory in (start, *start.parents):
        git_marker = directory / ".git"
        if git_marker.is_dir():
            return directory if (directory / ".beads").is_dir() else None
        if git_marker.exists():
            return None
    return None


def _path_from_tool_input(tool_name: str, tool_input: dict, cwd: Path) -> Path | None:
    raw = tool_input.get(PATH_KEY_BY_TOOL[tool_name])
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    return _safe_resolve(path if path.is_absolute() else cwd / path)


def _quote(value: object) -> str:
    text = str(value)
    if text and all(char.isalnum() or char in "/._-" for char in text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _deny_reason(operation: str, root: Path) -> str:
    prefix = bundled_cli_prefix(Path(__file__))
    if prefix is None:
        repair = (
            "Worktree creation is unavailable: broken Escapement installation; "
            "the bundled escapement-worktree CLI is missing. Repair or reinstall "
            "Escapement before creating a worktree."
        )
    else:
        invocation = (
            " ".join(_quote(token) for token in prefix)
            + f" create --repo {_quote(root)} --name <task> --branch <branch>"
        )
        repair = (
            "Create a linked worktree with the Escapement-owned "
            f"escapement-worktree transaction: `{invocation}`, then make the "
            "change there."
        )
    return (
        f"`{operation}` targets the primary checkout of a beads-managed repo "
        f"at `{root}`. Routine agent implementation work must not dirty the "
        f"root checkout. {repair}"
    )


def _deny(operation: str, root: Path, tool_name: str) -> NoReturn:
    _record_signal(
        gate_name="root_checkout_guard",
        decision="deny",
        reason=f"primary checkout explicit edit blocked: {operation}",
        tool=tool_name,
        target=str(root),
    )
    _emit_deny(_deny_reason(operation, root))


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if data.get("hook_event_name") != "PreToolUse":
        return 0
    tool_name = data.get("tool_name", "")
    if tool_name not in GATED_EDIT_TOOLS:
        return 0
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    cwd = _safe_resolve(
        Path(data.get("cwd") or data.get("workingDirectory") or os.getcwd())
    )
    target = _path_from_tool_input(tool_name, tool_input, cwd)
    if target is None:
        return 0
    root = _primary_checkout_root_for(target)
    if root is not None:
        _deny(f"{tool_name} {target}", root, tool_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
