#!/usr/bin/env python3
"""Guard repository-managed worktree creation paths.

For ordinary literal shell commands, this PreToolUse hook:

* redirects ``git worktree add`` to ``bd worktree create`` in Beads projects;
* honors ``.agents/worktree-entrypoint`` when a repository declares a more
  specific creator; and
* applies the existing location policy to otherwise-valid
  ``bd worktree create`` commands.

The declared entrypoint owns repository-specific invariants such as refreshing
and verifying ``origin/main``. This hook is an accidental-bypass guardrail, not
a shell security boundary. Dynamic expansion, aliases, nested interpreters,
and adversarial shell obfuscation are intentionally outside its contract.

A denial is a ``permissionDecision="deny"`` JSON document on stdout with exit
code 0. Malformed hook payloads and unparseable shell text fail open.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterator, List, NamedTuple, NoReturn, Optional

# Shared signal capture per claude/rules/gate-design.md Rule 2.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None

from beads_worktree_location_guard import evaluate_bd_worktree_location


_SHELL_SEP_RE = re.compile(r"&&|\|\||[;|\n]")
_ENVVAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ENTRYPOINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MARKER_RELATIVE_PATH = Path(".agents/worktree-entrypoint")

_GIT_VALUE_FLAGS = frozenset({
    "-C",
    "--work-tree",
    "--git-dir",
    "--git-common-dir",
    "--namespace",
    "--super-prefix",
    "--config-env",
    "-c",
})
_GIT_TERMINAL_FLAGS = frozenset({
    "-v",
    "--version",
    "-h",
    "--help",
    "--exec-path",
    "--html-path",
    "--man-path",
    "--info-path",
})
_GIT_BARE_FLAG_RE = re.compile(
    r"^(?:-p|--paginate|-P|--no-pager|--no-optional-locks|--no-replace-objects|"
    r"--no-lazy-fetch|--no-advice|--bare|--exec-path=\S+|"
    r"--list-cmds=\S+|--literal-pathspecs|--no-literal-pathspecs|"
    r"--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs|"
    r"-(?:q+))$"
)
_BD_VALUE_FLAGS = frozenset({
    "-C",
    "--directory",
    "--db",
    "--actor",
    "--dolt-auto-commit",
})
_BD_TERMINAL_FLAGS = frozenset({
    "-h",
    "--help",
    "-V",
    "--version",
})
_BD_BARE_FLAGS = frozenset({
    "--global",
    "--ignore-schema-skew",
    "--json",
    "--profile",
    "-q",
    "--quiet",
    "--readonly",
    "--sandbox",
    "-v",
    "--verbose",
})


class LiteralCreate(NamedTuple):
    kind: str
    tokens: List[str]
    args_index: int
    effective_cwd: Path


def _command_index(tokens: List[str]) -> Optional[int]:
    """Return the executable index for a simple literal command segment."""
    i = 0
    n = len(tokens)
    while i < n and _ENVVAR_RE.match(tokens[i]):
        i += 1

    while i < n and tokens[i] in ("env", "command"):
        i += 1
        while i < n:
            token = tokens[i]
            if _ENVVAR_RE.match(token):
                i += 1
                continue
            if token in ("-u", "-S") and i + 1 < n:
                i += 2
                continue
            if token.startswith("-"):
                i += 1
                continue
            break

    return i if i < n else None


def _path_from(value: str, cwd: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve()
    except OSError:
        return path


def _git_dir_worktree(value: str, cwd: Path) -> Path:
    git_dir = _path_from(value, cwd)
    return git_dir.parent if git_dir.name == ".git" else cwd


def _literal_environment(tokens: List[str], command_index: int) -> dict[str, str]:
    environment = os.environ.copy()
    for token in tokens[:command_index]:
        if _ENVVAR_RE.match(token):
            name, _, value = token.partition("=")
            environment[name] = value
    return environment


def _git_policy_cwd(
    tokens: List[str],
    cwd: Path,
    command_index: int,
    subcommand_index: int,
    fallback: Path,
) -> Path:
    """Ask Git which common directory owns this literal invocation."""
    command = [
        tokens[command_index],
        *tokens[command_index + 1 : subcommand_index],
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    ]
    try:
        resolved = subprocess.run(
            command,
            cwd=cwd,
            env=_literal_environment(tokens, command_index),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return fallback
    if resolved.returncode != 0 or not resolved.stdout.strip():
        return fallback
    common_dir = Path(resolved.stdout.strip())
    try:
        common_dir = common_dir.resolve()
    except OSError:
        return fallback
    return common_dir.parent if common_dir.name == ".git" else fallback


def _git_worktree_add(
    tokens: List[str],
    cwd: Path,
) -> Optional[LiteralCreate]:
    command_index = _command_index(tokens)
    if command_index is None or tokens[command_index].split("/")[-1] != "git":
        return None

    effective_cwd = cwd
    explicit_git_dir: Optional[str] = None
    for token in tokens[:command_index]:
        if token.startswith("GIT_DIR="):
            explicit_git_dir = token.partition("=")[2]

    i = command_index + 1
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if token in _GIT_TERMINAL_FLAGS:
            return None
        if token == "-C" and i + 1 < n:
            effective_cwd = _path_from(tokens[i + 1], effective_cwd)
            i += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            effective_cwd = _path_from(token[2:], effective_cwd)
            i += 1
            continue
        if token in ("--work-tree", "--git-dir") and i + 1 < n:
            value = tokens[i + 1]
            if token == "--git-dir":
                explicit_git_dir = value
            i += 2
            continue
        if token.startswith("--work-tree="):
            i += 1
            continue
        if token.startswith("--git-dir="):
            explicit_git_dir = token.partition("=")[2]
            i += 1
            continue
        if token in _GIT_VALUE_FLAGS:
            i += 2
            continue
        if (
            token.startswith("--git-common-dir=")
            or token.startswith("--namespace=")
            or token.startswith("--super-prefix=")
            or token.startswith("--config-env=")
            or token.startswith("-c") and len(token) > 2
        ):
            i += 1
            continue
        if _GIT_BARE_FLAG_RE.match(token):
            i += 1
            continue
        break

    if i + 1 < n and tokens[i] == "worktree" and tokens[i + 1] == "add":
        fallback = (
            _git_dir_worktree(explicit_git_dir, effective_cwd)
            if explicit_git_dir is not None
            else effective_cwd
        )
        policy_cwd = _git_policy_cwd(
            tokens,
            cwd,
            command_index,
            i,
            fallback,
        )
        return LiteralCreate("git", tokens, i + 2, policy_cwd)
    return None


def _bd_worktree_create(
    tokens: List[str],
    cwd: Path,
) -> Optional[LiteralCreate]:
    command_index = _command_index(tokens)
    if command_index is None or tokens[command_index].split("/")[-1] != "bd":
        return None

    effective_cwd = cwd
    i = command_index + 1
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if token in _BD_TERMINAL_FLAGS:
            return None
        if token in ("-C", "--directory") and i + 1 < n:
            effective_cwd = _path_from(tokens[i + 1], effective_cwd)
            i += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            effective_cwd = _path_from(token[2:], effective_cwd)
            i += 1
            continue
        if token.startswith("--directory="):
            effective_cwd = _path_from(
                token.partition("=")[2],
                effective_cwd,
            )
            i += 1
            continue
        if token in _BD_VALUE_FLAGS:
            i += 2
            continue
        if token in _BD_BARE_FLAGS:
            i += 1
            continue
        if token.startswith("--"):
            i += 1
            continue
        break

    if i + 1 < n and tokens[i] == "worktree" and tokens[i + 1] == "create":
        return LiteralCreate("bd", tokens, i + 2, effective_cwd)
    return None


def _literal_creates(command: str, cwd: Path) -> Iterator[LiteralCreate]:
    """Yield ordinary literal Git or Beads worktree-create invocations."""
    for segment in _SHELL_SEP_RE.split(command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        create = _git_worktree_add(tokens, cwd)
        if create is None:
            create = _bd_worktree_create(tokens, cwd)
        if create is not None:
            yield create


def _command_has_worktree_add(command: str) -> bool:
    """Compatibility helper retained for existing host-specific fixtures."""
    return any(
        create.kind == "git"
        for create in _literal_creates(command, Path.cwd())
    )


def _beads_project_root(cwd: Path) -> Optional[Path]:
    try:
        start = cwd.resolve()
    except OSError:
        start = cwd
    for directory in (start, *start.parents):
        if (directory / ".beads").is_dir():
            return directory
    return None


def _in_beads_project(cwd: Path) -> bool:
    """Return whether cwd belongs to a Beads project.

    ``BEADS_DIR`` is retained for backward compatibility with direct commands.
    Effective ``-C`` repository routing is decided before this function is
    called, so target-repository tests remain independent of payload cwd.
    """
    return bool(os.environ.get("BEADS_DIR")) or _beads_project_root(cwd) is not None


def _repository_entrypoint(cwd: Path) -> tuple[str, Optional[str]]:
    """Return (missing|valid|invalid, entrypoint) for the effective Beads repo."""
    root = _beads_project_root(cwd)
    if root is None:
        return "missing", None
    marker = root / _MARKER_RELATIVE_PATH
    if not os.path.lexists(marker):
        return "missing", None
    if marker.is_symlink() or not marker.is_file():
        return "invalid", None
    try:
        entrypoint = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return "invalid", None
    if not _ENTRYPOINT_RE.fullmatch(entrypoint):
        return "invalid", None
    return "valid", entrypoint


def _path_and_branch(create: LiteralCreate) -> tuple[Optional[str], Optional[str]]:
    path: Optional[str] = None
    branch: Optional[str] = None
    i = create.args_index
    while i < len(create.tokens):
        token = create.tokens[i]
        if token in ("-b", "-B", "--branch") and i + 1 < len(create.tokens):
            branch = create.tokens[i + 1]
            i += 2
            continue
        if token in ("--reason",) and i + 1 < len(create.tokens):
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        if path is None:
            path = token
        i += 1
    return path, branch


def _managed_suggestion(
    entrypoint: str,
    create: LiteralCreate,
) -> str:
    path, branch = _path_and_branch(create)
    name = Path(path).name if path else "<name>"
    if not _ENTRYPOINT_RE.fullmatch(name):
        name = "<name>"
    command = [entrypoint, "create", name, "--branch", branch or "<branch>"]
    return shlex.join(command)


def _generic_suggestion(create: LiteralCreate) -> str:
    path, branch = _path_and_branch(create)
    command = ["bd", "worktree", "create", path or "<path>", "-b", branch or "<branch>"]
    return shlex.join(command)


def _emit_deny(reason: str) -> NoReturn:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def _deny_invalid_marker() -> NoReturn:
    _emit_deny(
        "The repository's `.agents/worktree-entrypoint` marker is invalid. "
        "It must contain exactly one executable name using letters, numbers, "
        "dots, underscores, or hyphens. Repair the marker before creating a "
        "worktree."
    )


def _deny_managed(entrypoint: str, create: LiteralCreate) -> NoReturn:
    suggestion = _managed_suggestion(entrypoint, create)
    _emit_deny(
        f"Direct `{create.kind}` worktree creation is blocked in this repository. "
        f"Use its declared worktree entrypoint instead: `{suggestion}`."
    )


def deny(create: LiteralCreate) -> NoReturn:
    suggestion = _generic_suggestion(create)
    _emit_deny(
        "`git worktree add` is blocked in beads projects so new worktrees use "
        f"the Beads-managed creation path: `{suggestion}`. Existing linked "
        "worktrees share tracker state through Git's common directory, but new "
        "worktrees must use the repository's managed entrypoint."
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if data.get("tool_name", "") != "Bash":
        return 0
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command", "")
    if not command:
        return 0

    cwd_raw = data.get("cwd") or data.get("workingDirectory") or os.getcwd()
    cwd = Path(cwd_raw)

    for create in _literal_creates(command, cwd):
        marker_state, entrypoint = _repository_entrypoint(create.effective_cwd)
        if marker_state == "invalid":
            _record_signal(
                gate_name="beads_worktree_guard",
                decision="deny",
                reason="invalid repository worktree entrypoint marker",
                tool="Bash",
            )
            _deny_invalid_marker()
        if marker_state == "valid" and entrypoint is not None:
            _record_signal(
                gate_name="beads_worktree_guard",
                decision="deny",
                reason=f"{create.kind} redirected to repository worktree entrypoint",
                tool="Bash",
            )
            _deny_managed(entrypoint, create)
        if create.kind == "git" and _in_beads_project(create.effective_cwd):
            _record_signal(
                gate_name="beads_worktree_guard",
                decision="deny",
                reason="git worktree add redirected to bd worktree create",
                tool="Bash",
            )
            deny(create)

    # A marker-less Beads repo retains the existing location policy.
    evaluate_bd_worktree_location(command, cwd, _record_signal)
    return 0


if __name__ == "__main__":
    sys.exit(main())
