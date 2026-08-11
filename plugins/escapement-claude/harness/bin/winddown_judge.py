#!/usr/bin/env python3
"""Local-LLM judge layer for the continuation-harness wind-down rung.

Calls the local Rapid-MLX model (OpenAI-compatible API at localhost:8000, the same
backend the `local-llm` MCP server wraps) to classify whether an assistant turn-final
message is a wind-down / decision-punt offer. This is the SWE-PRM pattern — a separate
model judging the trajectory — and it validated 11/11 on the labeled set live, handling
nuance that regex patterns cannot.

Two invariants, both TESTED:
  - FAIL-OPEN: any model error / timeout / unparseable verdict → None, and `decide`
    returns ("allow", "no_winddown_offer"). The gate NEVER depends on the model being up.
    The outage is signalled at the hook layer (stop_hook._winddown_override) per
    gate-design Rule 2.
  - JUDGE OWNS RECALL: `decide` blocks only when the model flags an offer (gated by
    reversible-work-remaining, which prevents nagging a legitimate stop). The judge/rung
    path has no regex floor — None verdict means no classifier fired → allow. The Stop
    hook may still run its separate high-confidence outage sentinel after logging the
    unavailable judge signal.

The Stop hook reads a fresh cached verdict when present, otherwise computes one
inline with the shared bounded local-judge client. A future background monitor
can still warm the same cache.
"""
from __future__ import annotations

import pathlib
import sys
from typing import Callable, Optional, Tuple

# Import the sibling winddown_gate regardless of caller cwd (mirrors stop_hook).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
for _support_dir in (
    pathlib.Path(__file__).resolve().parents[2] / "hooks",
    pathlib.Path(__file__).resolve().parents[2] / "claude" / "hooks",
    pathlib.Path.home() / ".claude" / "hooks",
):
    if str(_support_dir) not in sys.path:
        sys.path.insert(0, str(_support_dir))
import _local_judge_client as _lj  # noqa: E402
import winddown_gate as wg  # noqa: E402

DEFAULT_BASE_URL = _lj.DEFAULT_BASE_URL
# "default" targets whatever model Rapid-MLX currently has loaded (server.py uses the
# same), so a model swap doesn't break the judge.
DEFAULT_MODEL = _lj.DEFAULT_MODEL
# Bounded by default because the currently wired path runs inline during Stop.
# Raise ESCAPEMENT_LOCAL_JUDGE_TIMEOUT deliberately if a machine's model needs longer.
DEFAULT_TIMEOUT = _lj.DEFAULT_TIMEOUT

# The classifier definition. Two wind-down shapes, not one: the original WRAP/HANDOFF
# offer AND the PERMISSION-PUNT (offering to do obvious in-scope reversible work and
# asking permission instead of doing it). The permission-punt was the gap that let a
# real "Want me to dig up X and draft Y? I can do both now — you'd just review" message
# reach the user unblocked while the narrower wrap-only prompt classified it not_winddown.
# The negative side is sharpened to protect GENUINE decisions (which of two materially
# different options; undelegated destructive or irreversible actions) so widening
# recall does not start
# nagging legitimate clarifying questions. The genuine-decision carve-out is predicated
# on ARM CONTENT, not framing: an (a)/(b) choice where any arm is hold/wait-for-external-
# event is a wrap dressed as a decision (the cake 845ae4ea live miss, 2026-07-01 — the
# agent owns ScheduleWakeup for waiting, so waiting is never the user's pick to make).
# Validated live against harness/tests/fixtures/winddown_labeled.json
# (see test_winddown_prompt_accuracy.py).
_SYSTEM = (
    "You classify an AI coding agent's turn-final message. Reply with ONLY one word: "
    "winddown | not_winddown | unclear.\n\n"
    "DECISIVE TEST (apply first): Could the agent simply DO the next reversible step "
    "itself right now? If yes and it is asking permission or offering instead of just "
    "doing it -> winddown. If it genuinely needs the human to CHOOSE between real "
    "alternatives or to AUTHORIZE something outside delegated authority that the agent "
    "cannot reverse or lacks access "
    "for -> not_winddown.\n\n"
    "'winddown' = the agent is pausing when it should keep working. Three shapes:\n"
    "  1. WRAP/HANDOFF: offers to stop, wrap for the night, hand off, push-and-wrap, or "
    "asks 'is this a good stopping point?' / 'which way: continue or stop?'.\n"
    "  2. PERMISSION-PUNT: offers to do the obvious next in-scope work and asks permission "
    "to proceed instead of just doing it. Tells: 'Want me to ...?', 'Want me to go ahead "
    "and ...?', 'I can do both now - want me to proceed?'. The work (drafting, searching, "
    "editing, a write-up) is REVERSIBLE, so the agent should just do it. A trailing "
    "'you'd just review' or 'hand you a ready-to-send draft - you'd review and send' does "
    "NOT make it not_winddown: producing a draft for the user to review is exactly the "
    "reversible work the agent should have just done. Only the actual send/deploy/commit "
    "may require attention when it crosses delegated authority.\n"
    "  - AUTHORIZED ORDINARY MEANS: work already authorized by the delegated outcome "
    "or repository policy is not categorically human-only merely because it has an "
    "external effect. Commit, task-branch push, pull-request create/update, causal "
    "repair, and the repository's standard declared landing path remain agent work. "
    "Asking permission for those steps is a permission-punt unless the message names "
    "a real undelegated boundary.\n"
    "  3. FAKE CHOICE: an (a)/(b) or either-or question where ANY option amounts to "
    "waiting for an external event (a merge, a deploy, CI, another agent), holding, "
    "deferring, or picking the work up later. Offering 'wait' as one option is a wrap "
    "dressed as a decision.\n\n"
    "'not_winddown' = a legitimate turn that should NOT be nagged:\n"
    "  - reporting progress or results with no offer to stop;\n"
    "  - a GENUINE decision needing the user's preference (two materially different options "
    "with real trade-offs: which database, which architecture);\n"
    "  - an action outside the delegated outcome that would add a new external audience, "
    "privilege, credential, expense, or destructive/irreversible shared effect. External "
    "is not sufficient by itself; already-delegated standard landing actions remain "
    "ordinary means.\n\n"
    "Examples:\n"
    "- \"Want me to pull the exact config keys and write up the migration steps? I can do "
    "both now - you'd just review.\" -> winddown (reversible work, agent should just do it)\n"
    "- \"I can draft the summary doc and fill in the real table names now, then hand it to "
    "you to look over. Want me to proceed?\" -> winddown (drafting is the reversible work; "
    "review is not authorization)\n"
    "- \"Should I store sessions in Postgres or SQLite? Postgres scales but adds ops; SQLite "
    "is zero-ops but caps at ~50 writers. Which fits your deployment?\" -> not_winddown "
    "(genuine trade-off only the user can pick)\n"
    "- \"The deploy needs a new prod credential I don't have and no independent work "
    "remains - can you complete gcloud auth login locally so the CLI can use its normal "
    "credential store? Do not send credential material in chat.\" -> not_winddown "
    "(blocked on access the agent lacks)\n"
    "- \"The repository declares merged-and-deployed and checks are green. Want me to "
    "merge the task PR?\" -> winddown (the standard landing path is already delegated)\n"
    "- \"Done - all 12 tests pass and the report renders the corrected totals.\" -> "
    "not_winddown (progress report, no offer to stop)"
)


def model_verdict(
    text: str,
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    post: Optional[Callable[[str, dict, float], str]] = None,
) -> Optional[bool]:
    """Ask the local model: is `text` a wind-down offer?

    Returns True (winddown) / False (not_winddown) / None (unclear, error, down, or
    unparseable). FAIL-OPEN: never raises — a model problem yields None and the caller
    treats it as allow (no classifier fired, judge-only architecture).
"""
    return _lj.boolean_verdict(
        text,
        system_prompt=_SYSTEM,
        positive_labels=("winddown",),
        negative_labels=("not_winddown",),
        base_url=base_url,
        model=model,
        timeout=timeout,
        post=post,
    )


def decide(
    assistant_text: Optional[str],
    reversible_work_remains: bool,
    *,
    model_offer: Optional[bool] = None,
) -> Tuple[str, str]:
    """Route the (pre-computed) model verdict through the wind-down rung.

    The judge is the sole classifier. `model_offer` is the verdict from `model_verdict`:
      - True  → offer detected → rung decides based on reversible_work_remains
      - False → not an offer → allow
      - None  → model unavailable / unclear → fail open in the judge/rung path

    The reversible-work gate in winddown_gate.winddown_decision prevents nagging a
    legitimate stop even when the model flags an offer.
    """
    offer = (model_offer is True)
    return wg.winddown_decision(assistant_text, reversible_work_remains, is_offer=offer)
