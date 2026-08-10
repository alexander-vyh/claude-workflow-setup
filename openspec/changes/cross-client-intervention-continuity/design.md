# Cross-Client Intervention Continuity

## Problem Statement

`[ACCEPTED SKELETON]`

After this change, an approval card, edit confirmation, clarification, credential
request, or informational question is a durable Escapement intervention attached to
the action it actually blocks. It is no longer synonymous with “stop the session.”
Already-authorized routine work proceeds without repeated command-shaped approval
negotiations; unrelated parent and sibling work stays runnable while one action waits;
and the user can query every human intervention across supported clients, repositories,
sessions, and agents for a requested period.

The current failure is observable across recent Codex sessions and repositories:
verification commands, isolated-worktree Git operations, pull-request creation, and
agent-authored edits repeatedly park on native client prompts even though the delegated
task and declared repository outcome already authorize the work. Informational side
questions produce the same global waiting behavior. Escapement currently has durable
outcome contracts, per-actor thread state, wakeups, gate telemetry, and host adapters,
but it has no host-neutral intervention lifecycle or attention view. The cost of doing
nothing is continued loss of unattended execution, recurring human polling across tabs,
and prompt habituation that makes genuinely consequential decisions harder to notice.

## Non-Goals

1. **Escapement will not become a general authorization engine.** There is no policy
   DSL, remote decision service, OPA, Cedar, OpenFGA, generic ABAC library, or user-facing
   rule builder. This locks in a deliberately small vocabulary owned by Escapement code.
2. **Escapement will not become a hostile-code sandbox or execution broker.** Kernel
   containment, microVMs, credential proxies, syscall mediation, and broker-owned Git
   are separate security products. Native client and operating-system denials continue
   to win.
3. **The design will not copy GitLab Enterprise Edition implementation code.** GitLab’s
   public API and documentation are architectural prior art only; Escapement remains a
   clean-room implementation with its own vocabulary, state, tests, and source history.
4. **The design will not promise parity a host cannot prove.** Codex, Claude Code, Pi,
   and later adapters remain explicitly partial or unsupported for lifecycle events they
   cannot fixture and exercise on the installed client version.
5. **The design will not keep all work running regardless of dependency.** A question
   can legitimately block the action whose parameters it decides and actions that
   depend on that result. The requirement is to preserve independent work, not to ignore
   causal or shared-state dependencies.
6. **Beads will not store intervention authority or resolution.** Beads remains task
   state. A Beads issue may reference a discovered failure, but an audit entry, label,
   status, or comment can never authorize an action.
7. **The first delivery will not build an inbox UI or notification service.** The
   walking skeleton combines an observer/query surface with one controlled
   action-local continuation path. Native cards, terminal output, and a deterministic
   CLI query are sufficient before designing another interface.
8. **The change will not rewrite every existing hook at once.** Existing point-of-action
   gates remain compatibility adapters until the observer proves the normalized model.
   Migration is incremental and reversible.

### What This Is NOT

This is not GitLab Duo Agent Platform embedded in Escapement, a replacement for Claude
Code/Codex/Pi permission systems, a blanket auto-approve mode, a second task tracker, a
workflow engine, or an attempt to make shell-command strings into secure capabilities.
It is a host-neutral intervention lifecycle and continuity layer built into the existing
Escapement harness.

## Capabilities

### New Capabilities

- `intervention-event-model` — normalize client-visible approval and input events into
  stable action, intervention, wait-scope, status, provenance, and timing fields.
- `cross-session-intervention-query` — answer which interventions occurred, surfaced,
  waited, resolved, duplicated, or remained pending across hosts and repositories for a
  requested time window.
- `action-local-wait-continuity` — bind a pending intervention to the affected actor and
  action so independent work remains runnable; aggregate to `input_required` only when
  no independent work exists.
- `semantic-preauthorization` — derive narrow routine authority from explicit task intent,
  repository outcome, green-state prerequisites, and confirmation carve-outs instead of
  exact command strings.
- `host-intervention-adapters` — translate Claude Code, Codex, Pi, and later host payloads
  into the common model and render only behavior each installed host can prove.
- `intervention-attention-view` — project pending interventions and their provenance into
  a single queryable attention surface without becoming the authoritative state itself.

### Modified Capabilities

- `outcome-contract` and `repo-outcome-authorization` additionally expose the routine
  actions implied by a delegated task and declared outcome; they remain the policy owner.
- `durable-wakeup-registry` gains request-bound decision wakeups only after the observer
  proves the common event model.
- `identity-layer` expands host-neutral identity without losing the existing parent and
  subagent isolation contract.
- `stop-barrier-supervisor` must distinguish `running_with_attention` from a truly
  quiescent `input_required` state.
- `agent-surface-parity` records installed-version capability probes and prohibits
  documentation-only readiness claims.
- gate-signal persistence becomes an analytics projection of normalized interventions;
  it never becomes an authorization source.

## Stakeholders

- **Decision authority and approver:** the user, who owns this personal workflow and the
  task/outcome declarations that grant or withhold authority.
- **Workflow-policy owner:** Escapement core plus committed `.escapement/repo.json`.
- **Behavioral population:** Claude Code, Codex, Pi, future coding-agent sessions, parent
  orchestrators, and subagents operating through Escapement.
- **Adapter inputs:** public client contracts and payload-specific behavioral fixtures;
  client documentation alone cannot approve readiness.
- **Task-state provider:** Beads, read where task dependency is relevant but never used
  as an intervention decision store.
- **Prior-art source:** GitLab’s documented Agent Platform privilege, checkpoint, session,
  and attention behavior; its EE source is excluded from implementation input.

## Impact

- **Harness runtime state:** introduce host-neutral intervention journals and request
  records alongside existing per-thread contract, schedule, checkout, and actor state.
- **Repository declarations:** extend outcome resolution with derived routine actions and
  confirmation carve-outs without introducing a general rule language.
- **Host surfaces:** add observer and, later, decision adapters through
  `agent-surfaces/manifest.json`; generated plugin files remain projections.
- **Continuation:** change aggregate session state so a pending intervention does not
  imply global suspension while another actor/action is runnable.
- **Telemetry:** unify native approval requests, edit confirmations, clarifications,
  credential waits, and other action-required states while preserving redacted raw-event
  references for reconciliation.
- **Installation boundary:** canonical workflow state becomes Escapement-owned. Adapters
  may read host-native logs and install plugin-owned hooks, but this change does not claim
  ownership of unrelated personal `~/.claude`, `~/.codex`, or Pi configuration residue.
- **No runtime dependency additions:** the core remains standard-library Python and
  filesystem state for the walking skeleton.

## Architecture Context

```text
 Explicit task intent          .escapement/repo.json
          │                              │
          └──────────────┬───────────────┘
                         ▼
              Existing outcome authority
                         │
                         ▼
 Claude ─┐       Host-neutral action/intervention       ┌─ per-session journal
 Codex  ─┼─adapter────── normalization core ────────────┼─ pending requests
 Pi     ─┘              │            │                  └─ time-window query
                        │            │
                        │            └─ decision/wait provenance
                        ▼
             action-local continuation state
                  │                  │
         affected action waits      independent actions run
                  │                  │
                  └──────────┬───────┘
                             ▼
                  host-specific rendering/resume
```

The core keeps three state axes separate:

1. **Session state:** `running`, `running_with_attention`, `input_required`,
   `finished`, `failed`, or `stopped`.
2. **Action state:** `ready`, `running`, `waiting_human`, `denied`, `succeeded`, or
   `failed`.
3. **Intervention state:** `requested`, `surfaced`, `resolved_allowed`,
   `resolved_denied`, `cancelled`, `expired`, or `superseded`.

An intervention has a stable request id, host and installed-version provenance,
session/actor/repository/worktree identity, native event kind, semantic action,
intervention kind, wait scope, timestamps, resolution source, and a redacted reference
to the native event. Natural-language explanations are evidence for display, not
authority. The global attention view is derived by querying unresolved request records;
it is not a second mutable source of truth.

## Strategic Alternatives

1. **Do nothing and rely on native client approvals.** Rejected because the documented
   incidents already span clients and repositories, native memory is inconsistent or
   command-shaped, and no cross-session inventory exists.
2. **Adopt GitLab Duo Agent Platform as the runtime.** Rejected because the relevant
   implementation is EE-licensed, GitLab-centric, and tied to GitLab sessions, runners,
   and services. It would replace host neutrality rather than provide it.
3. **Enable blanket auto-approval or bypass modes.** Rejected because it removes the
   visible friction by discarding the distinction between routine delegated work and
   genuinely consequential unresolved actions.
4. **Build a local policy daemon, authorization framework, or signed-intent broker.**
   Rejected because Escapement already owns workflow policy, the present actors share a
   local-user trust domain, and the problem is lifecycle continuity rather than a new
   security perimeter.
5. **Build separate per-client fixes.** Rejected because it duplicates authority and
   analytics, prevents a global 48-hour query, and guarantees semantic drift as client
   hook surfaces change.

## Riskiest Assumption

`[ACCEPTED SKELETON]`

We believe a small host-neutral event and action vocabulary can faithfully normalize
the real intervention population and identify whether an intervention blocks one action
or the whole session, without parsing exact command strings into authority. We will know
this is true when an observer-only implementation classifies the captured recent corpus
and controlled Claude/Codex/Pi fixtures with one-to-one reconciliation against visible
native prompts, stable semantic grouping across dynamic arguments, and correct sibling
liveness during a pending informational intervention. If false, we will retain a common
analytics envelope but abandon shared behavioral decisions: each host will need a
separately specified intervention classifier, and action-local continuation will remain
host-specific until a common invariant can be proved.

This passes the liveness test: discovering after two implementation phases that equivalent
events cannot share a trustworthy classification would invalidate semantic
preauthorization, the attention view, adapter parity, and the continuation state machine.
The observer skeleton therefore tests normalization before any prompt is suppressed.

The embedded alternative is an observability-only federation: each client writes its own
native event schema, and a report joins them without attempting shared decisions or
continuation behavior. It is cheaper and safer but cannot eliminate repeated prompts or
preserve cross-client workflow semantics.

## Walking Skeleton

`[ACCEPTED SKELETON]`

1. **Build the independent intervention corpus and expected labels.** Collect redacted
   native payload fixtures for the supplied Codex examples plus controlled Claude Code
   and Pi approval, clarification, informational-question, and edit events. Label semantic
   action, intervention kind, action/session wait scope, and expected authority source
   independently of the normalizer.
2. **Add an observer-only normalizer and time-window query.** Write per-session events
   without influencing permission or continuation decisions. The query must return every
   fixture and controlled live event once, group dynamic variants semantically, and expose
   requested/surfaced/resolved timing where the host provides it.
3. **Run the controlled action-local continuation slice.** In a fixture-proven supported
   host, first surface an informational intervention and independently prove it remains
   visible and unresolved. Only then make a deterministic disjoint sibling ready and have
   Escapement dispatch it through the same supervisor. Verify the sibling's
   ready-to-running transition, first tool invocation, and reversible effect all occur
   inside the native pending interval. At sibling completion, independently re-check that
   the native card remains visible, no decision was submitted, and the affected action is
   still waiting. In a second control with no runnable sibling,
   verify the aggregate becomes `input_required`. A genuinely consequential unresolved
   action must remain unexecuted throughout.

The cutting test removes semantic preauthorization, prompt suppression, notification UI,
decision wakeups, and multi-host enforcement. The observer plus one controlled real
action-local dispatch is the smallest system that proves Escapement—not merely the native
host—can preserve independent work without risking a false allow.

## Test Oracle Brief

1. **Business invariant.** Every native human-attention event in the observed population
   is discoverable for the requested time window with honest provenance and wait scope;
   a pending informational intervention does not stop causally independent reversible
   work; and an unresolved consequential action does not execute.
2. **Independent source of truth.** Correctness is determined by a redacted corpus of
   native client payloads captured before the normalizer exists, the installed binary
   version and fixture hash recorded outside the adapter, visible native
   requested/resolved evidence, and independently observed supervisor/tool/filesystem
   timestamps from the controlled two-actor run. Escapement's normalized journal and
   adapter verdict are results under test, not the oracle.
3. **Solution constraints.** Normal-session Phase 1 adapters are observer-only; the
   controlled liveness harness may dispatch only an explicitly disjoint reversible
   sibling after native pending evidence. The core remains standard-library
   Python; Beads remains task state; `.escapement/repo.json` and explicit task intent
   remain workflow authority; manifest entries require installed-version fixtures;
   missing host capabilities stay explicit; secrets and raw credentials are not stored;
   and unrelated personal client configuration is not rewritten or claimed.
4. **Invalid solution classes.** A solution is invalid if it hides a card without
   restoring progress, counts screenshots instead of native events, derives authority
   from command prefixes or prose, lets an adapter self-assert its classification,
   treats any pending request as a global pause, invents parity from documentation, or
   uses a Beads/gate-signal record as authorization.
5. **Fragile implementation to reject.** Suppress or auto-answer the visible prompt while
   the underlying tool call, originating actor, or session remains blocked. Prompt count
   falls, but the user's work still stops.
6. **Negative control.** During the liveness run, an unresolved action deliberately
   classified as consequential and outside delegated authority MUST remain unexecuted.
   The same run MUST fail if the informational intervention is reported as globally
   `input_required` while the sibling is runnable. A second control with no independent
   action MUST become `input_required`, rejecting an implementation that always reports
   `running_with_attention`.
7. **Positive control.** After native evidence proves the informational intervention is
   pending and before it is resolved, the sibling MUST transition from not-ready to ready,
   be dispatched by the same Escapement supervisor, invoke its first tool, and complete a
   deterministic reversible effect. Those transitions and output are inspected
   independently of the intervention journal; the query MUST still return the pending
   intervention exactly once. At sibling completion, native evidence MUST still show the
   card visible, no submitted resolution, and the affected action waiting.
8. **Missing or unresolved handling.** Unknown payloads, missing identity, ambiguous
   dependency, or absent host lifecycle support are reported as incomplete or
   unsupported. They never become implicit allows. Only explicitly disjoint reversible
   work may continue while classification is unresolved.
9. **Final outcome verification.** Run the controlled host scenario with a barrier that
   makes the sibling ready only after the native prompt is visible. Inspect native
   requested/resolved evidence and the blocking control; independently verify supervisor
   dispatch, first tool activity, and sibling effect occurred during that interval;
   re-check native visibility, absence of a submitted decision, and the affected action's
   waiting state after the sibling effect; run the no-runnable-work control; then query the
   same interval through the public intervention query and reconcile native event ids and
   timestamps one-for-one.

The plan fails review if the fragile prompt-hiding implementation can satisfy the
reconciliation, sibling-liveness, and consequential-action controls.

## Proof of Delivery

This phase is done when a real pending informational intervention and a genuine blocking
control are both visible in the cross-session query; after the intervention is visibly
pending, Escapement dispatches an independent sibling through the same supervisor and it
completes reversible work before resolution; the no-runnable-work control becomes
`input_required`; and no prompt or consequential action has been silently suppressed.

## Anti-Metrics

1. **A prompt disappears while the underlying action or session still waits.** Hidden
   friction is worse than visible friction because the query would claim improvement
   without preserving work.
2. **A required confirmation silently executes.** One false allow of the blocking
   negative control invalidates behavioral enforcement, regardless of prompt reduction.
3. **The number of human attention events increases.** Deduplication and durable memory
   must not turn native prompts into duplicate Escapement notifications.
4. **Adapters become policy owners.** Equivalent actions receiving different authority
   because Claude, Codex, or Pi implemented separate rules is architectural failure.
5. **Unsupported capabilities are reported as ready.** Documentation parity without an
   installed-version behavioral fixture is a false success.
6. **Routine latency or maintenance exceeds the interruption cost removed.** The observer
   must be cheap, local, deterministic, and removable.

## Phased Delivery

### Phase 1 — Observer and Controlled Liveness Skeleton

`[ACCEPTED SKELETON]`

This is the walking skeleton above. Its riskiest assumption is that one common model can
faithfully represent native interventions and their wait scope. Its delivery proof is
one-to-one prompt reconciliation plus an Escapement-dispatched post-prompt sibling action
and the complementary no-runnable-work control, not unit-test status.

### Phase 2 — Non-Blocking Informational Interventions

`[PLACEHOLDER]`

This option is purchased only if Phase 1 proves the common model. It is done when an
informational question enters durable attention state without pausing the originating
actor, parent, or siblings, not when a new state enum exists.

### Phase 3 — Semantic Preauthorization

`[PLACEHOLDER]`

This option is done when controlled routine actions implied by explicit task and repo
outcome stop re-prompting across dynamic command variants while confirmation-class and
scope-expansion controls still ask, not when an allow-list file is populated.

### Phase 4 — Durable Attention and Resolution

`[PLACEHOLDER]`

This option is done when one pending-attention query spans supported hosts and a resolved
request resumes only its bound action or dependent edge, not when an inbox UI renders.

### Phase 5 — Additional Host Adapters

`[PLACEHOLDER]`

Each adapter is done when its installed-version fixtures prove observation, rendering,
resolution, and continuation behavior against the common contract. Unsupported lifecycle
events remain named rather than simulated.

## Decisions

1. **Use GitLab as behavioral prior art, not an implementation dependency.** Public
   privilege, session, checkpoint, notification, and trace contracts inform independent
   requirements; EE source is excluded.
2. **Split session, action, and intervention state.** GitLab’s typed states are useful,
   but a pending checkpoint must not collapse all three axes into a global pause.
3. **Derive preauthorization from existing authority.** Explicit user intent and the
   committed repo outcome are load-bearing. Command strings, prompt prose, Beads records,
   and adapter assertions are not authority.
4. **Observe before broad enforcement.** Normal-session Phase 1 adapters cannot suppress
   prompts or execute blocked actions. The off-by-default controlled harness may dispatch
   only its proven-disjoint reversible sibling after native pending evidence.
5. **Keep the core small and native.** Standard-library Python, strict data validation,
   atomic request files, and per-session journals match the repo. SQLite, a daemon,
   cryptographic delegation, and external policy systems require a later measured need.
6. **Keep adapters manifest-owned and capability-probed.** The canonical source is
   `agent-surfaces/manifest.json`; generated plugins are projections, and personal client
   settings remain outside Escapement ownership unless explicitly onboarded.
7. **Make the attention view derived.** Pending request records are authoritative; a
   query or UI is a read model that can be rebuilt.

## Risks & Trade-offs

- **Native clients do not expose every prompt or wait transition** → Record capability
  gaps and reconcile only behaviorally fixture-proven events; never infer completeness
  from documentation.
- **Semantic action categories become a disguised policy language** → Start with the
  small set of actions already governed by Escapement and reject unknown fields/actions;
  add a category only after a real unmatched event.
- **The observer repeats or misses cross-process writes** → Keep journals per actor,
  use atomic request-file replacement, assign stable event ids, and test replay/dedup.
- **A pending question races with changing task state** → Re-evaluate action and repo
  authority at resolution; stale requests become `superseded`, never implicit allows.
- **Independent work is misclassified and conflicts with the blocked action** → Phase 2
  must use explicit action/dependency binding and conservative unknown-dependency handling;
  the skeleton uses a deterministic disjoint sibling control.
- **Client updates break adapters** → Store installed-version provenance, run startup
  capability probes, and degrade to observation or explicit unsupported status.
- **Sensitive command, environment, or credential data leaks into telemetry** → Normalize
  action and target identity, redact secrets and raw bodies, and retain only a bounded
  native-event reference needed for reconciliation.
- **The design duplicates continuation-harness state** → Extend thread identity and
  wakeup ownership rather than creating a parallel supervisor; migration tests enforce
  one canonical owner.
- **Clean-room provenance becomes ambiguous** → Record public documentation and API
  references in the decision record; do not inspect or copy GitLab EE implementation
  source during execution.

## Migration Plan

1. Add observer state and query behind an off-by-default capability flag. It writes only
   new Escapement-owned runtime files and cannot affect existing gate decisions.
2. Enable observer mode for controlled sessions, then selected normal sessions. Compare
   normalized output with native prompts before increasing coverage.
3. Preserve existing `~/.claude/harness` behavior during the skeleton. Introduce a
   host-neutral canonical state root only with an explicit compatibility reader and
   rollback path; do not silently strand live schedules or contracts.
4. Promote one intervention class at a time from observe-only to behavior-changing after
   its negative and positive controls pass. Informational questions precede command
   preauthorization because they carry lower execution risk.
5. Keep each adapter independently disableable. Rollback removes its manifest event or
   feature flag and leaves the observer journals readable.
6. Migrate existing gates only after the core decision matches their current public
   contract. `_gate_signal` remains a mirror throughout and is never read to authorize.

## Open Questions

- **[DEFERRABLE] Native payload availability by installed version.** Owner: adapter
  implementer. Target: during Phase 1 fixture capture. The skeleton may report an
  unsupported host but cannot claim full-host reconciliation without its fixture.
- **[DEFERRABLE] Canonical runtime root migration.** Owner: harness maintainer. Target:
  before Phase 2. Phase 1 can use an isolated observer root without moving active
  contracts or schedules.
- **[DEFERRABLE] Exact dependency-binding source for non-Beads conversational work.**
  Owner: continuation design review. Target: before Phase 2 enforcement. The Phase 1
  sibling probe uses an explicit deterministic action graph.
- **[DEFERRABLE] Retention period and redaction policy for intervention journals.** Owner:
  user and harness maintainer. Target: before normal-session observer rollout. Controlled
  fixtures contain no secrets and can run with bounded temporary retention.
- **[DEFERRABLE] Which existing gate migrates first after informational questions.** Owner:
  implementation planning. Target: after Phase 1 evidence. The skeleton does not alter a
  gate.

No skeleton-blocking question remains: the corpus, expected labels, observer-only safety
boundary, controlled host, and independent positive/negative controls are all defined.
