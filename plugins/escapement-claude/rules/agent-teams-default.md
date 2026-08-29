# Agent Teams as Default — Global Rule

## Default to Agents

For any task beyond a single quick action, dispatch agents. This includes research, exploration, implementation, debugging, code review, investigation — anything that involves multiple steps or could benefit from parallelism.

A single file read, one search, or a small edit is fine inline. Everything else should go to agents.

## Always Use Named Agents

Every dispatched agent MUST have a `name`. This is the only requirement for coordination.
The session has a single implicit team — all named agents are automatically on it and can
address each other via `SendMessage({to: name})`.

(`TeamCreate` and `team_name` are deprecated and ignored by the current Claude Code runtime.)

<!-- escapement:detail:start -->

### Concrete Example — This Is What Every Dispatch Must Look Like

```
# Dispatch named agents — they are automatically on the implicit team
Agent(
  name="researcher-1",
  description="Research auth patterns",
  prompt="Investigate OAuth patterns in the codebase.
    Use SendMessage to share findings with researcher-2."
)
Agent(
  name="researcher-2",
  description="Research session handling",
  prompt="Investigate session management patterns.
    Use SendMessage to share findings with researcher-1."
)
# Named agents can SendMessage to each other by name

# ❌ WRONG — no name at all
Agent(prompt="Investigate OAuth patterns...")
# Fire-and-forget, no coordination possible
```


<!-- escapement:detail:end -->
<!-- escapement:detail:start -->

### Shared terminology before a design fan-out (gated)

**Most research does NOT need this.** But before a multi-agent **design** effort
in an **unfamiliar** domain whose terminology encodes a distinction the model's
default framing would get **wrong** (e.g. entitlement vs ownership, queue vs
group) **and** the output is load-bearing — dispatch a single **living
vocab-scout** to establish the team's shared glossary *before* the design agents
fan out, then have them work from (and challenge) that glossary. Load the
**`vocab`** skill (`/vocab`) for the full protocol. (vocab-first is the one case
this rule's point-to-point SendMessage doesn't cover: a shared living glossary
the whole team queries.)

**Skip** for familiar domains, codebase/org-internal questions, urgent one-fact
lookups, or topics with no external literature. Self-test: if you can't name the
specific wrong prior the field's vocabulary would correct, don't run it. Opt-in
guidance, not a gate.

### Vocabulary — What the User Means

> Canonical definitions for these and all cross-system terms live in
> [`docs/VOCABULARY.md`](../../docs/VOCABULARY.md). The table below is the local
> quick-reference; if the two disagree, the glossary wins.

| User says | What to do |
|-----------|-----------|
| "agent team" | 2-5 named agents |
| "roundtable" | Named agents with persona prompts that argue via SendMessage |
| "panel of experts" | Named agents with different expertise, share findings via SendMessage |
| "have them talk to each other" | Named agents using SendMessage — just give each a `name` |
| "use agents" | Named agents, not inline work |

**"Roundtable" NEVER means writing simulated dialogue in your output.** It ALWAYS means dispatching real named agents on a team that independently analyze and communicate via SendMessage.


<!-- escapement:detail:end -->
### Works With Beads

Named agents and beads are complementary. Beads tracks *what* to do (`bd ready`, `bd close`), named agents handle *how* they coordinate while doing it. When dispatching agents for beads-tracked work, give each agent a `name` — beads adds tracking, naming enables coordination.

## Causal Scope And Action-Local Continuation

Team capacity serves the delegated outcome, not every issue an agent happens to find.
Own and repair work that **causally blocks the delegated outcome** when it remains
inside the delegated repository, audience, privilege, effect, and ownership boundaries.
Record **adjacent discoveries** separately; do not dispatch execution for them or suspend
the active outcome to solicit a scope expansion.

When one lane reaches an unresolved consequential choice, preserve that dependency and
keep other authorized lanes running. A blocked agent is not a blocked team. Escalate
only the narrow decision after all independent authorized work has continued as far as
it can.

<!-- escapement:detail:start -->

## Agent Pairing for Quality

When dispatching implementation agents, consider pairing them with independent
QA agents that work from the success criteria or spec — NOT from the code.

The full QA-pattern catalog — with dispatch templates and worked examples — lives
in the **`dispatching-parallel-agents` skill**; load it when you actually pair.
The operative directive for each pattern, one line, stays here:

- **Independent Test Agent Pattern** — for any non-trivial implementation, pair a
  `qa-tester` with the `implementer`; the tester writes tests from the SPEC /
  success criteria and NEVER from the code, producing behavioral tests +
  positive/negative controls + a statement of which bad implementations they
  reject. The implementer must pass them.
- **Mutation Challenger Pattern** — before a non-trivial behavior change, dispatch
  a challenger (no production code) that invents 2-5 plausible bad implementations
  including the tempting shortcut, and BLOCKS implementation until the named
  fragile implementation fails at least one behavioral / fixture / contract /
  architecture / static check.
- **Outcome Verifier** — after implementation and review, dispatch a verifier that
  checks the actual user-facing result (run the report/query, call the endpoint,
  exercise the UI flow, verify the data/sync target), NEVER accepting "tests pass"
  or "looks correct" as proof.
- **Completeness Critic** — after the per-lens reviewers report and before
  declaring the review done, dispatch a generative, blinded critic that surfaces
  what is MISSING (gaps no lens owned), UNDERSTATED (severity to calibrate up),
  and MIS-SCOPED; each gap re-enters the loop as a new finding. Run
  loop-until-dry.

See the `dispatching-parallel-agents` skill for the full write-ups, the bad-
implementation-class checklist, and the dispatch templates.

### When to Pair

- **Always pair** for feature/epic work with behavioral specs
- **Consider pairing** for complex bug fixes where the fix could mask the root cause
- **Skip pairing** for simple chores, config changes, one-liners

### Subtask Parallelism

Agents aren't just for separate tasks. Within a single task, dispatch parallel
agents for:
- **Research + implementation** — one investigates the codebase, one drafts the code
- **Implementation + testing** — one codes, one writes tests from the spec
- **Multiple approaches** — two agents try different solutions, compare results
- **Ongoing verification** — a background agent runs tests continuously as code changes

### Aggressive Decomposition

If a task takes more than one session or produces more than ~200 lines of changes,
it should have been decomposed further. Break work into pieces small enough that
each agent can complete its piece independently. Smaller tasks = more parallelism =
faster delivery = easier verification.


<!-- escapement:detail:end -->
## Writer Isolation

Two or more agents that will COMMIT means one worktree and branch each —
one session-injected `escapement-worktree create` transaction per agent or
`isolation: "worktree"` on dispatch, with the lead merging branches back
deliberately. Prompt-level "you own these files" lanes are merge-planning notes,
never the isolation mechanism. This applies to concurrent *sessions* exactly as
it applies to dispatched agents — full rule: `worktree-discipline.md`.

## Anti-Patterns

- Sequential inline web searches instead of parallel search agents
- Reading 10 files one by one instead of dispatching explore agents
- Doing all investigation yourself instead of dispatching a team
- Using anonymous agents that can't talk to each other
- **Writing simulated persona dialogue in your output instead of dispatching real agents**
- **Dispatching agents without `name`** — they are anonymous and unaddressable
- **Using `Agent(prompt="...")` without `name`** — this is ALWAYS wrong
- **Winding down prematurely** — summarizing remaining work instead of doing it
- **Declaring "done" without verification** — reporting completion without running the actual test/command/workflow

## Continuation Discipline

Agents — both the main thread and subagents — tend to prematurely wind down. This
section exists to prevent that failure mode. These rules are NON-NEGOTIABLE.

### For the Main Agent (You)

**DO NOT STOP until the outcome is verified end-to-end.** Finding a problem is not
finishing — FIXING the problem is finishing. Summarizing remaining work is not
finishing — DOING the remaining work is finishing.

**If you discover additional work during execution, classify it by causality.** Fix or
dispatch causal blockers within the delegated boundary. Record adjacent discoveries for
a future owner without executing them, then KEEP GOING on the delegated outcome.

**If you hit an obstacle, work around it or escalate the narrow consequential choice —
do NOT treat it as a team-wide stopping point.** Continue every independent authorized
lane while the blocked action waits.

**Declaring "done" without running verification is FAILURE.** Run the actual
command/test/workflow. See the actual output. Confirm the actual result. "I believe
this works" is not verification. Tests count only when they exercise the actual outcome
and reject plausible bad implementations. Observed output must match the independent
oracle.

### Pre-Completion Checklist

Before declaring ANY task complete, mentally run this checklist. If any answer
triggers more work — do that work before stopping.

1. **Did I dispatch all agents that could work in parallel?** If independent work
   remains, dispatch agents for it NOW.
2. **Are there causal blockers I found during execution that remain unaddressed?**
   If yes, fix or dispatch them. Record adjacent discoveries without expanding scope.
3. **Did I verify the outcome end-to-end?** Not just "my change compiles" — did I
   run the actual workflow and confirm the actual result?
4. **Could I dispatch a review/QA agent to independently verify?** If the work is
   non-trivial, dispatch one.
5. **Am I stopping because the work is DONE, or because I'm tired of working on it?**
   Be honest. If you're stopping because it feels like enough, you're wrong — keep going.

### For Subagents (Include in Every Agent Prompt)

When dispatching agents, include this language in their prompts:

> **CONTINUATION DISCIPLINE:** DO NOT wind down prematurely. DO NOT summarize
> remaining work and stop. If you find additional problems during your work, FIX
> THEM. If you hit an obstacle, investigate and work around it — do not report it
> as a reason to stop. You are done when the OUTCOME is verified, not when you've
> made an attempt. "Maximum Steps Reached" is not an acceptable reason to stop
> unless you have genuinely exhausted every available action. Own work that causally
> blocks the delegated outcome; record adjacent discoveries without executing them.
> If one action needs an unresolved consequential choice, continue every independent
> authorized lane before escalating that narrow dependency.
