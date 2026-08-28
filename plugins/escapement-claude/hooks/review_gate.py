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

WHAT THE VERDICT IS BOUND TO, AND WHAT IS NOT YET ENFORCED (escapement-1l04)
-----------------------------------------------------------------------------
The dispatch ledger is written at `Agent` **PreToolUse**, before the subagent
emits a token. So for its first two versions this gate certified that a reviewer
had been *summoned*, never that one had been *read*: dispatch a genuinely
isolated reviewer naming the bead, ignore its answer, write your own
>=120-character verdict, close. Every check passed.

Three things changed, and they are enforced to different degrees. Saying so
precisely is the point — this repo's standing rule is that nothing may be
described as mechanically enforced when it is not:

  ENFORCED NOW. `--reviewer` must name a real dispatchable reviewer, and when a
    dispatch was observed the reviewer identity is taken from the ledger rather
    than from the flag. Records written under schema v1 — whose oracle never
    looked at the reviewer's output — are refused, and refused with an accurate
    reason. The pre-existing rules (a substantive review on record, bound to
    this bead, at the current work fingerprint, corroborated by an observed
    isolated dispatch) are unchanged.

  NOT ENFORCED YET — THE WHOLE VERDICT-CAPTURE FEATURE. Both the traceability
    rule (the recorded findings must BE the reviewer's returned text) and the
    blocking rule (a verdict naming unresolved blockers refuses the close until
    the work changes and is re-reviewed) depend on a hook being able to observe
    a subagent's final output, which
    `_review_verdict.VERDICT_CAPTURE_SUPPORTED` reports as unavailable pending
    escapement-g27c. Both deny branches are conditional on that one flag and
    switch on together.

    The blocking deny was NOT originally gated, and that was a live defect
    rather than a tidiness issue: `record_verdict` runs at `Agent` PostToolUse
    unconditionally, so on any host where capture happens to work, a verdict
    would be classified and the blocking deny would fire with the flag still
    False — using a classifier that escapement-1nzm showed returns "blocking"
    for a clean PASS. That denial's own remedy cannot clear it: it says "fix
    what the reviewer flagged" when nothing was flagged, at an unchanged
    fingerprint that re-recording cannot move, leaving REVIEW_WAIVER as the
    only exit. Capture, classification, and the blocking deny are one feature
    and are gated as one.

    Until the flag flips, an implementer who dispatches a reviewer and ignores
    it can still close the bead. This docstring must keep saying so.

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

from _review_command import (  # noqa: E402
    close_targets,
    waiver_reason,
    writes_reserved_metadata,
)
from _review_ledger import record_dispatch, record_verdict  # noqa: E402
from _review_record import (  # noqa: E402
    INDEPENDENT_REVIEWER_TYPES,
    METADATA_KEY,
    RECORD_VERSION,
    UNAVAILABLE,
    changed_paths_since,
    extract_bead_ids,
    read_record,
    validate_findings,
    validate_waiver_reason,
    work_fingerprint,
)
from _review_verdict import (  # noqa: E402
    VERDICT_CAPTURE_SUPPORTED,
    extract_verdict,
)

_RECORD_CLI = str(Path(__file__).resolve().parent / "escapement_review.py")

# Name of the environment-variable escape. A flag (`--review-waiver`) cannot be
# used here: `bd` rejects unknown flags outright, so a denial telling the agent
# to add one would hand it a command that will not run (escapement-6ge8).
WAIVER_VAR = "REVIEW_WAIVER"


#: Outcomes of a single bead's evaluation, so a multi-bead close can stop at
#: the first refusal instead of emitting two decision documents.
DENIED = "denied"
ALLOWED = "allowed"


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


def _deny(reason: str, bead_id: str, signal_decision: str, **extras) -> str:
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
    return DENIED


def _allow(reason: str, bead_id: str, **extras) -> str:
    _record_signal(
        gate_name="review_gate",
        decision="allow",
        reason=reason,
        bead_id=bead_id,
        **extras,
    )
    return ALLOWED


def _emit_deny(reason: str, signal_decision: str, **extras) -> str:
    """Deny without a bead id — the escape block needs a target, this does not."""
    _record_signal(
        gate_name="review_gate",
        decision=signal_decision,
        reason=reason,
        **extras,
    )
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }, sys.stdout)
    return DENIED


def _deny_unidentified(command: str) -> str:
    return _emit_deny(
        "This closes a bead the gate cannot identify, so its review cannot be "
        "checked. `bd close` with no id closes the last-touched issue, and an "
        "unexpanded variable or a reason in the positional slot resolves to "
        "nothing.\n\n"
        "Ways forward:\n"
        "  1. Name the bead explicitly: bd close <bead-id>\n"
        "  2. Keep the reason attached to its flag: "
        "bd close <bead-id> -r \"<reason>\"\n"
        "  3. If review genuinely does not apply, waive it with a reason that "
        "will be kept as signal:\n"
        "       REVIEW_WAIVER=\"<>=20-char rationale>\" bd close <bead-id>",
        "deny:unidentified-target",
        command_excerpt=command[:200],
    )


def _deny_reserved_metadata() -> str:
    return _emit_deny(
        f"`{METADATA_KEY}` is the review gate's own record and cannot be "
        "written by hand — doing so would let the author of the code mint its "
        "own independence stamp with no reviewer involved.\n\n"
        "Way forward: record the verdict through the CLI, which stamps "
        "independence from an observed reviewer dispatch:\n"
        f"       python3 -B {_RECORD_CLI} record --bead <bead-id> "
        "--findings-file <path>",
        "deny:reserved-metadata",
    )


# ---------------------------------------------------------------------------
# Close-path decision
# ---------------------------------------------------------------------------

def evaluate_close(command: str, bead_id: str, cwd: str | None,
                   claude_shape: bool) -> str:
    """Decide one bead's close, emitting the decision and signal."""
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

    if record.get("v") != RECORD_VERSION:
        # A review IS on record — it was just written under an older oracle,
        # one that never looked at what the reviewer returned. Saying "no
        # review is on record" here would send the agent to re-run a reviewer
        # without ever explaining why the first one stopped counting.
        return _deny(
            f"The review on record for {bead_id} was written under review "
            f"schema v{record.get('v')}, which recorded only that a reviewer "
            "was dispatched — not what it found. Re-record the verdict so it "
            "carries the reviewer's actual output.",
            bead_id,
            "deny:outdated-schema",
            record_version=record.get("v"),
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

    if current and not recorded:
        # The record was made somewhere the work could not be fingerprinted —
        # `--cwd /tmp` does this, and it is documented in `--help`. Accepting it
        # here would switch staleness off for that bead permanently, which is a
        # far more attractive door than the waiver because it never has to be
        # justified again.
        return _deny(
            f"The review on record for {bead_id} carries no work fingerprint, "
            "so it cannot be tied to any particular state of the code. Re-record "
            "it from the work tree being closed.",
            bead_id,
            "deny:unfingerprinted",
        )

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

    # Re-review after repair, expressed through the fingerprint that already
    # exists rather than through a second staleness mechanism. Reaching here
    # means the record's fingerprint matches the current tree, so nothing has
    # changed since the reviewer said "blocked" — the blockers cannot have been
    # addressed. The complementary case (the code DID change) is already
    # refused above as stale. Between the two, a blocking verdict requires both
    # a repair and a fresh review, and neither alone will do.
    if VERDICT_CAPTURE_SUPPORTED and record.get("blocking") is True:
        return _deny(
            f"The review on record for {bead_id} reported blocking findings, "
            "and the work has not changed since — so they are still open. Fix "
            "what the reviewer flagged, then record a fresh verdict against "
            "the repaired work.",
            bead_id,
            "deny:blocking-findings",
            reviewer=record.get("reviewer"),
        )

    # THE POINT OF escapement-1l04. Everything above this line is satisfied by
    # dispatching a reviewer and ignoring it: the dispatch ledger is written at
    # `Agent` PreToolUse, before the subagent emits a token, so `independent`
    # certifies that a reviewer was summoned, never that one was read.
    if claude_shape and VERDICT_CAPTURE_SUPPORTED and (
        record.get("verdict_source") != "captured"
    ):
        return _deny(
            f"The findings on record for {bead_id} are not the reviewer's own "
            "output. A reviewer was dispatched, but its verdict was never "
            "captured — so this record says the author of the code approves "
            "of it. Re-record so the reviewer's returned text is what gets "
            "stored.",
            bead_id,
            "deny:unbound-verdict",
            verdict_source=record.get("verdict_source"),
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


def _handle_agent_event(data: dict, tool_input: dict, beads: list[str],
                        session_id: str, cwd: str | None) -> None:
    """Ledger the dispatch (PreToolUse) or its verdict (PostToolUse).

    The same hook serves both events because they are two halves of one fact.
    PreToolUse establishes *which bead a reviewer was sent to read, and at what
    state of the work*; only PostToolUse can establish *what it said*, because
    at PreToolUse the subagent has not run yet. Recording the first and calling
    it a review is the bug escapement-1l04 exists to fix.
    """
    subagent_type = (tool_input.get("subagent_type") or "").strip()

    if data.get("hook_event_name") == "PostToolUse":
        verdict = extract_verdict(data.get("tool_response"))
        if verdict:
            record_verdict(session_id, beads, subagent_type, verdict)
        # A reviewer that returned nothing usable leaves its PreToolUse entry
        # without a verdict, which is the honest state: a dispatch happened and
        # no findings came back. It must not be upgraded into a review.
        return

    record_dispatch(session_id, beads, subagent_type, work_fingerprint(cwd))


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
                _handle_agent_event(data, tool_input, beads, session_id, cwd)
        return 0

    if tool_name != "Bash":
        return 0

    command = tool_input.get("command", "")

    # Writing the review record by hand would let an implementer mint
    # `independent: true` for itself with no reviewer involved. Recording must
    # go through the CLI, which is what reads the observed dispatch.
    if writes_reserved_metadata(command, METADATA_KEY):
        _deny_reserved_metadata()
        return 0

    targets = close_targets(command)
    if targets is None:
        return 0  # not a close

    if not targets:
        # A close whose target cannot be identified: a bare `bd close` (which
        # closes the last-touched issue), an unexpanded `$ID`, or prose sitting
        # in the positional slot. Treating this as "not a close" was a bypass —
        # `bd close -r "finished the work"` sailed straight through it. We
        # cannot check a review for a bead we cannot name, so we refuse.
        _deny_unidentified(command)
        return 0

    # `bd close` accepts several ids. Checking only the first let every
    # subsequent bead close unreviewed.
    for bead_id in targets:
        outcome = evaluate_close(
            command, bead_id, cwd, claude_shape=bool(session_id)
        )
        if outcome is DENIED:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
