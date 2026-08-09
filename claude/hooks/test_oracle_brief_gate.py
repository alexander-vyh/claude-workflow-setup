#!/usr/bin/env python3
"""Hook gate for requiring a Test Oracle Brief before behavioral code changes.

Claude use:
  PreToolUse on Write/Edit/NotebookEdit/Serena edit tools. Asks for approval
  before edits to code/test files unless .agent/runtime/test-oracle-brief.md
  exists in the repository and contains substantive required sections.

Codex use:
  PreToolUse on Bash. Blocks landing/closure commands such as git commit,
  git push, gh pr create, and bd close when code/test files changed but the
  repository lacks a structurally valid Test Oracle Brief.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Shared signal capture per claude/rules/gate-design.md Rule 2.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    _record_signal = None


BRIEF_RELATIVE_PATH = Path(".agent/runtime/test-oracle-brief.md")

REQUIRED_SECTIONS = (
    "Business invariant",
    "Independent source of truth",
    "Solution constraints",
    "Invalid solution classes",
    "Fragile implementation to reject",
    "Negative control",
    "Positive control",
    "Missing/unresolved handling",
    "Final outcome verification",
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

PLACEHOLDER_VALUES = frozenset(
    {
        "tbd",
        "todo",
        "n/a",
        "na",
        "???",
        "coming soon",
    }
)

CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".rb",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".clj",
        ".cljs",
        ".cs",
        ".fs",
        ".swift",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hpp",
        ".m",
        ".mm",
        ".php",
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
        ".lua",
        ".pl",
        ".pm",
        ".r",
        ".jl",
        ".zig",
        ".nim",
        ".v",
        ".vue",
        ".svelte",
        ".astro",
        ".ipynb",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
    }
)

TEST_FILE_PATTERNS = (
    re.compile(r"^test_.*\.py$"),
    re.compile(r"^.*_test\.py$"),
    re.compile(r"^conftest\.py$"),
    re.compile(r"^.*\.(test|spec)\.[a-z0-9]+$", re.IGNORECASE),
    re.compile(r"^.*_test\.go$"),
    re.compile(r"^.*_test\.rs$"),
)

EXEMPT_EXTENSIONS = frozenset(
    {
        ".md",
        ".mdx",
        ".rst",
        ".txt",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".env",
        ".csv",
        ".tsv",
        ".html",
        ".css",
        ".scss",
        ".sass",
        ".svg",
        ".xml",
        ".lock",
        ".sum",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".pdf",
    }
)

EXEMPT_DIR_SEGMENTS = frozenset(
    {
        ".agent",
        ".claude",
        ".codex",
        ".git",
        "docs",
        "doc",
        "scripts",
        "bin",
        "tools",
        "scratch",
        "spike",
        "spikes",
        "prototype",
        "prototypes",
        "tmp",
        "vendor",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "__pycache__",
    }
)

FINISHING_COMMANDS = (
    ("git", "commit"),
    ("git", "push"),
    ("gh", "pr", "create"),
    ("gh", "pr", "merge"),
    ("bd", "close"),
)

SHELL_CONTROL_TOKENS = {"&&", "||", ";", "|"}
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_KEYWORDS_BEFORE_COMMAND = {"if", "then", "elif", "else", "while", "until", "do", "time", "!"}
SHELL_EXECUTABLES = {"sh", "bash", "zsh", "dash"}

GIT_VALUE_FLAGS = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--git-common-dir",
    "--namespace",
    "--super-prefix",
}

GH_VALUE_FLAGS = {"--repo", "-R", "--hostname"}
BD_VALUE_FLAGS = {"--db", "-C", "--directory", "--actor", "--dolt-auto-commit"}
ENV_VALUE_FLAGS = {"-u", "--unset", "-S", "--split-string"}
ENV_BOOLEAN_FLAGS = {"-i", "--ignore-environment", "-0", "--null"}


def allow() -> int:
    return 0


def ask(reason: str) -> int:
    """Ask the user to confirm. The user can satisfy the gate by creating the
    brief OR by saying 'proceed' to override. Per gate-design.md Rule 1:
    every gate must have a first-class escape path, and an 'ask' with an
    explicit 'proceed' override is the standard convention in this repo
    (tdd-gate, outcome_assertion_gate, discovery-gate, discovery-close-gate
    all use it).
    """
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
    """Hard block via the canonical PreToolUse mechanism.

    CANONICAL DENY CONTRACT (see CONTRACT note at top of this module): a
    hard-deny hook signals the block with a SINGLE mechanism — the
    ``hookSpecificOutput.permissionDecision == "deny"`` JSON document on
    stdout, and exit code 0. Exit 2 is the *legacy* stderr-feedback path and
    is mutually exclusive with the JSON-decision path; emitting both is a
    contradictory double-block. We use the JSON path exclusively, so this
    returns 0, not 2.

    Landing actions intentionally use the hard-deny path: the edit-time ask is
    the escape path, while landing still requires a complete brief.
    """
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


def normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def section_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    stripped = re.sub(r"^#{1,6}\s*", "", stripped)
    stripped = re.sub(r"^\d+[.)]\s*", "", stripped)
    stripped = stripped.strip("*_ \t:-")
    normalized = normalize_heading(stripped)
    return next(
        (section for section in REQUIRED_SECTIONS if normalize_heading(section) == normalized),
        None,
    )


def section_has_substantive_content(lines: list[str]) -> bool:
    for line in lines:
        value = line.strip()
        value = re.sub(r"^(?:[-*+]|\d+[.)])\s*", "", value)
        value = value.strip("`*_ \t")
        if not value:
            continue
        normalized = re.sub(r"\s+", " ", value).strip(" .:;").casefold()
        if normalized not in PLACEHOLDER_VALUES:
            return True
    return False


def missing_brief_sections(brief_path: Path) -> list[str]:
    try:
        text = brief_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return list(REQUIRED_SECTIONS)
    section_bodies: dict[str, list[str]] = {}
    active_section: str | None = None
    for line in text.splitlines():
        heading = section_heading(line)
        if heading is not None:
            active_section = heading
            section_bodies.setdefault(heading, [])
        elif active_section is not None:
            section_bodies[active_section].append(line)

    return [
        section
        for section in REQUIRED_SECTIONS
        if not section_has_substantive_content(section_bodies.get(section, []))
    ]


def find_git_root(start: str | Path) -> Path | None:
    path = Path(start)
    search_dir = path if path.is_dir() else path.parent
    while not search_dir.exists():
        parent = search_dir.parent
        if parent == search_dir:
            return None
        search_dir = parent

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(search_dir),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def is_test_file(filepath: str) -> bool:
    path = Path(filepath)
    parts = set(path.parts)
    if parts & {"tests", "test", "spec", "__tests__", "__specs__"}:
        return True
    return any(pattern.match(path.name) for pattern in TEST_FILE_PATTERNS)


def is_relevant_file(filepath: str) -> bool:
    path = Path(filepath)
    parts = set(path.parts)

    if path.name == "__init__.py":
        return False
    if parts & EXEMPT_DIR_SEGMENTS:
        return False
    if is_test_file(filepath):
        return True

    suffix = path.suffix.lower()
    if suffix in EXEMPT_EXTENSIONS:
        return False
    return suffix in CODE_EXTENSIONS


def resolve_target_path(filepath: str, cwd: str | None) -> Path:
    path = Path(filepath).expanduser()
    if path.is_absolute():
        return path
    return Path(cwd or os.getcwd()) / path


def brief_status(repo_root: Path) -> tuple[bool, str | None, str]:
    brief_path = repo_root / BRIEF_RELATIVE_PATH
    if not brief_path.exists():
        return (
            False,
            f"Missing required Test Oracle Brief: {BRIEF_RELATIVE_PATH}",
            "missing-brief",
        )
    missing = missing_brief_sections(brief_path)
    if missing:
        return (
            False,
            "Test Oracle Brief is missing required substantive content for: "
            + ", ".join(missing),
            "invalid-brief",
        )
    return True, None, "valid-brief"


def record_decision_signal(
    data: dict,
    *,
    decision: str,
    reason: str,
    category: str,
    target: str,
    **extras,
) -> None:
    invocation_id = data.get("tool_use_id") or data.get("prompt_id") or data.get("session_id")
    signal_extras = {
        "tool": data.get("tool_name", ""),
        "target": target,
        "category": category,
        **extras,
    }
    if isinstance(invocation_id, str) and invocation_id:
        signal_extras["invocation_id"] = invocation_id

    if _record_signal is None:
        print(
            "test_oracle_brief_gate: gate signal unavailable; decision preserved",
            file=sys.stderr,
        )
        return
    try:
        _record_signal(
            gate_name="test_oracle_brief_gate",
            decision=decision,
            reason=reason,
            **signal_extras,
        )
    except Exception as error:  # pragma: no cover - recorder is fail-soft itself
        print(
            f"test_oracle_brief_gate: gate signal unavailable ({error}); decision preserved",
            file=sys.stderr,
        )


def block_message(
    reason: str,
    repo_root: Path,
    files: list[str],
    *,
    override_available: bool,
) -> str:
    sample_files = "\n".join(f"  - {name}" for name in files[:8])
    if len(files) > 8:
        sample_files += f"\n  - ... {len(files) - 8} more"

    message = (
        f"{reason}\n\n"
        "Before editing or landing behavior-bearing code/tests, create:\n"
        f"  {repo_root / BRIEF_RELATIVE_PATH}\n\n"
        "Required headings:\n"
        + "\n".join(f"  - {section}" for section in REQUIRED_SECTIONS)
        + "\n\n"
        "Relevant changed/target files:\n"
        + (sample_files if sample_files else "  - unknown")
    )
    if override_available:
        message += (
            "\n\nIf this work is genuinely exempt (one-off fix, scripts/, "
            "spike/, no real behavior change), say 'proceed' to skip the "
            "brief for this change. The override is captured; if it gets "
            "used often, the gate's heuristic needs tuning."
        )
    return message


def handle_edit_gate(data: dict) -> int:
    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    tool_name = data.get("tool_name", "")
    if hook_event != "PreToolUse" or tool_name not in GATED_EDIT_TOOLS:
        return allow()

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return allow()

    raw_path = next(
        (
            tool_input.get(path_key)
            for path_key in FILE_PATH_KEYS
            if isinstance(tool_input.get(path_key), str) and tool_input.get(path_key).strip()
        ),
        None,
    )
    if not isinstance(raw_path, str) or not raw_path.strip():
        return allow()

    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else None
    target = resolve_target_path(raw_path, cwd)
    repo_root = find_git_root(target)
    if repo_root is None:
        return allow()

    try:
        rel_target = target.relative_to(repo_root).as_posix()
    except ValueError:
        rel_target = raw_path

    if not is_relevant_file(rel_target):
        return allow()

    ok, reason, category = brief_status(repo_root)
    if ok:
        record_decision_signal(
            data,
            decision="allow",
            reason="oracle brief present and valid",
            category=category,
            target=rel_target,
        )
        return allow()
    reason = reason or "Test Oracle Brief is missing required content."
    record_decision_signal(
        data,
        decision="ask",
        reason=reason or "Invalid Test Oracle Brief.",
        category=category,
        target=rel_target,
    )
    return ask(
        block_message(
            reason,
            repo_root,
            [rel_target],
            override_available=True,
        )
    )


def command_from(data: dict) -> str:
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def split_command_segments(command: str) -> list[list[str]]:
    normalized = command.replace("\\\n", " ")
    try:
        lexer = shlex.shlex(normalized, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        # A malformed shell command should not silently launder a finishing
        # action. Fall back to the broad regex splitter used by the old path.
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
            if token in {"(", ")"}:
                continue
            current.append(token)
        if current:
            segments.append(current)
        return segments

    segments: list[list[str]] = []
    for raw in re.split(r"\s*(?:&&|\|\||;|\|)\s*", normalized):
        try:
            parts = shlex.split(raw)
        except ValueError:
            if re.search(r"\b(?:git\s+(?:[^\n;&|]*\s+)?(?:commit|push)|gh\s+pr\s+(?:create|merge)|bd\s+(?:[^\n;&|]*\s+)?close)\b", raw):
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
        if token.startswith("-"):
            index += 1
            continue
        if ENV_ASSIGNMENT_RE.match(token):
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
        return bool(nested and command_contains_finishing_action(nested))

    if _matches_executable(token, "git"):
        subcommand = _git_subcommand(parts, index + 1)
        return subcommand in {"commit", "push"}

    if _matches_executable(token, "gh"):
        group, subcommand = _gh_subcommand(parts, index + 1)
        return group == "pr" and subcommand in {"create", "merge"}

    if _matches_executable(token, "bd"):
        subcommand = _bd_subcommand(parts, index + 1)
        return subcommand == "close"

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


def is_finishing_segment(parts: list[str]) -> bool:
    if parts == ["__unparseable_finishing_command__"]:
        return True
    return any(_is_finishing_at(parts, index) for index in sorted(_candidate_command_positions(parts)))


def command_contains_finishing_action(command: str) -> bool:
    return any(is_finishing_segment(parts) for parts in split_command_segments(command))


def git_files(repo_root: Path, args: list[str]) -> list[str]:
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


def upstream_ref(repo_root: Path) -> str | None:
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
    value = result.stdout.strip()
    return value or None


def changed_files_for_landing(repo_root: Path) -> list[str]:
    files: set[str] = set()
    files.update(git_files(repo_root, ["diff", "--name-only"]))
    files.update(git_files(repo_root, ["diff", "--cached", "--name-only"]))
    files.update(git_files(repo_root, ["ls-files", "--others", "--exclude-standard"]))

    upstream = upstream_ref(repo_root)
    if upstream:
        files.update(git_files(repo_root, ["diff", "--name-only", f"{upstream}...HEAD"]))

    return sorted(files)


def handle_bash_landing_gate(data: dict) -> int:
    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    tool_name = data.get("tool_name", "")
    if hook_event != "PreToolUse" or tool_name != "Bash":
        return allow()

    command = command_from(data)
    if not command:
        return allow()
    if not command_contains_finishing_action(command):
        return allow()

    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    repo_root = find_git_root(cwd)
    if repo_root is None:
        return allow()

    relevant = [name for name in changed_files_for_landing(repo_root) if is_relevant_file(name)]
    if not relevant:
        return allow()

    ok, reason, category = brief_status(repo_root)
    target = relevant[0]
    if ok:
        record_decision_signal(
            data,
            decision="allow",
            reason="oracle brief present and valid",
            category=category,
            target=target,
            surface="landing-command",
            file_count=len(relevant),
        )
        return allow()
    reason = reason or "Test Oracle Brief is missing required content."
    record_decision_signal(
        data,
        decision="deny",
        reason=reason,
        category=category,
        target=target,
        surface="landing-command",
        file_count=len(relevant),
    )
    return deny(
        block_message(
            reason,
            repo_root,
            relevant,
            override_available=False,
        )
    )


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
