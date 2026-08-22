#!/usr/bin/env python3
"""Claude Code hook: block openspec commands when openspec/ doesn't exist.

Fires as PreToolUse on Bash commands containing `openspec`.

Blocks openspec commands (except `openspec init`) when the openspec/
directory doesn't exist in the current working directory. This prevents
confusing errors from openspec when it hasn't been initialized.

Fast-path exits:
  - Non-PreToolUse events
  - Non-Bash tools
  - Commands without "openspec"
  - openspec init commands (always allowed — that's how you set up)
  - openspec --help/--version commands (informational, always allowed)

Fail-open: any error silently allows.

Input (via stdin):
  JSON with hook_event_name, tool_name, tool_input
Exit codes:
  0 — allow
  2 — deny (JSON output explains why)
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn


# ---------------------------------------------------------------------------
# Command classification
# ---------------------------------------------------------------------------

# Commands that should always be allowed even without openspec/
_ALWAYS_ALLOWED = re.compile(
    r'openspec\s+(?:init|--help|-h|--version|-V|completion|config|feedback)',
    re.IGNORECASE,
)


def is_openspec_command(command: str) -> bool:
    """Return True if the command invokes openspec."""
    return bool(re.search(r'\bopenspec\b', command))


def is_always_allowed(command: str) -> bool:
    """Return True if this openspec subcommand doesn't require initialization."""
    return bool(_ALWAYS_ALLOWED.search(command))


# ---------------------------------------------------------------------------
# Init detection
# ---------------------------------------------------------------------------

def openspec_is_initialized(project_dir: str) -> bool:
    """Check if openspec/ directory exists in the given project directory."""
    if not project_dir:
        return False
    return Path(project_dir, "openspec").is_dir()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def allow() -> int:
    """Allow the action silently."""
    return 0


def deny(message: str) -> NoReturn:
    """Deny the action with an explanation.

    CANONICAL DENY CONTRACT: signal the block with a single mechanism — the
    permissionDecision="deny" JSON document on stdout, exit 0. Exit 2 is the
    mutually-exclusive legacy stderr-feedback path; emitting both is a
    contradictory double-block. We use the JSON path, so this exits 0.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }
    }))
    sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # Fail-open

    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    if hook_event != "PreToolUse":
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        return 0

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    # Fast-path: not an openspec command
    if not is_openspec_command(command):
        return 0

    # Always allow init, help, version, config, etc.
    if is_always_allowed(command):
        return allow()

    # Determine the project directory from the hook payload.
    # Claude Code passes the working directory as "cwd" in the hook JSON.
    # Fall back to os.getcwd() as a last resort.
    project_dir = data.get("cwd", "") or data.get("workingDirectory", "") or os.getcwd()

    # Check if openspec is initialized
    if openspec_is_initialized(project_dir):
        return allow()

    # Not initialized — block
    deny(
        "openspec/ directory not found in the current project. "
        "Run `openspec init` first to set up OpenSpec in this project. "
        "If this project doesn't use OpenSpec, you may be in the wrong directory."
    )

    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
