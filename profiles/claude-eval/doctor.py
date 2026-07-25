#!/usr/bin/env python3
"""Validate the Escapement Claude eval profile and optional install target."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PROFILE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROFILE_DIR.parents[1]
SETTINGS = PROFILE_DIR / "settings.json"
WORKFLOW_SETTINGS = PROFILE_DIR / "settings.workflow.json"
MANIFEST = PROFILE_DIR / "manifest.json"

FORBIDDEN_TEXT = (
    "/Users/",
    "refresh_token",
    "sessionToken",
    "apiKey",
    "sk-ant-",
)

SETTINGS_BY_PROFILE = {
    "gates": SETTINGS,
    "workflow": WORKFLOW_SETTINGS,
}

ALLOWED_EXTERNAL_COMMANDS: set[str] = set()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def hook_commands(settings: dict) -> list[str]:
    commands: list[str] = []
    for event_items in settings.get("hooks", {}).values():
        for item in event_items:
            for hook in item.get("hooks", []):
                command = hook.get("command")
                if command:
                    commands.append(command)
    return commands


def command_refs(command: str) -> list[str]:
    return re.findall(r"~/.claude/([^\"' ]+)", command)


def source_for_ref(ref: str) -> Path:
    parts = Path(ref).parts
    if not parts:
        return REPO_ROOT / ref
    if parts[0] == "hooks":
        return REPO_ROOT / "claude" / ref
    if parts[0] in {"skills", "rules", "commands", "agents"}:
        return REPO_ROOT / "claude" / ref
    if parts[0] == "harness":
        return REPO_ROOT / ref
    return REPO_ROOT / ref


def validate_profile() -> list[str]:
    errors: list[str] = []
    manifest = load_json(MANIFEST)
    profile_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (SETTINGS, WORKFLOW_SETTINGS, MANIFEST, PROFILE_DIR / "install.sh")
    )

    for forbidden in FORBIDDEN_TEXT:
        if forbidden in profile_text:
            errors.append(f"profile contains forbidden token: {forbidden}")

    for profile_name, settings_path in SETTINGS_BY_PROFILE.items():
        settings = load_json(settings_path)
        commands = hook_commands(settings)
        if not commands:
            errors.append(f"{settings_path.name} contains no hook commands")

        required_key = "required_hooks"
        if profile_name == "workflow":
            required_key = "required_workflow_hooks"
        for required in manifest[required_key]:
            if not any(required in command for command in commands):
                errors.append(f"{settings_path.name} does not wire required hook: {required}")

        for command in commands:
            refs = command_refs(command)
            if not refs:
                if command not in ALLOWED_EXTERNAL_COMMANDS:
                    errors.append(f"hook command does not reference ~/.claude: {command}")
                continue
            for ref in refs:
                source = source_for_ref(ref)
                if not source.exists():
                    errors.append(f"hook command references missing source: {ref}")

        events = settings.get("hooks", {})
        if profile_name == "gates" and "SessionStart" in events:
            errors.append("gates profile should not install SessionStart bootstrap hooks")
        if profile_name == "workflow" and "SessionStart" not in events:
            errors.append("workflow profile must include SessionStart hooks")
        if "Stop" not in events:
            errors.append(f"{settings_path.name} must include Stop hooks for completion checks")

    return errors


def validate_target(target: Path, profile: str, beads_target: Path | None) -> list[str]:
    errors: list[str] = []
    settings = load_json(SETTINGS_BY_PROFILE[profile])
    for command in hook_commands(settings):
        for ref in command_refs(command):
            installed = target / ref
            if not installed.exists():
                errors.append(f"installed target missing hook dependency: {installed}")
    if not (target / "settings.json").exists():
        errors.append(f"installed target missing settings.json: {target / 'settings.json'}")
    if profile == "workflow":
        if beads_target is None:
            errors.append("workflow target validation requires --beads-target")
        else:
            for rel in (
                "formulas/mol-feature.formula.json",
                "formulas/mol-rapid.formula.json",
                "mol-status.sh",
            ):
                path = beads_target / rel
                if not path.exists():
                    errors.append(f"workflow target missing beads artifact: {path}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, help="Optional installed ~/.claude directory to validate")
    parser.add_argument("--beads-target", type=Path, help="Optional installed ~/.beads directory to validate")
    parser.add_argument("--profile", choices=sorted(SETTINGS_BY_PROFILE), default="gates")
    args = parser.parse_args(argv)

    errors = validate_profile()
    if args.target:
        errors.extend(validate_target(args.target.expanduser(), args.profile, args.beads_target))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Claude eval profile OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
