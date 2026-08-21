#!/usr/bin/env python3
"""Remove only legacy global hooks now owned by the Escapement dispatcher."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PYTHON_COMMAND = re.compile(r"python(?:3(?:\.\d+)?)?")
SHELL_OPERATORS = {"&", "&&", ";", "<", "<<", ">", ">>", "|", "||"}


def _tokens(command: str) -> list[str] | None:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def plugin_owned_gate_scripts(plugin_hooks: dict[str, Any]) -> set[str]:
    """Return basenames declared by the plugin's generated Bash dispatcher."""

    owned: set[str] = set()
    for group in plugin_hooks.get("hooks", {}).get("PreToolUse", []):
        if group.get("matcher") != "Bash":
            continue
        for hook in group.get("hooks", []):
            command = hook.get("command")
            if not isinstance(command, str) or "codex_pretool_dispatch.py" not in command:
                continue
            tokens = _tokens(command)
            if tokens is None:
                continue
            for index, token in enumerate(tokens[:-1]):
                if token == "--gate":
                    owned.add(Path(tokens[index + 1]).name)
    return owned


def _legacy_script(
    command: object,
    owned: set[str],
    roots: set[Path],
) -> bool:
    if not isinstance(command, str):
        return False
    tokens = _tokens(command)
    if not tokens or any(token in SHELL_OPERATORS for token in tokens):
        return False
    interpreter = Path(tokens[0]).name
    if not PYTHON_COMMAND.fullmatch(interpreter):
        return False
    index = 1
    while index < len(tokens) and tokens[index] in {"-B", "-E", "-I", "-s", "-S"}:
        index += 1
    if index >= len(tokens):
        return False
    script = Path(tokens[index]).expanduser()
    if not script.is_absolute() or script.name not in owned:
        return False
    return script.parent.resolve() in roots


def prune_hooks(
    document: dict[str, Any],
    owned: set[str],
    *,
    codex_home: Path,
    home: Path,
) -> dict[str, Any]:
    """Return a copy with positively identified legacy registrations removed."""

    pruned = copy.deepcopy(document)
    roots = {
        (codex_home / "hooks").expanduser().resolve(),
        (home / ".claude" / "hooks").expanduser().resolve(),
    }
    events = pruned.get("hooks")
    if not isinstance(events, dict):
        return pruned
    for event, groups in list(events.items()):
        if not isinstance(groups, list):
            continue
        surviving_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                surviving_groups.append(group)
                continue
            group["hooks"] = [
                hook
                for hook in group["hooks"]
                if not (
                    isinstance(hook, dict)
                    and _legacy_script(hook.get("command"), owned, roots)
                )
            ]
            if group["hooks"]:
                surviving_groups.append(group)
        events[event] = surviving_groups
    return pruned


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(mode)
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(f"{path.name}.backup-{stamp}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin_hooks", type=Path)
    parser.add_argument("live_hooks", type=Path)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.live_hooks.exists():
        print(f"Codex global hooks already clean: {args.live_hooks} does not exist")
        return 0
    plugin = json.loads(args.plugin_hooks.read_text(encoding="utf-8"))
    original = args.live_hooks.read_bytes()
    live = json.loads(original)
    owned = plugin_owned_gate_scripts(plugin)
    if not owned:
        raise SystemExit("FATAL: dispatcher declares no owned gate scripts")
    pruned = prune_hooks(
        live,
        owned,
        codex_home=args.codex_home,
        home=args.home,
    )
    if pruned == live:
        print("Codex global hooks already clean; no migration needed")
        return 0

    removed = sum(
        len(group.get("hooks", []))
        for groups in live.get("hooks", {}).values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
    ) - sum(
        len(group.get("hooks", []))
        for groups in pruned.get("hooks", {}).values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
    )
    if args.dry_run:
        print(f"Would remove {removed} legacy Escapement global hook registration(s)")
        return 0

    backup = _backup_path(args.live_hooks)
    backup.write_bytes(original)
    backup.chmod(args.live_hooks.stat().st_mode & 0o777)
    rendered = (json.dumps(pruned, indent=2) + "\n").encode()
    _atomic_write(args.live_hooks, rendered, args.live_hooks.stat().st_mode & 0o777)
    print(f"Removed {removed} legacy Escapement hook registration(s); backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
