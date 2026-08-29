# Molecule Awareness — Global Rule

## On Every Session Start

If the current directory has a `.beads/` directory, run `~/.beads/mol-status.sh` and act on the output.

If active molecules are found:
- Present the current phase (Design / Validate / Build) and the next step
- Run the next ordinary authorized step after announcing it; don't turn progression
  into a permission question
- If a gate is ready and represents an **unresolved consequential choice**, present
  that narrow decision with a summary of what was produced
- Never show bd commands, gate IDs, step IDs, or molecule internals to the user

If no active molecules are found, say nothing about molecules.

## Gate Resolution

A molecule gate is for an **unresolved consequential choice**, not routine progression
**already included in the delegated outcome**. Design intent, materially different valid
outcomes, privilege expansion, destructive shared effects, or unsafe ownership overlap
may require attention. Creating the established worktree, editing scoped files, running
checks, committing, pushing the task branch, updating its pull request, repairing causal
failures, and following the repository's standard declared landing path do not require a
new product decision.

If a stored gate is mechanically ready but its answer is already fixed by the approved
specification, explicit delegation, or repository policy, resolve it from that durable
authority and continue. If the choice is genuinely unresolved, block only that gate and
its dependents; continue independent authorized work in other steps or molecules.

When the user approves a gate (says "looks good", "approved", "yes", "continue", "proceed", or similar affirmative):
- Run `bd gate resolve <gate-id>` silently
- Record the gate resolution (silently — never surface to user):
  ```
  bd audit record --kind gate_resolved \
    --issue-id <gate-id> \
    --response '{"molecule": "<mol-id>", "gate": "<gate-name>", "phase": "design|validate|build", "decision": "approved"}'
  ```
- Then check `bd mol current <molecule-id>` for the next step
- Announce and run the next ordinary authorized step

When the user rejects a gate (says "revise", "no", "go back", or similar):
- Record the rejection (silently):
  ```
  bd audit record --kind gate_resolved \
    --issue-id <gate-id> \
    --response '{"molecule": "<mol-id>", "gate": "<gate-name>", "phase": "design|validate|build", "decision": "rejected"}'
  ```
- Keep the gate open
- Help the user revise the previous step's output
- When they're satisfied, ask again

## Phase Presentation

Map internal steps to phases for the user:

| Internal Steps | User-Facing Phase |
|---------------|-------------------|
| discovery, review-discovery | **Design** — "We're designing X" |
| work-breakdown | **Design** (tail end) — "Breaking down the work" |
| execute-skeleton, review-skeleton | **Validate** — "Testing the riskiest assumption" |
| execute-full | **Build** — "Building out the full feature" |

## Automatic Progression

When a step completes and the next step has no gate:
- Proceed automatically — don't ask permission for mechanical steps
- Announce what you're doing: "Discovery is done. Running work-breakdown now."

When a step completes and the next step has a gate:
- If the gate contains an unresolved consequential choice, present only that decision
  and summarize the evidence needed to decide it
- Otherwise resolve the mechanical gate from established authority and continue
- In either case, continue independent authorized work rather than suspending the session

When all steps in a molecule are complete, record completion (silently):
```
bd audit record --kind molecule_complete \
  --issue-id <mol-id> \
  --response '{"name": "<feature-name>", "total_steps": N, "amendments": N, "formula": "mol-feature|mol-rapid"}'
```

## Timing Analysis

`bd mol progress <molecule-id>` already tracks step-level timing data including:
- Completed / total steps with percentage
- Rate (steps/hour based on closure times)
- ETA for remaining work

Use this for retrospectives or when the user asks about velocity — no need to
add redundant timing audit records. For per-issue timestamps, use
`bd show --long --local-time <issue-id>`.

## Multi-Molecule Routing

If multiple molecules are active, present the highest-priority one first and mention others exist:
- "Continuing the higher-priority Validate work (dark-mode); auth-refactor remains in Design."
- Ask for priority only when the alternatives encode a real unresolved outcome trade-off

<!-- escapement:detail:start -->

## Creating New Molecules

When the user delegates new feature work, create the standard molecule when that is the
repository's established design-to-delivery path; setup is already included in the
delegated outcome. Announce the transition and immediately start discovery. Ask only if
choosing a workflow would itself create a material outcome trade-off.

**How to create a molecule (exact sequence):**

1. Check available formulas: `bd formula list`
   - `mol-rapid` — bug fixes, chores, one-off tasks (2 steps, no gates)
   - `mol-feature` — standard features (6 steps, 2 gates)
   - If no formulas found, fall back to manual `bd create` with an epic + child tasks

2. Select the formula based on scope:
   - Quick fix / bug / chore → `mol-rapid`
   - Feature work → `mol-feature`

3. Pour the molecule:
   ```
   bd mol pour mol-feature --var name="<kebab-case-name>" --var problem="<one-sentence problem>"
   ```
   This creates the root epic + all step tasks + gate tasks with dependencies.

4. Check the molecule state: `bd mol current <molecule-id>`
   - The first step (discovery) will show as [ready]
   - Offer to start it immediately

**Do NOT use `bd mol show` to find formulas** — that command only works on existing molecules.
Use `bd formula list` to see available formulas and `bd formula show <name>` for details.

**Variable naming:** The `name` var should be kebab-case (e.g., "zoom-data-expansion"). The `problem` var is a one-sentence problem statement.


<!-- escapement:detail:end -->
## Scope Change Detection

During any conversation with an active molecule, listen for scope-change language:
- "let's also...", "can we add...", "what about including..."
- "actually we don't need...", "cut X", "remove Y from scope"
- "scope change:", "actually...", "change of plans"
- "the spec assumed... but actually..."

When detected:
- If the user explicitly directs the scope change, trigger the Spec Amendment Flow
  (§2h in beads-execution): draft the delta, update affected dependencies, and present
  any consequential design choice. Do not ask the user to confirm the instruction they
  just gave.
- If the language is ambiguous and would materially change intent or non-goals, preserve
  the proposed delta as an unresolved consequential choice and ask only that narrow
  question. Continue independent authorized work under the current approved scope.
- If the idea is merely an adjacent discovery, record it and continue current scope.

**Scope changes are always human-driven.** An explicit user direction supplies that
authority; an ambiguous conversational signal does not. Never silently change scope,
specifications, or task descriptions from an inference.
