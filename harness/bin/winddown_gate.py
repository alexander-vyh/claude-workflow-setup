#!/usr/bin/env python3
"""Continuation-harness wind-down rung — judge-driven gate.

The local-LLM judge (winddown_judge.py) is the SOLE wind-down classifier. This
module provides the rung logic that ACTS on the judge's verdict — it does not
classify prose itself.

DESIGN NOTES
- Classification is semantic-only. `winddown_decision` accepts the judge's pre-computed
  verdict via `is_offer`. When `is_offer` is None (judge unavailable), the rung
  fails open: no offer detected, allow. The outage is signalled at the hook layer
  (stop_hook._winddown_override) per gate-design Rule 2.
- The Stop hook has a separate, narrow outage sentinel for transcript-proven
  DWDEV-style wind-down shapes after that signal is recorded. That sentinel is not
  part of this rung and does not reintroduce a general prose classifier here.
- HONEST LIMITATION: the Stop hook fires AFTER the turn is rendered, so this rung forces
  *continuation*; it does NOT un-show the offer text. It converts "agent pauses and waits"
  into "agent offers but keeps going."
"""
from __future__ import annotations

from typing import Optional, Tuple

# The denial. Per gate-design Rule 1 the escape path is IN the message: proceed with the
# reversible work + async-flag only unresolved consequential boundaries; and the user's
# release valve ("stop") is preserved so the gate is not a trap.
RECOVERY_PROMPT = (
    "You ended your turn by offering to wind down (wrap / pause / 'which way?') while "
    "reversible in-scope work remains. Do NOT offer to wrap or ask which way — just "
    "PROCEED now with the next reversible task. Work already authorized by the delegated outcome "
    "is not categorically human-only merely because it is external: commit, task-branch "
    "push, pull-request work, and the repository's standard declared landing path remain "
    "ordinary means. If an unresolved consequential choice genuinely crosses that "
    "authority, async-FLAG only that action and its dependents and KEEP WORKING every "
    "independent authorized path; one blocked item does not block the session. The "
    "user can still release you by saying 'stop'."
)


def winddown_decision(
    assistant_text: Optional[str],
    reversible_work_remains: bool,
    *,
    is_offer: Optional[bool] = None,
) -> Tuple[str, str]:
    """The rung. Returns (decision, reason).

    `is_offer` is the judge's verdict. None means no classifier fired — fail open to
    allow. The rung never classifies prose; it only acts on the supplied verdict.
    """
    offer = bool(is_offer) if is_offer is not None else False
    if not offer:
        return ("allow", "no_winddown_offer")
    if not reversible_work_remains:
        # Genuinely blocked on an unresolved consequential boundary with nothing
        # reversible left: a legitimate stop. Nagging it is the over-correction the
        # research warns against (selective-quitting-improves-safety).
        return ("allow", "winddown_but_no_reversible_work")
    return ("block", "winddown_offer_work_remains")
