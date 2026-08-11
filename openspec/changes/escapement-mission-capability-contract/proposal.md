## Why

Escapement is currently described through whichever tools happen to implement it—OpenSpec, Beads, Claude Code, Codex, hooks, and GitHub—rather than through the outcome it exists to produce. That drift causes agents to re-ask for routine actions already delegated by the user or repository, spend attention on tool ceremonies, and confuse activity with verified delivery.

## What Changes

- Establish one client-neutral mission: Escapement converts available agent capacity plus delegated authority into verified, delivered outcomes while reserving human attention for consequential choices.
- Define the durable capability chain beneath that mission: intent and authority, design and specification, executable work breakdown, capacity allocation, isolated execution, action-local continuation, independent verification, authorized landing, and learning.
- Define OpenSpec, Beads, worktrees, current agent clients, and GitHub as replaceable adapters that implement those capabilities rather than defining the product.
- State that delegating an outcome delegates its ordinary, proportionate means—including the repository's declared worktree, edit, test, commit, push, pull-request, repair, merge, deployment, and verification path—without repeated confirmation.
- Bound that authority by the delegated outcome and distinguish causally necessary blockers from adjacent discoveries.
- Require an unresolved consequential decision to block only its dependent action while independent authorized work continues.
- Align authored public documentation, runtime instructions, generated client surfaces, and plugin metadata with the same mission and doctrine.
- Correct documentation that overstates currently enforced behavior, including confirmation-class, green-merge, deployment, and final-response interception claims.
- Add executable narrative-consistency checks that reject host-specific core definitions, README-only rewrites, and generated-surface drift while preserving explicit adapter sections.

## Capabilities

### New Capabilities

- `mission-capability-contract`: Defines Escapement's mission, durable capability model, replaceable-adapter boundary, and the canonical ownership of those definitions.
- `delegated-outcome-authority`: Defines which ordinary means are included when an outcome is delegated, the causal scope boundary, and the narrow conditions that justify human attention.
- `action-local-continuation`: Defines the observable rule that an unresolved action or question does not suspend independent authorized work and prevents documentation from claiming unsupported runtime enforcement.

### Modified Capabilities

- `agent-surface-parity`: Generated and public agent surfaces must derive or validate the canonical mission, capability terminology, adapter boundaries, and truthful support claims in addition to existing hook and skill parity.

## Impact

This change affects authored sources under `agent-surfaces/`, the agent-surface renderer and its tests, public documentation (`README.md`, `docs/VOCABULARY.md`, `docs/NAMING.md`, and `docs/deck.html`), and runtime rules that currently govern outcome ownership, continuation, teams, molecules, landing, and wind-down. Generated `AGENTS.md`, `CLAUDE.md`, plugin trees, and marketplace metadata are regenerated from their owners. No authorization service, policy engine, scheduler, Pi adapter, or native prompt-suppression mechanism is introduced by this change; later runtime work must implement those behaviors behind the capability contracts without changing the mission.
