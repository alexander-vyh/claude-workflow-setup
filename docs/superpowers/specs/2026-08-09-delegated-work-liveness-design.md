# Delegated Work Liveness Design

**Date:** 2026-08-09
**Status:** Accepted for implementation after review and research
**Tracked outcome:** `escapement-e3ai`

## Outcome

An Escapement-managed parent may delegate work and yield without depending on a
human prompt to restart it. In a Beads repository, every delegation that may
outlive the parent turn is durable child work in Beads. Each native execution
attempt is separately bounded by start, activity, and hard deadlines and is
reconciled after process/session loss. A local model may add semantic review on
a machine that has one, but no portable correctness or liveness guarantee
depends on that model.

The observed 2026-08-08 failure is the primary regression case: the parent
reported that two agents were running, its Stop hooks allowed `queue_drained`,
two children later stopped making observable progress inside tool calls, and no
parent turn occurred for more than 22 hours until the user typed `well?`.

The 2026-08-09 design-session failure is a second required regression case: two
research child beads closed successfully, but the parent bead remained
`in_progress`; the coordinator nevertheless returned a final answer saying the
design remained unchanged. The user had to explicitly demand continuation.

## Why the Current Design Failed

The current controls protect completion claims and recorded waits, but do not
own delegated-work liveness:

1. Task-mode Stop treats ready/blocked descendants as its completion boundary
   but does not require the claimed/root bead itself to be closed. An open or
   `in_progress` parent with all children closed is therefore misclassified as
   `queue_drained`. Beads also does not record whether a particular native child
   attempt is queued, running, stale, or terminal.
2. `ScheduleWakeup` is bridged into `scheduled.json`, but a future entry is
   accepted as resumption proof without establishing that an active scheduler
   will fire it.
3. `wakeup_waker.py` has a tested `--fire` path but defaults to dry-run, and its
   launchd installation/supervisor role remains unfinished.
4. Native completion notifications are delivery hints. Claude, Codex, and
   Gemini do not document a complete durable ledger plus activity lease plus
   independent wake-and-reconcile guarantee.
5. Transcript mtime is not a sufficient heartbeat. A tool-start record can sit
   unresolved for hours, and noisy output can appear without semantic progress.

## Approaches Considered

### A. Host-native status and notifications only

Consume Claude `background_tasks`, Codex child-thread state, and completion
events directly. This is the smallest code change, but it reproduces the failure
whenever the parent turn ends, a notification is lost, or the host cannot wake
an idle parent. Rejected.

### B. External durable workflow service

Use Temporal, LangGraph Agent Server, or Step Functions for durable history,
leases, retries, and recovery. These systems have the strongest off-the-shelf
semantics but add a service, workers, storage, authentication, and operational
ownership that are disproportionate to a local coding-session harness. Deferred
unless Escapement later needs cross-host distributed execution.

### C. Beads work graph plus Escapement attempt ledger and supervisor

Use Beads as the canonical durable work graph, persist a small per-parent native
execution-attempt ledger, let host adapters contribute authoritative events,
and make the existing waker the independent reconciliation authority. This
reuses the continuation harness, remains inspectable, and adds no service
dependency. **Selected.**

## Architecture

### 1. Beads work and durable execution-attempt ledger

In a Beads repository, a delegation that may outlive the parent turn requires a
child bead created before dispatch. Beads is authoritative for durable work
identity, outcome, acceptance criteria, hierarchy/dependencies, assignment,
blocked state, and verified closure. Permanent child beads represent durable
work; ephemeral wisps are reserved for disposable operational probes.

Each parent thread owns `executions.json` beside `contract.json` and
`scheduled.json`. The file contains native execution attempts, not a second task
graph. It is a versioned object, not an append-only transcript:

```json
{
  "version": 1,
  "parent_session_id": "session-id",
  "updated_at": "2026-08-09T20:00:00Z",
  "executions": [
    {
      "bead_id": "escapement-e3ai.1",
      "execution_id": "stable-random-id",
      "host": "claude",
      "agent_name": "mutation-challenger",
      "native_child_id": "optional-until-bound",
      "dispatch_tool_use_id": "host-tool-use-id",
      "attempt": 1,
      "generation": 1,
      "state": "queued",
      "queued_at": "2026-08-09T20:00:00Z",
      "started_at": null,
      "last_activity_at": null,
      "last_activity_kind": null,
      "start_deadline": "2026-08-09T20:02:00Z",
      "idle_deadline": "2026-08-09T20:15:00Z",
      "hard_deadline": "2026-08-09T22:00:00Z",
      "reconcile_due": null,
      "terminal_at": null,
      "terminal_reason": null,
      "terminal_event_id": null,
      "result_digest": null,
      "watchdog_id": "stable-wakeup-id",
      "recovery_count": 0,
      "recovery_claim": null,
      "result_application": {
        "state": "unapplied",
        "claim": null
      }
    }
  ]
}
```

Allowed states are `queued`, `running`, `terminal`, `cancelled`, and `unknown`.
Deadline breach is the sticky condition `reconcile_due=start|idle|hard`, not a
claim that native execution ended. A still-running or late-terminal child can
therefore coexist with an overdue reconciliation condition.

Terminal success means native execution ended and a result is available; it does
not mean the parent task or business outcome is verified.

Ledger writes use an exclusive file lock, a same-directory temporary file, and
atomic replacement. Ledger and directory ownership/permissions must pass the
existing trusted-source checks. A malformed or untrusted ledger is unresolved,
never empty or complete.

Runtime state never duplicates Beads task titles, outcomes, acceptance criteria,
dependencies, or task status. A worker terminal event never automatically closes
a bead. Escapement first verifies the business outcome and only then updates the
canonical Beads work state.

Task-mode completion checks both the claimed/root bead and its relevant
descendants. An open, `in_progress`, blocked, deferred, missing, or unreadable
root is not `queue_drained`, even when every child is closed. Parent readiness is
not parent completion, and Beads does not auto-close epics.

### 2. Three deadlines

The first release uses configurable defaults with bounded parsing:

- Start deadline: 2 minutes after dispatch registration. It catches work that
  was requested but never started or never bound to a native child.
- Activity deadline: 15 minutes after the last accepted activity. It catches a
  child stuck in a tool call or otherwise silent.
- Hard deadline: 2 hours after dispatch. It bounds a noisy or repeatedly active
  child and cannot be renewed by activity.

Accepted deterministic activity is a completed tool boundary, a non-empty child
assistant event, an explicit child checkpoint, or a terminal event. Tool start,
file mtime alone, repeated status polling, and local-model opinion do not renew
the activity deadline. A task that legitimately needs a longer blocking
operation must externalize it as scheduled/background work rather than hold an
agent tool call open indefinitely.

### 3. Supervisor and firing proof

`wakeup_waker.py --fire` becomes an installed macOS launchd service for the
first supported deployment. Its pure planner remains independently testable.
Each tick:

1. writes a non-authoritative `reconcile_started_at` diagnostic;
2. processes existing scheduled checks;
3. scans trusted execution ledgers and Beads work state;
4. reconciles deadlines and terminal evidence;
5. claims and emits bounded recovery dispatches;
6. only after the complete useful pass succeeds, atomically records
   `last_successful_reconcile_at`, installation identity, and a monotonically
   increasing completed generation.

A Stop path may accept a scheduled pause only when both the relevant future
wakeup/execution record exists and the supervisor health record is recent
(within two configured tick intervals). A managed wake is relevant only when
its `parent_session_id`, `watchdog_id`, `execution_id`, `attempt`, and
`generation` exactly match the current execution. Unrelated scheduled checks and
old attempts/generations cannot launder a pause. A file written by an inactive,
dry-run, or stale supervisor is not resumption proof.

The supervisor is level-triggered: restart or a missed tick re-evaluates current
state. Supervisor process/tick liveness is not health; only a successfully
completed useful reconciliation across every trusted thread and scheduled check
is health. Partial success for one thread never advances global health.

Recovery uses an expiring claim fenced by `(execution_id, attempt, generation)`.
The claim is persisted under the same lock before spawn. A later reconciler may
take over only after claim expiry and advances the generation before dispatch.
Exactly-once spawn is not assumed: recovery is at-least-once with generation
fencing and idempotency. A bounded crash budget prevents a spawn storm and ends
in a durable structured escalation.

### 4. Recovery behavior

Deadline expiry means **wake and reconcile**, not automatic replay.

The recovery prompt names the parent, child, attempt, last accepted activity,
deadline breached, native status, and relevant artifact paths. The recovering
coordinator must inspect the tool boundary and actual outcome before deciding to
cancel, retry, resume, or accept a late terminal result. External mutations may
only be retried when an idempotency key or an independent outcome check makes the
retry safe.

Late and duplicate completion events are idempotent. Old-generation completion
is retained as incident evidence but cannot mutate the current attempt. Applying
a current result requires an expiring result-application claim and an independent
outcome check through the public application orchestrator. Claiming leaves the
result unapplied/applying; terminal status or a digest cannot directly provide
verification. The current fenced claimant must query the business outcome and,
when application is still required, use a stable execution-scoped idempotency
key. If the external effect succeeds before ledger persistence and the process
dies, takeover queries the outcome or reuses that key before acting, preventing
duplicate mutation. Only then can it mark the result applied. Equal digests
across distinct executions do not share application identity.

### 5. Host adapters

The core accepts normalized events:

```text
dispatch_registered
child_bound
child_started
activity_completed
child_terminal
child_cancelled
snapshot_reconciled
```

Each event includes host, parent session, Beads work identity, stable execution
attempt/generation identity, event time, and native evidence. Adapter-specific
payload parsing stays outside the ledger state machine.

#### Claude Code

- Before an `Agent` call, the coordinator prepares an execution attempt with an
  explicit child `bead_id`, host, session, and agent name through the ledger CLI.
  This is structural state, not a Bead ID parsed from free-form prompt prose.
- PreToolUse `Agent` requires the matching prepared attempt and atomically marks
  dispatch intent before native execution. A missing, closed, or foreign Bead or
  missing prepared attempt denies dispatch with a concrete repair command.
- PostToolUse `Agent` binds the returned child identifier when the current
  payload fixture proves where it is exposed.
- Child PostToolUse/assistant lifecycle events renew activity only after a
  completed boundary.
- Stop reconciles authoritative `background_tasks` and `session_crons`, the
  claimed/root bead plus relevant descendants, and execution-ledger state. It
  cannot return `queue_drained` while the claimed/root bead remains unresolved.
  A scoped Beads queue cannot release Stop while a current execution is
  unresolved unless a healthy watchdog owns the pause.
- `SubagentStop`, `TaskCompleted`, and `TeammateIdle` are used only after real
  payload fixtures prove their current shapes.
- The eight-block host override remains an external limit, so the independent
  supervisor is mandatory rather than a second Stop-only defense.

#### Codex

- The host-neutral ledger and reconciliation command are shared.
- Upstream Stop/SubagentStop schema support is not treated as effective local
  support. Registration is enabled only when an installed-plugin fixture and a
  real `codex exec` smoke prove that the hook runs and its block is honored.
- Until that proof exists, PreToolUse dispatch registration where available,
  SessionStart reconciliation, parent-bead state, and the independent supervisor
  provide recovery. Codex prose discipline is not treated as a mechanical gate.
- Implementing and proving the Stop adapter subsumes the intended outcome of
  `escapement-2waa`; wakeup authoring/firing subsumes the relevant part of
  `escapement-u7aq`.

#### Other hosts

Unknown hosts may write/read the normalized ledger through a CLI. Without a
verified scheduler adapter they cannot claim a durable scheduled pause. Gemini
local subagents may remain synchronous under their native hard bound; detached
work must use the Escapement ledger.

### 6. Optional local-model maintenance

The deterministic state machine never requires a model. When a compatible local
endpoint is healthy, an advisory auditor may classify recent child evidence as
`progress`, `blocked`, `churn`, or `unclear`, summarize repeated failures, and
attach that annotation to the recovery report. The annotation cannot renew any
deadline, mark terminal state, suppress a wake, or authorize replay.

The existing local judge contract remains OpenAI-compatible. It gains optional
Bearer authentication sourced from an environment variable or a mode-0600 key
file. The portable default remains unauthenticated loopback. Secrets must not be
embedded in the repository, ledger, logs, fixtures, or process arguments.

For the personal macOS overlay:

- Rapid-MLX remains opt-in and launchd-managed.
- Bind to loopback unless access from another machine is explicitly required.
- The MCP wrapper, batch reviewer, hook client, and health probe use the same
  endpoint/auth configuration.
- Unavailability emits health/incident telemetry and degrades to deterministic
  operation.
- Live prompt-accuracy tests run only when the capability probe succeeds; unit,
  routing, and outage tests run everywhere.

### 7. Installation and deployment

The repository remains the source of portable hook and harness code. Generated
Claude/Codex surfaces are updated through `agent-surfaces/manifest.json` and its
renderer. The launchd service is installed by Escapement deployment code, not by
ad-hoc user instructions, and must invoke `wakeup_waker.py --fire` explicitly.

Deployment verification must distinguish:

1. source tests passing;
2. generated plugin parity;
3. pinned installed checkout containing the revision;
4. launchd service loaded with a recent tick;
5. a disposable real session recovering without user input.

## Error Handling and Safety

- Missing/malformed/untrusted child, Beads, or ledger state is unresolved and triggers
  reconciliation; it is never treated as terminal.
- Supervisor health missing or stale invalidates scheduled-pause release.
- Model errors are advisory outages and never disable deterministic controls.
- Native notification delivery is deduplicated and cannot be the sole terminal
  oracle.
- Recovery never executes arbitrary commands from an untrusted ledger.
- Human explicit release remains an auditable override.
- No automatic retry occurs across an unknown mutation boundary.
- Old-format thread directories without a ledger retain current behavior until
  their first managed dispatch; migration does not fabricate active children.

## Test Oracle Brief

### 1. Business invariant

After a managed delegation, the harness either observes verified terminal child
state and verified parent outcome or performs bounded automatic reconciliation.
It cannot wait indefinitely for a completion callback or user prompt, and it
cannot report completion merely because child beads or the descendant queue are
drained while the parent bead remains unresolved.

### 2. Independent source of truth

- A sanitized replay fixture from session
  `028fb5cb-83bb-46b9-8848-1713f2cd9caa`, preserving event ordering and elapsed
  time without relying on production helper outputs.
- Captured current Claude and Codex hook payload fixtures.
- An independent reference transition model used by stateful/restart tests.
- Wall-clock observation of an installed supervisor firing a disposable recovery
  without a user message.
- Final child/business outcome checks, not ledger status alone.

### 3. Solution constraints

- Python standard library for the durable core; no Temporal/LangGraph service.
- Atomic, trusted, per-session state under the continuation harness.
- Beads is the canonical durable work graph; Escapement owns execution attempts,
  liveness, reconciliation, outcome verification, and completion policy.
- Host-specific payload parsing stays in adapters.
- Local models are optional and advisory.
- No secret material in Git, process arguments, transcripts, or incident logs.
- New files stay focused and below the repository complexity thresholds.
- Generated plugin surfaces must remain source-parity checked.

### 4. Invalid solution classes

- Treating Beads queue drain, a transcript mtime, a task status string, or a
  future `scheduled.json` timestamp as sufficient completion/resumption proof.
- Treating closed children or empty descendants as completion while the
  claimed/root bead remains open or `in_progress`.
- Mirroring Beads task metadata/status into `executions.json` or auto-closing a
  bead from a worker terminal event.
- Depending exclusively on parent process lifetime or completion notifications.
- Renewing a lease on tool start, polling output, or local-model opinion.
- Automatically replaying a child across an unknown mutation boundary.
- Implementing only Claude-specific state in the core.
- Requiring Rapid-MLX or weakening behavior when it is absent.
- Enabling Codex Stop based on schema/source presence without an effective-hook
  fixture and real smoke.

### 5. Fragile implementation to reject

Add only a Stop check that asks whether descendant child beads or a team
directory contain unresolved children, and allow Stop when children are closed
or any future wakeup exists. This passes the research-child case while the parent
outcome remains open, and still stalls if the parent already ended, the waker is
dry-run/uninstalled, child state disappears, or a completion notification is
lost.

### 6. Negative controls

- Exact incident replay: queue drained, two non-terminal children, no qualifying
  wake/supervisor proof must not allow an unbounded stop.
- Exact design-session replay: both research child beads closed while parent bead
  remains `in_progress` must block completion and trigger the next parent action.
- Standalone claimed leaf bead `in_progress` with no descendants must not be
  classified as `queue_drained`; the same bead closed is the positive control.
- Child registered but never bound/started breaches the start deadline.
- Tool-start with no result for more than 15 minutes breaches the activity
  deadline.
- Repeated noisy status/tool-start events do not renew the lease.
- Future wakeup plus stale/missing supervisor tick is rejected.
- Missing, malformed, cross-session, or untrusted ledger remains unresolved.
- Duplicate/late completion does not apply a result twice.
- A healthy outer supervisor process whose useful reconciliation fails does not
  update `last_successful_reconcile_at`.
- Crash after recovery claim but before spawn is recovered after claim expiry;
  stale-generation completion cannot mutate the replacement attempt.
- Local endpoint unavailable or unauthorized does not disable the watchdog.
- Codex hook declared but not effectively registered fails the installation
  contract.

### 7. Positive controls

- Parent with no managed children preserves ordinary verified Stop behavior.
- A claimed/root bead that is closed with all children terminal and results
  verified permits normal completion.
- A running child with recent accepted activity, unexpired hard deadline, and a
  healthy watchdog permits a bounded pause.
- Legitimate scheduled external work with a healthy supervisor fires once and
  is pruned.
- Local semantic annotation appears when the endpoint is healthy without
  changing the underlying deadline decision.

### 8. Missing/unresolved handling

Fail closed on completion: missing child/ledger/supervisor evidence means
unresolved. Do not blindly retry. The system wakes/reconciles or records a
specific auditable human override.

### 9. Final outcome verification

1. Run focused ledger, adapter, Stop, supervisor, restart, security, and optional
   local-judge tests.
2. Run a mutation challenger that plants at least these bad implementations:
   descendant-only queue-drain completion, child-terminal auto-close, any-mtime
   heartbeat, health-before-reconcile, unfenced recovery spawn,
   future-file-only wake proof, missing-state-is-terminal, and automatic retry.
3. Run the full repository suite; rerun loopback-server tests outside a sandbox
   when local bind restrictions are the only failure.
4. Render and check Claude/Codex plugin surfaces.
5. Deploy through the pinned-checkout path and verify installed file revision.
6. Verify launchd is loaded, `--fire` is present, and the supervisor tick is
   current.
7. Exercise a disposable real Claude delegation whose child intentionally stops
   producing completed activity; observe automatic reconciliation before the
   configured deadline without a user message.
8. Exercise the effective Codex lifecycle smoke appropriate to the installed
   adapter; do not claim Stop parity unless the hook actually blocks.
9. On the personal machine, prove both authenticated local classification and
   unavailable-model deterministic fallback.

## Delivery Boundaries

The work may land in several reviewed commits or child Beads, but it is one
verified outcome. Existing Beads `escapement-uf5`, `escapement-u7aq`, and
`escapement-2waa` are reconciled as subsumed, completed, or still externally
blocked rather than left as unexplained duplicates.

The first release is complete only when the installed scheduler fires and the
incident replay plus a real disposable session are bounded. A ledger file,
passing unit tests, or a merged PR alone is not completion.
