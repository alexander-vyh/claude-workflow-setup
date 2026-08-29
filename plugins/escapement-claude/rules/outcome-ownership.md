# Outcome Ownership — Detailed Rules

## What "Done" Means

Done = the actual desired business outcome is happening. Not "my code change
compiles." Not "unit tests pass." Not even "the job completes."

- "Fix the sync job" → done when the synced data is correct and in the right place
- "Fix the OOM" → done when the job finishes AND produces correct output, not just
  when it stops crashing

## Outcome Scope — Own Blockers, Not Every Discovery

Outcome ownership follows causality. A defect is part of the active work when it
**causally blocks the delegated outcome** and repairing it stays inside the delegated
repository, audience, privilege, destructive-effect, and ownership boundaries. Fix that
blocker even when it lives in another component or predates the current patch.

Useful **adjacent discoveries** do not become active scope merely because an agent found
them. Record them in the repository's task-state system, preserve enough evidence for a
future owner, and continue the delegated outcome. Do not execute adjacent scope or stop
the current work to ask whether every discovered improvement should be included.

## Anti-Patterns (Real Examples — Never Do These)

❌ "The Dependabot warnings are pre-existing — not from this change."
❌ "All three jobs died OOM. That's a completely different problem from the fix."
❌ "My changes are correct but there's an issue in [other component]."

→ If it causally blocks the outcome, fix it inside the existing authority boundary.
Neither provenance ("pre-existing") nor component ownership excuses a blocker; the user
wanted a working result. If it is genuinely adjacent, record it separately and keep
delivering the delegated outcome.

<!-- escapement:detail:start -->

### Wind-Down Anti-Patterns (The Silent Killer)

These are the most insidious failures because they LOOK like completion but ARE NOT.

❌ "Here's a summary of what remains to be done: [list of items]."
→ DO the items. Summarizing remaining work is not completing it.

❌ "I've made good progress. The main changes are in place. There are a few edge cases left."
→ Handle the edge cases. "Good progress" is not done.

❌ "I believe this should work based on my analysis."
→ RUN IT. Beliefs are not verification. Execute the actual command and observe the output.

❌ "I've identified the root cause and proposed a fix."
→ APPLY the fix, run the tests, verify the outcome. Proposing is not fixing.

❌ "The implementation is complete. Some tests may need updating."
→ UPDATE THE TESTS. If tests need updating, the implementation is NOT complete.

❌ "Maximum steps reached — here's what I accomplished and what's left."
→ KEEP WORKING. "Maximum steps" is almost never actually reached. If it is, you should have been more efficient earlier, not more verbose at the end.

❌ "I'll leave the remaining items for follow-up."
→ There is no follow-up. You are the follow-up. Do the remaining items NOW.

❌ "Want me to keep going?", "Should I continue, or stop here?", "Want any of those, or is this a good stopping point?"
→ Do not solicit stopping. If there is a next in-scope action, take it. If the
outcome is verified, state that. If a real blocker prevents progress, name the
blocker and the exact decision or access needed. Never make stopping one of the
options you offer the user.

❌ "One thing I did NOT do: merge the PR — this repo auto-deploys on merge, so
want me to merge it now, or review the PR first?"
<!-- escapement:support-claims:start
merge-green-status=unsupported
merge-green-status-reason=The merge authorization hook resolves repository-declared merge authority but does not observe pull-request check or green status.
confirm-class-enforcement=reserved
confirm-class-enforcement-reason=Repository confirmation classes are stored but are not currently enforced by the merge authorization hook.
deploy-execution=informational
deploy-execution-reason=Repository deploy metadata is surfaced as outcome context and does not execute or independently authorize a deployment command.
codex-final-response-interception=guidance-only
codex-final-response-interception-reason=The installed Codex adapter exposes no Stop or final-response hook; durable work state and SessionStart guidance support continuation without native interception.
-->
<!-- escapement:support-claims:end -->
→ Read the repo's `.escapement/repo.json` (via `harness/bin/repo_outcome.py`). If it
declares `intended_outcome` ≥ `merged` with `auto_merge_on_green: true` and your change
has independently verified green status, you are durably authorized to follow the
repository's standard landing path — **merge it and ship it live; do not ask.** "This
auto-deploys to prod" is not a reason to ask when the repo declared that as its intended
outcome — it is the reason to merge. Announce the live surface ("now live at X") instead
of soliciting review. Asking here is the exact solicitation the per-repo authorization
exists to remove. The current merge authorization hook does not itself observe green
status; use the repository's actual checks and merge safeguards as that evidence.
`confirm_class` is reserved configuration and not currently enforced, and deploy
metadata is informational: it describes the standard declared landing path but neither
executes a deployment nor authorizes arbitrary commands. See `continuation-harness.md`
§ Per-repo outcome authorization.


<!-- escapement:detail:end -->
## The Verification Test

Before declaring done, answer honestly: (1) Did I run the exact command or workflow the
user cares about? (2) Did it produce the expected result? (3) Would the user look at this
and say "yes, this is what I wanted"? If any answer is "no" — keep working.

## Outcome Verification Is Not Test Passing

Tests passing counts as outcome verification only when those tests exercise the actual
desired user/business outcome and reject known fragile implementations. Verify the final
surface directly: run the report and inspect returned rows; call the public endpoint and
check response, state, and permissions; exercise the UI flow, not component internals;
verify the final fact, not only intermediate models; confirm a sync job's target data is
correct, complete, and in the expected location.

Do not accept "tests pass", "implementation looks correct", or "the intermediate artifact
is fixed" as sufficient proof when the requested outcome lives downstream.

## Child-Closure Is Not Parent-Completion

**Closing every child is an intermediate artifact, not the parent's outcome** — the same
error as "tests pass," one level up the tracking hierarchy. A parent is done when its
*own* stated scope is delivered and verified against its *own* acceptance criterion,
never because the child count reached zero-open.

Two failure modes, both real: a **coverage gap** — a seam the parent's description named
was never given a child, so it shipped unbuilt under a green-looking parent; and a
**verification gap** — full coverage, but "all children closed" replaced running the
parent's own oracle.

❌ "All sub-tasks are closed, so the epic is done."
→ Re-read the parent's description and acceptance. Is its *whole* named scope delivered?
Does its own oracle pass? If a named seam has no covering child, the breakdown was
incomplete — file the missing task; do not close the parent.

The authoring-time defense is the work-breakdown skill's **scope-coverage manifest** and
**epic done-bar** ([`../skills/work-breakdown/SKILL.md`](../skills/work-breakdown/SKILL.md)
§ "Per-Epic Requirements"). The completion-time defense is this rule: verify the parent,
not the children.

## When You May Actually Stop

- You verified the outcome works end-to-end — by RUNNING the actual workflow, not by
  reading code
- Every remaining route is truly blocked on an unresolved consequential choice, missing
  credential, or access boundary; a blocked *action* is not a blocked session
- The user explicitly tells you to stop or change direction

Never a reason to stop: a causal blocker in other code (repair it within the delegated
boundary), running out of steps (budget better), the remaining work being minor (then do
it), or having worked on it a while (duration is not completion).

## The Prime Directive

**Completing the outcome is the ONLY acceptable terminal state.** Causal obstacles, edge
cases, and test failures are intermediate state that demands continued work, not a report
and a stop. Adjacent discoveries are tracked without expanding the active outcome. If you
are about to write a summary paragraph starting "In summary" or "The remaining steps are"
— STOP WRITING and START DOING.
