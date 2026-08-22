#!/usr/bin/env python3
"""Claude Code hook: block false-closing an epic whose scope is not delivered.

Fires as PreToolUse on Bash commands containing `bd close <epic-id>`.

The cake-ta5.1 lesson (reported by user 2026-05-29): an epic read as "done"
because all ~50 of its children were closed — but the epic's own description
named a seam (`create_parser`, ~1,867 LOC) that no child ever covered. The
largest named seam shipped unextracted under a green parent. "All children
closed" is an intermediate artifact, not the parent's outcome
(outcome-ownership.md § "Child-Closure Is Not Parent-Completion").

This gate is the runtime defense. On `bd close <epic>` it blocks if either:

  (a) the epic carries NO own acceptance oracle — no "Done when ... not when
      all children closed" line and no structured `verify:` block. Closing on
      child-count alone is exactly the failure the gate exists to prevent.
  (b) the epic names seams in a structured scope-coverage manifest, and one or
      more of those seams maps to NO closed child. A named seam with no
      covering closed child means the breakdown was incomplete — the parent's
      whole scope was not delivered.

Why a *structured* manifest rather than free-text prose matching: a naive
prose-match gate would be mock bureaucracy (it would fire on incidental nouns
and miss real seams). The reliable enumeration of "named seams" is a
scope-coverage manifest the epic carries explicitly, per the fxh.10 verify-block
convention. The work-breakdown skill's "scope-coverage manifest" requirement is
the authoring-time complement; this is the close-time enforcement.

Manifest grammar (in the epic description):

    Seams:
    - parser: extract create_parser / argparse setup
    - dispatch: extract the command dispatch table
    seam: handlers — extract per-command handler functions

i.e. either a `Seams:` block of `- <name>: ...` bullets, and/or standalone
`seam: <name> — ...` lines. The `<name>` token (before the first ':' or '—')
is the seam key. A seam is COVERED when some closed child's title or its
`seam:` metadata mentions that key (case-insensitive word match).

Acceptance-oracle grammar (any one satisfies check (a)):

    Done when: <criterion> ... not when all children closed
    verify: <shell command>

Escape (gate-design.md Rule 1): a reasoned waiver allows the close without
disabling the gate:

    bd close <epic> --epic-coverage-waiver "<>=20-char rationale>"

Signal (gate-design.md Rule 2): every decision — deny, waiver-accepted, allow —
is recorded via _gate_signal.record() to .beads/.gate-signal.jsonl.

Value-not-presence (gate-design.md Rule 3): the waiver reason is validated
(placeholder-rejected, >=20 chars, must not echo the epic id); seam coverage is
checked against real closed children, not against the mere presence of a
manifest.

Fail-open: any error (bd not found, JSON parse, etc.) silently allows. The gate
never blocks a close because its own machinery broke.

Input (via stdin):
  JSON with hook_event_name, tool_name, tool_input
Exit codes (single-signal contract per fxh.7):
  0 — always. A deny is carried by the permissionDecision="deny" JSON on
      stdout, NOT by a non-zero exit. An allow exits 0 with no output.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

# Shared signal capture per claude/rules/gate-design.md Rule 2.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None


GATE_NAME = "epic_coverage_gate"

# Per-call subprocess timeout (seconds).
_SUBPROCESS_TIMEOUT = 5.0

# Minimum substance threshold for a waiver reason (gate-design.md Rule 3).
_WAIVER_MIN_LEN = 20

# Null patterns rejected by the standard waiver convention.
_WAIVER_PLACEHOLDERS = {
    "tbd", "n/a", "na", "todo", "wip", "fixme", "none", "xxx",
    "?", "??", "???",
}

# Statuses that count as "closed" for coverage purposes.
_CLOSED_STATUSES = {"closed", "done", "completed", "resolved"}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_quoted_flag(command: str, flag: str) -> str | None:
    """Extract a possibly-quoted, multi-word --flag value.

    Handles --flag 'a multi word reason', --flag "...", --flag=value,
    --flag=bareword, and --flag bareword. Returns None if absent.
    """
    m = re.search(rf"--{re.escape(flag)}=(['\"])(.*?)\1", command, re.DOTALL)
    if m:
        return m.group(2)
    m = re.search(rf"--{re.escape(flag)}\s+(['\"])(.*?)\1", command, re.DOTALL)
    if m:
        return m.group(2)
    m = re.search(rf"--{re.escape(flag)}=(\S+)", command)
    if m:
        return m.group(1)
    m = re.search(rf"--{re.escape(flag)}\s+(\S+)", command)
    if m and not m.group(1).startswith("-"):
        return m.group(1)
    return None


def has_flag(command: str, flag: str) -> bool:
    """Check if --flag or --flag=... is present."""
    return bool(re.search(rf"--{re.escape(flag)}(?:\s|=|$)", command))


def extract_close_target(command: str) -> str | None:
    """Return the issue id being closed by a `bd close` command, or None.

    Grabs the first non-flag positional token after `bd close`. Returns None
    if the command is not a `bd close` or carries no positional target (e.g.
    `bd close --help`).
    """
    m = re.search(r"\bbd\s+close\b(.*)", command, re.DOTALL)
    if not m:
        return None
    tail = m.group(1)
    # Walk tokens; skip flags and their attached '=value' but NOT a separate
    # value token (close takes positional id, flags are boolean-ish or take
    # quoted reasons we don't care about here). The first bareword that does
    # not start with '-' is the target id.
    tokens = re.findall(r"(?:'[^']*'|\"[^\"]*\"|\S)+", tail)
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-"):
            # --flag=value is self-contained; --flag <value> would consume the
            # next token. We conservatively do NOT skip the next token because
            # bd close flags we know (--reason, --epic-coverage-waiver) take
            # quoted values that won't be mistaken for a bare id. A bare id
            # never starts with '-'.
            continue
        return tok.strip("'\"")
    return None


# ---------------------------------------------------------------------------
# bd data access (fail-open)
# ---------------------------------------------------------------------------

def _bd_json(args: list[str]) -> object | None:
    """Run `bd <args> --json` and return parsed JSON, or None on any error."""
    try:
        result = subprocess.run(
            ["bd", *args, "--json"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


def get_issue(issue_id: str) -> dict | None:
    """Fetch a single issue's data dict, or None on error."""
    data = _bd_json(["show", issue_id])
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) else None


def get_children(parent_id: str) -> list[dict] | None:
    """Fetch all children (incl. closed) of an epic, or None on error."""
    data = _bd_json(["children", parent_id])
    if isinstance(data, dict):
        # Some bd versions wrap in {"issues": [...]}
        data = data.get("issues", data.get("children", []))
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    return None


# ---------------------------------------------------------------------------
# Epic-scope analysis
# ---------------------------------------------------------------------------

def is_epic(issue: dict) -> bool:
    """True if the issue's type is 'epic'."""
    return str(issue.get("issue_type", issue.get("type", ""))).lower() == "epic"


def has_acceptance_oracle(description: str) -> bool:
    """True if the epic description carries its OWN acceptance oracle.

    Satisfied by either:
      - a "Done when ... not when ... children" line (the epic done-bar
        convention from work-breakdown SKILL.md), or
      - a structured `verify:` block (the fxh.10 verify-block convention).

    A bare "Done when all children closed" is NOT an oracle — it is exactly
    the child-count proxy the gate rejects; the "not when ... children"
    qualifier is what makes it a real own-outcome criterion.
    """
    text = description or ""
    lower = text.lower()

    # verify-block convention: a `verify:` line with a non-empty command.
    for line in text.splitlines():
        m = re.match(r"\s*verify\s*:\s*(.+)", line, re.IGNORECASE)
        if m and m.group(1).strip():
            return True

    # Done-bar convention: "Done when ..." that also asserts "not when ...
    # children". Require the disqualifier so a child-count proxy doesn't pass.
    if re.search(r"done\s+when", lower):
        if re.search(r"not\s+when[^.]*child", lower):
            return True

    return False


_SEAMS_BLOCK_RE = re.compile(r"^\s*seams?\s*:\s*$", re.IGNORECASE)
_SEAM_BULLET_RE = re.compile(r"^\s*[-*]\s*([A-Za-z0-9_][\w-]*)\s*[:—-]")
_SEAM_INLINE_RE = re.compile(
    r"^\s*seam\s*:\s*([A-Za-z0-9_][\w-]*)\s*[:—-]?", re.IGNORECASE
)


def extract_seams(description: str) -> list[str]:
    """Enumerate named seams from the epic's scope-coverage manifest.

    Recognizes two structured forms (NOT free-text prose — see module docstring
    for why prose-matching would be mock bureaucracy):

      Seams:
      - parser: ...
      - dispatch — ...

    and standalone:

      seam: handlers — ...

    Returns the list of seam keys (the token before the first ':' / '—' / '-'),
    de-duplicated, preserving first-seen order. Empty list if no manifest.
    """
    text = description or ""
    lines = text.splitlines()
    seams: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            seams.append(key)

    in_block = False
    for line in lines:
        # Standalone "seam: <name>" anywhere.
        m_inline = _SEAM_INLINE_RE.match(line)
        if m_inline:
            _add(m_inline.group(1))
            continue

        if _SEAMS_BLOCK_RE.match(line):
            in_block = True
            continue

        if in_block:
            m_bullet = _SEAM_BULLET_RE.match(line)
            if m_bullet:
                _add(m_bullet.group(1))
                continue
            # A non-bullet, non-blank line ends the block.
            if line.strip():
                in_block = False

    return seams


def _child_is_closed(child: dict) -> bool:
    return str(child.get("status", "")).lower() in _CLOSED_STATUSES


def _child_covers_seam(child: dict, seam: str) -> bool:
    """True if a closed child covers the given seam key.

    Coverage is asserted when the seam key appears as a whole word in the
    child's title, OR the child carries a `seam:` metadata field naming it.
    Whole-word match (not substring) so seam 'parse' doesn't spuriously match
    a child titled 'reparser'.
    """
    seam_key = seam.lower()

    # Metadata seam tag.
    meta = child.get("metadata", {})
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    if isinstance(meta, dict):
        meta_seam = str(meta.get("seam", "")).lower()
        if meta_seam and seam_key in re.findall(r"[\w-]+", meta_seam):
            return True

    # Title word-match.
    title = str(child.get("title", "")).lower()
    title_words = re.findall(r"[\w-]+", title)
    if seam_key in title_words:
        return True

    return False


def uncovered_seams(seams: list[str], children: list[dict]) -> list[str]:
    """Return seams that no CLOSED child covers."""
    closed = [c for c in children if _child_is_closed(c)]
    missing = []
    for seam in seams:
        if not any(_child_covers_seam(c, seam) for c in closed):
            missing.append(seam)
    return missing


# ---------------------------------------------------------------------------
# Waiver reason validation (gate-design.md Rules 1 & 3)
# ---------------------------------------------------------------------------

def validate_waiver_reason(
    reason: str | None, epic_id: str = ""
) -> tuple[bool, str]:
    """Validate an --epic-coverage-waiver reason per gate-design.md Rules 1 & 3.

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
            f"rationale. Explain WHY closing this epic is legitimate despite "
            f"the coverage finding."
        )

    if len(stripped) < _WAIVER_MIN_LEN:
        return False, (
            f"waiver reason is too short ({len(stripped)} chars). At least "
            f"{_WAIVER_MIN_LEN} characters of substantive rationale are required."
        )

    if epic_id and stripped.lower() == epic_id.strip().lower():
        return False, (
            "waiver reason merely echoes the epic id and carries no rationale. "
            "Explain WHY the coverage finding is acceptable."
        )

    return True, ""


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def allow() -> int:
    return 0


def deny(message: str) -> NoReturn:
    # Single-signal contract (fxh.7): the block is carried solely by the
    # permissionDecision="deny" JSON on stdout, with exit 0. Emitting exit 2
    # *alongside* this JSON is the contradictory double-block fxh.7 removed
    # from every other hook (Claude Code treats exit 2 as its own
    # stderr-block signal; pairing it with a deny-JSON sends two competing
    # blocks). Mirror discovery-close-gate.py's exit-0 structured-decision path.
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }
    }))
    sys.exit(0)


_WAIVER_HINT = (
    "Escape (gate-design Rule 1): if the close is legitimate despite this "
    "finding, supply a reasoned waiver instead of disabling the gate: "
    "bd close {epic} --epic-coverage-waiver "
    "\"<>={minlen}-char rationale>\""
)


# ---------------------------------------------------------------------------
# Core decision (pure, testable)
# ---------------------------------------------------------------------------

def evaluate(command: str) -> tuple[str, str]:
    """Decide allow/deny for a `bd close` command. Pure given bd data access.

    Returns (decision, message). decision is one of:
      'allow'           — not an epic close, or coverage is satisfied
      'waiver-accepted' — a valid --epic-coverage-waiver bypassed the finding
      'deny:<reason>'   — blocked; message is the denial text

    Fail-open: any inability to fetch issue data returns 'allow'.
    """
    epic_id = extract_close_target(command)
    if not epic_id:
        return "allow", ""

    issue = get_issue(epic_id)
    if issue is None:
        return "allow", ""  # fail-open: cannot fetch the issue

    if not is_epic(issue):
        return "allow", ""  # gate only fires on epics

    description = str(issue.get("description", "") or "")

    # Compute the findings first so the waiver path can report what it bypassed.
    has_oracle = has_acceptance_oracle(description)
    seams = extract_seams(description)

    children = get_children(epic_id)
    if children is None:
        return "allow", ""  # fail-open: cannot fetch children

    missing = uncovered_seams(seams, children) if seams else []

    findings: list[str] = []
    if not has_oracle:
        findings.append(
            "(a) the epic carries no own acceptance oracle. Add a "
            "'Done when: <criterion> ... not when all children closed' line "
            "or a 'verify: <command>' block so the close is gated on the "
            "epic's OWN outcome, not its child count."
        )
    if missing:
        findings.append(
            "(b) these named seams map to no CLOSED child: "
            + ", ".join(sorted(missing))
            + ". Either deliver a covering child (its title or seam: metadata "
            "must name the seam) or remove the seam from the manifest if it is "
            "out of scope."
        )

    if not findings:
        return "allow", ""

    # First-class escape: a valid waiver bypasses the finding (Rule 1).
    if has_flag(command, "epic-coverage-waiver"):
        reason = parse_quoted_flag(command, "epic-coverage-waiver")
        valid, werr = validate_waiver_reason(reason, epic_id)
        if valid:
            return "waiver-accepted", reason or ""
        # Invalid waiver attempt is itself the failure to surface — block.
        return (
            "deny:invalid-waiver",
            f"--epic-coverage-waiver reason rejected: {werr}\n"
            f"A waiver reason must be substantive free-text (>= "
            f"{_WAIVER_MIN_LEN} chars, no placeholders like tbd/n/a/todo, and "
            f"must not merely echo the epic id). Example: "
            f"--epic-coverage-waiver 'seam X intentionally deferred to "
            f"follow-up epic Y; this epic's own oracle passes for the "
            f"delivered scope'",
        )

    msg = (
        f"Refusing to close epic {epic_id}: parent completion is not the same "
        f"as 'all children closed' (cake-ta5.1 lesson, outcome-ownership.md "
        f"§ Child-Closure Is Not Parent-Completion).\n\n"
        + "\n\n".join(findings)
        + "\n\n"
        + _WAIVER_HINT.format(epic=epic_id, minlen=_WAIVER_MIN_LEN)
    )
    return "deny:coverage", msg


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open

    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    if hook_event != "PreToolUse":
        return 0

    if data.get("tool_name", "") != "Bash":
        return 0

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    if "bd close" not in command:
        return 0  # fast-path

    decision, message = evaluate(command)
    epic_id = extract_close_target(command) or ""

    if decision == "allow":
        # Only record a positive signal when an epic close actually passed the
        # coverage check — avoid logging every non-epic close as noise.
        return allow()

    if decision == "waiver-accepted":
        _record_signal(
            gate_name=GATE_NAME,
            decision="waiver-accepted",
            reason=message,
            epic_id=epic_id,
            command=command[:500],
        )
        return allow()

    # deny:*
    _record_signal(
        gate_name=GATE_NAME,
        decision="deny",
        reason=decision.split(":", 1)[1] if ":" in decision else decision,
        epic_id=epic_id,
        command=command[:500],
    )
    deny(message)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
