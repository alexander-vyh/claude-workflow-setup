# Claude Plugin Cutover Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every supported Claude install/update path converge to the native Escapement plugin so the legacy pinned installer cannot roll back an attended cutover.

**Architecture:** `scripts/plugin-update.sh` is the authority for refreshing the Claude plugin, preserving user state, migrating recognized legacy links, and installing stable harness wrappers. `INSTALL.sh` delegates plugin-owned work to that updater and retains only auxiliary assets that the plugin cannot ship: `claude/bin` and Beads formulas/status. Migration is fail-closed when the user-scope installed plugin cannot be resolved and preserves unknown user-owned files.

**Tech Stack:** Bash, Python 3 for JSON parsing, jq, Claude Code plugin CLI, Beads, pytest.

## Global Constraints

- The user-scope `escapement@escapement` `installPath` in `~/.claude/plugins/installed_plugins.json` is the authoritative deployed plugin.
- Preserve plugin enabled state, the configured model, personal settings hooks, personal hook files, and writable harness state.
- Do not replace unknown real files or symlinks that do not resolve into a recognized legacy Escapement checkout.
- The native plugin is the sole owner of plugin-shipped skills, agents, commands, hooks, bootstrap, and harness code.
- `INSTALL.sh` may continue to deploy only `claude/bin`, Beads formulas, and `beads/mol-status.sh`.
- A missing or invalid authoritative plugin installation fails closed before legacy workflow links are removed.
- Final verification must use a fresh Claude process because an existing process may retain a stale versioned plugin root.

---

### Task 1: Define the post-cutover updater oracle

**Files:**
- Modify: `tests/test_plugin_update.sh`
- Modify: `tests/test_install_pinned.sh`
- Modify: `tests/test_install_pinned_drift.sh`

**Interfaces:**
- Consumes: public CLI contracts `scripts/plugin-update.sh` and `INSTALL.sh --update`.
- Produces: an isolated-home regression fixture proving durable plugin ownership and auxiliary preservation.

- [ ] **Step 1: Replace the pre-cutover preservation assertion**

Build a fixture whose installed plugin contains representative `harness/bin`,
`harness/schemas`, skill, agent, command, and hook files. Seed matching
`~/.claude` links into `.escapement-pinned`, plus personal hooks and harness
runtime state.

- [ ] **Step 2: Assert public post-update behavior**

After `scripts/plugin-update.sh`, assert:

- `harness/bin` and `harness/schemas` resolve to the registry-selected plugin;
- recognized pin-owned workflow links are absent;
- personal hooks and unknown files are unchanged;
- plugin enabled/model state and harness runtime data are unchanged;
- vendored harness executables are executable.

- [ ] **Step 3: Add missing-plugin negative control**

Run the updater with no valid user-scope `installPath`. Assert non-zero exit and
that the original legacy links remain untouched.

- [ ] **Step 4: Assert the legacy installer cannot roll back cutover**

Run `INSTALL.sh --update` after the plugin updater. Assert it refreshes only the
auxiliary pin-owned links (`~/.claude/bin`, Beads formulas/status), while the
stable harness wrappers still target the installed plugin and no plugin-owned
legacy links reappear.

- [ ] **Step 5: Preserve legacy pin drift coverage through an auxiliary sentinel**

Change the effective pin-directory and dirty-pin fixtures to use
`~/.claude/bin` as the pinned deployment sentinel. Keep the existing
wrong-directory and fail-loudly controls.

- [ ] **Step 6: Run the focused tests and verify RED**

Run:

```bash
bash tests/test_plugin_update.sh
bash tests/test_install_pinned.sh
bash tests/test_install_pinned_drift.sh
```

Expected: the updater test fails because it preserves the legacy harness link,
and the installer test fails because `INSTALL.sh --update` recreates
plugin-owned links.

- [ ] **Step 7: Submit the tests to the mutation challenger**

Challenge at least these bad implementations:

1. one-time `ln -sfn` of `harness/bin`;
2. fix `plugin-update.sh` but leave `INSTALL.sh` rollback intact;
3. remove every user hook indiscriminately;
4. select the newest cache directory instead of the registry user-scope entry;
5. migrate links before validating the installed plugin.

Strengthen the tests until each bad implementation is rejected.

### Task 2: Make plugin update the sole workflow-surface owner

**Files:**
- Modify: `scripts/plugin-update.sh`
- Test: `tests/test_plugin_update.sh`

**Interfaces:**
- Consumes: Claude CLI, `settings.json`, and `installed_plugins.json`.
- Produces: converged plugin state and stable `~/.claude/harness/{bin,schemas}` wrappers.

- [ ] **Step 1: Resolve only the valid user-scope plugin**

Parse `installed_plugins.json` for the user-scope `escapement@escapement`
entry. Reject missing, malformed, nonexistent, or incomplete plugin roots; do
not fall back to an arbitrary cache sibling.

- [ ] **Step 2: Install stable harness wrappers safely**

Replace recognized legacy or prior-plugin symlinks with links to
`$installPath/harness/bin` and `$installPath/harness/schemas`. Refuse to replace
an unknown real file/directory or unrelated symlink.

- [ ] **Step 3: Remove recognized legacy global duplicates**

For plugin-shipped skills, agents, commands, and hooks, remove only links whose
resolved targets are under recognized legacy Escapement pinned checkouts or the
repository working tree. Remove the recognized legacy
`~/.claude/project-bootstrap.sh` link. Preserve unrelated and real user files.

- [ ] **Step 4: Keep settings migration and state restoration**

Retain enabled/model preservation, executable-bit repair, and
`prune_settings_hooks.py` convergence.

- [ ] **Step 5: Run the updater test and verify GREEN**

```bash
bash tests/test_plugin_update.sh
pytest -q tests/test_install_settings_prune.py
```

Expected: all assertions pass.

### Task 3: Retire plugin-owned work from the legacy installer

**Files:**
- Modify: `INSTALL.sh`
- Test: `tests/test_install_pinned.sh`
- Test: `tests/test_install_pinned_drift.sh`

**Interfaces:**
- Consumes: `scripts/plugin-update.sh`.
- Produces: an auxiliary-only pinned deployment that cannot restore plugin-owned Claude surfaces.

- [ ] **Step 1: Shrink the symlink plan**

Remove plugin-owned skills, agents, commands, hooks, harness code, and
`project-bootstrap.sh` from `PLAN`. Keep `claude/bin`, Beads formulas, and
`beads/mol-status.sh`.

- [ ] **Step 2: Replace the legacy sentinel**

Resolve the effective pin checkout from the `~/.claude/bin` auxiliary symlink
instead of a hook symlink.

- [ ] **Step 3: Delegate plugin convergence**

For install/update, invoke `scripts/plugin-update.sh` before installing
auxiliary links. If plugin convergence fails, abort before modifying the
auxiliary plan.

- [ ] **Step 4: Remove obsolete hook wiring instructions**

Delete the settings-era Stop wiring warning and next-step copy that implies the
legacy installer owns plugin hooks.

- [ ] **Step 5: Run installer tests and verify GREEN**

```bash
bash tests/test_install_pinned.sh
bash tests/test_install_pinned_drift.sh
pytest -q tests/test_install_settings_prune.py
```

Expected: all assertions pass, including plugin-update followed by installer-update.

### Task 4: Update operator documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the final updater/installer behavior.
- Produces: one unambiguous Claude install and update procedure.

- [ ] **Step 1: Document native Claude installation**

Replace the pinned-workflow install instructions with Claude marketplace/plugin
installation followed by `scripts/plugin-update.sh`.

- [ ] **Step 2: Explain the compatibility installer**

Document that `INSTALL.sh` is retained only for non-plugin auxiliary assets and
delegates Claude workflow ownership to the plugin updater.

- [ ] **Step 3: Remove obsolete manual settings merge and `--dev` hook claims**

State that plugin-registered hooks are not copied into `settings.json`, and that
a fresh Claude process is required after an upgrade.

### Task 5: Verify, review, land, and deploy

**Files:**
- Modify only if verification exposes a defect.

**Interfaces:**
- Consumes: committed implementation and tests.
- Produces: merged main and verified local Claude cutover.

- [ ] **Step 1: Run focused and repository-wide checks**

```bash
bash tests/test_plugin_update.sh
bash tests/test_install_pinned.sh
bash tests/test_install_pinned_drift.sh
pytest -q
python3 tools/render_agent_surfaces.py --check
git status --short
```

- [ ] **Step 2: Review against the approved design and oracle**

Confirm the known fragile implementation fails the tests and inspect the diff
for destructive handling of unknown user files.

- [ ] **Step 3: Commit, push, open a PR, and land on green**

Use a feature branch and a pull request. Do not deploy an ephemeral branch.

- [ ] **Step 4: Run the merged updater locally**

From merged `main`, run:

```bash
./scripts/plugin-update.sh
./INSTALL.sh --update
```

- [ ] **Step 5: Verify the actual live state**

Read the installed registry entry and live filesystem:

- the plugin is installed and enabled;
- `harness/bin` and `harness/schemas` target its `installPath`;
- no plugin-owned `~/.claude` link targets `.escapement-pinned*` or `.cws-pinned`;
- personal hooks, harness state, `~/.claude/bin`, and Beads assets remain;
- the harness `verify` and `init_contract.py` commands execute.

- [ ] **Step 6: Verify a fresh Claude process**

Start a new Claude process, exercise representative SessionStart/PreToolUse
behavior, and confirm one effective Escapement hook execution rather than zero
or duplicates.

- [ ] **Step 7: Update/close Beads**

Close `escapement-841` only after merged-main deployment and live outcome
verification; update dependent cutover beads with the observed result.
