# Integration-Base Echo-Gate Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the implementation-echo finishing gate scan the current change relative to the trustworthy remote landing branch without attributing landing-branch-only commits to the feature.

**Architecture:** Keep `changed_files()` as the public scope collector. Resolve committed-history scope from Git's local `refs/remotes/origin/HEAD` identity and use three-dot merge-base semantics; never guess the landing branch from the feature's tracking ref. When landing identity is unresolved, scan working, staged, and untracked state while failing open only for committed history.

**Tech Stack:** Python standard library, pytest, native Git fixtures, generated Escapement agent surfaces.

## Global Constraints

- Scope is only integration-base/change-scope behavior; do not change data-fixture classification.
- The independent oracle is a real Git DAG with semantically owned commits and a remote-default symref, not helper return values or repeated implementation logic.
- Preserve working-tree, staged, and untracked scans.
- Use three-dot merge-base semantics for committed feature history.
- Do not use network lookup from the PreToolUse hook.
- Do not hardcode `main`, `master`, or any default-branch spelling as authority.
- Do not fall back to `@{upstream}` or any feature tracking ref when landing identity is unresolved.
- A feature-owned committed implementation echo must deny exactly once via the canonical JSON decision and exit zero.
- A landing-branch-only echo after rebase must not deny.
- Rendered Claude and Codex plugin surfaces must remain synchronized.

---

### Task 1: Correct committed change scope and prove public gate behavior

**Files:**
- Create: `tests/test_implementation_echo_gate_change_scope.py`
- Modify: `claude/hooks/implementation_echo_test_gate.py`
- Modify via renderer as required: generated plugin hook surfaces owned by `agent-surfaces/manifest.json`

**Interfaces:**
- Consumes: `changed_files(repo_root: Path) -> list[str]`, `main() -> int`, canonical PreToolUse payload and deny JSON contract.
- Produces: a trustworthy local landing-ref resolver used by `changed_files()`; no public CLI or payload change.

- [x] **Step 1: Write real-Git negative and positive controls**

Create reusable temporary-repository helpers that build a bare `origin`, clone it, set an explicit `refs/remotes/origin/HEAD -> origin/trunk`, push a feature once, advance `trunk`, and create rebased or divergent histories.

Add behavioral tests proving:

1. After rebase with stale `origin/feature`, a landing-only committed source/test echo produces no deny while feature-owned committed files remain in the exact changed-file set.
2. A feature-owned committed source/test echo produces exactly one `permissionDecision="deny"` JSON document and exit zero.
3. With feature and `origin/trunk` diverged, feature-owned files are scanned and trunk-only files are excluded, rejecting two-dot diffing.
4. With no trustworthy remote-default identity but stale feature and local-default refs present, committed history is omitted rather than guessed; unstaged, staged, and untracked echo pairs each still deny exactly once.
5. On local `trunk` ahead of `origin/trunk`, a committed echo is scanned and denies exactly once.

- [x] **Step 2: Run the strengthened tests against canonical code and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider -q tests/test_implementation_echo_gate_change_scope.py
```

Expected failures must specifically demonstrate the stale-feature attribution, hardcoded-default vulnerability, unresolved-landing fallback, two-dot vulnerability, or dropped local-ahead committed history. Fix fixture errors until failures are behavioral rather than setup errors.

- [x] **Step 3: Implement the minimum trustworthy landing-base resolver**

In `claude/hooks/implementation_echo_test_gate.py`:

- resolve the symbolic remote default only from local Git metadata;
- verify the resolved ref identifies a commit;
- compare committed history with `<remote-default>...HEAD`;
- retain the existing working, staged, and untracked scans;
- return no committed-history base when the remote-default identity is missing or invalid;
- never fall back to the feature's `@{upstream}`;
- do not add conventional-name candidates or change data-fixture classification.

- [x] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider -q tests/test_implementation_echo_gate_change_scope.py
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider -q claude/hooks/tests/test_implementation_echo_test_gate.py tests/test_implementation_echo_gate_change_scope.py
```

Expected: all tests pass with no warnings or cache artifacts.

- [x] **Step 5: Render and verify generated surfaces**

Run:

```bash
python3 tools/render_agent_surfaces.py
python3 tools/render_agent_surfaces.py --check
```

Inspect the diff to confirm only the canonical hook, intended generated copies, the change-scope tests, and this plan changed.

- [x] **Step 6: Run the relevant repository verification**

Run the repository-prescribed hook and generated-surface test suites that cover the modified source and packaged plugin behavior. At minimum:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider -q claude/hooks/tests/test_implementation_echo_test_gate.py tests/test_implementation_echo_gate_change_scope.py tests/test_agent_surfaces.py
python3 tools/render_agent_surfaces.py --check
```

Re-exercise the public hook outcomes from Step 1; passing unit status alone is not sufficient.

- [x] **Step 7: Self-review and commit**

Confirm:

- the hardcoded `origin/main` mutation fails;
- the stale `@{upstream}` mutation fails;
- a two-dot diff mutation fails;
- returning no committed base unconditionally fails;
- unresolved landing metadata never causes a false attribution;
- data-fixture code is untouched.

Commit all intended source, generated surfaces, tests, and this plan with a focused bug-fix message.
