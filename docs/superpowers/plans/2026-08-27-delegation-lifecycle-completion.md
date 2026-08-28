# Delegation Lifecycle Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete managed Claude delegation lifecycle observation and enable it only after an isolated canary proves real spawn, abort, activity, terminal, completion-release, and rollback behavior.

**Architecture:** Keep Beads as work state and the existing execution ledger as the host-neutral state machine. Add one Claude-only adapter that converts exact installed-host records into normalized ledger events; keep unsupported or ambiguous records unresolved. Reuse the existing plugin-update rollback journal and place the live canary before transaction commit.

**Tech Stack:** Python 3 standard library, pytest, JSONL host fixtures, generated plugin manifests, Claude Code 2.1.247.

**Spec:** `docs/superpowers/specs/2026-08-25-scoped-agent-delegation-design.md`

## Global Constraints

- Native Agent/Explore capacity always fails open; only trusted managed completion evidence fails closed.
- Parse structured host fields only. Never parse result prose, invent child IDs, or default an omitted generation.
- `idle_notification` is nonterminal: the captured child resumed after it.
- A terminal record must carry the original tool-use ID or the exact bound child ID and a unique host event identity.
- Unknown, mismatched, replayed, malformed, or host-drifted records remain unresolved without renewing deadlines.
- A legitimate Beads record with no parent is a standalone root, not a broken relationship.
- Source, rendered, selected-cache, and active installed surfaces must agree before global enable commits.
- Use existing updater transaction and wrapper ownership; do not add a second deployment transaction.
- Keep every production file below the repository's 500-line soft threshold where practical; extract the Claude adapter rather than growing `delegation_hook.py` or `execution_reconcile.py` into multi-responsibility files.
- Preserve prefix behavior at the public boundary: spawn and idle evidence leave managed completion blocked; only a separately observed matching terminal record releases it.
- Treat captured fixtures as independent evidence. Each sanitized record needs a provenance sidecar containing the Claude version, capture time, raw-record digest, sanitizer command/version, and retained JSON pointers before adapter code is written.
- At canary invocation, the original updater journal must still be armed and uncommitted. The ordered lifecycle is one begin, candidate activation, parity, canary, then one commit or rollback; a second deployment transaction is forbidden.

---

### Task 1: Make resolved ledger state honest and replay-safe

**Files:**
- Modify: `harness/bin/execution_ledger.py`
- Modify: `harness/bin/execution_validation.py`
- Modify: `harness/schemas/executions.schema.json`
- Modify: `harness/bin/execution_reconcile.py`
- Modify: `harness/bin/execution_supervisor.py`
- Test: `harness/tests/test_execution_ledger.py`
- Test: `harness/tests/test_execution_validation.py`
- Test: `harness/tests/test_execution_reconcile.py`
- Test: `harness/tests/test_execution_supervisor.py`

**Interfaces:**
- Produces: normalized `dispatch_aborted` event; `state == "aborted"`; one shared terminal cleanup path; idempotent host event IDs.
- Produces: canonical-parent classification where absent/null means standalone, a non-empty string means parented, and every other value is unresolved.

- [ ] **Step 1: Add failing behavioral controls**

Add literal tests proving:

```python
apply_event(queued, dispatch_aborted, at("2026-08-27T21:27:02Z"))
assert execution["state"] == "aborted"
assert execution["native_child_id"] is None
assert all(execution[key] is None for key in DEADLINE_AND_CLAIM_RESIDUE)
```

Also prove abort-after-bind rejects; terminal/cancelled/aborted clear every deadline, `reconcile_due`, and `recovery_claim`; replaying the same host event is a no-op; replaying a different event cannot renew activity. Add a top-level Bead positive control and malformed-parent negative control in both reconciliation paths.

For replay, snapshot `updated_at`, `last_activity_at`, `last_activity_kind`, all
deadlines, `reconcile_due`, and `recovery_claim`. An identical host event at a
later reconcile time must preserve that snapshot byte-for-byte; reused identity
with altered semantics must reject without mutation. A genuinely new activity
event must advance activity and idle deadline without changing hard deadline.

For canonical parents, cover absent and explicit null as standalone; empty
string, boolean, list, and mapping as unresolved; and a non-empty string as one
exact parent lookup.

- [ ] **Step 2: Run the controls and verify RED**

Run:

```bash
pytest -q harness/tests/test_execution_ledger.py harness/tests/test_execution_validation.py harness/tests/test_execution_reconcile.py harness/tests/test_execution_supervisor.py
```

Expected: failures for absent `dispatch_aborted`, retained residue, replay renewal, and parentless warning.

- [ ] **Step 3: Implement the smallest shared state-machine changes**

Add `aborted` to the schema/validator. Add `dispatch_aborted` to `EVENT_KINDS`; allow it only while queued and unbound. Extract one helper used by terminal, cancelled, and aborted transitions to clear all deadlines, sticky reconciliation state, and recovery claims. Record/compare host event identity so the same observation is idempotent and a conflicting replay rejects. Centralize canonical-parent classification and use it from both reconcilers.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/bin/execution_ledger.py harness/bin/execution_validation.py \
  harness/schemas/executions.schema.json harness/bin/execution_reconcile.py \
  harness/bin/execution_supervisor.py harness/tests
git commit -m "fix: make delegation lifecycle resolution honest"
```

### Task 2: Normalize only proven Claude lifecycle records

**Files:**
- Create: `harness/bin/claude_agent_lifecycle.py`
- Create: `harness/tests/fixtures/claude-agent-lifecycle-2.1.247.jsonl`
- Create: `harness/tests/fixtures/claude-agent-lifecycle-2.1.247.provenance.json`
- Create: `harness/tests/test_claude_agent_lifecycle.py`
- Modify: `harness/bin/delegation_hook.py`
- Modify: `harness/bin/execution_reconcile.py`
- Modify: `harness/bin/execution_supervisor.py`
- Modify: `harness/tests/test_delegation_hook.py`
- Modify: `harness/tests/test_execution_reconcile.py`
- Modify: `harness/tests/test_execution_supervisor.py`
- Modify: `tools/render_agent_surfaces.py`
- Modify: `tests/test_agent_surfaces.py`

**Interfaces:**
- Consumes: Task 1 normalized ledger events and replay identity.
- Produces: `observe_post_tool(payload, ledger) -> dict` with `status` and literal normalized `events`.
- Produces: `observe_transcript(path, ledger) -> list[dict]` for structured spawn, no-spawn, peer activity, and task-terminal records.

- [ ] **Step 1: Add sanitized installed-host fixtures**

Preserve exact structural fields from the verified Claude 2.1.247 captures:

- interactive spawn: `toolUseResult.status == "teammate_spawned"`, equal non-empty `agent_id`/`teammate_id`, exact Agent name, original tool-use ID;
- no-spawn: `tool_result.is_error == true`, original tool-use ID, and no child identity;
- nonterminal idle: typed `idle_notification` followed by later child activity;
- terminal: typed `task_notification.status == "completed"`, exact original tool-use ID and structured task ID.

Remove prompts, user content, filesystem paths, and unrelated model metadata.
Generate fixtures through a structural allowlist independent of adapter
constants. The provenance sidecar must record immutable digests of the raw
records and the exact retained JSON pointers. Add a fixture contract that
checks those witnessed relationships before any adapter implementation.

- [ ] **Step 2: Add failing adapter and public-boundary tests**

Tests must prove exact spawn binds then starts; proven no-spawn aborts; idle stays running; terminal closes only the matching generation; mismatched name/tool ID/task ID, content-only results, invented IDs, and late-generation records mutate nothing. Public PostToolUse must durably apply events, while any adapter/store failure returns success to Claude and leaves managed completion unresolved.

Parameterize the spawn negatives across only `agent_id`, only `teammate_id`,
unequal IDs, empty IDs, IDs present only in prose/content, and surplus invented
`native_child_id`. Each case must produce literal `events == []`, byte-identical
ledger state, no bound child, and unresolved managed completion. Add a prefix
oracle: captured spawn produces exactly `child_bound` then `child_started`,
durable running state, no terminal fields, and blocked public completion; idle
also remains running and blocking; only a separately captured matching terminal
record cleans up and releases completion.

- [ ] **Step 3: Run tests and verify RED**

```bash
pytest -q harness/tests/test_claude_agent_lifecycle.py \
  harness/tests/test_delegation_hook.py harness/tests/test_execution_reconcile.py \
  harness/tests/test_execution_supervisor.py tests/test_agent_surfaces.py
```

Expected: adapter missing; current `post_tool()` always unresolved; no transcript observation exists.

- [ ] **Step 4: Implement one Claude adapter and wire disabled observation paths**

Keep host parsing in `claude_agent_lifecycle.py`. Match executions by exact parent session plus dispatch tool-use ID; require exact Agent name and equal structured child identifiers for interactive spawn. Treat only the captured `is_error` shape as abort. Treat peer messages as activity, never terminal. Treat only matching typed task terminal records as terminal. Hash the canonical raw host record for event identity/result digest. Use the adapter from PostToolUse and the existing transcript/native-status seams, but keep incomplete hook registrations partial until Task 3 passes.

- [ ] **Step 5: Render and verify GREEN**

```bash
python3 tools/render_agent_surfaces.py
pytest -q harness/tests/test_claude_agent_lifecycle.py \
  harness/tests/test_delegation_hook.py harness/tests/test_execution_reconcile.py \
  harness/tests/test_execution_supervisor.py tests/test_agent_surfaces.py
```

Expected: PASS; source and vendored adapter copies match; active global registration remains contained.

- [ ] **Step 6: Commit**

```bash
git add harness plugins agent-surfaces tools/render_agent_surfaces.py \
  tests/test_agent_surfaces.py
git commit -m "feat: observe proven Claude delegation lifecycle"
```

### Task 3: Build the canary and transactional pre-commit gate

**Files:**
- Create: `scripts/delegation-canary.py`
- Create: `tests/test_delegation_canary.py`
- Modify: `scripts/plugin-update.sh`
- Modify: `tests/test_plugin_update_supervisor_transaction.py`

**Interfaces:**
- Consumes: Task 2 Claude adapter and existing plugin update journal.
- Produces: canary exit 0 only for unmanaged first attempt plus three managed children with distinct structured IDs, overlapping intervals, a peer-message dependency used in a conclusion, honest terminal/abort, and public completion release.
- Produces: updater commits its existing transaction only after installed-surface parity and canary success; global lifecycle registrations remain partial in this change.

- [ ] **Step 1: Add failing canary and rollback controls**

Use a fake Claude process for deterministic contract tests and inject one failure after candidate installation. Assert candidate failure restores prior settings, registry, wrappers, executable modes, and supervisor state byte-for-byte, while leaving native Agent capacity available. Assert sequential children, duplicated IDs, missing peer dependency, unresolved terminal state, or source/cache/hook drift fail the canary.

At fake-canary invocation, assert the original journal exists, is uncommitted,
names the prior disabled generation, and the candidate settings, registry,
wrappers, and supervisor are live. Record helper calls and require exactly one
ordered transaction: begin, candidate activation, installed parity, canary,
then commit on success or rollback/recovery on failure. Assert no second begin,
journal, or backup authority appears. After injected failure, compare bytes,
modes, symlink targets, supervisor marker/plist/load state, and candidate-only
residue, then run recovery again to prove idempotence.

- [ ] **Step 2: Run tests and verify RED**

```bash
pytest -q tests/test_delegation_canary.py \
  tests/test_plugin_update_supervisor_transaction.py tests/test_agent_surfaces.py
```

Expected: missing canary and updater commits before lifecycle proof.

- [ ] **Step 3: Implement the isolated runner and reuse the transaction**

Run Claude with scratch configuration, harness, and Beads repo; accept the installed host version only when its captured capability shape matches. Verify public ledger/completion results, not prompt claims. In `plugin-update.sh`, invoke installed-surface parity plus the canary after candidate wrappers/supervisor are live and before `plugin-update-transaction.py commit`; route any nonzero exit through the existing rollback path. Keep next-invocation journal recovery as the SIGKILL path.

- [ ] **Step 4: Prove isolated canary before registration**

Run the canary directly against the source candidate. Expected: PASS without changing global user settings.

- [ ] **Step 5: Verify GREEN and full suite**

```bash
pytest -q tests/test_delegation_canary.py \
  tests/test_plugin_update_supervisor_transaction.py tests/test_agent_surfaces.py
pytest -q --ignore=pi-adapter
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/delegation-canary.py scripts/plugin-update.sh tests
git commit -m "feat: gate delegation enable on live canary"
```

### Task 4: Land repaired-disabled lifecycle support

- [ ] Push the feature branch, open a PR referencing `escapement-xncx`, carry checks through green, and merge under the repository-declared authorization.
- [ ] From merged `main`, run the isolated source-candidate canary. Expected: unmanaged and managed workflows pass while global lifecycle registration remains partial.
- [ ] Verify the merged source, rendered bundles, and candidate canary artifacts agree. Do not install global registration from this PR.

### Task 5: Enable proven Claude events as a separate reviewable change

**Files:**
- Modify: `agent-surfaces/manifest.json`
- Modify: `tests/test_agent_surfaces.py`
- Generated: `plugins/escapement-claude/hooks/hooks.json`

- [ ] Create a fresh Escapement-managed feature worktree from the merged repaired-disabled `origin/HEAD`.
- [ ] Add a failing surface test requiring only the exact Claude Agent PostToolUse and expectation-aware SessionStart events proven by the canary. Keep Codex unsupported/partial.
- [ ] Run `pytest -q tests/test_agent_surfaces.py` and verify RED because both capabilities remain partial.
- [ ] Mark only the proven Claude events ready, cite the exact canary fixture, render surfaces, and rerun the focused test GREEN.
- [ ] Run `pytest -q --ignore=pi-adapter`, commit, push, open a second PR, carry it through green, and merge.

### Task 6: Install and verify the live outcome

- [ ] From merged `main`, run the transactional plugin updater. The journal must remain armed until the live canary succeeds.
- [ ] Start fresh unmanaged Claude and Codex sessions. Verify no prepared-execution denial, no missing-ledger warning, and no bookkeeping-only Bead/ledger.
- [ ] Run the managed three-agent canary against the selected installed cache. Verify distinct IDs, overlap, peer dependency, terminal/abort cleanup, and public completion release.
- [ ] Inject canary failure and verify byte-exact rollback to the prior installed generation.
- [ ] Verify source, rendered plugin, selected registry cache, active hook registration, wrappers, and supervisor generation agree.
- [ ] Close `escapement-xncx` only after all seven acceptance criteria pass and the merged/deployed outcome is observed.
