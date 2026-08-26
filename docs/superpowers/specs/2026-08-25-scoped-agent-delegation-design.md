# Scoped Agent Delegation Design

## Outcome

Native Claude `Agent` and `Explore` capacity must work on the first attempt in
every repository. Escapement may observe and verify delegated execution only
inside an explicitly managed task session. Missing, stale, corrupt, or
host-incompatible Escapement state must never deny an Agent call. It may prevent
an unverifiable managed session from claiming completion.

Routine filesystem commands must likewise be classified from the operands the
shell will actually use. In particular, shell home-directory syntax such as
`~/Downloads/file.pdf` must be expanded before root-checkout containment. A
repo-shaped string inside an unrelated source path or session scratch path is
not evidence that the repository is being mutated.

This replaces the globally enabled prepared-execution gate and the unconditional
SessionStart ledger warning introduced by the delegated-work liveness feature.

## Core boundary

Escapement uses different failure policies at two boundaries:

| Boundary | Failure policy | Reason |
| --- | --- | --- |
| Native Agent capacity | Fail open | Bookkeeping failure is not authority to remove delegated capacity. |
| Managed completion claim | Fail closed | A managed outcome cannot be called complete without trusted execution evidence. |

Security, destructive-action, permission, and landing gates remain independent.
This design changes only delegated-execution bookkeeping.

The root-checkout safety gate remains enabled. Its boundary is an actual write
target resolved with shell-compatible path semantics, never the session cwd or
an unexpanded operand when a concrete destination exists.

Claude supplies the effective command cwd needed by that boundary. The current
Codex PreTool payload does not expose `exec_command.workdir`; its session cwd can
differ from the command execution cwd. The Codex registration therefore remains
`partial` and unregistered until a captured payload proves the per-command
working directory. Shipping a blocking classifier without that operand is not a
safety feature.

## Filesystem intent classification

For recognized shell mutations, Escapement resolves the command's actual write
targets before asking whether they are inside a Beads-managed primary checkout.

- `cp` and `install` read their sources and write their destination.
- `mv` mutates both source and destination.
- explicit target-directory flags identify the destination.
- relative paths resolve against the effective cwd after literal `cd` segments.
- leading `~` and `~/` expand exactly as the invoking user's shell would.
- quoted repo-shaped text and scratch-directory names have no authority by
  themselves.

If the parser cannot identify an operand for a recognized mutating command, the
existing conservative cwd fallback remains. If it identifies a concrete target,
the gate must decide from that target and must not replace it with cwd.

## Modes

### Unmanaged session

No trusted `session_mode.json` means the session is unmanaged.

- Agent calls run natively without Escapement denial.
- Escapement does not create `executions.json`.
- Missing `executions.json` is normal and silent.
- No bookkeeping-only Bead is created.

### Managed task session

A trusted `session_mode.json` created by an explicit Beads claim establishes the
task scope. Agent PreToolUse then records dispatch intent automatically from the
trusted session scope and host fields. No manual `prepare` command and no child
Bead are required.

The execution record uses:

- exact parent session identity;
- the trusted task or parent scope from `session_mode.json`;
- host-provided `tool_use_id`;
- exact Agent name;
- host `claude`;
- an Escapement execution UUID and watchdog UUID.

If persistence fails, Escapement allows the Agent call and records an incident
when it can. The managed completion gate remains unresolved until independent
host evidence repairs the gap.

Expectation persistence uses a separate atomic path from the fallback
`execution_incident.json`. If the expectation path itself is unavailable but
the thread directory remains writable, Escapement records the exact session and
tool-use identity in that incident and does not attempt ledger registration.
Either trusted expectation or trusted incident makes managed completion
unresolved. If the entire thread directory is unwritable, Agent capacity still
runs; transcript evidence is the recovery oracle and the adapter must report the
loss rather than convert it into an Agent denial.

## Lifecycle

```text
Agent PreToolUse
  -> unmanaged: allow without ledger
  -> managed: record dispatch intent, then allow

Agent PostToolUse
  -> teammate_spawned + structured agent_id: bind and start exact execution
  -> result proving no spawn: dispatch_aborted
  -> unknown or conflicting result: unresolved, never guessed

Trusted transcript observation
  -> delivered child activity: activity_completed
  -> matching terminal/idle evidence: child_terminal

Terminal or aborted transition
  -> clear deadlines, reconcile_due, and recovery claim
  -> retain honest durable history
```

`dispatch_aborted` is legal only for the active attempt and generation before a
native child is bound. It never fabricates a child identifier.

## Ledger expectation

An independent `execution_expectation.json` is written before managed dispatch
registration. It contains the parent session, trusted task scope, Agent name,
and tool-use ID. The ledger consumes or references that expectation.

- No expectation and no ledger: silent.
- Existing ledger: validate and reconcile.
- Trusted expectation but missing ledger: actionable unresolved evidence.
- Existing malformed, untrusted, symlinked, or actor-mismatched ledger:
  actionable unresolved evidence.

The expectation is not completion evidence and cannot authorize replay.

## Host capability

The Claude adapter is enabled only for hook events and fields proven by captured
installed-host fixtures and a live canary. Codex remains unsupported for native
Agent lifecycle enforcement until Codex exposes and proves an equivalent
boundary. Unsupported hosts do not receive ledger warnings for state they cannot
create.

If a host version changes its payload contract, installation downgrades the
adapter to observe-only or disabled. It must not leave a globally denying hook
active.

## Delivery stages

### 1. Contain

- Replace the denying Claude Agent PreTool adapter with a narrowly ready,
  non-blocking managed-dispatch observer. Unmanaged calls pass without state;
  managed calls durably write expectation before attempting ledger registration.
- If managed ledger registration fails after expectation persistence, allow the
  Agent call and keep public managed completion unresolved.
- Mark SessionStart execution reconciliation `partial` for hosts without a
  trusted expectation writer.
- Render, test, merge, install, and prove a fresh unmanaged Agent call succeeds
  without `prepared_execution_required`.
- Repair home-relative shell target resolution and prove a scratchpad-to-
  `~/Downloads` copy succeeds without a waiver while a real copy into the
  primary checkout remains denied.

### 2. Repair incomplete lifecycle while observation remains enabled

- Add honest `dispatch_aborted`.
- Bind real structured Claude child identity.
- Observe deterministic activity and terminal evidence.
- Clear terminal deadline residue.
- Correct managed completion classification.

Only the incomplete PostToolUse, transcript, and terminal lifecycle surfaces
stay unregistered during this stage. The proven non-blocking PreTool observer
remains enabled.

### 3. Isolated canary

Use a scratch `CLAUDE_CONFIG_DIR`, scratch harness root, scratch Beads repository,
and fresh Claude process. Prove both an unmanaged first-attempt Agent call and a
managed three-agent workflow. Three children must have distinct native IDs,
overlapping execution intervals, and a peer message used in their conclusion.

### 4. Transactional global enable

Global enable is a separate reviewable change. The plugin updater retains the
disabled generation and an uncommitted journal while the same canary runs
against the installed candidate. Success finalizes. Failure, timeout, signal,
or interrupted next invocation restores the disabled generation byte for byte.

## Migration

Existing ledgers are classified from trusted host evidence:

- proven spawn: bind the real child identity;
- proven pre-spawn error: abort honestly;
- ambiguous consumed dispatch: retain an unresolved incident;
- no managed delegation evidence: treat missing ledger as normal;
- resolved ledger: retain history and clear stale deadlines.

No migration deletes or archives state merely to make a gate pass.

## Readiness vocabulary

- `contained`: denying global hook removed and unmanaged Agent smoke passed;
- `repaired-disabled`: lifecycle code merged while global hook remains partial;
- `canary-verified`: isolated unmanaged and managed workflows passed;
- `re-enabled`: candidate installed with rollback journal still open;
- `live-verified`: installed canary passed and transaction finalized;
- `rolled-back`: exact disabled generation restored after failure.

Only `live-verified` is the shipped permanent outcome.
