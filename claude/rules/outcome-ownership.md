# Outcome Ownership — Detailed Rules

## What "Done" Means

Done = the actual desired business outcome is happening. Not "my code change compiles." Not "unit tests pass." Not even "the job completes." The actual result the user needs in the real world.

- "Fix the sync job" → done when the synced data is correct and in the right place
- "Fix the date filter" → done when the query returns the right data for the right dates
- "Add the report" → done when the report shows accurate, correct numbers to the user
- "Fix the OOM" → done when the job finishes AND produces correct output, not just when it stops crashing

## Outcome Scope — Own Blockers, Not Every Discovery

Outcome ownership follows causality. A defect is part of the active work when it
**causally blocks the delegated outcome** and repairing it stays inside the delegated
repository, audience, privilege, destructive-effect, and ownership boundaries. Fix
that blocker even when it lives in another component or predates the current patch.

Useful **adjacent discoveries** do not become active scope merely because an agent found
them. Record them in the repository's task-state system, preserve enough evidence for a
future owner, and continue the delegated outcome. Do not execute adjacent scope or stop
the current work to ask whether every discovered improvement should be included.

## Anti-Patterns (Real Examples — Never Do These)

❌ "The Dependabot warnings are pre-existing — not from this change."
→ If they block the outcome, fix them or address them. Don't dismiss them.

❌ "It exposed a second bug: the LogDate filter uses date format instead of datetime format."
→ If that bug causally prevents the delegated outcome, fix it. If it is adjacent,
record it separately and keep delivering the delegated outcome.

❌ "All three jobs died OOM. That's a completely different problem from the schema mismatch fix."
→ The job still doesn't run. The user wanted a working job. Keep going.

❌ "My changes are correct but there's an issue in [other component]."
→ Component ownership does not excuse a causal blocker. Repair it within the existing
authority boundary; track a non-blocking adjacent issue without expanding scope.

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

Before declaring done, answer honestly:
1. Did I run the exact command/workflow the user cares about?
2. Did it produce the expected result?
3. Would the user look at this and say "yes, this is what I wanted"?

If any answer is "no" — keep working.

## Outcome Verification Is Not Test Passing

Tests pass only counts as outcome verification when those tests exercise the
actual desired user/business outcome and reject known fragile implementations.

When the user cares about a report, API, UI flow, data model, sync job, or
workflow, verify that final surface directly where possible:
- Report: run the report/query and inspect returned rows or metrics
- API: call the public endpoint and verify response, state, and permissions
- UI: exercise the user flow, not just component internals
- Data: verify the final fact/report, not only intermediate models
- Sync job: verify target data is correct, complete, and in the expected location

Do not accept "tests pass", "implementation looks correct", or "the intermediate
artifact is fixed" as sufficient proof when the requested outcome lives
downstream.

## Child-Closure Is Not Parent-Completion

When work is tracked as a parent/epic with child tasks, **closing every child is an
intermediate artifact, not the parent's outcome** — the same error as "tests pass"
or "the job ran," one level up the tracking hierarchy. A parent is done when its
*own* stated scope is delivered, verified against the parent's own acceptance
criterion — never because the child count reached zero-open.

Two distinct ways this fails, both real:

1. **Coverage gap** — the child set never covered the whole parent scope. A seam the
   parent's own description named was never given a child, so it was never built,
   yet the parent looks done once the children that *do* exist are closed.
2. **Verification gap** — even with full coverage, "all children closed" was treated
   as the close condition instead of running the parent's own oracle.

> **Real example (2026-05-29 [reported by the user]):** epic `cake-ta5.1` was a
> seam-extraction refactor. ~50 child tasks (one per handler function) were created
> and closed, so the epic read as complete — but the epic's description named
> `create_parser` / argparse setup (≈1,867 LOC) as a seam, and no child ever covered
> it. The largest named seam shipped unextracted under a green parent. A human, not
> the workflow, caught it.

❌ "All sub-tasks are closed, so the epic is done."
→ Re-read the parent's description and acceptance. Is its *whole* named scope
delivered? Does its own oracle pass? If a named seam has no covering child, the
breakdown was incomplete — file the missing task; do not close the parent.

The authoring-time defense is the work-breakdown skill's **scope-coverage manifest**
and **epic done-bar** (every named seam maps to a child; the epic carries its own
"Done when … **not when** all children closed" oracle). See
[`../skills/work-breakdown/SKILL.md`](../skills/work-breakdown/SKILL.md)
§ "Per-Epic Requirements". The completion-time defense is this rule: before closing
any parent, verify the parent, not the children.

## When You May Actually Stop

- You've verified the outcome works end-to-end — by RUNNING the actual workflow, not by reading code
- Every remaining route is truly blocked on an unresolved consequential choice,
  missing credential, or access boundary; a blocked action alone is not a blocked session
- The user explicitly tells you to stop or change direction

"I found a causal blocker in other code" is never a reason to stop. Repair it within
the delegated boundary. An adjacent discovery is recorded, not silently adopted.
"I ran out of steps" is never a reason to stop. Budget your steps better.
"The remaining work is minor" is never a reason to stop. If it's minor, do it — it'll take seconds.
"I've been working on this for a while" is never a reason to stop. Duration is not completion.

## The Prime Directive

**Completing the outcome is the ONLY acceptable terminal state.** Causal obstacles,
edge cases, and test failures are intermediate state that demands continued work, not a
report and a stop. Adjacent scope discoveries are tracked without expanding the active
outcome. If you are about to write a summary paragraph that starts with "In summary"
or "To complete this work" or "The remaining steps are" — STOP WRITING and START DOING.
