#!/usr/bin/env python3
"""Remove only legacy global hooks now owned by the Escapement dispatcher."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
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
LEGACY_REGISTRATIONS = {
    "test_oracle_brief_gate.py": {
        "statusMessage": "Checking Test Oracle Brief gate",
        "timeout": 30,
        "sha256": {
            "1213b2981f7a78664b749da5c107ce454df4d585568bd699909e4ed897df9fdb",
            "d9b69df769e10fe12146c988f30f0a83d542a567e1a27eb5abe8802b32a13a43",
        },
    },
    "implementation_echo_test_gate.py": {
        "statusMessage": "Checking implementation-echo tests",
        "timeout": 30,
        "sha256": {
            "63b324594a1eb6ab2ebd1902c72e277fb7bfc280c7d6c98b752a7da76f8e511e",
            "1fa43667fbebac9555573729873faf3d269fc68d88f7e680570ef9abdf8ce19e",
        },
    },
    "oracle_downgrade_warning_gate.py": {
        "statusMessage": "Checking oracle downgrade warnings",
        "timeout": 30,
        "sha256": {
            "ef0eb10e0a67cf6ca3609c51497d25cf98986e09aea5ad75ad12eb058fb3c3ff",
            "b58fec6c22be3be34e68415ebfc70ecc7544b802e7cc45e9baf2da32939b95c8",
        },
    },
    "beads_worktree_guard.py": {
        "statusMessage": "Checking bd worktree location (.worktrees/)",
        "timeout": 10,
        "sha256": {
            "9bdc640a7ee7568454923727961b129871c2f43d5121343abc8ee98c8e0d642c",
            "ddc78e1978810b652eeb53a02563cd03cf67088adc7225beb1fb4a8299921c6b",
        },
    },
}


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
    hook: dict[str, Any],
    owned: set[str],
    roots: set[Path],
) -> bool:
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    tokens = _tokens(command)
    if not tokens or any(token in SHELL_OPERATORS for token in tokens):
        return False
    interpreter = Path(tokens[0]).name
    if not PYTHON_COMMAND.fullmatch(interpreter):
        return False
    if len(tokens) != 2:
        return False
    script = Path(tokens[1]).expanduser()
    registration = LEGACY_REGISTRATIONS.get(script.name)
    if (
        not script.is_absolute()
        or script.name not in owned
        or registration is None
        or script.is_symlink()
        or not script.is_file()
        or set(hook) != {"command", "statusMessage", "timeout", "type"}
        or hook.get("type") != "command"
        or hook.get("statusMessage") != registration["statusMessage"]
        or hook.get("timeout") != registration["timeout"]
    ):
        return False
    if script.parent.resolve() not in roots:
        return False
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    return digest in registration["sha256"]


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
            if (
                event != "PreToolUse"
                or not isinstance(group, dict)
                or group.get("matcher") != "Bash"
                or not isinstance(group.get("hooks"), list)
            ):
                surviving_groups.append(group)
                continue
            group["hooks"] = [
                hook
                for hook in group["hooks"]
                if not (
                    isinstance(hook, dict)
                    and _legacy_script(hook, owned, roots)
                )
            ]
            if group["hooks"]:
                surviving_groups.append(group)
        events[event] = surviving_groups
    return pruned


def _prepare_atomic(path: Path, content: bytes, mode: int) -> Path:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(mode)
        return temporary_path
    except BaseException:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _sync_directory(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    prepared = _prepare_atomic(path, content, mode)
    try:
        os.replace(prepared, path)
        _sync_directory(path.parent)
    finally:
        prepared.unlink(missing_ok=True)


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(f"{path.name}.backup-{stamp}")


def _conflict_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(f"{path.name}.conflict-{stamp}")


def _replace_prepared(path: Path, prepared: Path, inspected: bytes) -> None:
    current = path.read_bytes()
    if current != inspected:
        conflict = _conflict_path(path)
        _atomic_write(conflict, current, path.stat().st_mode & 0o777)
        raise RuntimeError(
            "Codex hooks changed during migration; refusing overwrite; "
            f"concurrent bytes preserved at {conflict}"
        )
    os.replace(prepared, path)
    _sync_directory(path.parent)


def write_if_unchanged(
    path: Path,
    *,
    inspected: bytes,
    replacement: bytes,
    backup_path: Path,
) -> None:
    """Durably back up and replace only the exact bytes that were inspected."""

    if path.read_bytes() != inspected:
        raise RuntimeError("Codex hooks changed during migration; refusing overwrite")
    mode = path.stat().st_mode & 0o777
    _atomic_write(backup_path, inspected, mode)
    prepared = _prepare_atomic(path, replacement, mode)
    try:
        _replace_prepared(path, prepared, inspected)
    finally:
        prepared.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin_hooks", type=Path)
    parser.add_argument("live_hooks", type=Path)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    lock_path = args.live_hooks.with_name(f"{args.live_hooks.name}.migration.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.live_hooks.is_symlink():
            raise SystemExit(
                f"FATAL: refusing to detach symlinked Codex hooks: {args.live_hooks}"
            )
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
            print(
                f"Would remove {removed} legacy Escapement global hook registration(s)"
            )
            return 0

        backup = _backup_path(args.live_hooks)
        rendered = (json.dumps(pruned, indent=2) + "\n").encode()
        write_if_unchanged(
            args.live_hooks,
            inspected=original,
            replacement=rendered,
            backup_path=backup,
        )
        print(
            f"Removed {removed} legacy Escapement hook registration(s); "
            f"backup: {backup}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
