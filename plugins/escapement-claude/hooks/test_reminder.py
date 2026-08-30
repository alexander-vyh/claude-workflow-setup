#!/usr/bin/env python3
"""Claude Code hook: PostToolUse test reminder after file edits.

Fires after Write and Edit tool calls. If the edited file is a code file
(not docs/config) and the project has test infrastructure, emits a
systemMessage reminding the agent to run tests — with the likely test
command auto-detected.

Cooldown: at most one reminder per 60 seconds per session.

Exit codes:
  0 — always (advisory only, never blocks)
"""

# PEP 604 annotations below are evaluated when each def executes, so without
# this import the module raises TypeError on import under Python 3.9.
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_COOLDOWN_SECONDS = 60

# Extensions that are NOT code — skip reminder for these
_NON_CODE_EXTENSIONS = frozenset({
    ".md", ".rst", ".txt",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".tsv",
    ".env", ".env.example", ".env.local",
    ".gitignore", ".dockerignore", ".editorconfig",
    ".lock", ".sum",
    ".html", ".css", ".svg", ".xml",
    ".sql",
    ".license", ".licence",
})

# Filenames (case-insensitive) that are not code
_NON_CODE_FILENAMES = frozenset({
    "license", "licence", "changelog", "authors", "contributors",
    "readme", "makefile", "dockerfile", "justfile",
    ".gitignore", ".dockerignore", ".editorconfig",
    ".eslintrc", ".prettierrc", ".prettierignore",
})


# ---------------------------------------------------------------------------
# State management (cooldown)
# ---------------------------------------------------------------------------

def _state_file() -> Path:
    session_id = os.environ.get("CLAUDE_SESSION_ID") or str(os.getppid())
    return Path(f"/tmp/test_reminder_{session_id}.ts")


def _read_last_fire(path: Path) -> float:
    try:
        return float(path.read_text().strip())
    except (OSError, ValueError):
        return 0.0


def _write_last_fire(path: Path, ts: float) -> None:
    try:
        path.write_text(str(ts) + "\n")
    except OSError:
        pass


def _cooldown_active() -> bool:
    state = _state_file()
    last = _read_last_fire(state)
    return (time.time() - last) < _COOLDOWN_SECONDS


def _record_fire() -> None:
    _write_last_fire(_state_file(), time.time())


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

def _is_code_file(filepath: str) -> bool:
    """Return True if the file looks like a code file worth testing."""
    name = os.path.basename(filepath).lower()
    ext = os.path.splitext(name)[1].lower()

    # Check by extension
    if ext in _NON_CODE_EXTENSIONS:
        return False

    # No extension — check by filename
    if not ext and name in _NON_CODE_FILENAMES:
        return False

    # Must have a recognized code extension
    code_extensions = frozenset({
        ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
        ".go", ".rs", ".rb", ".java", ".kt", ".scala", ".clj",
        ".c", ".cpp", ".cc", ".h", ".hpp",
        ".cs", ".fs",
        ".swift", ".m",
        ".lua", ".ex", ".exs", ".erl",
        ".php", ".pl", ".pm",
        ".r", ".jl",
        ".zig", ".nim", ".v",
        ".sh", ".bash", ".zsh",
    })
    return ext in code_extensions


# ---------------------------------------------------------------------------
# Test infrastructure detection
# ---------------------------------------------------------------------------

def _find_git_root(filepath: str) -> str | None:
    """Walk up from filepath to find .git root."""
    import subprocess

    search_dir = Path(filepath).parent
    while not search_dir.is_dir():
        parent = search_dir.parent
        if parent == search_dir:
            return None
        search_dir = parent

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
            cwd=str(search_dir),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _detect_test_command(project_root: str) -> str | None:
    """Detect test infrastructure and return the likely test command.

    Returns None if no test infrastructure is found.
    """
    root = Path(project_root)

    # Check for test directories
    has_tests_dir = (root / "tests").is_dir() or (root / "test").is_dir()

    # Check for test config files and determine the right command
    # Order matters: more specific configs first

    # --- Justfile with test recipe ---
    justfile = root / "Justfile"
    if not justfile.exists():
        justfile = root / "justfile"
    if justfile.exists():
        try:
            content = justfile.read_text(errors="replace")
            # Look for a test recipe (line starting with "test" possibly
            # followed by args or colon)
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("test") and (
                    len(stripped) == 4
                    or stripped[4] in (" ", ":", "\t")
                ):
                    return "just test"
        except OSError:
            pass

    # --- Python: pytest ---
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
        # Check pyproject.toml for [tool.pytest] section
        pyproject = root / "pyproject.toml"
        is_python = (root / "pytest.ini").exists()
        if not is_python and pyproject.exists():
            try:
                content = pyproject.read_text(errors="replace")
                is_python = "[tool.pytest" in content or "[project]" in content
            except OSError:
                pass
        if is_python and has_tests_dir:
            # Prefer uv if uv.lock exists, else plain pytest
            if (root / "uv.lock").exists():
                return "uv run pytest"
            return "pytest"

    # --- Node.js: npm/yarn/pnpm test ---
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(errors="replace"))
            scripts = pkg.get("scripts", {})
            if "test" in scripts:
                # Check for specific test runners in devDependencies
                dev_deps = pkg.get("devDependencies", {})
                deps = pkg.get("dependencies", {})
                all_deps = {**deps, **dev_deps}

                if "vitest" in all_deps:
                    return "npm run test"
                if "jest" in all_deps:
                    return "npm test"
                # Generic
                return "npm test"
        except (OSError, json.JSONDecodeError):
            pass

    # Vitest / Jest config files
    for pattern_name in ("vitest.config", "jest.config"):
        for ext in (".js", ".ts", ".mjs", ".cjs"):
            if (root / f"{pattern_name}{ext}").exists():
                return "npm test"

    # --- Go ---
    if (root / "go.mod").exists() and has_tests_dir:
        return "go test ./..."

    # --- Rust ---
    if (root / "Cargo.toml").exists():
        return "cargo test"

    # --- Ruby ---
    if (root / "Gemfile").exists() and has_tests_dir:
        return "bundle exec rspec"

    # --- Fallback: test directory exists but no specific runner detected ---
    if has_tests_dir:
        return "the project's test suite"

    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # Only fire for Write and Edit
    if tool_name not in ("Write", "Edit"):
        return 0

    # Extract file path
    if not isinstance(tool_input, dict):
        return 0
    filepath = tool_input.get("file_path", "")
    if not filepath:
        return 0

    # Skip non-code files
    if not _is_code_file(filepath):
        return 0

    # Find project root
    project_root = _find_git_root(filepath)
    if not project_root:
        return 0

    # Detect test infrastructure
    test_cmd = _detect_test_command(project_root)
    if not test_cmd:
        return 0

    # Check cooldown
    if _cooldown_active():
        return 0

    # Fire the reminder
    _record_fire()

    message = f"You modified code — run tests to verify. Run `{test_cmd}`"
    json.dump({"systemMessage": message}, sys.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
