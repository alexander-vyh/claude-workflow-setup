#!/usr/bin/env python3
"""Recovery for a delegated child that never reported.

The delegation hook registers an execution in `queued` with no
`native_child_id`, and `execution_ledger.apply_event` refuses `child_started`,
`child_terminal`, and `child_cancelled` without one. A child that dies before
the host ever reports its identity therefore has NO route to a terminal state
through the event API, and its parent session can never stop.

This module owns the one supported way out. It is deliberately narrow: the
execution must already have crossed one of its own deadlines, the reason must
meet the repo waiver substance bar, and every cancellation is written into the
ledger's `incidents` naming the actor, the crossed deadline, and the state it
was cancelled from. It records only that no result will arrive — never that the
child's work succeeded — so the terminal-versus-applied distinction survives.
"""

from __future__ import annotations

import datetime as dt

from execution_ledger import find_execution

UTC = dt.timezone.utc

# Manual cancellation of an unreported child carries the same substance bar as
# a `--<gate>-waiver` reason: one honest sentence, never a placeholder.
CANCEL_REASON_MIN_CHARS = 20
CANCEL_REASON_PLACEHOLDERS = frozenset(
    {
        "none",
        "tbd",
        "todo",
        "wip",
        "n/a",
        "na",
        "fixme",
        "xxx",
        "unknown",
        "dead",
        "stuck",
        "?",
        "??",
        "???",
    }
)


def _iso(now: dt.datetime) -> str:
    if (
        not isinstance(now, dt.datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError("datetime must be timezone-aware")
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def overdue_reason(item: dict, now: dt.datetime) -> str | None:
    """Return the deadline an active execution has crossed, else None.

    Deliberately a second, independent reading of the same deadlines that
    `would_block_stop._deadline_due` consults.  The two fail in opposite
    directions on purpose: the Stop gate fails CLOSED (an unreadable deadline
    blocks the stop), while cancellation must fail closed the other way — an
    unreadable deadline is never evidence that a child is overdue, so it must
    not license terminalizing that child.
    """
    if item.get("state") in {"terminal", "cancelled"}:
        return None
    if item.get("reconcile_due") in {"start", "idle", "hard"}:
        return item["reconcile_due"]
    current = now.astimezone(UTC)
    try:
        if current >= _parse(item["hard_deadline"]):
            return "hard"
        if item.get("state") == "queued" and current >= _parse(item["start_deadline"]):
            return "start"
        if item.get("state") in {"running", "unknown"} and current >= _parse(
            item["idle_deadline"]
        ):
            return "idle"
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _substantive_reason(reason: object) -> str:
    """Return a reason meeting the repo waiver substance bar, else raise.

    The placeholder check is token-wise rather than whole-string so that
    padding a null answer to length ("tbd tbd tbd tbd tbd tbd") does not buy
    its way past the character floor.  One real word is enough to pass it; the
    floor then does the rest.
    """
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    stripped = reason.strip()
    tokens = [token.strip("-–—.,;:()[]\"'") for token in stripped.lower().split()]
    if tokens and all(
        token in CANCEL_REASON_PLACEHOLDERS or not token for token in tokens
    ):
        raise ValueError(f"reason {stripped!r} is a placeholder, not a rationale")
    if len(stripped) < CANCEL_REASON_MIN_CHARS:
        raise ValueError(
            f"reason must be at least {CANCEL_REASON_MIN_CHARS} characters and say "
            f"why no result will arrive; got {len(stripped)}"
        )
    return stripped


def cancel_unreported(
    ledger: dict,
    execution_id: str,
    now: dt.datetime,
    *,
    reason: str,
    actor: str,
) -> dict:
    """Cancel one overdue execution whose native child never reported.

    This is the supported recovery for the child that dies silently.  Because
    the host bound no `native_child_id`, no `child_terminal` or
    `child_cancelled` event can ever be accepted for it, so without this the
    execution stays active forever and the parent session can never stop.

    It is deliberately narrow rather than a blanket "mark everything done":
    the execution must already have crossed one of its OWN deadlines, the
    reason must meet the waiver substance bar, and the cancellation is written
    into `incidents` naming the actor, the crossed deadline, and the state it
    was cancelled from.  It records only that no result will arrive.  It never
    asserts the child's work succeeded: `result_digest` stays null, so
    `claim_result_application` still refuses this execution and the
    terminal-versus-applied distinction survives intact.
    """
    timestamp = _iso(now)
    substantive = _substantive_reason(reason)
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor must be a non-empty string")
    item = find_execution(ledger, execution_id)
    terminal_event_id = (
        f"unreported-cancel:{execution_id}:{item['attempt']}:{item['generation']}"
    )
    if item["state"] == "cancelled" and item["terminal_event_id"] == terminal_event_id:
        return ledger
    if item["state"] in {"terminal", "cancelled"}:
        raise ValueError("execution already has different terminal evidence")
    basis = overdue_reason(item, now)
    if basis is None:
        raise ValueError(
            "execution has not crossed its start, idle, or hard deadline; it is "
            "still within its own budget and cannot be cancelled as unreported"
        )
    state_before = item["state"]
    item["state"] = "cancelled"
    item["terminal_at"] = timestamp
    item["terminal_reason"] = "unreported_child_cancelled"
    item["terminal_event_id"] = terminal_event_id
    item["result_digest"] = None
    item["last_activity_at"] = timestamp
    item["last_activity_kind"] = "terminal_event"
    item["reconcile_due"] = None
    ledger.setdefault("incidents", []).append(
        {
            "type": "unreported_child_cancelled",
            "execution_id": execution_id,
            "attempt": item["attempt"],
            "generation": item["generation"],
            "actor": actor.strip(),
            "reason": substantive,
            "overdue_basis": basis,
            "state_before": state_before,
            "native_child_id": item["native_child_id"],
            "recorded_at": timestamp,
        }
    )
    ledger["updated_at"] = timestamp
    return ledger
