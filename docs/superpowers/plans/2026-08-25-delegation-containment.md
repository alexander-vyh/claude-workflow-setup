# Delegation Containment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove globally denying and false-warning delegation hooks, and remove unsound shell and Serena classification from the root-checkout gate, before repairing lifecycle observation behind an isolated canary.

**Architecture:** Capability status in `agent-surfaces/manifest.json` is the source of global hook registration. Replace the denying Agent PreTool adapter with a narrowly ready, non-blocking managed-dispatch observer; mark unconditional reconciliation partial and render it without SessionStart registration. A managed dispatch expectation is written before ledger registration so evidence failure can never deny native capacity or silently authorize completion. Keep root-checkout enforcement only for Claude built-in edit tools with explicit destination fields. Do not register Bash because arbitrary shell effects have no sound PreTool oracle. Do not register Serena on any cwd-rooted path classifier because its relative paths use an independently activated project root absent from the hook payload. Accept task mode only from an exact successful single-line claim command in both public PostToolUse and transcript recovery.

**Tech Stack:** Python 3 standard library, pytest, JSON plugin manifests, generated Claude and Codex plugin surfaces.

---

### Task 1: Encode containment in behavioral surface tests

**Files:**
- Modify: `tests/test_agent_surfaces.py`

- [ ] **Step 1: Replace ready-registration assertions with containment assertions**

Assert that `delegation_hook` is ready only for Claude Agent PreTool observation,
names automatic non-blocking behavior, and cites the managed first-attempt
fixture. Assert that `execution_reconcile` is `partial` for both Claude and
Codex with no events and carries a concrete reason naming missing
expectation-aware reconciliation behavior.

- [ ] **Step 2: Assert generated plugins contain only safe delegation observation**

```python
assert all("execution_reconcile.py" not in command for _, command in claude_session)
assert all("execution_reconcile.py" not in command for _, command in codex_session)
```

Flatten every event in both generated packages. Assert the delegation observer
appears exactly once as Claude `PreToolUse`/`Agent`, never in Codex or a different
event, and reconciliation appears nowhere.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
pytest -q \
  tests/test_agent_surfaces.py::test_manifest_registers_only_nonblocking_managed_dispatch_observation \
  tests/test_agent_surfaces.py::test_generated_plugins_only_register_safe_delegation_observation
```

Expected: FAIL because delegation still describes manual preparation and
reconciliation remains globally registered.

### Task 2: Make managed dispatch observation non-blocking and completion-safe

**Files:**
- Modify: `harness/tests/test_delegation_hook.py`
- Modify: `harness/tests/test_execution_stop_gate.py`
- Modify: `harness/bin/delegation_hook.py`
- Create: `harness/bin/execution_expectation.py`
- Modify: `harness/bin/execution_stop_adapter.py`
- Modify: `agent-surfaces/manifest.json`
- Modify: `tools/render_agent_surfaces.py`
- Generated: `plugins/escapement/hooks/hooks.json`
- Generated: `plugins/escapement-claude/hooks/hooks.json`
- Generated: `plugins/escapement/**`
- Generated: `plugins/escapement-claude/**`

- [ ] **Step 1: Prove unmanaged, managed, and persistence-failure behavior RED**

Assert unmanaged first attempt allows with no Escapement state. Assert a trusted
managed first attempt automatically writes expectation plus ledger without a
prepare command or child Bead lookup. Force ledger persistence failure after a
durable expectation, assert Agent allow, and assert the public completion adapter
returns `delegated_execution_unresolved`.

- [ ] **Step 2: Implement expectation-first automatic registration**

Read only trusted exact-session task mode. Unmanaged or malformed state returns
native allow without creating files. Managed mode writes a trusted expectation
keyed by host tool-use identity, then registers the execution from task scope.
Every state-write failure still returns allow. Remove manual prepare guidance
because this adapter must emit no Agent denial.

If expectation persistence fails, write the exact dispatch identity to the
separate trusted `execution_incident.json` fallback and skip ledger registration.
The completion adapter treats either trusted expectation or trusted incident as
managed unresolved evidence.

If both evidence writes fail, still return native allow with unresolved status.
Treat symlinked, world-writable, malformed, or foreign-session task mode as
unmanaged and create no execution state.

- [ ] **Step 3: Make completion expectation-aware**

When a trusted expectation exists but its ledger is absent or does not contain
the matching dispatch, public managed completion returns
`delegated_execution_unresolved`. No expectation plus no ledger preserves legacy
completion behavior.

- [ ] **Step 4: Change manifest capability status**

Describe Claude `delegation_hook` as automatic and non-blocking, keep only its
ready Agent PreTool event, and cite the managed first-attempt fixture. Set Claude
and Codex `execution_reconcile` to `partial`, remove their events, and state that
expectation-aware SessionStart behavior is not yet verified.

- [ ] **Step 5: Keep incomplete sources bundled for isolated repair tests**

Extend renderer bundle ownership so partial delegation source files remain in
the plugin package even though `_render_claude_plugin_hooks` and
`_codex_ready_hook_sources` do not register them.

- [ ] **Step 6: Render generated surfaces**

Run:

```bash
python3 tools/render_agent_surfaces.py
```

Expected: generated Claude contains exactly one Agent PreTool observation;
generated Claude and Codex contain no `execution_reconcile.py` registration;
bundled repair sources remain present.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run:

```bash
pytest -q tests/test_agent_surfaces.py harness/tests/test_delegation_hook.py harness/tests/test_execution_reconcile.py
```

Expected: PASS.

### Task 3: Narrow root-checkout enforcement to proven destination contracts

**Files:**
- Modify: `claude/hooks/tests/test_root_checkout_guard.py`
- Modify: `claude/hooks/root_checkout_guard.py`
- Generated: `plugins/escapement/claude/hooks/root_checkout_guard.py`
- Generated: `plugins/escapement-claude/hooks/root_checkout_guard.py`

- [ ] **Step 1: Add the reported positive control and built-in edit controls**

Assert that the installed Claude manifest contains no Bash or Serena
root-checkout matcher. Send Bash and each Serena symbol-edit payload directly to
the source hook with an exploding `tool_input`; each must return untouched
without recording a gate signal. For Write, Edit, NotebookEdit, and MultiEdit,
assert a primary-checkout destination denies while linked-worktree and outside
destinations allow.

- [ ] **Step 2: Run the focused controls and verify RED**

Run:

```bash
pytest -q \
  claude/hooks/tests/test_root_checkout_guard.py::test_unregistered_tool_is_outside_root_checkout_hard_gate \
  claude/hooks/tests/test_root_checkout_guard.py::test_explicit_edit_to_primary_checkout_denied_when_cwd_outside_repo
```

Expected: Serena cases FAIL while they remain registered; the built-in primary
checkout control remains denied.

- [ ] **Step 3: Remove unsupported tool registrations**

Remove Bash from the root source gate and remove Serena from root, TDD, and Test
Oracle Brief manifest registration. Limit the root source path contract to the
canonical fields used by Write, Edit, NotebookEdit, and MultiEdit. Do not add a
shell parser or resolve Serena `relative_path` against Claude cwd.

Mark the Codex root-checkout surface `partial` with no events because its current
hook payload does not expose `exec_command.workdir`. Keep the source bundled for
fixtures, but assert no generated Codex event or dispatcher registers it. Keep
Claude ready only for the four proven built-in edit contracts.

- [ ] **Step 4: Run the full root-checkout guard suite and verify GREEN**

Run:

```bash
pytest -q claude/hooks/tests/test_root_checkout_guard.py
```

Expected: PASS, including built-in write-into-root controls and direct
unregistered-tool non-inspection controls.

### Task 4: Verify source and package containment

**Files:**
- Modify: `tests/test_agent_surfaces.py`

- [ ] **Step 1: Add package-parity assertions**

Assert that source scripts remain byte-identical to both vendored plugin copies
while no active hook command references them.

- [ ] **Step 2: Run the generated-surface and package tests**

Run:

```bash
pytest -q tests/test_agent_surfaces.py tests/test_codex_plugin_update.py
```

Expected: PASS.

- [ ] **Step 3: Run the broad repository suite**

Run:

```bash
pytest -q --ignore=pi-adapter
```

Expected: PASS with no failures.

### Task 5: Commit and land containment

**Files:**
- Modify: tracker state through `bd`
- Commit: design, oracle, plan, manifest, renderer, tests, and generated surfaces

- [ ] **Step 1: Update the bead spec link**

Run:

```bash
bd update escapement-xncx --spec-id docs/superpowers/specs/2026-08-25-scoped-agent-delegation-design.md
```

- [ ] **Step 2: Commit the containment change**

```bash
git add agent-surfaces/manifest.json tools/render_agent_surfaces.py \
  tests/test_agent_surfaces.py claude/hooks/root_checkout_guard.py \
  claude/hooks/tests/test_root_checkout_guard.py \
  plugins/escapement plugins/escapement-claude \
  docs/superpowers/specs/2026-08-25-scoped-agent-delegation-design.md \
  docs/superpowers/specs/2026-08-25-scoped-agent-delegation-test-oracle-brief.md \
  docs/superpowers/plans/2026-08-25-delegation-containment.md
git commit -m "fix: contain incomplete delegation hooks"
```

- [ ] **Step 3: Push, open a PR, carry checks through green, and merge**

Use the repository-declared feature-branch landing path. The PR test plan must
name the first-attempt unmanaged Agent smoke and normal Codex SessionStart smoke.

- [ ] **Step 4: Install the merged plugin transactionally**

Run the repository-declared plugin update command against merged `main`, keeping
the prior enabled generation recoverable until fresh-session verification
passes.

- [ ] **Step 5: Verify the installed outcome**

In fresh Claude and Codex processes, prove:

- one unmanaged Agent starts on its first invocation;
- no `prepared_execution_required` appears;
- no normal-session missing-ledger instruction appears;
- active installed hook configs contain neither incomplete hook registration;
- installed bundle hashes match merged source.

Expected status: `contained`. Continue immediately with the lifecycle repair
plan; containment alone is not the user's permanent shipped outcome.
