# System-wide Codex Hook Fan-out Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce an ordinary Codex Bash tool call to one Escapement-owned PreToolUse process and permanently migrate recognized duplicate global registrations without disturbing unrelated hooks.

**Architecture:** The renderer collapses manifest-declared Codex Bash gates into one in-process dispatcher command while leaving non-Bash matchers alone. A separate provenance-aware migration prunes only dispatcher-declared legacy gates from the global hook file. An installed-runtime verifier checks the actual plugin, global config, concurrency behavior, and public hook output.

**Tech Stack:** Python 3 standard library, pytest, generated JSON plugin surfaces, Bash updater, Codex CLI.

---

### Task 1: Define the dispatcher behavioral oracle

**Files:**
- Create: `tests/test_codex_pretool_dispatch.py`
- Modify: `tests/test_agent_surfaces.py`

- [ ] **Step 1: Write failing aggregation and continuation tests**

Create temporary gate scripts which read the real hook payload and emit deny,
ask, advisory context, and system-message outputs. Execute the not-yet-created
dispatcher with repeated `--gate` arguments and assert:

```python
assert result.returncode == 0
assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
assert "deny reason" in output["hookSpecificOutput"]["permissionDecisionReason"]
assert "first context" in output["hookSpecificOutput"]["additionalContext"]
assert "second context" in output["hookSpecificOutput"]["additionalContext"]
```

Add a broken-gate fixture followed by a valid gate and assert the broken gate is
reported in `systemMessage` while the valid gate's result survives.

- [ ] **Step 2: Write a failing no-subprocess architecture check**

Parse `claude/hooks/codex_pretool_dispatch.py` with `ast` and reject imports or
calls that can recreate the process storm:

```python
forbidden_imports = {"subprocess", "multiprocessing"}
assert not imported_names & forbidden_imports
assert not {"system", "popen", "spawn"} & called_attributes
```

- [ ] **Step 3: Strengthen the rendered-surface oracle**

Change the Codex sole-owner test to require exactly one effective Bash command,
require the dispatcher basename, and compare its declared `--gate` paths against
all ready manifest Bash sources. Keep the existing nonempty and non-Bash positive
controls.

- [ ] **Step 4: Run RED**

Run:

```bash
pytest -q tests/test_codex_pretool_dispatch.py tests/test_agent_surfaces.py
```

Expected: failures because the dispatcher does not exist and the renderer still
emits one process per gate.

### Task 2: Implement one in-process Codex Bash dispatcher

**Files:**
- Create: `claude/hooks/codex_pretool_dispatch.py`
- Modify: `tools/render_agent_surfaces.py`
- Regenerate: `plugins/escapement/hooks/hooks.json`
- Regenerate: `plugins/escapement/claude/hooks/codex_pretool_dispatch.py`

- [ ] **Step 1: Implement bounded input and gate loading**

The dispatcher accepts repeated plugin-relative paths and executes each with the
same serialized payload via `runpy.run_path`, isolated `StringIO` streams, and
restored `sys.stdin`, `sys.stdout`, and `sys.stderr`:

```python
parser.add_argument("--gate", action="append", required=True)
payload = sys.stdin.read()
for relative in args.gate:
    result = run_gate(plugin_root / relative, payload)
```

Reject absolute paths and `..` traversal before execution.

- [ ] **Step 2: Implement public-result aggregation**

Use `deny > ask > allow` precedence. Deduplicate and join reasons, advisory
contexts, and system messages in manifest order. Continue after an individual
gate exception and surface it as a system warning.

- [ ] **Step 3: Consolidate renderer output**

Collect ready Codex `PreToolUse` events with matcher `Bash`; render one command:

```text
python3 -B "${PLUGIN_ROOT}/claude/hooks/codex_pretool_dispatch.py" --gate claude/hooks/gate_one.py --gate claude/hooks/gate_two.py
```

Use the maximum existing Bash timeout, retain non-Bash entries unchanged, and
add the dispatcher source to Codex plugin support files.

- [ ] **Step 4: Render and run GREEN**

Run:

```bash
python3 tools/render_agent_surfaces.py --write
pytest -q tests/test_codex_pretool_dispatch.py tests/test_agent_surfaces.py
```

Expected: all dispatcher and surface tests pass.

### Task 3: Define and implement surgical global-hook migration

**Files:**
- Create: `scripts/prune_codex_hooks.py`
- Create: `tests/test_prune_codex_hooks.py`
- Modify: `scripts/codex-plugin-update.sh`
- Modify: `tests/test_codex_plugin_update.py`

- [ ] **Step 1: Write failing migration tests**

Use a live-shaped global hook fixture containing four legacy Escapement gates,
the Sifi policy, and the independent PR guard. Assert the first four are removed,
the latter two and their metadata remain equal, an external same-basename hook
survives, the input is not mutated, and a second prune is a no-op.

- [ ] **Step 2: Write failing updater integration test**

Run the updater against a temporary `CODEX_HOME` with the fixture. Assert the
live file equals the expected surgical result and a byte-exact backup exists.

- [ ] **Step 3: Run migration RED**

Run:

```bash
pytest -q tests/test_prune_codex_hooks.py tests/test_codex_plugin_update.py
```

Expected: failures because the pruner and updater call do not exist.

- [ ] **Step 4: Implement conservative pruning**

Parse dispatcher `--gate` arguments with `shlex`. Remove a live command only
when its script is directly beneath the current user's `.codex/hooks` or
`.claude/hooks` directory and the declared gate basename matches. Preserve all
other JSON structure, write a timestamped backup, then atomically replace the
file. Support `--dry-run` and no-file no-op behavior.

- [ ] **Step 5: Wire the updater and run GREEN**

Invoke the pruner after authoritative plugin hooks are available and before the
updater's final verification. Run:

```bash
pytest -q tests/test_prune_codex_hooks.py tests/test_codex_plugin_update.py
```

Expected: all migration tests pass.

### Task 4: Add installed-runtime verification

**Files:**
- Create: `scripts/verify_codex_hook_runtime.py`
- Create: `tests/test_verify_codex_hook_runtime.py`

- [ ] **Step 1: Write verifier RED tests**

Build temporary installed-plugin and global-hook trees. Assert the verifier
fails for two Bash commands, legacy overlap, path traversal, or a dispatcher
that loses advisory output; assert it passes for one dispatcher, no overlap,
and concurrent allow probes.

- [ ] **Step 2: Implement the verifier**

Resolve the enabled plugin using `codex plugin list --marketplace escapement
--json` when `--require-installed` is set. Require one Bash dispatcher, zero
legacy overlap, valid declared gate paths, and successful concurrent executions
of the installed dispatcher using representative `pwd` payloads.

- [ ] **Step 3: Run verifier GREEN**

Run:

```bash
pytest -q tests/test_verify_codex_hook_runtime.py
```

Expected: all verifier tests pass.

### Task 5: Challenge, verify, land, and deploy

**Files:**
- Modify generated surfaces only if `--check` reports drift.

- [ ] **Step 1: Run the mutation challenger before production implementation**

Require it to test these bad implementations: timeout increase, delete-all
migration, subprocess wrapper, first-result-only aggregation, and basename-only
pruning. Strengthen tests for any surviving mutant before proceeding.

- [ ] **Step 2: Run the focused and full repository checks**

```bash
pytest -q tests/test_codex_pretool_dispatch.py tests/test_prune_codex_hooks.py tests/test_verify_codex_hook_runtime.py tests/test_agent_surfaces.py tests/test_codex_plugin_update.py
python3 tools/render_agent_surfaces.py --check
pytest -q
git diff --check
```

- [ ] **Step 3: Commit, push, and open the PR**

```bash
git add claude/hooks/codex_pretool_dispatch.py scripts/prune_codex_hooks.py scripts/verify_codex_hook_runtime.py tools/render_agent_surfaces.py scripts/codex-plugin-update.sh tests plugins docs/superpowers/plans/2026-08-20-system-wide-codex-hook-fanout.md
git commit -m "fix: collapse Codex hook process storms"
git push -u origin fix/system-wide-codex-hook-fanout
gh pr create --fill
```

- [ ] **Step 4: Carry CI and review through merge**

Repair causal failures, enable or use the declared merge path, and confirm the PR
merge commit is reachable from the default branch.

- [ ] **Step 5: Refresh and verify the machine-wide installation**

From clean merged `main`, run:

```bash
./scripts/codex-plugin-update.sh
python3 scripts/verify_codex_hook_runtime.py --codex-home /Users/alexandervyhmeister/.codex --require-installed
codex exec --json --skip-git-repo-check 'Run exactly one shell command: pwd. Then answer with only the output.'
```

Inspect fresh Codex hook telemetry for one Escapement Bash hook start and no
PreToolUse failure. Confirm the unrelated global Sifi and PR hooks remain.

- [ ] **Step 6: Outcome verification and Beads closure**

Dispatch an independent outcome verifier over the installed state. Only after it
confirms the process count, preserved hooks, concurrency probe, and fresh Codex
command, run `bd close escapement-vjn1` and verify clean repository/worktree
status.
