# Escapement-Owned Session Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove automatic `bd prime` policy injection and replace it with a small Escapement-owned context that keeps Beads limited to task state while enforcing each repository's Escapement landing contract.

**Architecture:** A host-neutral Python lifecycle hook emits the minimal tracker contract and reports the existing `.escapement/repo.json` outcome. The shared manifest wires that hook for Claude and Codex at SessionStart and PreCompact, and the renderer vendors it into both plugins. Generated onboarding text names Escapement as workflow authority and never invokes `bd prime`.

**Tech Stack:** Python 3, pytest, JSON hook payloads, `agent-surfaces/manifest.json`, `tools/render_agent_surfaces.py`.

## Global Constraints

- Beads supplies task state only; it cannot supply Git, landing, completion, memory, or agent-behavior policy.
- The undeclared or malformed repository default is `pr-opened` with auto-merge disabled.
- The installed Claude and Codex plugin artifacts are the delivery surface.
- Existing `harness/bin/repo_outcome.py` remains the outcome-policy authority.
- Tests must fail any implementation that still invokes `bd prime` and merely appends stronger Escapement prose.

---

### Task 1: Behavioral Oracle for Escapement-Owned Context

**Files:**
- Create: `claude/hooks/tests/test_escapement_session_context.py`
- Create: `claude/hooks/escapement_session_context.py`

**Interfaces:**
- Consumes: lifecycle JSON on stdin with `hook_event_name` or `hookEventName` and `cwd`.
- Produces: JSON with `hookSpecificOutput.hookEventName` and `additionalContext`; exit code `0`.

- [x] **Step 1: Write failing public-hook tests**

Cover the hook as a subprocess, not through private helpers:

```python
def test_missing_policy_defaults_to_branch_push_pr_and_offers_configuration(tmp_path):
    result = run_hook(tmp_path, {"hook_event_name": "SessionStart", "cwd": str(tmp_path)})
    context = emitted_context(result)
    assert "feature branch" in context
    assert "push" in context
    assert "pull request" in context
    assert "committed" in context
    assert "merged-and-deployed" in context
    assert "bd prime" not in context
    assert "stealth mode" not in context


def test_hostile_beads_global_config_cannot_change_context(tmp_path):
    result = run_hook(
        tmp_path,
        {"hook_event_name": "SessionStart", "cwd": str(tmp_path)},
        env={"XDG_CONFIG_HOME": str(write_hostile_beads_config(tmp_path))},
    )
    context = emitted_context(result)
    assert "feature branch" in context
    assert "pull request" in context
    assert "no git operations" not in context.lower()


def test_configured_outcome_is_reported_without_reimplementing_authorization(tmp_path):
    write_repo_policy(tmp_path, outcome="merged-and-deployed", auto_merge=True)
    context = emitted_context(
        run_hook(tmp_path, {"hook_event_name": "SessionStart", "cwd": str(tmp_path)})
    )
    assert "merged-and-deployed" in context
    assert "auto_merge_on_green=true" in context
    assert "existing Escapement authorization gates" in context
```

Also cover malformed JSON failing closed to `pr-opened`, PreCompact output,
non-lifecycle silence, and the positive tracker-command set.

- [x] **Step 2: Run tests and verify the expected RED**

Run:

```bash
pytest -q claude/hooks/tests/test_escapement_session_context.py
```

Expected: collection or subprocess failure because
`claude/hooks/escapement_session_context.py` does not exist.

- [x] **Step 3: Mutation-challenge the proposed oracle**

Require the independent challenger to evaluate at least:

1. Keep `bd prime`, then append Escapement prose.
2. Remove only the current `no-git-ops` phrases.
3. Fix Claude but leave Codex on `bd prime`.
4. Hardcode `pr-opened` and ignore configured outcomes.
5. Emit no tracker commands, making the result policy-safe but unusable.

Strengthen the tests until each bad implementation fails.

- [x] **Step 4: Implement the minimal lifecycle hook**

The hook must:

```python
TRACKER_COMMANDS = (
    "bd ready",
    "bd show <id>",
    "bd update <id> --claim",
    "bd close <id>",
    "bd worktree create",
)
```

It reads `.escapement/repo.json` only to report the outcome declaration. Missing,
malformed, or invalid data becomes `pr-opened` and `auto_merge_on_green=false`.
It never executes `bd`, reads Beads configuration, or imports Beads memories.

- [x] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
pytest -q claude/hooks/tests/test_escapement_session_context.py
ruff check claude/hooks/escapement_session_context.py claude/hooks/tests/test_escapement_session_context.py
```

Expected: all tests pass and Ruff reports no errors.

### Task 2: Replace `bd prime` Across Generated Surfaces

**Files:**
- Modify: `agent-surfaces/manifest.json`
- Modify: `agent-surfaces/onboarding/beads.md`
- Modify: `tools/render_agent_surfaces.py`
- Modify: `tests/test_agent_surfaces.py`
- Regenerate: `AGENTS.md`
- Regenerate: `CLAUDE.md`
- Regenerate: `.codex/hooks.json`
- Regenerate: `plugins/escapement/**`
- Regenerate: `plugins/escapement-claude/**`

**Interfaces:**
- Consumes: manifest hook source `claude/hooks/escapement_session_context.py`.
- Produces: synchronized repository, Claude plugin, and Codex plugin lifecycle surfaces.

- [x] **Step 1: Replace old structural assertions with failing behavioral distribution assertions**

Tests must assert:

```python
assert all("bd prime" not in command for command in all_generated_hook_commands())
assert context_hook_present_for("codex", "SessionStart")
assert context_hook_present_for("codex", "PreCompact")
assert context_hook_present_for("claude", "SessionStart")
assert context_hook_present_for("claude", "PreCompact")
```

They must also execute the vendored hook from both plugin trees and verify the
default PR policy plus absence of Beads Git-policy phrases.

- [x] **Step 2: Run focused distribution tests and verify RED**

Run:

```bash
pytest -q tests/test_agent_surfaces.py -k "prime or session_context or hooks_include"
```

Expected: failures showing `bd prime` remains in generated artifacts and the new
hook is absent.

- [x] **Step 3: Update the manifest and renderer**

Replace the `bd_prime` manifest item with an Escapement-source hook wired at
SessionStart and PreCompact for both hosts. Update renderer validation so it
requires the Escapement hook and rejects any generated `bd prime` command.

- [x] **Step 4: Update onboarding authority**

Rewrite `agent-surfaces/onboarding/beads.md` so it says:

```markdown
Beads is the task-state system, not the workflow-policy authority.
Use `bd ready`, `bd show <id>`, `bd update <id> --claim`, `bd close <id>`,
and `bd worktree create`.
Git, pull-request, merge, deployment, completion, memory, and agent-behavior
policy come from Escapement and `.escapement/repo.json`.
```

- [x] **Step 5: Render all generated surfaces**

Run:

```bash
python3 tools/render_agent_surfaces.py
```

- [x] **Step 6: Run distribution tests and verify GREEN**

Run:

```bash
pytest -q tests/test_agent_surfaces.py
python3 tools/render_agent_surfaces.py --check
```

Expected: all surface tests pass and generated files have no drift.

### Task 3: Final Integration and Outcome Verification

**Files:**
- Modify only if verification exposes a defect in Task 1 or Task 2 files.

**Interfaces:**
- Consumes: actual generated repository and plugin hook commands.
- Produces: observed startup context proving Escapement policy ownership.

- [x] **Step 1: Run the focused combined suite**

```bash
pytest -q claude/hooks/tests/test_escapement_session_context.py tests/test_agent_surfaces.py
ruff check claude/hooks/escapement_session_context.py claude/hooks/tests/test_escapement_session_context.py tools/render_agent_surfaces.py tests/test_agent_surfaces.py
python3 tools/render_agent_surfaces.py --check
git diff --check
```

- [x] **Step 2: Exercise the actual source and vendored hooks**

For the source hook, Codex plugin hook, and Claude plugin hook, provide a real
SessionStart payload in:

1. a temporary repo with no `.escapement/repo.json`;
2. a temporary repo declaring `merged-and-deployed`;
3. an environment whose Beads config contains `no-git-ops: true`.

Inspect emitted JSON and prove:

- default context says feature branch, push, and pull request;
- configuration choices are offered when absent;
- configured outcome is reported;
- no output contains `bd prime`, `stealth mode`, or `no git operations`;
- tracker commands remain present.

- [x] **Step 3: Dispatch the independent outcome verifier**

The verifier must run the actual generated hook commands and reject “tests pass”
as sufficient evidence.

- [x] **Step 4: Inspect final repository state**

```bash
git status --short --branch
git diff --stat origin/main...HEAD
bd show escapement-rtkn --json
```

- [x] **Step 5: Commit the verified implementation**

```bash
git add agent-surfaces claude/hooks tests tools AGENTS.md CLAUDE.md .codex plugins docs/superpowers
git commit -m "fix: make Escapement own session workflow policy (escapement-rtkn)"
```
