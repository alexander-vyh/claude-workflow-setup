# Pi Adapter Design

Date: 2026-08-21
Status: approved for planning

## Outcome

Escapement is installable by Pi directly from the same Git repository root used
to produce its Claude and Codex packages:

```bash
pi install git:github.com/alexander-vyh/escapement
```

The installed Pi package receives Escapement's shared instructions, skills,
tool gates, lifecycle context, and completion/continuation decisions without a
second implementation of workflow policy.

## Source of Truth

The existing neutral sources remain authoritative:

- `agent-surfaces/manifest.json` owns host support, lifecycle bindings, and
  fixture claims.
- `agent-surfaces/onboarding/` owns shared instruction text.
- `claude/hooks/` and `harness/bin/` own Python policy and decision logic.
- `tools/render_agent_surfaces.py` projects those sources into host packages.

Pi is a third generated host alongside Claude and Codex. The Pi extension owns
only payload translation, process invocation, and mapping shared decisions back
to Pi's extension API.

## Considered Approaches

### Generated Pi adapter using the shared dispatcher (selected)

Add Pi bindings to the neutral manifest, render a Pi package, and invoke all
applicable Python gates through one dispatcher process per Pi tool call. This
preserves one policy implementation and avoids the process fanout repaired in
PR #164.

### One Python process per gate (rejected)

This is initially smaller, but reproduces the host-wide process storm that made
ordinary Codex Bash calls miss their deadlines.

### Native TypeScript policy rewrite (rejected)

This is idiomatic Pi code but creates competing Python and TypeScript policy
authorities. Similar output text would hide semantic drift.

## Package Shape

The renderer owns these Pi distribution artifacts:

- root `package.json`, containing the `pi` resource manifest and the
  `pi-package` keyword;
- `plugins/escapement-pi/extensions/index.ts`, the thin host adapter;
- `plugins/escapement-pi/gates.json`, the generated ready-gate inventory;
- `plugins/escapement-pi/PI.md`, rendered shared instructions;
- Pi skill paths selected from the same canonical skill sources already used by
  Claude and Codex.

The root manifest points into `plugins/escapement-pi/` and the shared skill
tree. Pi therefore installs the repository root rather than a separate release
repository or copied subtree.

The TypeScript extension is maintained host-specific code. Its gate inventory,
instruction document, package metadata, and skill selection are generated from
the neutral root and checked for drift.

## Runtime Components

### Host-neutral pre-tool dispatcher

The current Codex dispatcher becomes a host-neutral implementation module.
Codex retains a compatibility entrypoint while Pi invokes the same dispatcher.
For each Pi `tool_call`, the extension:

1. maps the Pi tool name and arguments to the established hook payload;
2. selects the applicable ready gates from generated `gates.json`;
3. starts one Python dispatcher process;
4. supplies all selected gates and their individual timeout budgets;
5. maps the aggregate decision to Pi.

Pi tool mappings initially cover its built-ins:

- `bash` to `Bash`;
- `write` to `Write`;
- `edit` to `Edit`.

A deny decision returns `{ block: true, reason }`. An allow decision returns no
block. An ask decision uses Pi UI confirmation when UI is available and blocks
with the reason in non-interactive mode. Advisory context is retained in the
session as an Escapement custom message rather than discarded.

The extension contains no gate-specific business rules.

### Shared session context

At `session_start`, the extension runs the shared session-context adapter and
stores its returned context for the current session. At `before_agent_start`,
it appends the rendered Pi instructions plus current dynamic context to the
chained system prompt. At `session_before_compact`, it refreshes the dynamic
context so the post-compaction turn receives current work and landing state.

### Shared continuation decision

Pi's current extension API emits `agent_settled` only after retry, compaction,
and queued follow-up work are exhausted. It also allows an idle extension to
trigger a new turn immediately.

The Pi adapter invokes a small Python CLI around the existing
`would_block_stop()` policy and the existing repository-outcome checks. When the
shared decision blocks completion, the adapter sends a labelled Escapement
message with `deliverAs: "followUp"` and `triggerTurn: true`. The next settled
event re-evaluates durable state. When the shared decision allows completion,
the adapter does nothing.

The adapter stores the last emitted decision key in Pi session entries to avoid
duplicate display noise, but it never treats prior emission as completion
evidence. A changed or still-blocking decision may continue to trigger turns.

## Capability Honesty

Every manifest hook must declare `hosts.pi.status` as either `ready` or
`unsupported`. A ready claim requires a Pi payload fixture and an outcome test.
Claude-only tool semantics such as Claude Agent dispatch remain unsupported
until Pi exposes a matching capability. Unsupported entries carry a concrete
reason and are not included in `gates.json`.

Packaging success is not described as full Claude parity. The delivered claim
is parity for every capability explicitly marked ready for Pi.

## Failure Handling

- Missing `python3`, malformed gate inventory, path escape, or an invalid
  dispatcher response blocks the affected gate evaluation with an actionable
  configuration error. It does not silently claim enforcement.
- A gate timeout is reported by the shared dispatcher while later gates still
  run; aggregate deny precedence is preserved.
- The extension enforces an overall process deadline derived from declared
  per-gate budgets and terminates the dispatcher if that deadline expires.
- Repositories without Escapement work or landing configuration remain
  conversational; completion enforcement does not infer work from unrelated
  backlog.
- Package loading must not start background processes. Python runs only for a
  lifecycle or tool event.

## Test Oracle Brief

### Business invariant

A Pi installation from the Escapement Git root enforces the same shared policy
decisions claimed ready for Pi, through one policy source and one dispatcher
process per tool event.

### Independent source of truth

Pi's installed package inventory and real JSON-mode event stream, combined with
the Python gates' established behavioral fixtures and durable repository/task
state.

### Constraints

- one repository root and one neutral manifest;
- no TypeScript reimplementation of gate decisions;
- one dispatcher process per tool event;
- Pi 0.84.2 public package and extension interfaces;
- explicit unsupported classifications;
- no destructive modification of the user's normal Pi configuration during
  pre-merge tests.

### Invalid solution classes

- a Pi-only copy of rules, skills, or gate logic;
- a package that installs but loads no resources;
- spawning one Python process per gate;
- blocking every tool call on adapter uncertainty;
- advisory prose presented as deterministic enforcement;
- tests that invoke the extension directly but never load it through Pi.

### Fragile implementation to reject

A root `package.json` that makes `pi install` succeed while omitting the
extension or pointing at a stale copied gate inventory.

### Negative controls

- a denied Bash command is blocked by a real shared Python gate;
- a non-interactive ask decision fails closed;
- a package with a stale or missing generated inventory fails the renderer
  check;
- a planted per-gate-spawn adapter fails the static/process-count check;
- unresolved completion state triggers another Pi turn.

### Positive controls

- a safe Bash command executes;
- a repository with no active Escapement outcome can settle;
- instructions and at least one shared skill are visible in an installed Pi
  session;
- multiple applicable gates execute inside one dispatcher PID.

### Missing or unresolved handling

Missing runtime/package evidence fails closed for a capability claimed ready.
Missing task or landing configuration is explicitly conversational and does not
create work.

### Final outcome verification

Using an isolated `PI_CODING_AGENT_DIR`, install Escapement from the merged Git
root, inspect `pi list`, run Pi in JSON mode in a scratch repository, observe a
safe Bash call complete, observe a known-invalid Bash call blocked by the shared
gate, and observe unresolved durable work cause an immediate continuation turn.
Then verify a normal user-scope install/update path from the same Git root.

## Non-goals

- Reimplementing Claude-only Agent/team semantics in Pi.
- Publishing an npm package in the first delivery; Git-root installation is the
  required distribution path.
- Adding new workflow policy while porting the adapter.
- Making Pi depend on Codex or Claude configuration directories.
- Running a persistent background Python daemon solely for hook dispatch.

## Delivery

The change follows the repository's declared feature-branch, pull-request,
merge, and post-merge verification path. Delivery is complete only when a fresh
Pi configuration installs the merged repository root and the real allow, deny,
context, skill, and continuation outcomes are observed.
