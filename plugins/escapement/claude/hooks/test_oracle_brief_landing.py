"""Landing-command recognition and changed-file context for the oracle gate."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))
from test_oracle_brief_policy import find_git_root, is_relevant_file  # noqa: E402


SHELL_CONTROL_TOKENS = {"&&", "||", ";", "|"}
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_KEYWORDS_BEFORE_COMMAND = {"if", "then", "elif", "else", "while", "until", "do", "time", "!"}
SHELL_EXECUTABLES = {"sh", "bash", "zsh", "dash"}

GIT_VALUE_FLAGS = {
    "-C", "-c", "--git-dir", "--work-tree", "--git-common-dir",
    "--namespace", "--super-prefix",
}
GH_VALUE_FLAGS = {"--repo", "-R", "--hostname"}
BD_VALUE_FLAGS = {"--db", "-C", "--directory", "--actor", "--dolt-auto-commit"}
ENV_VALUE_FLAGS = {"-u", "--unset", "-S", "--split-string"}
ENV_BOOLEAN_FLAGS = {"-i", "--ignore-environment", "-0", "--null"}


def _split_command_segments(command: str) -> list[list[str]]:
    normalized = command.replace("\\\n", " ")
    try:
        lexer = shlex.shlex(normalized, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        tokens = []

    if tokens:
        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token in SHELL_CONTROL_TOKENS:
                if current:
                    segments.append(current)
                    current = []
                continue
            if token not in {"(", ")"}:
                current.append(token)
        if current:
            segments.append(current)
        return segments

    segments = []
    for raw in re.split(r"\s*(?:&&|\|\||;|\|)\s*", normalized):
        try:
            parts = shlex.split(raw)
        except ValueError:
            finishing_pattern = (
                r"\b(?:git\s+(?:[^\n;&|]*\s+)?(?:commit|push)|"
                r"gh\s+pr\s+(?:create|merge)|"
                r"bd\s+(?:[^\n;&|]*\s+)?close)\b"
            )
            if re.search(finishing_pattern, raw):
                return [["__unparseable_finishing_command__"]]
            continue
        if parts:
            segments.append(parts)
    return segments


def _skip_env_assignments(parts: list[str], index: int = 0) -> int:
    while index < len(parts) and ENV_ASSIGNMENT_RE.match(parts[index]):
        index += 1
    return index


def _clean_shell_token(token: str) -> str:
    return token.strip("`")


def _matches_executable(token: str, name: str) -> bool:
    return Path(_clean_shell_token(token)).name == name


def _skip_flag_values(parts: list[str], index: int, value_flags: set[str]) -> int:
    while index < len(parts):
        token = parts[index]
        if token in value_flags:
            index += 2
            continue
        if any(token.startswith(flag + "=") for flag in value_flags if flag.startswith("--")):
            index += 1
            continue
        if token.startswith("-C") and "-C" in value_flags and len(token) > 2:
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return index


def _git_subcommand(parts: list[str], index: int) -> str | None:
    index = _skip_flag_values(parts, index, GIT_VALUE_FLAGS)
    return parts[index] if index < len(parts) else None


def _gh_subcommand(parts: list[str], index: int) -> tuple[str | None, str | None]:
    index = _skip_flag_values(parts, index, GH_VALUE_FLAGS)
    if index + 1 >= len(parts):
        return None, None
    return parts[index], parts[index + 1]


def _bd_subcommand(parts: list[str], index: int) -> str | None:
    index = _skip_flag_values(parts, index, BD_VALUE_FLAGS)
    return parts[index] if index < len(parts) else None


def _env_command_index(parts: list[str], index: int) -> int:
    index += 1
    while index < len(parts):
        token = _clean_shell_token(parts[index])
        if token in ENV_BOOLEAN_FLAGS:
            index += 1
            continue
        if token in ENV_VALUE_FLAGS:
            index += 2
            continue
        if token.startswith("--unset=") or token.startswith("-u="):
            index += 1
            continue
        if token.startswith("-") or ENV_ASSIGNMENT_RE.match(token):
            index += 1
            continue
        return index
    return index


def _shell_c_command(parts: list[str], index: int) -> str | None:
    index += 1
    while index < len(parts):
        token = _clean_shell_token(parts[index])
        if token == "-c":
            return parts[index + 1] if index + 1 < len(parts) else None
        if token.startswith("-"):
            index += 1
            continue
        return None
    return None


def _is_finishing_at(parts: list[str], index: int) -> bool:
    index = _skip_env_assignments(parts, index)
    if index >= len(parts):
        return False
    token = _clean_shell_token(parts[index])
    if token in {"command", "builtin"}:
        return _is_finishing_at(parts, index + 1)
    if token == "env":
        return _is_finishing_at(parts, _env_command_index(parts, index))
    if Path(token).name in SHELL_EXECUTABLES:
        nested = _shell_c_command(parts, index)
        return bool(nested and _command_contains_finishing_action(nested))
    if _matches_executable(token, "git"):
        return _git_subcommand(parts, index + 1) in {"commit", "push"}
    if _matches_executable(token, "gh"):
        group, subcommand = _gh_subcommand(parts, index + 1)
        return group == "pr" and subcommand in {"create", "merge"}
    if _matches_executable(token, "bd"):
        return _bd_subcommand(parts, index + 1) == "close"
    return False


def _candidate_command_positions(parts: list[str]) -> set[int]:
    positions = {0}
    for index, token in enumerate(parts):
        clean = _clean_shell_token(token)
        if clean in SHELL_KEYWORDS_BEFORE_COMMAND and index + 1 < len(parts):
            positions.add(index + 1)
        if token == "$" and index + 1 < len(parts):
            positions.add(index + 1)
        if token.startswith("`"):
            positions.add(index)
    return positions


def _command_contains_finishing_action(command: str) -> bool:
    for parts in _split_command_segments(command):
        if parts == ["__unparseable_finishing_command__"]:
            return True
        if any(_is_finishing_at(parts, index) for index in sorted(_candidate_command_positions(parts))):
            return True
    return False


def _git_files(repo_root: Path, args: list[str]) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _upstream_ref(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _changed_files(repo_root: Path) -> list[str]:
    files: set[str] = set()
    files.update(_git_files(repo_root, ["diff", "--name-only"]))
    files.update(_git_files(repo_root, ["diff", "--cached", "--name-only"]))
    files.update(_git_files(repo_root, ["ls-files", "--others", "--exclude-standard"]))
    upstream = _upstream_ref(repo_root)
    if upstream:
        files.update(_git_files(repo_root, ["diff", "--name-only", f"{upstream}...HEAD"]))
    return sorted(files)


def landing_context(command: str, cwd: str | None) -> tuple[Path, list[str]] | None:
    """Return repository and relevant changed files for a finishing command."""
    if not command or not _command_contains_finishing_action(command):
        return None
    repo_root = find_git_root(cwd or Path.cwd())
    if repo_root is None:
        return None
    relevant = [name for name in _changed_files(repo_root) if is_relevant_file(name)]
    return (repo_root, relevant) if relevant else None
