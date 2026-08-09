# Delegated Work Liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure delegated Escapement work continues or is automatically
reconciled until its parent Beads outcome is verified, without requiring a user
message to restart a stalled coordinator.

**Architecture:** Beads is the durable desired-work graph. A per-parent
`executions.json` records only native attempts and their leases. The installed
`wakeup_waker.py --fire` process is a level-triggered reconciler whose health is
recorded only after a successful useful pass. Claude contributes Stop and Agent
events; Codex contributes supported PreToolUse/SessionStart events and relies on
the external reconciler where no final-response hook is effective.

**Tech Stack:** Python 3 standard library, pytest, Beads 1.1.x CLI, generated
Claude/Codex plugin surfaces, macOS launchd, Bash deployment tests.

## Global Constraints

- Beads owns work identity, dependencies, claims, and verified closure;
  Escapement owns attempts, deadlines, wake/recovery, and completion policy.
- No Temporal, Redis, Postgres, SQS, or other service dependency.
- Local-model classification is optional and cannot renew a lease or suppress a
  deterministic action.
- Runtime files require trusted ownership/permissions, exclusive locking,
  same-directory temporary writes, and atomic replacement.
- Deadline expiry means reconcile; it never proves execution terminated and
  never authorizes blind replay across an unknown mutation boundary.
- Exactly-once spawn is not assumed. Recovery is at-least-once with expiring
  claims, generation fencing, and independently checked result application.
- Claude/Codex behavior is marked supported only when a host-specific payload
  fixture and effective installed smoke prove the surface.
- Do not add more responsibility to `harness/bin/stop_hook.py` (already above
  1,000 lines); extract task-state and execution-state logic into sibling modules.
- Source, generated plugin trees, pinned installed code, launchd state, and the
  real user workflow are separate verification boundaries.

---

### Task 1: Reject Parent-Open Queue Drain

**Files:**

- Create: `harness/bin/beads_task_state.py`
- Modify: `harness/bin/stop_hook.py`
- Modify: `harness/tests/test_task_mode_queue.py`
- Create: `harness/tests/test_parent_outcome_gate.py`

**Interfaces:**

- Produces: `check_task_scope(session_mode: dict, run_bd=None) -> tuple[str, str]`
- `run_bd(args: list[str]) -> list[dict] | None` remains injectable and receives
  exact argument lists such as `['show', 'escapement-e3ai']`, `['ready']`, and
  `['blocked', '--parent', 'escapement-e3ai']`.
- `stop_hook._check_task_mode_queue` remains as a compatibility alias while
  delegating to the sibling module.

- [ ] **Step 1: Write the design-session regression test**

  Add a literal fixture where `bd show` returns the parent as `in_progress`,
  `bd ready --parent` returns `[]`, and `bd blocked --parent` returns `[]`:

  ```python
  def test_closed_children_do_not_complete_in_progress_parent():
      responses = {
          ("show", "escapement-e3ai"): [{"id": "escapement-e3ai", "status": "in_progress"}],
          ("ready", "--parent", "escapement-e3ai"): [],
          ("blocked", "--parent", "escapement-e3ai"): [],
      }
      decision = check_task_scope(_mode("escapement-e3ai"), responses.get)
      assert decision == ("block", "parent_outcome_unresolved")
  ```

  The production change this catches is omission of the root `bd show` check.

- [ ] **Step 2: Add positive and failure controls**

  Assert a closed root plus empty descendants returns `queue_drained`; missing,
  malformed, open, blocked, deferred, and failed `bd show` all block. Preserve
  the existing non-Beads-cwd degradation control.

- [ ] **Step 3: Run RED**

  Run:

  ```bash
  python -m pytest harness/tests/test_parent_outcome_gate.py harness/tests/test_task_mode_queue.py -q
  ```

  Expected: the new parent-open and missing-root cases fail because the current
  code checks descendants only.

- [ ] **Step 4: Extract and implement the root-aware check**

  Move the queue-query responsibility from `stop_hook.py` into
  `beads_task_state.py`. Resolve `root_id = parent_id or task_id`; require
  `status == 'closed'` before returning `queue_drained`; preserve universal
  user-release/wakeup handling outside this function.

- [ ] **Step 5: Run GREEN and regression tests**

  ```bash
  python -m pytest harness/tests/test_parent_outcome_gate.py harness/tests/test_task_mode_queue.py harness/tests/test_implicit_queue_scope.py harness/tests/test_worktree_stop_degradation.py -q
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add harness/bin/beads_task_state.py harness/bin/stop_hook.py harness/tests/test_parent_outcome_gate.py harness/tests/test_task_mode_queue.py
  git commit -m "fix: keep task mode active until parent outcome closes"
  ```

### Task 2: Build the Execution-Attempt State Machine

**Files:**

- Create: `harness/bin/execution_ledger.py`
- Create: `harness/schemas/executions.schema.json`
- Create: `harness/tests/test_execution_ledger.py`
- Modify: `harness/tests/test_trusted_source.py`

**Interfaces:**

- `new_ledger(parent_session_id: str) -> dict`
- `register_execution(ledger: dict, event: dict, now: datetime) -> dict`
- `apply_event(ledger: dict, event: dict, now: datetime) -> dict`
- `reconcile_deadlines(ledger: dict, now: datetime) -> list[dict]`
- `claim_recovery(ledger: dict, execution_id: str, now: datetime,
  owner: str, ttl_seconds: int) -> dict | None`
- `claim_result_application(...) -> dict | None`
- `load_trusted(path: Path, expected_parent: str) -> dict | None`
- `mutate_atomic(path: Path, mutation: Callable[[dict], dict]) -> dict`

- [ ] **Step 1: Write literal transition tests**

  Cover queued registration, native-child binding, accepted completed activity,
  non-renewing tool start/poll/LLM annotation, terminal event identity, duplicate
  terminal events, and old-generation terminal evidence. Expected timestamps and
  states are hand-authored literals rather than computed with production helpers.

- [ ] **Step 2: Write deadline and fencing controls**

  Prove start, idle, and hard deadlines independently set
  `reconcile_due=start|idle|hard` without changing `state` to terminal. Prove a
  live recovery claim prevents another claim, expiry advances generation, and a
  stale generation cannot claim application or mutate the active result.

- [ ] **Step 3: Write storage/security controls**

  Prove cross-session, malformed, world-writable, non-dictionary, and invalid
  enum inputs are unresolved; concurrent mutations serialize; a failed write
  leaves the previous valid JSON intact.

- [ ] **Step 4: Run RED**

  ```bash
  python -m pytest harness/tests/test_execution_ledger.py harness/tests/test_trusted_source.py -q
  ```

  Expected: import/contract failures because the ledger module and schema do not
  exist.

- [ ] **Step 5: Implement the smallest pure state machine and atomic store**

  Use UTC ISO-8601 timestamps, `fcntl.flock`, a same-directory `NamedTemporaryFile`
  or explicit `.tmp`, `fsync`, and `os.replace`. Reject unknown event kinds and
  invalid attempt/generation identities rather than silently ignoring them.

- [ ] **Step 6: Run GREEN and schema checks**

  ```bash
  python -m pytest harness/tests/test_execution_ledger.py harness/tests/test_trusted_source.py -q
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add harness/bin/execution_ledger.py harness/schemas/executions.schema.json harness/tests/test_execution_ledger.py harness/tests/test_trusted_source.py
  git commit -m "feat: persist fenced delegated execution attempts"
  ```

### Task 3: Register and Reconcile Host Delegations

**Files:**

- Create: `harness/bin/delegation_hook.py`
- Create: `harness/bin/execution_reconcile.py`
- Create: `harness/tests/test_delegation_hook.py`
- Create: `harness/tests/test_execution_reconcile.py`
- Modify: `agent-surfaces/manifest.json`
- Modify: `tools/render_agent_surfaces.py`
- Modify: `tests/test_agent_surfaces.py`

**Interfaces:**

- `delegation_hook.find_prepared_execution(tool_input: dict, ledger: dict) -> dict | None`
- `delegation_hook.pre_tool(payload: dict, run_bd, ledger_path) -> dict`
- `delegation_hook.post_tool(payload: dict, ledger_path) -> dict`
- `execution_reconcile.reconcile_session(payload: dict, run_bd, ledger_loader,
  now: datetime) -> dict`
- Hook stdout uses host-native `additionalContext`/permission decision formats;
  the pure functions return normalized dictionaries for fixture testing.

- [ ] **Step 1: Write Claude dispatch fixture tests**

  Use a complete Agent PreToolUse payload containing `name`, `description`,
  `prompt`, and `run_in_background`. Before the call, prepare an execution through
  the ledger CLI with explicit `bead_id`, session, host, and matching agent name.
  The hook must not scrape IDs from free-form prompts. Missing prepared attempts
  or missing/closed/foreign beads deny dispatch with an exact preparation command;
  a valid attempt is marked dispatched before native execution.

- [ ] **Step 2: Write PostToolUse binding and duplicate controls**

  Capture the documented current Agent result fixture. Bind the native child ID
  once; duplicate delivery is idempotent; an unparseable result leaves the
  attempt queued and therefore subject to its start deadline.

- [ ] **Step 3: Write SessionStart reconciliation controls**

  An unresolved parent or due execution emits actionable continuation context.
  A closed parent with no due attempts emits nothing. Missing Beads/ledger state
  emits unresolved context. Include a Codex-specific SessionStart fixture and do
  not claim Stop support.

- [ ] **Step 4: Run RED**

  ```bash
  python -m pytest harness/tests/test_delegation_hook.py harness/tests/test_execution_reconcile.py tests/test_agent_surfaces.py -q
  ```

- [ ] **Step 5: Implement adapters and renderer packaging**

  Register Claude PreToolUse/PostToolUse `Agent` hooks. Register the same
  `execution_reconcile.py` SessionStart source for Claude and Codex. Extend the
  renderer’s plugin-relative command rewriting and Codex support-file bundle so
  it never calls through `~/.claude`.

- [ ] **Step 6: Render and run GREEN**

  ```bash
  python3 tools/render_agent_surfaces.py
  python -m pytest harness/tests/test_delegation_hook.py harness/tests/test_execution_reconcile.py tests/test_agent_surfaces.py -q
  python3 tools/render_agent_surfaces.py --check
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add harness/bin/delegation_hook.py harness/bin/execution_reconcile.py harness/tests/test_delegation_hook.py harness/tests/test_execution_reconcile.py agent-surfaces/manifest.json tools/render_agent_surfaces.py tests/test_agent_surfaces.py plugins/escapement plugins/escapement-claude .claude-plugin .codex AGENTS.md CLAUDE.md
  git commit -m "feat: bind host delegations to Beads work"
  ```

### Task 4: Add the Level-Triggered Supervisor and Recovery Fencing

**Files:**

- Create: `harness/bin/execution_supervisor.py`
- Modify: `harness/bin/wakeup_waker.py`
- Modify: `harness/bin/wakeup_dispatch.py`
- Create: `harness/tests/test_execution_supervisor.py`
- Modify: `harness/tests/test_wakeup_waker.py`

**Interfaces:**

- `execution_supervisor.plan_thread(thread_dir: Path, now: datetime,
  native_status, run_bd) -> dict`
- `execution_supervisor.reconcile_all(threads_root: Path, now: datetime,
  owner: str, spawn: Callable) -> dict`
- Health JSON contains `reconcile_started_at`,
  `last_successful_reconcile_at`, `completed_generation`, `installation_id`, and
  counts; only the last four fields authorize a bounded pause.
- `_spawn` accepts `host=claude|codex`; Codex uses
  `codex exec resume <session-id> <prompt>`, Claude uses the existing resume or
  fresh handoff path.

- [ ] **Step 1: Write the false-health regression**

  Simulate a successful process tick whose second thread scan raises. Assert no
  new `last_successful_reconcile_at` or completed generation is written. This
  rejects health-before-useful-work.

- [ ] **Step 2: Write recovery crash-window controls**

  Simulate: claim persisted, crash before spawn; immediate second tick cannot
  spawn; post-expiry tick advances generation and spawns; late generation-one
  completion cannot apply. Prove spawn failure leaves a bounded retryable claim
  and budget exhaustion creates one durable escalation.

- [ ] **Step 3: Write restart and host-spawn controls**

  A fresh process with no memory scans current level state and handles all due
  attempts. Assert exact argv lists for Claude and Codex without executing real
  CLIs. Missing/untrusted state is reported unresolved, never treated empty.

- [ ] **Step 4: Run RED**

  ```bash
  python -m pytest harness/tests/test_execution_supervisor.py harness/tests/test_wakeup_waker.py harness/tests/test_wakeup_dispatch.py -q
  ```

- [ ] **Step 5: Implement supervisor and integrate `wakeup_waker.py --fire`**

  Keep planning pure and dependency-injected. The CLI performs locked writes and
  spawns only claimed current generations. Preserve existing scheduled-check
  semantics and dry-run safety.

- [ ] **Step 6: Run GREEN**

  ```bash
  python -m pytest harness/tests/test_execution_supervisor.py harness/tests/test_wakeup_waker.py harness/tests/test_wakeup_dispatch.py -q
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add harness/bin/execution_supervisor.py harness/bin/wakeup_waker.py harness/bin/wakeup_dispatch.py harness/tests/test_execution_supervisor.py harness/tests/test_wakeup_waker.py
  git commit -m "feat: reconcile expired execution leases independently"
  ```

### Task 5: Gate Pauses on Root Outcome, Execution State, and Supervisor Proof

**Files:**

- Create: `harness/bin/supervisor_health.py`
- Modify: `harness/bin/would_block_stop.py`
- Modify: `harness/bin/stop_hook.py`
- Create: `harness/tests/test_execution_stop_gate.py`
- Modify: `harness/tests/test_winddown_stop_integration.py`
- Modify: `harness/tests/test_winddown_wakeup_backstop.py`

**Interfaces:**

- `supervisor_health.is_fresh_successful(record: dict, now: datetime,
  max_age_seconds: int) -> bool`
- `execution_stop_decision(root_status: str, ledger: dict | None,
  health: dict | None, scheduled: list, now: datetime) -> tuple[str, str]`

- [ ] **Step 1: Write the two incident replay tests**

  Replay (a) queue drained plus two non-terminal children and no healthy
  supervisor; (b) both child beads closed plus parent `in_progress`. Both block.
  The positive control closes the parent and terminals all attempts.

- [ ] **Step 2: Write wakeup/supervisor controls**

  Future wakeup plus absent, stale, pre-scan-only, wrong-installation, or malformed
  health blocks. Future wakeup plus a fresh successful generation and a running
  attempt within all deadlines permits a bounded pause. Hard-overdue work blocks
  even if assistant chatter is recent.

- [ ] **Step 3: Run RED**

  ```bash
  python -m pytest harness/tests/test_execution_stop_gate.py harness/tests/test_winddown_stop_integration.py harness/tests/test_winddown_wakeup_backstop.py -q
  ```

- [ ] **Step 4: Implement the pure decision and thin Stop wiring**

  Keep payload parsing and logging in `stop_hook.py`; put freshness and execution
  decisions in sibling modules. Preserve explicit user release as the auditable
  override. Codex remains SessionStart/supervisor-only until a real Stop smoke
  succeeds.

- [ ] **Step 5: Run GREEN and the whole harness suite**

  ```bash
  python -m pytest harness/tests -q
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add harness/bin/supervisor_health.py harness/bin/would_block_stop.py harness/bin/stop_hook.py harness/tests/test_execution_stop_gate.py harness/tests/test_winddown_stop_integration.py harness/tests/test_winddown_wakeup_backstop.py
  git commit -m "fix: require durable reconciliation before delegated pause"
  ```

### Task 6: Install the Supervisor and Repair Optional Local Judge Authentication

**Files:**

- Create: `scripts/continuation-supervisor-install.sh`
- Modify: `scripts/plugin-update.sh`
- Create: `tests/test_continuation_supervisor_install.sh`
- Modify: `tests/test_install_pinned.sh`
- Modify: `claude/hooks/_local_judge_client.py`
- Modify: `claude/hooks/tests/test_local_judge_client.py`

**Interfaces:**

- Installer creates/reloads
  `~/Library/LaunchAgents/com.escapement.continuation-supervisor.plist` with
  `wakeup_waker.py --fire`, `RunAtLoad`, and a bounded `StartInterval`.
- `configured_auth_header() -> str | None` reads
  `ESCAPEMENT_LOCAL_JUDGE_API_KEY` or a mode-0600 file named by
  `ESCAPEMENT_LOCAL_JUDGE_API_KEY_FILE`; environment value wins.

- [ ] **Step 1: Write isolated launchd installation tests**

  Under a throwaway HOME and stub `launchctl`, prove install, idempotent reload,
  `--fire` presence, stable wrapper target, log paths, uninstall, and failure
  before deployment when the waker is absent. Assert `plugin-update.sh --dry-run`
  reports but does not load the job.

- [ ] **Step 2: Write local-judge authentication controls**

  Inspect the actual `urllib.request.Request` headers. Prove bearer header from
  env and mode-0600 file, env precedence, no header by default, and fail-closed
  omission for group/world-readable key files. Health remains `unavailable` on
  HTTP 401 without affecting deterministic supervisor behavior.

- [ ] **Step 3: Run RED**

  ```bash
  bash tests/test_continuation_supervisor_install.sh
  python -m pytest claude/hooks/tests/test_local_judge_client.py -q
  ```

- [ ] **Step 4: Implement installer and auth header**

  Generate plist content with resolved absolute paths and no secret arguments.
  Call the installer from `plugin-update.sh` only after plugin validation and
  stable harness wrappers succeed. Preserve non-macOS portability with an
  explicit unsupported/no-op result rather than a false installed claim.

- [ ] **Step 5: Run GREEN and installation regression suite**

  ```bash
  bash tests/test_continuation_supervisor_install.sh
  bash tests/test_install_pinned.sh
  python -m pytest claude/hooks/tests/test_local_judge_client.py -q
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add scripts/continuation-supervisor-install.sh scripts/plugin-update.sh tests/test_continuation_supervisor_install.sh tests/test_install_pinned.sh claude/hooks/_local_judge_client.py claude/hooks/tests/test_local_judge_client.py
  git commit -m "feat: install liveness supervisor with optional judge auth"
  ```

### Task 7: Distribute, Verify, Land, and Observe the Real Outcome

**Files:**

- Modify: `harness/README.md`
- Modify: `docs/superpowers/specs/2026-08-09-delegated-work-liveness-design.md`
- Modify: generated plugin files from `tools/render_agent_surfaces.py`
- Modify: Beads state for `escapement-e3ai` and reconciled duplicate tasks only
  after their outcomes are verified.

**Interfaces:**

- Source verification command:
  `python -m pytest claude/hooks/tests harness/tests tests -q -rs`
- Surface verification command:
  `python3 tools/render_agent_surfaces.py --check`
- Installed supervisor verification:
  `launchctl print gui/$(id -u)/com.escapement.continuation-supervisor`

- [ ] **Step 1: Run mutation challenge**

  Demonstrate tests reject descendant-only drain, child-terminal auto-close,
  mtime/noisy heartbeat, health-before-reconcile, unfenced duplicate recovery,
  stale-generation application, future-file-only wake proof, and missing-state
  completion.

- [ ] **Step 2: Run full source verification**

  ```bash
  python -m pytest claude/hooks/tests harness/tests tests -q -rs
  python3 tools/render_agent_surfaces.py --check
  bash tests/test_continuation_supervisor_install.sh
  bash tests/test_install_pinned.sh
  ```

- [ ] **Step 3: Dispatch independent spec and code-quality reviews**

  Review every requirement in the accepted design against actual code and test
  evidence. Fix all critical/important findings and rerun the affected plus full
  suites.

- [ ] **Step 4: Commit final generated/docs parity**

  ```bash
  git add harness/README.md docs/superpowers/specs/2026-08-09-delegated-work-liveness-design.md plugins/escapement plugins/escapement-claude .claude-plugin .codex AGENTS.md CLAUDE.md
  git commit -m "docs: document verified delegated-work recovery"
  ```

- [ ] **Step 5: Push, open the PR, and carry it through merge**

  Push `feat/delegated-work-liveness`, open a PR referencing
  `escapement-e3ai`, enable/observe auto-merge, repair any CI failures, and
  verify the merge commit on the declared default branch.

- [ ] **Step 6: Deploy both supported host surfaces**

  Run the existing Claude plugin updater and Codex plugin updater from merged
  main. Verify the installed source hashes match the merge and the launchd plist
  invokes the installed `wakeup_waker.py --fire`.

- [ ] **Step 7: Exercise installed behavior**

  Use disposable state and short test deadlines to prove:

  1. an `in_progress` parent with closed children is surfaced for continuation;
  2. a registered child that never starts triggers one fenced recovery without
     a user message;
  3. a failed reconciliation does not refresh supervisor health;
  4. duplicate/late completion is not applied twice;
  5. local judge auth works when configured and deterministic fallback works
     when the endpoint is unavailable.

- [ ] **Step 8: Reconcile and close Beads only after outcome verification**

  Append the merge/deployment/live evidence to `escapement-e3ai`, reconcile
  `escapement-uf5`, `escapement-u7aq`, and `escapement-2waa` against their actual
  remaining outcomes, and close only work proven complete.
