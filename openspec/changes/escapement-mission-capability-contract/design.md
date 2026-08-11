## Context

Escapement already owns worktree, landing, completion, and agent-behavior policy, while OpenSpec and Beads provide design artifacts and task state. Its documentation nevertheless defines the product differently in different places: as an OpenSpec workflow, a Claude/Codex workflow layer, a continuation harness, or an outcome-verification mechanism. Routine delivery authority is also scattered across session context, repository settings, hooks, and prose, so agents repeatedly ask whether to take actions already included in the delegated outcome.

This is a cross-cutting contract change, not a new authorization service. It must align public positioning, runtime guidance, generated client packages, and tests without claiming that unsupported clients or lifecycle hooks already enforce the complete future behavior.

The governing management model is mission command and leverage: human-chosen intent directs available agent capacity. Closed-loop control is the operating model: define the outcome, act within authority, compare against an independent oracle, repair, and land. Lean flow and constraint management supply measures; enabling-bureaucracy principles govern whether a gate helps or merely consumes attention.

## Goals / Non-Goals

**Goals:**

- Make one client-neutral mission canonical across the repository.
- Preserve design, specification, executable work breakdown, and oracle quality as first-class Escapement capabilities.
- Define current tools as replaceable adapters to those capabilities.
- Make ordinary delivery actions part of delegated outcome authority instead of separate human decisions.
- Draw a causal scope boundary that owns blockers without authorizing adjacent scope creep.
- Establish action-local continuation as doctrine while accurately labeling current adapter limitations.
- Make contradictory or host-specific product definitions fail an executable check.
- Correct false or ambiguous claims about confirmation classes, green merges, deployment, final-response interception, and universal host support.

**Non-Goals:**

- Build a policy engine, OPA/Cedar integration, remote authorization service, cryptographic capability system, or general-purpose access-control language.
- Build the semantic authority resolver, action-local scheduler, intervention inbox, or cross-client prompt suppressor in this change.
- Add a Pi adapter or claim parity for clients without fixture-backed evidence.
- Replace OpenSpec, Beads, Git worktrees, GitHub, or current clients now.
- Invent abstract adapter interfaces before a real replacement needs them.
- Weaken discovery, work breakdown, test-oracle, or outcome-verification discipline in pursuit of fewer prompts.

## Decisions

### 1. Mission command defines the purpose; closed-loop control defines operation

The canonical mission is:

> Escapement converts available agent capacity plus delegated authority into verified, delivered outcomes while reserving human attention for consequential choices.

The mission is intentionally outcome-oriented rather than tool-oriented. The operating loop remains explicit: intent and authority → design → executable work graph → capacity allocation → isolated execution → continuation and repair → independent verification → authorized landing → learning.

**Alternatives considered:** Closed-loop control alone was too mechanical to express decision rights or strategic intent. Lean alone could optimize flow toward the wrong result. Theory of Constraints and value-stream mapping are useful diagnostics after telemetry exists, not product identity. Playing to Win belongs upstream in outcome choice. Enabling bureaucracy governs gate quality, not the mission. A pure “escapement mechanism” metaphor does not define authority or success.

### 2. Capabilities are stable; tools are adapters

The durable capability model is:

1. Intent and authority contract
2. Design and specification
3. Executable dependency-aware work breakdown
4. Capacity allocation
5. Isolated execution
6. Action-local continuation and repair
7. Independent outcome verification
8. Authorized landing and delivery
9. Learning and feedback

OpenSpec currently implements design/specification; Beads implements task and dependency state; Git worktrees implement isolation; client agents provide capacity; hooks and continuation state support control; tests and live checks implement oracles; GitHub and repository policy implement landing. These mappings may change without changing Escapement's mission or capability contract.

**Alternatives considered:** Defining Escapement as “built on OpenSpec” makes a replaceable tool the identity. Creating a formal adapter framework now would add speculative structure without a second real implementation. The selected approach documents stable ports and current mappings, then adds an adapter abstraction only when substitution creates a concrete contract.

### 3. Delegating an outcome delegates its ordinary means

When a user asks an agent to build, fix, change, execute, deliver, or ship a bounded outcome, the delegation includes routine, proportionate actions necessary to achieve and verify it within the named repository, systems, and constraints. That includes the established worktree, inspect/edit, test/lint/build, commit, task-branch push, pull-request creation/update, CI/review repair, and repository-declared merge/deployment/verification path.

These actions must not be re-presented as product decisions. A host approval prompt may still be mechanically unavoidable until an adapter or broker can suppress it; the documentation must distinguish that limitation from a need for new user intent.

Authority remains bounded. A discovered issue is in scope only when it causally blocks the delegated outcome and its repair does not materially expand behavior, repository set, audience, privileges, destructive effects, or another owner's work. Adjacent discoveries are tracked separately without stopping the delegated work.

Human attention is reserved for changed intent or non-goals, materially different valid outcomes, undelegated repositories/accounts/audiences, new privileges or credentials, destructive or irreversible shared effects, an actually enforced confirmation class, impossible isolation from another owner's work, or a missing standard landing path.

**Alternatives considered:** Wording-only encouragement would leave conflicting runtime rules intact. Treating all external effects as human-only contradicts durable repository authorization. Blanket “fix everything discovered” language expands scope without authority. A general policy engine is disproportionate to a typed, local delegation doctrine.

### 4. An escalation is action-local, not session-global

An unresolved decision blocks the action and dependents that require it. Independent authorized work remains runnable. A session is genuinely `input_required` only when no authorized route toward the delegated outcome can continue.

This change installs that rule in shared instructions and truthful capability documentation. It does not claim native scheduler enforcement where the client lacks a hook or queue primitive. Codex final-response interception, cross-client prompt suppression, and durable intervention resumption remain separate runtime work and must be version/capability gated.

**Alternatives considered:** A global pause is simpler but wastes capacity and turns side questions into blockers. Silently auto-approving every action preserves flow but violates bounded authority. The selected rule keeps flow high while limiting effect to already-delegated authority.

### 5. One structured identity owner; generated and authored surfaces are checked against it

Add a small `agent-surfaces/identity.json` containing the canonical mission, short product description, operating-loop labels, capability labels, and principle labels. It is the canonical identity data source. The renderer consumes it for generated instruction/package metadata and validates authored identity sections.

`agent-surfaces/manifest.json` continues to own adapter capability status, fixtures, and distribution. It does not become the owner of mission prose. `docs/VOCABULARY.md` owns client-neutral definitions. Onboarding fragments own runtime policy. README, naming, and deck remain authored presentation surfaces, but consistency tests require their core identity to agree and remove stale hand-maintained counts.

Generated `AGENTS.md`, `CLAUDE.md`, `.codex/hooks.json`, plugin trees, and marketplace metadata are never hand-edited.

**Alternatives considered:** README-only ownership would not govern runtime behavior. Copying mission prose into every file without validation recreates drift. Generating the entire README and deck would over-centralize presentation and make small narrative edits cumbersome.

### 6. Capability truth outranks aspirational symmetry

Core terms remain client-neutral, but adapter sections may name current clients and their exact ready, partial, guidance-only, or unsupported behavior. Claims such as “green merge is enforced,” “confirmation classes are live,” or “final answers are intercepted” require point-of-effect fixtures. Stored configuration or prose intent is insufficient.

## Risks / Trade-offs

- **Risk: A strong mission sentence becomes marketing while behavior stays unchanged.** → Update runtime doctrine and add negative tests that fail a README-only rewrite.
- **Risk: “Ordinary means” becomes a route to scope creep.** → Require causal necessity plus unchanged repository, audience, privilege, and effect boundaries.
- **Risk: Documentation promises action-local continuation that current clients cannot enforce.** → Separate normative doctrine from adapter enforcement status and retain explicit gaps.
- **Risk: Exact mission duplication is brittle.** → Keep a structured canonical source and validate only identity-bearing regions; allow presentation copy outside those regions.
- **Risk: New consistency checks become bureaucracy.** → Limit checks to high-impact contradictions already observed and provide named repair output.
- **Risk: Tool neutrality de-emphasizes valuable current workflow assets.** → Preserve an explicit current-adapter mapping and make design/work breakdown more prominent, not less.

## Migration Plan

1. Add the OpenSpec contracts and Test Oracle Brief.
2. Add failing identity/delegation consistency tests and run a mutation challenge.
3. Add the structured identity source and renderer validation.
4. Update authored onboarding and runtime rules, then regenerate client surfaces and packages.
5. Align README, vocabulary, naming, and deck; remove stale counts and unsupported claims.
6. Run renderer, OpenSpec, targeted, and repository-wide checks.
7. Land through the declared PR/merge/deploy path and verify the installed adapter exposes the new contract.

Rollback is a normal revert of the merged change followed by renderer and plugin refresh. No persisted user data or authorization state is migrated.

## Open Questions

No question blocks this documentation-and-contract change. The exact runtime representation of semantic authority, action-local scheduling, attention events, and additional client adapters remains intentionally deferred to implementation changes that can prove those behaviors independently.
