# Pi Adapter Design

Date: 2026-08-21
Status: approved for planning

## Outcome

Escapement is installable by Pi directly from the same Git repository root used
to produce its Claude and Codex packages:

```bash
pi install git:github.com/alexander-vyh/escapement
```

The installed Pi package receives Escapement's shared instructions and
Codex-ready Bash tool gates without a second implementation of workflow policy.
Skills continue to load through Pi's native user/project `.agents/skills`
discovery so the Git package does not register duplicate names.

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
- no package-level skill resource; Pi uses the generated native user/project
  `.agents/skills` surfaces already shared with Claude and Codex.

The root manifest points into `plugins/escapement-pi/`. Pi therefore installs
the repository root rather than a separate release repository or copied
subtree, without reporting collisions against native skill copies.

The TypeScript extension is maintained host-specific code. Its gate inventory,
instruction document and package metadata are generated from the neutral root
and checked for drift.

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

The initial tool mapping is deliberately narrow: Pi `bash` maps to the shared
`Bash` payload. A deny decision returns `{ block: true, reason }`, an allow
decision returns no block, and an ask decision fails closed with its reason.
Write/edit mappings remain outside this basic package until a ready gate needs
them and a fixture proves the payload contract.

The extension contains no gate-specific business rules.

### Capability boundary

At `before_agent_start`, the extension appends the generated Pi instructions to
Pi's chained system prompt. Dynamic session-context refresh and mechanical
completion/continuation interception are not part of this basic package. They
remain explicit capability gaps, matching the repository's honesty about the
Codex final-response boundary, rather than being approximated with new control
machinery.

## Capability Honesty

The manifest owns one explicit Pi adapter mapping from Codex-ready
`PreToolUse/Bash` gates to Pi `tool_call/bash`. Claude-only tool semantics such
as Claude Agent dispatch remain unsupported. Capabilities outside that mapping
are not included in `gates.json` and are not claimed as enforced.

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
- Package loading must not start background processes. Python runs only for a
  tool event.

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
- an explicit, narrow Pi adapter mapping;
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

### Positive controls

- a safe Bash command executes;
- instructions are visible and package loading does not duplicate native
  user/project skills in an installed Pi session;
- multiple applicable gates execute inside one dispatcher PID.

### Missing or unresolved handling

Missing runtime/package evidence fails closed for a capability claimed ready.

### Final outcome verification

Using an isolated `PI_CODING_AGENT_DIR`, install Escapement from the merged Git
root, inspect `pi list`, create a real model-free Pi SDK session, prove the
installed extension loaded without package-owned skill duplicates, observe a
safe Bash call complete, and observe a runtime-generated denial from two shared
Python gates with one dispatcher PID. Then verify a normal user-scope
install/update path from the same Git root.

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
instruction, and collision-free native-skill outcomes are observed.
