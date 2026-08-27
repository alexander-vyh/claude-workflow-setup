"""Render the minimal Pi package surface from Escapement's neutral manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PI_PLUGIN_ROOT = Path("plugins/escapement-pi")
PI_HOOK_SUPPORT = {
    "claude/hooks/codex_pretool_dispatch.py",
    "claude/hooks/root_checkout_guard.py",
    "harness/bin/repo_outcome.py",
}


def ready_bash_gates(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    adapter = manifest["adapters"]["pi"]
    gates: list[dict[str, Any]] = []
    for hook in manifest.get("hooks", []):
        host = hook.get("hosts", {}).get(adapter["gate_source_host"], {})
        if host.get("status") != "ready":
            continue
        for event in host.get("events", []):
            if (
                event.get("event") == adapter["source_event"]
                and event.get("matcher") == adapter["source_matcher"]
            ):
                gates.append(
                    {
                        "id": hook["id"],
                        "source": hook["source"],
                        "timeout_seconds": event["timeout_seconds"],
                    }
                )
    return gates


def render_gate_inventory(manifest: dict[str, Any]) -> str:
    payload = {
        "version": 1,
        "dispatcher": "claude/hooks/codex_pretool_dispatch.py",
        "gates": ready_bash_gates(manifest),
    }
    return json.dumps(payload, indent=2) + "\n"


def render_package(identity: dict[str, Any]) -> str:
    payload = {
        "name": "escapement",
        "version": "1.0.0",
        "description": identity["mission"],
        "license": "GPL-3.0-or-later",
        "repository": "https://github.com/alexander-vyh/escapement",
        "keywords": ["pi-package", "workflow", "oracle", "tdd", "beads"],
        "pi": {
            "extensions": ["./plugins/escapement-pi/extensions/index.ts"],
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def validate_adapter(manifest: dict[str, Any]) -> list[str]:
    expected = {
        "gate_source_host": "codex",
        "source_event": "PreToolUse",
        "source_matcher": "Bash",
        "target_event": "tool_call",
        "target_matcher": "bash",
    }
    errors: list[str] = []
    if manifest.get("adapters", {}).get("pi") != expected:
        errors.append("Pi adapter mapping must preserve the verified Bash event contract")
    if not ready_bash_gates(manifest):
        errors.append("Pi adapter must select at least one ready behavioral gate")
    return errors
