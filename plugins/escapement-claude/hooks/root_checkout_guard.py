#!/usr/bin/env python3
"""PreToolUse guard: prevent accidental mutations in a beads repo's primary checkout.

Business outcome: agents should not dirty the root checkout of a beads-managed
repository during normal implementation work. Use a linked worktree instead.

Independent oracle: filesystem shape, not path strings. A primary checkout has
`.git/` as a directory and `.beads/` at the same root. A linked worktree has
`.git` as a file, so it is not blocked by this guard.
"""

from __future__ import annotations

import json
import os
import re
import shlex
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


GATED_EDIT_TOOLS = frozenset(
    {
        "Write",
        "Edit",
        "NotebookEdit",
        "MultiEdit",
        "mcp__serena__replace_symbol_body",
        "mcp__serena__insert_after_symbol",
        "mcp__serena__insert_before_symbol",
    }
)
FILE_PATH_KEYS = ("file_path", "relative_path", "notebook_path")
GIT_STATE_CHANGING_SUBCOMMANDS = frozenset(
    {
        "add",
        "checkout",
        "cherry-pick",
        "clean",
        "commit",
        "merge",
        "mv",
        "pull",
        "rebase",
        "reset",
        "restore",
        "rm",
        "stash",
        "switch",
        "tag",
    }
)
SHELL_STATE_CHANGING_COMMANDS = frozenset({"cp", "install", "mkdir", "mv", "rm", "rmdir", "tee", "touch"})
GIT_VALUE_FLAGS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--git-common-dir", "--namespace", "--super-prefix"})
ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-S", "--split-string"})
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_SEP_RE = re.compile(r"&&|\|\||[;\n]")
WAIVER_RE = re.compile(r"#\s*root-checkout-waiver:\s*(\S.*?)\s*$", re.MULTILINE)
WAIVER_PLACEHOLDERS = frozenset({"<reason>", "tbd", "n/a", "na", "none", "todo", "fixme", "wip", "?", "??", "???"})
MIN_WAIVER_REASON_LEN = 20


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
    """Return the primary beads checkout root containing `path`, or None."""
    resolved = _safe_resolve(path)
    start = resolved if resolved.is_dir() else resolved.parent
    for directory in (start, *start.parents):
        git_marker = directory / ".git"
        if git_marker.is_dir():
            return directory if (directory / ".beads").is_dir() else None
        if git_marker.exists():
            return None
    return None


def _path_from_tool_input(tool_input: dict, cwd: Path) -> Path | None:
    for key in FILE_PATH_KEYS:
        raw = tool_input.get(key)
        if isinstance(raw, str) and raw:
            path = Path(raw)
            return path if path.is_absolute() else cwd / path
    return None


def _extract_waiver_reason(text: str) -> str | None:
    match = WAIVER_RE.search(text)
    if match:
        return match.group(1).strip()
    env_reason = os.environ.get("ROOT_CHECKOUT_WAIVER", "").strip()
    return env_reason or None


def _is_substantive_waiver(reason: str | None) -> bool:
    if not reason:
        return False
    normalized = reason.strip().lower()
    return len(reason.strip()) >= MIN_WAIVER_REASON_LEN and normalized not in WAIVER_PLACEHOLDERS


def _strip_command_prefixes(tokens: list[str]) -> int | None:
    i = 0
    while i < len(tokens) and ENV_ASSIGNMENT_RE.match(tokens[i]):
        i += 1
    while i < len(tokens) and tokens[i] in ("env", "command"):
        i += 1
        while i < len(tokens):
            tok = tokens[i]
            if ENV_ASSIGNMENT_RE.match(tok):
                i += 1
            elif tok in ENV_VALUE_FLAGS and i + 1 < len(tokens):
                i += 2
            elif tok.startswith("-"):
                i += 1
            else:
                break
    return i if i < len(tokens) else None


def _command_name(tokens: list[str]) -> str | None:
    i = _strip_command_prefixes(tokens)
    return Path(tokens[i]).name if i is not None else None


def _git_target_and_subcommand(tokens: list[str], cwd: Path) -> tuple[Path, str] | None:
    i = _strip_command_prefixes(tokens)
    if i is None or Path(tokens[i]).name != "git":
        return None
    i += 1
    target_cwd = cwd
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-C" and i + 1 < len(tokens):
            candidate = Path(tokens[i + 1])
            target_cwd = candidate if candidate.is_absolute() else target_cwd / candidate
            i += 2
        elif tok.startswith("-C") and len(tok) > 2:
            candidate = Path(tok[2:])
            target_cwd = candidate if candidate.is_absolute() else target_cwd / candidate
            i += 1
        elif tok in GIT_VALUE_FLAGS:
            i += 2
        elif any(tok.startswith(prefix + "=") for prefix in GIT_VALUE_FLAGS if prefix.startswith("--")):
            i += 1
        elif tok.startswith("-"):
            i += 1
        else:
            return _safe_resolve(target_cwd), tok
    return None


def _bash_mutation_target(command: str, cwd: Path) -> tuple[Path, str] | None:
    current_cwd = cwd
    for segment in SHELL_SEP_RE.split(command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return None
        if not tokens:
            continue
        name = _command_name(tokens)
        if name == "cd" and len(tokens) > 1:
            candidate = Path(tokens[1])
            current_cwd = _safe_resolve(candidate if candidate.is_absolute() else current_cwd / candidate)
            continue
        git = _git_target_and_subcommand(tokens, current_cwd)
        if git is not None:
            target, subcommand = git
            if subcommand in GIT_STATE_CHANGING_SUBCOMMANDS:
                return target, f"git {subcommand}"
            continue
        if name in SHELL_STATE_CHANGING_COMMANDS:
            return _safe_resolve(current_cwd), name
    return None


def _deny_reason(operation: str, root: Path) -> str:
    prefix = bundled_cli_prefix(Path(__file__))
    if prefix is None:
        repair = (
            "Worktree creation is unavailable: broken Escapement installation; "
            "the bundled escapement-worktree CLI "
            "is missing. Repair or reinstall Escapement before creating a worktree."
        )
    else:
        command = (
            f"{shlex.join(prefix)} create --repo {shlex.quote(str(root))} "
            "--name <task> --branch <branch>"
        )
        repair = (
            "Create a linked worktree with the Escapement-owned "
            f"`escapement-worktree create` transaction: `{command}`, then make "
            "the change there."
        )
    return (
        f"`{operation}` targets the primary checkout of a beads-managed repo at `{root}`. "
        "Routine agent implementation work must not dirty the root checkout. "
        f"{repair} If this is intentional root maintenance, rerun the Bash "
        "command with `# root-checkout-waiver: <reason>` using a substantive reason. "
        "Placeholder waiver reasons are rejected."
    )


def _deny(operation: str, root: Path, tool_name: str, command: str = "") -> NoReturn:
    _record_signal(
        gate_name="root_checkout_guard",
        decision="deny",
        reason=f"primary checkout mutation blocked: {operation}",
        tool=tool_name,
        target=str(root),
        command=command,
    )
    _emit_deny(_deny_reason(operation, root))


def _allow_waiver(reason: str, root: Path, tool_name: str, command: str) -> int:
    _record_signal(
        gate_name="root_checkout_guard",
        decision="waiver-accepted",
        reason=reason,
        event_type="waiver",
        tool=tool_name,
        target=str(root),
        command=command,
    )
    return 0


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if data.get("hook_event_name") != "PreToolUse":
        return 0
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    cwd = _safe_resolve(Path(data.get("cwd") or data.get("workingDirectory") or os.getcwd()))
    if tool_name in GATED_EDIT_TOOLS:
        target = _path_from_tool_input(tool_input, cwd)
        if target is None:
            return 0
        root = _primary_checkout_root_for(target)
        if root is not None:
            _deny(f"{tool_name} {target}", root, tool_name)
        return 0
    if tool_name != "Bash":
        return 0
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command:
        return 0
    mutation = _bash_mutation_target(command, cwd)
    if mutation is None:
        return 0
    target, operation = mutation
    root = _primary_checkout_root_for(target)
    if root is None:
        return 0
    waiver_reason = _extract_waiver_reason(command)
    if _is_substantive_waiver(waiver_reason):
        return _allow_waiver(waiver_reason or "", root, tool_name, command)
    _deny(operation, root, tool_name, command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
