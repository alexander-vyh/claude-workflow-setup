#!/usr/bin/env python3
"""PreToolUse gate: an independent critical review must precede `bd close`.

WHAT CHANGED AND WHY
--------------------
The previous version recorded any `Agent` dispatch whose name/description/prompt
matched the word "review", then emitted `ask` at `bd close`. Three holes, all
observed in production:

  1. Gameable oracle — the check was "a string existed", and the implementer
     authors the string. A self-written rubber stamp satisfied it.
  2. No bead binding — a review of bead A satisfied closing bead B. Nothing
     associated the recorded review with the bead being closed.
  3. `ask` is inert. In the gate-signal corpus this gate logged 1270 nudges
     against 324 allows; in one captured 329-line session an identical `ask`
     from a sibling gate fired nine times with no agent reaction and no
     course correction from the human clicking past it. An advisory verdict
     only pays for itself when someone answers it, and in headless runs there
     is no channel for the answer to land at all.

So the verdict is now `deny`, and — more importantly — the oracle changed from
"an Agent call mentioning review existed" to "a substantive review of THIS bead
at THIS state of the work is on record". Hardening a gameable check without
fixing its oracle would only have made the rubber stamp mandatory.

HOST NEUTRALITY
---------------
Codex exposes no `Agent` event; its reliable surface is `Bash` PreToolUse plus
`SessionStart`. That is why the manifest previously marked this gate
`codex: unsupported`. The load-bearing evidence therefore lives in Beads
(`_review_record.py`) plus a git work fingerprint — both visible to either
host through a plain `Bash` close command.

The Claude `Agent` event still carries something Codex genuinely cannot
provide: proof that a structurally isolated reviewer (no shared conversation
history) was actually dispatched. That is kept as a *corroborating* check,
stamped into the record when observable. Following the repo's convention (see
test_codex_discovery_close_gate.py), an absent `session_id` identifies the
Codex shape, where the corroboration is skipped rather than failed — a check
we cannot run must not read as a check that failed.

GATE-DESIGN COMPLIANCE (claude/rules/gate-design.md)
----------------------------------------------------
Rule 1 — escape path, named in the denial itself:

    REVIEW_WAIVER="<>=20-char rationale>" bd close <bead>

  An environment-variable prefix rather than the repo's usual
  `--<gate-name>-waiver` flag, because that convention is mechanically broken
  on `bd` commands: `bd` rejects unknown flags outright ("Error: unknown flag:
  --epic-coverage-waiver"), so an agent following such a denial verbatim gets
  a command that will not run. The corpus shows the cost — epic_coverage_gate
  logged 199 denies against 1 accepted waiver. The skill sanctions this
  carve-out ("If a gate cannot use the standard convention... document the
  alternative escape"). Tracked for the flag-based gates separately.

Rule 2 — persistent signal: every decision (deny, waiver-accepted, allow) goes
  to `_gate_signal.record()`, so denial reasons accumulate as the labeled
  corpus that half-life review reads.

Rule 3 — value, not presence: a recorded review must clear a substance bar and
  its fingerprint must match the current work tree. Presence-only would make
  the record a checkbox, which is mock bureaucracy by construction.

Exit code is always 0; the decision travels as a single `permissionDecision`
JSON document on stdout (the canonical single-mechanism contract).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover - signal is best-effort
    def _record_signal(*_args, **_kwargs) -> None:
        return None

from _review_command import close_target, waiver_reason  # noqa: E402
from _review_ledger import record_dispatch  # noqa: E402
from _review_record import (  # noqa: E402
    INDEPENDENT_REVIEWER_TYPES,
    UNAVAILABLE,
    changed_paths_since,
    extract_bead_ids,
    read_record,
    validate_findings,
    validate_waiver_reason,
    work_fingerprint,
)

_RECORD_CLI = str(Path(__file__).resolve().parent / "escapement_review.py")

# Name of the environment-variable escape. A flag (`--review-waiver`) cannot be
# used here: `bd` rejects unknown flags outright, so a denial telling the agent
# to add one would hand it a command that will not run (escapement-6ge8).
WAIVER_VAR = "REVIEW_WAIVER"


def is_close_command(command: str) -> bool:
    """True when the command actually closes a bead."""
    return close_target(command) is not None


def parse_waiver(command: str) -> str | None:
    """Return the waiver reason set as an env prefix on the bd invocation."""
    return waiver_reason(command, WAIVER_VAR)


# ---------------------------------------------------------------------------
# Claude-only corroboration ledger
# ---------------------------------------------------------------------------

def is_independent_reviewer(tool_input: dict) -> bool:
    """True when an Agent dispatch is a structurally isolated reviewer.

    Only `subagent_type` counts. The old name/description/prompt word-match is
    deliberately gone: it was the gameable half of the oracle, satisfiable by
    naming any agent "review-helper".
    """
    subagent_type = (tool_input.get("subagent_type") or "").strip()
    return subagent_type in INDEPENDENT_REVIEWER_TYPES


# ---------------------------------------------------------------------------
# Decision output
# ---------------------------------------------------------------------------

def _escape_block(bead_id: str) -> str:
    return (
        "Ways forward:\n"
        f"  1. Dispatch an isolated reviewer (subagent_type one of: "
        f"{', '.join(sorted(INDEPENDENT_REVIEWER_TYPES))}) whose prompt names "
        f"{bead_id}, then record its verdict:\n"
        f"       python3 -B {_RECORD_CLI} record --bead {bead_id} "
        f"--findings-file <path>\n"
        f"  2. If review genuinely does not apply here, waive it with a reason "
        f"that will be kept as signal:\n"
        f"       REVIEW_WAIVER=\"<>=20-char rationale>\" bd close {bead_id}"
    )


def _deny(reason: str, bead_id: str, signal_decision: str, **extras) -> int:
    """Emit a deny decision with its escape path, and persist the signal."""
    full = f"{reason}\n\n{_escape_block(bead_id)}"
    _record_signal(
        gate_name="review_gate",
        decision=signal_decision,
        reason=reason,
        bead_id=bead_id,
        **extras,
    )
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": full,
        },
    }, sys.stdout)
    return 0


def _allow(reason: str, bead_id: str, **extras) -> int:
    _record_signal(
        gate_name="review_gate",
        decision="allow",
        reason=reason,
        bead_id=bead_id,
        **extras,
    )
    return 0


# ---------------------------------------------------------------------------
# Close-path decision
# ---------------------------------------------------------------------------

def evaluate_close(command: str, bead_id: str, cwd: str | None,
                   claude_shape: bool) -> int:
    """Decide a `bd close`, emitting the decision and signal. Returns exit code."""
    waiver = parse_waiver(command)
    if waiver is not None:
        ok, err = validate_waiver_reason(waiver, bead_id)
        if ok:
            return _allow("review waived with a recorded rationale", bead_id,
                          waiver_reason=waiver)
        # A rejected waiver is itself the thing to surface — do not fall
        # through to the missing-review message, which would hide the real
        # failure (the agent did try the escape path; it was not substantive).
        return _deny(
            f"REVIEW_WAIVER reason rejected: {err}",
            bead_id,
            "deny:invalid-waiver",
        )

    record = read_record(bead_id, cwd=cwd)
    if record is UNAVAILABLE:
        # The task store did not answer. That is our failure, not a missed
        # review, and denying on it would stop every close in the repository
        # until `bd` recovers. Allow, but leave the gap in the signal corpus.
        return _allow(
            "review status unknown: the task store could not be consulted",
            bead_id,
            store_unavailable=True,
        )

    if record is None:
        return _deny(
            f"No independent review is on record for {bead_id}. Closing it "
            "would land work that only its own author has read.",
            bead_id,
            "deny:no-review",
        )

    if record.get("bead") != bead_id:
        return _deny(
            f"The review on record covers {record.get('bead')!r}, not "
            f"{bead_id}. A review of a different bead is not a review of this "
            "one.",
            bead_id,
            "deny:wrong-bead",
            record_bead=record.get("bead"),
        )

    ok, err = validate_findings(record.get("findings"), bead_id)
    if not ok:
        return _deny(
            f"The review on record for {bead_id} does not carry a usable "
            f"verdict: {err}",
            bead_id,
            "deny:insubstantial",
        )

    current = work_fingerprint(cwd)
    recorded = record.get("fingerprint")
    if current and recorded and current != recorded:
        changed = changed_paths_since(cwd)
        listing = ", ".join(changed[:5]) or "uncommitted changes"
        more = f" (+{len(changed) - 5} more)" if len(changed) > 5 else ""
        return _deny(
            f"The review on record for {bead_id} predates the current work: "
            f"the tree changed after it was recorded — {listing}{more}. A "
            "review of an earlier state cannot vouch for what is being closed.",
            bead_id,
            "deny:stale",
            changed_count=len(changed),
        )

    # Corroboration: only meaningful where Agent dispatches are observable.
    if claude_shape and record.get("independent") is not True:
        return _deny(
            f"The review on record for {bead_id} was not corroborated by an "
            "isolated reviewer dispatch. A verdict written by the same agent "
            "that wrote the code is self-assessment, not independent review.",
            bead_id,
            "deny:uncorroborated",
            record_independent=record.get("independent"),
        )

    return _allow(
        "independent review on record, bound to this bead and current work",
        bead_id,
        reviewer=record.get("reviewer"),
        independent=record.get("independent"),
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0

    cwd = data.get("cwd") or None
    # Absent session_id is the Codex payload shape (see module docstring).
    session_id = data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")

    if tool_name == "Agent":
        if session_id and is_independent_reviewer(tool_input):
            beads = extract_bead_ids(
                tool_input.get("name"),
                tool_input.get("description"),
                tool_input.get("prompt"),
            )
            if beads:
                record_dispatch(
                    session_id,
                    beads,
                    (tool_input.get("subagent_type") or "").strip(),
                    work_fingerprint(cwd),
                )
        return 0

    if tool_name != "Bash":
        return 0

    command = tool_input.get("command", "")
    if not is_close_command(command):
        return 0

    bead_id = close_target(command)
    if not bead_id:
        # Cannot bind the close to a bead (e.g. `bd close --help`). Fail open:
        # denying a command we cannot even parse would block work for a reason
        # unrelated to review discipline.
        return 0

    return evaluate_close(command, bead_id, cwd, claude_shape=bool(session_id))


if __name__ == "__main__":
    sys.exit(main())
