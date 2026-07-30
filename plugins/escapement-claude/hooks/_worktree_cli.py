"""Locate Escapement's bundled worktree CLI for hook repair guidance."""

from __future__ import annotations

from pathlib import Path


def bundled_cli_path(hook_file: Path) -> Path | None:
    """Resolve the canonical CLI beside a source, Codex, or flat Claude hook."""
    here = hook_file.resolve()
    candidates = (
        here.parents[2] / "bin" / "escapement-worktree",
        here.parents[1] / "bin" / "escapement-worktree",
    )
    return next((path for path in candidates if path.is_file()), None)


def bundled_cli_prefix(hook_file: Path) -> tuple[str, str, str] | None:
    """Return the Python command prefix when the CLI is packaged correctly."""
    path = bundled_cli_path(hook_file)
    return ("python3", "-B", str(path)) if path is not None else None
