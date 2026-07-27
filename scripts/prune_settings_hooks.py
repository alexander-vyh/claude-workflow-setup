#!/usr/bin/env python3
"""Remove plugin-owned hook registrations from a live settings.json (escapement-ptzz).

Two installers both registered escapement's hooks — ``INSTALL.sh`` (into
``~/.claude/settings.json``) and the Claude plugin (``hooks/hooks.json``). Claude
Code does not dedupe them, so 38 gates fired **twice** per matching tool call.

The plugin is now the sole owner. This prunes the settings-side duplicates.

  - SURGICAL: removes only hooks whose script basename the plugin registers.
    The user's own hooks (never shipped by escapement) are preserved verbatim.
  - BASENAME-MATCHED: the same script appears as ``~/.claude/hooks/x.py`` in
    settings and ``"${CLAUDE_PLUGIN_ROOT}/hooks/x.py"`` in the plugin.
  - MIGRATION-AWARE: when the plugin owns ``escapement_session_context.py``, the
    obsolete exact ``bd prime`` lifecycle command is removed as its predecessor.
  - SCOPED to the ``hooks`` block: every other settings key is preserved.
  - IDEMPOTENT, non-mutating, BACKED UP before writing.

Usage: prune_settings_hooks.py <plugin_hooks.json> <live_settings.json> [--dry-run]
Exit 0 on success (or no-op); 1 on error.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import re
import sys

_SCRIPT_RE = re.compile(r"([\w.-]+\.(?:py|sh))")
_SESSION_CONTEXT_SCRIPT = "escapement_session_context.py"
_LEGACY_PRIME_COMMAND = "bd prime"
_SESSION_CONTEXT_EVENTS = {"SessionStart", "PreCompact"}


def _script_of(command: str) -> str | None:
    match = _SCRIPT_RE.search(command or "")
    return match.group(1) if match else None


def plugin_owned_scripts(plugin_hooks: dict) -> set[str]:
    """Basenames of every script the plugin registers."""
    owned: set[str] = set()
    for groups in (plugin_hooks.get("hooks") or {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                script = _script_of(hook.get("command", ""))
                if script:
                    owned.add(script)
    return owned


def prune_hooks(settings: dict, owned: set[str]) -> dict:
    """Return settings with plugin-owned hook registrations removed.

    Empty hook-groups and empty events are dropped so the file does not
    accumulate husks. The input is not mutated.
    """
    out = copy.deepcopy(settings) if isinstance(settings, dict) else {}
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out

    remove_legacy_prime = _SESSION_CONTEXT_SCRIPT in owned
    surviving_events: dict[str, list] = {}
    for event, groups in hooks.items():
        surviving_groups = []
        for group in groups:
            kept = []
            for hook in group.get("hooks", []):
                command = hook.get("command", "")
                plugin_owned = _script_of(command) in owned
                legacy_prime = (
                    remove_legacy_prime
                    and event in _SESSION_CONTEXT_EVENTS
                    and isinstance(command, str)
                    and command.strip() == _LEGACY_PRIME_COMMAND
                )
                if not plugin_owned and not legacy_prime:
                    kept.append(hook)
            if kept:
                new_group = {k: v for k, v in group.items() if k != "hooks"}
                new_group["hooks"] = kept
                surviving_groups.append(new_group)
        if surviving_groups:
            surviving_events[event] = surviving_groups

    out["hooks"] = surviving_events
    return out


def _load(path: str, label: str) -> dict | None:
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"prune-settings: cannot read {label} {path}: {exc}", file=sys.stderr)
        return None


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in argv
    if len(args) != 2:
        print(
            "usage: prune_settings_hooks.py <plugin_hooks.json> <live_settings.json> [--dry-run]",
            file=sys.stderr,
        )
        return 1
    plugin_path, settings_path = args

    plugin = _load(plugin_path, "plugin hooks")
    if plugin is None:
        return 1
    if not os.path.exists(settings_path):
        print("prune-settings: no live settings.json — nothing to prune.")
        return 0
    settings = _load(settings_path, "settings")
    if settings is None:
        print("prune-settings: refusing to overwrite unreadable settings.", file=sys.stderr)
        return 1

    owned = plugin_owned_scripts(plugin)
    if not owned:
        print("prune-settings: plugin registers no hooks — refusing to prune.", file=sys.stderr)
        return 1

    pruned = prune_hooks(settings, owned)
    if pruned == settings:
        print("prune-settings: no plugin-owned duplicates in settings — already clean.")
        return 0

    def _count(cfg: dict) -> int:
        return sum(
            len(g.get("hooks", []))
            for groups in (cfg.get("hooks") or {}).values()
            for g in groups
        )

    removed = _count(settings) - _count(pruned)
    if dry_run:
        print(f"prune-settings: (dry-run) would remove {removed} duplicate registration(s).")
        return 0

    try:
        text = json.dumps(pruned, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        print(f"prune-settings: refusing to write invalid JSON: {exc}", file=sys.stderr)
        return 1

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{settings_path}.backup-{stamp}"
    with open(settings_path) as src, open(backup, "w") as dst:
        dst.write(src.read())
    print(f"prune-settings: backup -> {backup}")
    with open(settings_path, "w") as handle:
        handle.write(text)
    print(f"prune-settings: removed {removed} plugin-owned registration(s) from {settings_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
