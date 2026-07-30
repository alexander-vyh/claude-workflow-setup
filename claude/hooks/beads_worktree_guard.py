#!/usr/bin/env python3
"""Deny literal direct worktree creation and point to Escapement's CLI.

This PreToolUse guard is deliberately a narrow detector, not a shell parser or
repository policy engine. It never creates, inspects through subprocesses, or
otherwise mutates a repository. Dynamic shell constructs fail open.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, NoReturn

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None

from _worktree_cli import bundled_cli_path


@dataclass(frozen=True)
class LiteralCreation:
    kind: Literal["git", "bd"]
    repo: Path
    name: str | None
    branch: str | None
    source: str | None


_DYNAMIC_CHARS = frozenset("$`*?[]{}")
_BRANCH_FLAGS = frozenset({"-b", "-B", "--branch"})
_SOURCE_FLAGS = frozenset({"--source", "--commit-ish"})
_VALUE_FLAGS = frozenset({"--reason", "--track", "--orphan", "--lock"})
_GIT_VALUE_OPTIONS = frozenset({
    "-c", "-C", "--config-env", "--exec-path", "--git-dir", "--namespace",
    "--super-prefix", "--work-tree",
})
_BD_VALUE_OPTIONS = frozenset({
    "-C", "--actor", "--db", "--directory", "--dolt-auto-commit",
})
_GIT_TERMINAL_OPTIONS = frozenset({"-h", "--help", "-v", "--version"})
_BD_TERMINAL_OPTIONS = frozenset({"-h", "--help", "-V", "--version"})


def _is_literal(value: str) -> bool:
    return bool(value) and not any(char in value for char in _DYNAMIC_CHARS)


def _path_from(value: str, cwd: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve()
    except OSError:
        return path


def _repository_root(cwd: Path) -> Path:
    """Find a local repository root without invoking Git."""
    try:
        start = cwd.resolve()
    except OSError:
        start = cwd
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return start


def _shell_tokens(command: str) -> list[str] | None:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return None


def _separator_tokens(token: str) -> list[str] | None:
    if not token or any(char not in ";&|" for char in token):
        return None
    separators: list[str] = []
    i = 0
    while i < len(token):
        pair = token[i : i + 2]
        if pair in {"&&", "||"}:
            separators.append(pair)
            i += 2
        else:
            separators.append(token[i])
            i += 1
    return separators


def _segments(tokens: list[str]) -> Iterator[tuple[list[str], str | None]]:
    current: list[str] = []
    for token in tokens:
        separators = _separator_tokens(token)
        if separators is None:
            current.append(token)
            continue
        for separator in separators:
            if current:
                yield current, separator
                current = []
    if current:
        yield current, None


def _command_cwd(
    tokens: list[str],
    payload_cwd: Path,
    options: frozenset[str],
    terminal_options: frozenset[str],
) -> tuple[int, Path] | None:
    """Consume only literal global options and apply a literal ``-C``."""
    cwd = payload_cwd
    i = 1
    while i < len(tokens) and tokens[i] != "worktree":
        token = tokens[i]
        if not token.startswith("-") or not _is_literal(token):
            return None
        if token in terminal_options:
            return None
        if token == "-C":
            if i + 1 >= len(tokens) or not _is_literal(tokens[i + 1]):
                return None
            cwd = _path_from(tokens[i + 1], cwd)
            i += 2
        elif token.startswith("-C") and len(token) > 2:
            value = token[2:]
            if not _is_literal(value):
                return None
            cwd = _path_from(value, cwd)
            i += 1
        elif token in options:
            if i + 1 >= len(tokens) or not _is_literal(tokens[i + 1]):
                return None
            i += 2
        else:
            i += 1
    return i, cwd


def _creation_arguments(
    kind: Literal["git", "bd"], args: list[str]
) -> tuple[str | None, str | None, str | None] | None:
    """Return (name, branch, source) for a bounded literal create invocation."""
    if any(not _is_literal(token) for token in args):
        return None
    path: str | None = None
    branch: str | None = None
    source: str | None = None
    i = 0
    while i < len(args):
        token = args[i]
        if token in _BRANCH_FLAGS or token in _SOURCE_FLAGS or token in _VALUE_FLAGS:
            if i + 1 >= len(args):
                return None
            value = args[i + 1]
            if token in _BRANCH_FLAGS:
                branch = value
            elif token in _SOURCE_FLAGS:
                source = value
            i += 2
            continue
        if token.startswith("--branch="):
            branch = token.partition("=")[2]
        elif token.startswith("--source="):
            source = token.partition("=")[2]
        elif token.startswith("-"):
            pass
        elif path is None:
            path = token
        elif source is None:
            source = token
        else:
            return None
        i += 1
    name = Path(path).name if path else None
    return name, branch, source


def _creation_from_segment(
    tokens: list[str], payload_cwd: Path
) -> LiteralCreation | None:
    if not tokens or not _is_literal(tokens[0]):
        return None
    executable = tokens[0].split("/")[-1]
    if executable == "git":
        command = _command_cwd(
            tokens, payload_cwd, _GIT_VALUE_OPTIONS, _GIT_TERMINAL_OPTIONS
        )
        if command is None:
            return None
        index, cwd = command
        if tokens[index : index + 2] != ["worktree", "add"]:
            return None
        parsed = _creation_arguments("git", tokens[index + 2 :])
        kind: Literal["git", "bd"] = "git"
    elif executable == "bd":
        command = _command_cwd(
            tokens, payload_cwd, _BD_VALUE_OPTIONS, _BD_TERMINAL_OPTIONS
        )
        if command is None:
            return None
        index, cwd = command
        if tokens[index : index + 2] != ["worktree", "create"]:
            return None
        parsed = _creation_arguments("bd", tokens[index + 2 :])
        kind = "bd"
    else:
        return None
    if parsed is None:
        return None
    name, branch, source = parsed
    return LiteralCreation(kind, _repository_root(cwd), name, branch, source)


def literal_creations(
    command: str, payload_cwd: Path
) -> Iterator[LiteralCreation]:
    """Yield direct worktree creations in ordinary literal shell segments."""
    tokens = _shell_tokens(command)
    if tokens is None:
        return
    cwd = payload_cwd
    for segment, separator in _segments(tokens):
        if (
            len(segment) == 2
            and segment[0] == "cd"
            and _is_literal(segment[1])
            and separator in {"&&", ";"}
        ):
            cwd = _path_from(segment[1], cwd)
            continue
        create = _creation_from_segment(segment, cwd)
        if create is not None:
            yield create


def repair_command(create: LiteralCreation, cli_path: Path) -> str:
    """Render the safe, concrete transactional command for a denied create."""
    command = [
        "python3", "-B", str(cli_path), "create", "--repo", str(create.repo),
        "--name", create.name or "<name>", "--branch", create.branch or "<branch>",
    ]
    if create.source is not None:
        command.extend(("--source", create.source))
    return shlex.join(command)


def _emit_deny(reason: str) -> NoReturn:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    raise SystemExit(0)


def _deny(create: LiteralCreation) -> NoReturn:
    cli_path = bundled_cli_path(Path(__file__))
    if cli_path is None:
        _emit_deny(
            "Direct worktree creation remains blocked because this Escapement "
            "installation is missing its bundled `escapement-worktree` CLI. "
            "Repair or update Escapement before creating a worktree."
        )
    _emit_deny(
        f"Direct `{create.kind} worktree` creation is blocked. Use Escapement's "
        f"verified creator instead: `{repair_command(create, cli_path)}`."
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return 0
    command = tool_input["command"]
    if not command:
        return 0
    cwd_raw = data.get("cwd") or data.get("workingDirectory") or os.getcwd()
    for create in literal_creations(command, Path(cwd_raw)):
        _record_signal(
            gate_name="beads_worktree_guard",
            decision="deny",
            reason=f"direct {create.kind} worktree creation redirected",
            tool="Bash",
        )
        _deny(create)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
