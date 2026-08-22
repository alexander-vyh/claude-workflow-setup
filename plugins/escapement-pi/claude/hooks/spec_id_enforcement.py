#!/usr/bin/env python3
"""Claude Code hook: enforce --spec-id on bd create under mol-feature molecules.

Fires as PreToolUse on Bash commands containing `bd create`.

When the command has a --parent flag pointing to an issue that belongs to a
mol-feature molecule, this hook blocks if --spec-id is missing.

Fast-path exits:
  - Non-PreToolUse events
  - Non-Bash tools
  - Commands without "bd create"
  - Commands that already have --spec-id
  - Commands without --parent (standalone creates are not gated here)

Fail-open: any error (bd not found, JSON parse, etc.) silently allows.

Input (via stdin):
  JSON with hook_event_name, tool_name, tool_input
Exit codes:
  0 — allow
  2 — deny (JSON output explains why)
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

# Shared signal capture per claude/rules/gate-design.md Rule 2.
# Import is best-effort; if the module is unavailable we silently skip
# logging so the gate never breaks because of its observer.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Total time budget for all bd show subprocess calls (seconds)
_TOTAL_TIMEOUT = 5.0

# Maximum ancestor hops to walk when checking for mol-feature
_MAX_ANCESTOR_DEPTH = 3

# Per-call subprocess timeout (seconds) — will be clamped by remaining budget
_PER_CALL_TIMEOUT = 2.0


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_flag(command: str, flag: str) -> str | None:
    """Extract the value of a --flag from a command string.

    Handles: --flag=value, --flag value
    Returns None if the flag is not present.
    """
    # --flag=value
    m = re.search(rf'--{re.escape(flag)}[=](\S+)', command)
    if m:
        return m.group(1)

    # --flag value
    m = re.search(rf'--{re.escape(flag)}\s+(\S+)', command)
    if m:
        val = m.group(1)
        if not val.startswith('-'):
            return val

    return None


def has_flag(command: str, flag: str) -> bool:
    """Check if --flag or --flag=... is present."""
    return bool(re.search(rf'--{re.escape(flag)}(?:\s|=|$)', command))


def parse_quoted_flag(command: str, flag: str) -> str | None:
    """Extract a possibly-quoted, multi-word --flag value.

    Unlike parse_flag (which stops at the first whitespace), this handles
    free-text reasons supplied as --flag 'a multi word reason',
    --flag "a multi word reason", or --flag=value. Returns None if the
    flag is absent.
    """
    # --flag='...' or --flag="..."
    m = re.search(rf"--{re.escape(flag)}=(['\"])(.*?)\1", command, re.DOTALL)
    if m:
        return m.group(2)
    # --flag 'quoted reason' / --flag "quoted reason"
    m = re.search(rf"--{re.escape(flag)}\s+(['\"])(.*?)\1", command, re.DOTALL)
    if m:
        return m.group(2)
    # --flag=bareword
    m = re.search(rf"--{re.escape(flag)}=(\S+)", command)
    if m:
        return m.group(1)
    # --flag bareword (single token, not another flag)
    m = re.search(rf"--{re.escape(flag)}\s+(\S+)", command)
    if m and not m.group(1).startswith("-"):
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Molecule detection
# ---------------------------------------------------------------------------

def _check_issue_for_mol_feature(issue_data: dict) -> bool:
    """Check if a single issue's labels or metadata indicate mol-feature."""
    labels = issue_data.get("labels", [])
    if isinstance(labels, str):
        labels = [labels]

    for label in labels:
        if "mol-feature" in str(label).lower():
            return True

    metadata = issue_data.get("metadata", {})
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    formula = metadata.get("formula", "")
    if "mol-feature" in str(formula).lower():
        return True

    return False


def is_mol_feature_parent(parent_id: str) -> bool:
    """Check if the parent issue belongs to a mol-feature molecule.

    Walks up the issue tree iteratively (up to _MAX_ANCESTOR_DEPTH hops)
    with a shared time budget of _TOTAL_TIMEOUT seconds across all
    subprocess calls.

    Returns False on any error (fail-open).
    """
    deadline = time.monotonic() + _TOTAL_TIMEOUT
    current_id = parent_id
    visited: set[str] = set()

    for _ in range(_MAX_ANCESTOR_DEPTH):
        if not current_id or current_id in visited:
            return False

        visited.add(current_id)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False  # Time budget exhausted — fail-open

        try:
            result = subprocess.run(
                ["bd", "show", current_id, "--json"],
                capture_output=True,
                text=True,
                timeout=min(_PER_CALL_TIMEOUT, remaining),
            )
            if result.returncode != 0:
                return False

            data = json.loads(result.stdout)
            # `bd show --json` returns a single-element JSON array, not a bare
            # object (verified against current bd). The sibling readers
            # derive_contract.fetch_bead and spec_id_preflight already unwrap
            # the list; this hook must too, or _check_issue_for_mol_feature
            # gets a list and raises AttributeError — crashing the gate and
            # failing it OPEN for every mol-feature molecule.
            if isinstance(data, list):
                data = data[0] if data else {}
            if not isinstance(data, dict):
                return False  # Unexpected shape — fail-open

        except Exception:
            return False  # Fail-open

        if _check_issue_for_mol_feature(data):
            return True

        # Walk up to the parent
        current_id = data.get("parent", "")

    return False


# ---------------------------------------------------------------------------
# spec-id value validation
# ---------------------------------------------------------------------------

_PLACEHOLDER_VALUES = {
    "none", "tbd", "todo", "wip", "n/a", "na", "fixme", "xxx", "?", "??", "???",
}


def validate_spec_id(spec_id: str, project_dir: Path) -> tuple[bool, str]:
    """Validate that a --spec-id value points to a real spec requirement.

    Closes the mock-bureaucracy hole: presence of --spec-id alone is not
    enough; the value must resolve. Per delicate-art-of-bureaucracy.md:
    a gate that checks symbolic compliance without checking the underlying
    behavior produces mock bureaucracy by the rule's own definition.

    Validation:
      1. Reject placeholder strings (none/tbd/todo/wip/etc.)
      2. The path part (before '#') must resolve to a real file under
         openspec/changes/<change>/specs/ or openspec/specs/.
      3. If an anchor is present, it should match a `### Requirement: <name>`
         heading in the file (kebab-case or space-separated form).

    Returns (is_valid, error_message). error_message is empty on valid.
    """
    if not spec_id or spec_id.strip().lower() in _PLACEHOLDER_VALUES:
        return False, (
            f"value '{spec_id}' is a placeholder, not a real reference. "
            f"Link to an actual spec requirement."
        )

    # Parse path#anchor
    if "#" in spec_id:
        path_part, anchor = spec_id.split("#", 1)
    else:
        path_part, anchor = spec_id, ""

    # Reject non-openspec paths — the convention is openspec/changes/*/specs/
    # or openspec/specs/. Tolerate either absolute-from-project or relative.
    spec_path = (project_dir / path_part).resolve()
    if not spec_path.is_file():
        return False, (
            f"path '{path_part}' does not resolve to a file. "
            f"Expected under openspec/changes/<change>/specs/ or openspec/specs/."
        )

    # If an anchor is present, verify it matches a Requirement heading.
    # Match either the literal anchor or a kebab→space-separated form.
    if anchor:
        try:
            content = spec_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False, f"path '{path_part}' exists but cannot be read."

        anchor_spaced = anchor.replace("-", " ").lower()
        requirement_headers = [
            line[len("### Requirement:"):].strip().lower()
            for line in content.splitlines()
            if line.startswith("### Requirement:")
        ]
        if not requirement_headers:
            return False, (
                f"spec file '{path_part}' contains no '### Requirement: ...' "
                f"headings to anchor to."
            )

        matched = any(
            header == anchor.lower() or header == anchor_spaced
            for header in requirement_headers
        )
        if not matched:
            return False, (
                f"anchor '#{anchor}' does not match any '### Requirement: ...' "
                f"heading in '{path_part}'. Available: "
                f"{', '.join(sorted(requirement_headers)[:3])}"
                f"{'...' if len(requirement_headers) > 3 else ''}"
            )

    return True, ""


# ---------------------------------------------------------------------------
# Waiver reason validation (gate-design.md Rules 1 & 3)
# ---------------------------------------------------------------------------

# Minimum substance threshold for a waiver reason, per the standard waiver
# convention in gate-design.md ("Reason is required free-text … Minimum 20
# characters").
_WAIVER_MIN_LEN = 20

# Null patterns rejected by the standard waiver convention.
_WAIVER_PLACEHOLDERS = {
    "tbd", "n/a", "na", "todo", "wip", "fixme", "none", "xxx",
    "?", "??", "???",
}


def validate_waiver_reason(
    reason: str | None, parent_id: str = ""
) -> tuple[bool, str]:
    """Validate a --spec-waiver reason per gate-design.md Rules 1 & 3.

    A waiver's reason is the labeled training data future revisions of the
    gate read, so a presence-only check would manufacture mock bureaucracy
    (an agent under pressure learns the shortest passing string). This
    validates the *value*:

      1. Non-empty after stripping.
      2. Rejects null/placeholder patterns (tbd, n/a, todo, wip, ...).
      3. Enforces the >=20-char substance threshold.
      4. Rejects reasons that merely echo the source artifact (the parent
         issue id) — an echo carries no signal.

    Returns (is_valid, error_message). error_message is empty on valid.
    """
    if reason is None:
        return False, "no waiver reason supplied."

    stripped = reason.strip()
    if not stripped:
        return False, "waiver reason is empty."

    if stripped.lower() in _WAIVER_PLACEHOLDERS:
        return False, (
            f"waiver reason '{stripped}' is a placeholder, not a real "
            f"rationale. Explain WHY this task legitimately has no spec yet."
        )

    if len(stripped) < _WAIVER_MIN_LEN:
        return False, (
            f"waiver reason is too short ({len(stripped)} chars). "
            f"At least {_WAIVER_MIN_LEN} characters of substantive rationale "
            f"are required."
        )

    if parent_id and stripped.lower() == parent_id.strip().lower():
        return False, (
            "waiver reason merely echoes the parent issue id and carries no "
            "rationale. Explain WHY the spec link is being waived."
        )

    return True, ""


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
    mutually-exclusive legacy stderr-feedback path; emitting both the JSON
    decision *and* exit 2 is a contradictory double-block. We use the JSON
    path, so this exits 0.
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

    # Fast-path: not a bd create command
    if "bd create" not in command:
        return 0

    # Only enforce when creating under a parent
    parent_id = parse_flag(command, "parent")
    if not parent_id:
        return allow()

    # Check if the parent belongs to a mol-feature molecule
    if not is_mol_feature_parent(parent_id):
        return allow()

    # Parent IS mol-feature. First honor the first-class escape: a
    # --spec-waiver with a substantive reason (gate-design.md Rule 1)
    # allows the create without a resolving --spec-id. The reason is
    # validated (Rule 3) and persisted (Rule 2) so the waiver corpus is
    # labeled training data, not a rubber stamp.
    if has_flag(command, "spec-waiver"):
        waiver_reason = parse_quoted_flag(command, "spec-waiver")
        valid_waiver, waiver_error = validate_waiver_reason(waiver_reason, parent_id)
        if valid_waiver:
            _record_signal(
                gate_name="spec_id_enforcement",
                decision="waiver-accepted",
                reason=waiver_reason,
                event_type="waiver",
                parent_id=parent_id,
                command=command[:500],
            )
            return allow()
        # Bad waiver reason — block. Do NOT fall through to spec-id checks;
        # an invalid escape attempt is itself the failure to surface.
        _record_signal(
            gate_name="spec_id_enforcement",
            decision="deny",
            reason=f"invalid --spec-waiver: {waiver_error}",
            parent_id=parent_id,
            command=command[:500],
        )
        deny(
            f"--spec-waiver reason rejected: {waiver_error}\n"
            f"A waiver reason must be substantive free-text (>= "
            f"{_WAIVER_MIN_LEN} chars, no placeholders like tbd/n/a/todo). "
            f"Example: --spec-waiver 'spec deferred — this is an "
            f"exploratory spike to de-risk the parser approach before "
            f"requirements are written'"
        )

    # Now check spec-id.
    spec_id = parse_flag(command, "spec-id")
    if not spec_id:
        # Missing entirely — block, recording the signal first
        _record_signal(
            gate_name="spec_id_enforcement",
            decision="deny",
            reason="missing --spec-id under mol-feature parent",
            parent_id=parent_id,
        )
        deny(
            "This bd create is under a mol-feature molecule but is missing --spec-id. "
            "Add --spec-id <spec-identifier> to link this task to its specification. "
            "Example: bd create \"my task\" --parent {parent} "
            "--spec-id openspec/changes/<change-name>/specs/<capability>.md#<requirement-name>\n"
            "Escape (gate-design Rule 1): if this task legitimately has no spec yet "
            "(spike/exploratory), supply a reasoned waiver instead: "
            "--spec-waiver \"<>= {minlen}-char rationale for why no spec applies>\""
            .format(parent=parent_id, minlen=_WAIVER_MIN_LEN)
        )

    # spec-id is present — validate the value resolves to a real spec.
    # Closes the mock-bureaucracy hole flagged by the bureaucracy-principle
    # audit (delicate-art-of-bureaucracy.md): a gate that checks presence
    # but not resolution lets agents satisfy it symbolically with values
    # like --spec-id none.
    project_dir_str = (
        data.get("cwd", "")
        or data.get("workingDirectory", "")
        or os.getcwd()
    )
    project_dir = Path(project_dir_str)
    valid, error = validate_spec_id(spec_id, project_dir)
    if valid:
        _record_signal(
            gate_name="spec_id_enforcement",
            decision="allow",
            reason="spec_id resolved",
            parent_id=parent_id,
            spec_id=spec_id,
        )
        return allow()

    # spec-id is invalid — block with the specific resolution error
    _record_signal(
        gate_name="spec_id_enforcement",
        decision="deny",
        reason=f"invalid spec_id: {error}",
        parent_id=parent_id,
        spec_id=spec_id,
    )
    deny(
        f"--spec-id '{spec_id}' does not resolve to a real spec requirement: "
        f"{error}\n"
        f"Expected format: openspec/changes/<change-name>/specs/"
        f"<capability>.md#<requirement-name>. The path must point at a real "
        f"file; the anchor must match a '### Requirement: ...' heading in it.\n"
        f"Escape (gate-design Rule 1): if no spec legitimately applies yet, "
        f"supply a reasoned waiver instead: "
        f"--spec-waiver \"<>= {_WAIVER_MIN_LEN}-char rationale>\""
    )

    return 0  # unreachable, but keeps type checkers happy


if __name__ == "__main__":
    sys.exit(main())
