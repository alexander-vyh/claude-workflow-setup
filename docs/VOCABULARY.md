# Vocabulary

<!-- escapement:core-identity:start -->
Escapement converts available agent capacity plus delegated authority into verified, delivered outcomes while reserving human attention for consequential choices.

This document defines the client-neutral mechanics behind that mission. The vocabulary
is modern (multi-agent orchestration and agent harnesses), but its roots are older:
division of labor, message-passing concurrency, mission command, control theory, the
test-oracle problem, and enabling bureaucracy. Current tools appear only in explicit
adapter definitions; they do not define the system.
<!-- escapement:core-identity:end -->

## How to use this document

- **Reference, not standing context.** This file is linked from the rules but is *not*
  injected into every session. Read it when a term is unclear; don't pay its token cost
  on every turn. (This is itself an application of the attention/context-load discipline
  the repo cares about.)
- **Single source of truth.** Before this file, "agent team" was defined in three places
  (`claude/rules/agent-teams-default.md`, `claude/skills/dispatching-parallel-agents/SKILL.md`,
  `openspec/changes/continuation-harness/design.md`). Those sections still exist for local
  context; this file is the canonical definition they should agree with. If they drift,
  this file wins and the others should be updated.

### Provenance convention

Base-principle attributions are load-bearing — a reader may act on them. So each carries
a provenance marker (per `claude/rules/evidence-provenance.md`):

- **Repo cites** — the lineage is stated in the repo's own files (verified, with path).
- **Roots** — a well-established origin from the general literature.
- **[analytical mapping]** — an interpretive connection drawn here, not claimed as the
  repo's documented intent.

---

## 0. Mission and capability model

- **Mission** — The durable outcome Escapement exists to produce. Mission command and
  leverage determine *why*: human-chosen intent directs available capacity within
  delegated authority while consequential choices remain human.

- **Closed-loop control** — The operating model. Escapement records an intended outcome,
  acts within authority, compares the observed result with an independent oracle,
  repairs divergence, lands the verified result, and feeds evidence into the next cycle.

- **Durable capability chain** — The stable responsibilities beneath the mission, in
  execution order:

  1. **Intent and authority**
  2. **Design and specification**
  3. **Executable dependency-aware work breakdown**
  4. **Capacity allocation**
  5. **Isolated execution**
  6. **Action-local continuation and repair**
  7. **Independent outcome verification**
  8. **Authorized landing and delivery**
  9. **Learning and feedback**

- **Adapter** — A current implementation of one or more durable capabilities. Replacing
  an adapter does not change the mission. Today OpenSpec carries design/specification,
  Beads carries work and dependency state, Git worktrees provide isolation, Claude Code
  and Codex supply host-specific agent execution, and GitHub plus repository policy
  provide the current landing path.

- **Delegated outcome authority** — Delegating a bounded outcome includes its ordinary,
  proportionate means within the named repository, systems, and constraints: isolated
  worktree creation, scoped inspection and editing, tests/lint/builds, commit, task-branch
  push, pull-request creation or update, causal CI/review repair, and the repository's
  declared landing and verification path. These means are not new product decisions.

- **Consequential choice** — A decision that changes intent or non-goals, selects among
  materially different valid outcomes, crosses into an undelegated repository/account/
  audience, expands privilege or credentials, creates destructive or irreversible shared
  effects, invokes an actually enforced confirmation class, overlaps unsafely with another
  owner, or lacks a standard landing path. Human attention is reserved for these choices.

- **Action-local continuation** — An unresolved consequential choice blocks only the
  action and dependents that need it. Independent authorized work continues. A session is
  `input_required` only when no authorized route to the delegated outcome remains runnable.

---

## 1. Multi-agent organization

The core modern idea: instead of one agent doing everything serially, dispatch several
specialized agents that work in parallel and coordinate. The base principle is the oldest
one in management — **division of labor** (Smith, Babbage) — combined with
**message-passing concurrency** (the actor model) for how the agents talk.

- **Agent / subagent** — An independent agent execution dispatched to do a scoped piece
  of work, with its own context. A *subagent* is dispatched by another agent (the parent).
  The host adapter determines how results and intermediate messages are surfaced.
  *Roots:* the actor model (Hewitt, 1973) — independent computational entities that
  process work and communicate only by messages.

- **Addressable agent** — An agent execution with a stable host-provided identity so it
  can receive scoped follow-up work and coordinate with peers. The exact name/id field and
  messaging primitive belong to the host adapter.
  *In repo:* current client rules and fixtures define the supported identity fields.

- **Agent group** — The addressable agents cooperating on one bounded outcome. The group
  is a logical coordination scope, not a dependency on any client's team-construction
  API. Shared filesystem state and explicit messages form its working blackboard.
  *In repo:* `claude/rules/agent-teams-default.md` describes the current adapters.
  *Roots:* a team is a shared communication channel — a *blackboard* / shared workspace
  in classic AI architecture [analytical mapping].

- **Agent message** — A host-provided primitive by which agents exchange findings, argue,
  or hand off work. Coordination should be explicit rather than reconstructed only in the
  parent agent's private context.
  *Roots:* message-passing concurrency (actor model); cf. Erlang/OTP, which the harness
  design doc cites by name.

- **Roundtable** — A group of addressable agents that *argue* with each other via messages,
  each holding a persona/position. Critically: a roundtable is **never** simulated dialogue
  written in the main agent's output — it is always real dispatched agents.
  *In repo:* `claude/skills/dispatching-parallel-agents/SKILL.md:208` and
  `agent-teams-default.md:54`.
  *Roots:* adversarial collaboration / dialectic — independent positions tested against
  each other produce better conclusions than a single voice.

- **Panel of experts** — Like a roundtable, but the agents hold *different expertise*
  rather than opposing positions; they share findings to assemble a fuller picture.
  *Roots:* ensemble/diversity — independent specialists catch failure modes a single
  generalist misses [analytical mapping].

- **`adversarial-reviewer`** — A specialized agent type that runs with *no conversation
  history* and sees only the artifact under review, motivated to find failures. The
  isolation is the point: it cannot be biased by the author's reasoning.
  *In repo:* `claude/agents/adversarial-reviewer.md`.
  *Roots:* independent verification — the reviewer must not be the author.

### Agent pairing patterns

These are roles agents take *relative to each other*, from `agent-teams-default.md`:

- **Independent test agent** — Writes tests from the spec/success criteria, **without
  reading the implementation**. Catches the gap between what the spec says and what the
  code does, because the tester never saw the code.

- **Mutation challenger** — Before implementation, invents 2-5 plausible *bad*
  implementations and asks whether the proposed tests would catch each. Blocks
  implementation until the named fragile shortcut fails at least one check.
  *Roots:* mutation testing — a test suite is only as good as the mutants it kills.

- **Outcome verifier** — After implementation and review, verifies the *actual
  user-facing result* (runs the report, calls the endpoint, exercises the flow), not just
  "tests pass."
  *Roots:* the distinction between verification (built it right) and validation (built the
  right thing).

---

## 2. Workflow orchestration

When the *control flow* between agents should be deterministic (loops, fan-out,
conditionals) rather than decided turn-by-turn by a model, it is encoded as a **workflow**
— a script that spawns agents programmatically.

- **Workflow** — A deterministic orchestration that decides what runs in parallel, what
  verifies what, and what synthesizes. A host may implement it as a script, a native
  workflow primitive, or another scheduler.

- **Fan-out** — Spawning many agents at once over a work-list (one per file, dimension,
  channel). Each is blind to the others.
  *Roots:* the *map* phase of MapReduce; SPMD parallelism.

- **Barrier** — A synchronization point that waits for *all* parallel agents before
  continuing. Correct only when the next stage genuinely needs every prior result at once
  (dedup across the full set, early-exit on zero, cross-item comparison).
  *Roots:* the barrier primitive in parallel computing (e.g., `MPI_Barrier`).

- **Pipeline** — Running each item through all stages independently, with *no barrier*
  between stages: item A can be in stage 3 while item B is still in stage 1. The default
  multi-stage shape, because wall-clock equals the slowest single chain, not the sum of
  slowest-per-stage.
  *Roots:* pipeline parallelism / instruction pipelining — overlap stages instead of
  draining each.

- **Adversarial verify (in a workflow)** — After a fan-out of finders, spawn independent
  skeptics per finding, each prompted to *refute* it; keep only findings that survive.
  Converts plausible-but-wrong output into evidence.
  *Roots:* falsificationism (Popper) — a claim earns belief by surviving attempts to
  refute it.

- **Loop-until-dry** — For unknown-size discovery, keep spawning finders until *K
  consecutive rounds* return nothing new, rather than a fixed count. Catches the tail a
  simple `while count < N` misses.

---

## 3. Beads — task tracking

**Beads** (`bd`) is the issue tracker. Its non-obvious architecture: issues live in a
local **Dolt** database (a versioned SQL database); sync travels over `refs/dolt/data` on
the git remote; `.beads/issues.jsonl` is a *passive export*, not the source of truth.

- **Bead / issue** — A single tracked unit of work (task, bug, feature, epic, spec, spike)
  with id, status, priority (P0–P4), dependencies, and acceptance criteria.

- **Dolt** — The versioned SQL database backing beads. "Beads breaks" usually means a Dolt
  state problem (see `claude/rules/beads-worktree-integration.md`).
  *Roots:* Dolt = "Git for data" — version control semantics applied to a SQL database.

- **Ready / blocked** — A bead is *ready* when nothing it depends on is open (`bd ready`);
  *blocked* when a dependency remains. The dependency graph is a **DAG**.
  *Roots:* topological scheduling — execute only nodes whose predecessors are complete.

- **Spec issue / `--spec-id`** — A reusable issue holding behavioral contracts
  (WHEN/THEN). Multiple task beads reference the same spec via `--spec-id`, giving
  longitudinal coverage tracking via named requirement IDs.
  *In repo:* `claude/hooks/spec_id_enforcement.py` validates the reference resolves to a
  real spec + anchor.

- **Beads memory** — Persistent knowledge stored by Beads. Escapement does not
  inject it automatically or treat it as workflow policy; durable project
  guidance belongs in Escapement-owned repository surfaces.

---

## 4. Molecules & formulas

A **molecule** is a composed, multi-step workflow instantiated from a reusable template
(a **formula**). The chemistry metaphor: a formula is the recipe, pouring it creates the
molecule (a connected graph of bead tasks + gates).

- **Formula** — A reusable template defining the work nodes, decisions, and dependencies
  of a workflow. Current examples include `mol-rapid` for bounded work and `mol-feature`
  for a feature pipeline.
  *In repo:* `beads/formulas/*.formula.json`. *Roots:* templating / scaffolding —
  capturing a proven process so it isn't re-derived each time.

- **Pour** — Instantiating a formula into live beads (`bd mol pour mol-feature ...`):
  creates the root epic, all step tasks, and gate tasks with their dependencies.

- **Phase** — The user-facing stage of a molecule. `mol-feature`'s phases:
  **THINK** (challenge whether to build at all) → **DESIGN** (discovery + work-breakdown)
  → **VALIDATE** (walking skeleton + review gate) → **BUILD** (full execution + spec
  audit) → **LEARN** (retrospective + outcome check).
  *In repo:* phase strings in `beads/formulas/mol-feature.formula.json`; user-facing
  mapping in `claude/rules/molecule-awareness.md`.

- **Gate (molecule gate)** — A decision node in a molecule. It requires human attention
  only when an unresolved consequential choice remains; ordinary progression already
  included in the delegated outcome must continue. A blocked decision node does not stop
  independent authorized branches. Distinct from a *hook gate* (§6).
  *Roots:* stage-gate process (Cooper) — go/no-go checkpoints between phases.

- **Walking skeleton** — The first deliverable of any feature: the minimum end-to-end
  system that tests the *riskiest assumption* (1–3 tasks, 30–60 min each). Built before
  the bulk of the feature.
  *In repo:* `claude/rules/planning-discipline.md` ("The Walking Skeleton Rule").
  *Roots:* the term is Alistair Cockburn's — a tiny end-to-end implementation that
  exercises the architecture before fleshing it out.

- **Scope circuit breaker** — Hard ceilings on how many spec/task issues a feature may
  spawn, applied during work-breakdown to prevent spec inflation.
  *In repo:* `beads/formulas/mol-feature.formula.json` (DESIGN phase).
  *Roots:* WIP limits / batch-size control (Lean, Kanban).

---

## 5. The continuation harness

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

A control system that favors verified outcomes over activity summaries. Its mechanical
enforcement is adapter-specific: clients with a proven final-response lifecycle hook can
gate premature stopping, while clients without that primitive rely on durable work state
and explicit continuation guidance. The capability is shared; the enforcement level is
not assumed to be symmetrical.

- **Contract (`contract.json`)** — A declaration, made before implementation, of what
  "done" means (`--goal`) and the shell command whose exit 0 proves it
  (`--verify`). *Declare before acting; verify before stopping.*
  *In repo:* `harness/bin/init_contract.py`.

- **Oracle (verification command)** — The `--verify` command whose exit code *mechanically*
  demonstrates the outcome. The oracle is the heart of the contract: a weak oracle
  (`--verify true`) is a fake contract.
  *Roots:* the **test oracle** in software testing — the mechanism that decides whether a
  result is correct. The "oracle problem" (how do you know the answer?) is the field's
  classic hard problem.

- **`verify`** — The script that runs the contract's oracle, records the result, and exits
  with the same code. Exit 0 within the turn window permits Stop.
  *In repo:* `harness/bin/verify`.

- **Final-response gate** — An adapter-specific hook that can reject a final response
  when the client exposes and fixtures prove the relevant lifecycle event. The current
  Claude Code adapter can use the continuation harness at this point of effect. Codex
  exposes no Stop/final-response hook, so its equivalent discipline is guidance-only and
  cannot mechanically prevent the turn from ending.
  *In repo:* `harness/bin/stop_hook.py`, `would_block_stop.py`, and the host capability
  manifest.

- **Scheduled wakeup** — Registering a future check-in when work genuinely waits on an
  external event (CI, a merge, a DAG run) and the current adapter exposes a supported
  wakeup primitive. It is the structured alternative to writing "I'll check back" as
  prose and stopping.
  *In repo:* `scheduled.json` in the session thread dir.

- **Outcome-bias (vs action-bias)** — The governing principle: more tool calls, more
  subagent dispatches, more bead-claims do **not** substitute for proof of completion or
  proof of resumption. Action is not outcome.
  *In repo:* `claude/rules/continuation-harness.md`, `outcome-ownership.md`.

- **Level-triggered vs edge-triggered** — A level-triggered gate re-evaluates state on
  every supported final-response attempt (the queue is empty or it is not); an
  edge-triggered gate fires once on a transition and is bypassable by missing the edge.
  Where installed, the current final-response gate is level-triggered, stateless, and
  idempotent.
  *Repo cites:* `openspec/changes/continuation-harness/design.md:152` — borrowed from the
  Kubernetes reconciler architecture.

- **Liveness vs safety** — *Safety:* the gate never allows a bad stop (no false positives).
  *Liveness:* a good stop is eventually always allowed (no false negatives). The harness
  prioritizes safety over liveness deliberately.
  *Repo cites:* `design.md:156` — Lamport's safety/liveness distinction.

- **Supervisor strategies** — The three resumption paths map to Erlang/OTP restart
  strategies: verification-passed → *temporary*; user-released → *transient*; neither →
  *permanent* (must restart on exit).
  *Repo cites:* `design.md:154`.

- **Task-mode / queue-drain** — A session-scope stopping criterion: a session does not end
  until all beads tasks in its queue are claimed or drained (checked with `bd ready` AND
  `bd list` to distinguish *done* from *blocked*). Picking up unrelated ready tasks to
  satisfy the gate is scope creep, not progress.
  *Repo cites:* `design.md:160` flags this as novel prior-art-wise.

---

## 6. Bureaucracy & gate design

The repo frames *itself* as a bureaucracy — a structured set of routines that turn
problem-solving successes into reusable practice — and insists those routines stay
**lean, learning, and enabling** rather than decaying.

- **Bureaucracy (operative sense)** — Not pejorative: the codified routines (hooks, rules,
  skills, harnesses) that prevent re-solving solved problems.
  *Repo cites:* Schwartz (2020); `claude/rules/delicate-art-of-bureaucracy.md`.

- **Enabling vs coercive formalization** — The central distinction. *Enabling* procedures
  give the practitioner discretion, expose their rationale, and treat deviations as
  learning. *Coercive* procedures exist to force compliance and treat deviation as suspect.
  *Repo cites:* Adler & Borys (1996) — *the* operative source.

- **The four design features** — The tests every gate must pass: **repair** (can the user
  fix a misfire themselves?), **internal transparency** (does it explain *why*?), **global
  transparency** (can a newcomer see how it fits the whole?), **flexibility** (is there a
  reasoned-exception path?).
  *Repo cites:* Adler & Borys (1996); `delicate-art-of-bureaucracy.md`.

- **The four failure modes** — How bureaucracies decay: **bloated** (more rules than the
  risk warrants), **petrified** (rules that outlived their problem), **coercive** (gates
  that say "no" without enabling the next step), **mock** (rules followed for symbolic
  value but gamed in practice).

- **Mock bureaucracy** — The specific failure where an agent satisfies a gate's letter
  (a fake `--spec-id`, throwaway waiver text) without doing the underlying work. Both
  enabling *and* coercive designs can produce it if implementation conditions are wrong.
  *Repo cites:* Wiesche, Schermann & Krcmar (2013).

- **Hook gate** — A `PreToolUse`/`Stop`/etc. hook that intercepts an action and denies,
  asks, or warns. Distinct from a *molecule gate* (§4, a human decision point).
  *In repo:* `claude/hooks/*.py`.

- **Escape path** — The first-class, agent-invokable way *past* a gate that does not
  require disabling it (e.g., "say 'proceed' to skip TDD"). Gate-design Rule 1: every gate
  must have one.
  *In repo:* `claude/rules/gate-design.md`.

- **Waiver** — A reasoned exception asserted via a standard flag
  (`--<gate-name>-waiver "<reason>"`). The reason is required free-text (≥20 chars, no
  placeholders), persisted as labeled training data, and never expires from the log.
  *Roots:* deviations as learning opportunities (the *flexibility* design feature).

- **Signal** — The durable record a gate produces (a bead, a log line, an audit record, a
  waiver entry). Gate-design Rule 2: a gate whose decisions live only in the conversation
  produces no learning data and "you cannot prune what you cannot count."
  *In repo:* `claude/hooks/_gate_signal.py`.

- **Value-not-presence validation** — Gate-design Rule 3: a gate that requires a value must
  check the value *resolves* (a real file/anchor) or *meets a substance threshold*, not
  merely that *something* was supplied — otherwise it manufactures mock bureaucracy by
  rewarding the shortest passing string.

- **Half-life** — Every rule gets an annual review minimum; a rule unrevised in a year is a
  candidate for re-justification, not veneration.
  *Roots:* the operating rule against *petrification*.

---

## 7. TDD, oracles & test quality

The discipline that a test must prove *user/business behavior*, not echo the
implementation. Sequence: **Outcome → Oracle → Constraints → Tests → Code.**

- **Test Oracle Brief** — The pre-implementation document naming the business invariant,
  the independent source of truth, the invalid solution classes, a fragile implementation
  to reject, and positive/negative controls. Required before non-trivial implementation.
  *In repo:* `claude/rules/tdd-enforcement.md`; `claude/hooks/test_oracle_brief_gate.py`.

- **Oracle (test)** — The independent source of truth that decides correctness *without
  reference to the implementation*. See §5 for the harness's contract-oracle.

- **Implementation echo** — A test that passes by *repeating* the production code's
  constant, algorithm, private helper, mock interaction, or generated ID. Forbidden,
  because it would also pass the fragile shortcut it claims to guard against.
  *In repo:* `claude/hooks/implementation_echo_test_gate.py`.

- **Oracle downgrade** — Weakening what a test *proves* to make it pass: swapping a
  business-outcome assertion for an implementation detail, testing an intermediate artifact
  instead of the user-facing output, removing a negative control. Forbidden.
  *In repo:* `claude/rules/never-suppress.md`; `claude/hooks/oracle_downgrade_warning_gate.py`.

- **Positive control** — A fixture/input that proves valid output is *not accidentally
  dropped* (the fix didn't make the result empty).
  *Roots:* experimental design — a positive control confirms the assay can detect a true
  signal.

- **Negative control** — A fixture/input that *should fail* if the code is wrong. A test
  with no negative control cannot demonstrate it protects anything.
  *Roots:* experimental design — a negative control confirms the assay isn't always-positive.

- **Behavioral config (not exempt)** — Config that *drives runtime behavior* (CI YAML,
  Terraform, k8s manifests, DAGs). "It parses" is a gate, not an oracle; the verification
  owed scales with behavioral risk (parse → lint → predict → observe).
  *In repo:* `tdd-enforcement.md` § "Behavioral config is not exempt".

- **Never-suppress** — When something fails, fix *why* it fails; never make the failure
  invisible (no skip lists, `# noqa`, `except: pass`, error→warning downgrades, `--no-verify`).
  *In repo:* `claude/rules/never-suppress.md`.

---

## 8. Planning, discovery & specs

- **OpenSpec change** — A structured unit of proposed work living in
  `openspec/changes/{name}/`, holding the `design.md` (design intent — the authority on
  *why*), `specs/` (behavioral contracts), and tasks.
  *Authority:* `design.md` wins on design intent; beads wins on task state.

- **Discovery** — The pre-design-doc phase that produces genuine thinking via adversarial
  questions (riskiest assumption, non-goals), not checkbox compliance. Routed to by
  brainstorming based on complexity.
  *In repo:* `claude/skills/discovery/`, `claude/rules/planning-discipline.md`.

- **Brainstorming** — The entry skill that challenges whether work should be done *at all*
  and rotates creative lenses to fight semantic clustering, then routes to discovery or
  planning.

- **Work-breakdown** — Translating a validated design into a beads task graph with
  outcome-based acceptance criteria, failure modes, scope boundaries, and `--spec-id`
  traceability.
  *In repo:* `claude/skills/work-breakdown/`.

- **Proof of delivery** — The single sentence describing the actual user-facing outcome
  that must be true for the work to count as delivered; verified by running the real
  workflow, not by "tests pass."
  *Roots:* the *outcome-ownership* principle (§5).

---

## 9. Navigation, memory & epistemics

- **Serena** — An LSP-backed semantic code tool (MCP). Preferred over grep+read for code
  *navigation* and *editing*: symbol overview, find-symbol, find-references,
  replace-symbol-body. Grep/Read are fallbacks for string literals and short files.
  *In repo:* `claude/rules/serena-first.md`.

- **context-mode** — A plugin that does heavy processing in a sandbox and surfaces only the
  derived answer, keeping raw bytes out of the conversation. Note the standing tension: it
  *saves* context while always-on rules *consume* it.
  *Roots:* the "Think-in-Code" idea — program the analysis instead of reading raw data into
  the window.

- **The memory systems (and their guidance conflict)** — Several stores coexist:
  `bd remember` (beads, project task knowledge), `~/.claude/.../memory/` (user-scoped,
  cross-project, file-per-fact), Serena memory (project-scoped, cross-session), and the
  harness's own memory instructions. Guidance about MEMORY.md files differs between them;
  when in doubt, the project's `CLAUDE.md` (prefer `bd remember`, no MEMORY.md) governs
  beads-tracked repos. *This overlap is a known integration seam, documented here so it
  isn't rediscovered each session.*

- **Load-bearing claim** — A claim where, if it were wrong, a decision/design/recommendation
  downstream would change. Such claims must be verified, marked (`[inferred]`, "likely"),
  asked, or placeholdered — never asserted with the flat confidence of a measured fact.
  *In repo:* `claude/rules/evidence-provenance.md`.

- **Provenance marker** — The language that calibrates a reader's confidence to the
  author's (`[inferred]`, "appears to", "I'm assuming"). What lets a reader trust
  *selectively* instead of re-auditing or blindly trusting everything.

---

## Appendix: the base-principle map at a glance

| Modern term | Base principle | Provenance |
|---|---|---|
| Named agents / teams / SendMessage | Actor model, message-passing concurrency | Roots (Hewitt); repo cites Erlang/OTP |
| Roundtable / panel | Dialectic; ensemble diversity | analytical mapping |
| Fan-out / barrier / pipeline | MapReduce; parallel-computing primitives | Roots |
| Adversarial verify | Falsificationism (Popper) | analytical mapping |
| Contract + verify oracle | The test-oracle problem | Roots |
| Final-response gate (where supported) | Kubernetes reconciler; control theory | Repo cites (design.md) |
| Resumption paths | Erlang/OTP supervisor strategies | Repo cites (design.md) |
| Safety/liveness trade | Lamport | Repo cites (design.md) |
| Enabling vs coercive bureaucracy | Adler & Borys (1996) | Repo cites |
| Mock bureaucracy | Wiesche et al. (2013) | Repo cites |
| Walking skeleton | Cockburn | Roots |
| Scope circuit breaker / WIP limits | Lean / Kanban | Roots |
| Positive / negative control | Experimental design | Roots |
| Molecule / formula / pour | Templating + DAG scheduling | analytical mapping |
| Dolt (beads backend) | Version control for data | Roots |
