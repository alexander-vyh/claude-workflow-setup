# Escapement-Owned Worktree Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one host-neutral Escapement transaction that creates a new
feature worktree in an explicitly named repository from an exact source commit,
then verifies Git ownership and Beads continuity without mutating the primary
checkout.

**Architecture:** A thin `bin/escapement-worktree` executable calls an
importable standard-library Python transaction module. Native Git owns fetch,
reference, and linked-worktree mechanics; the transaction module owns
validation, source pinning, verification, and guarded rollback. The existing
PreToolUse hook becomes a non-mutating detector that redirects direct Git and
Beads creation commands to the bundled CLI, while the surface renderer ships
the same executable and guard to Claude and Codex.

**Tech Stack:** Python 3.10+ standard library (`argparse`, `dataclasses`,
`fcntl`, `json`, `pathlib`, `subprocess`), Git CLI, optional Beads 1.1+
postcondition checks, pytest, Escapement's manifest-driven surface renderer.

## Global Constraints

- Escapement, not Beads or a repository-specific wrapper, owns worktree policy.
- Native Git creates and removes linked worktrees.
- The primary checkout is never switched, merged, fast-forwarded, or required
  to occupy the remote default branch.
- Default creation uses a freshly discovered and fetched remote `HEAD` commit;
  explicit `--source` uses exactly the locally resolved commit and makes no
  freshness claim.
- The target is exactly `<repo>/.worktrees/<name>`, must be ignored, must not
  traverse a symlink, and must not exist before creation.
- The branch is explicit, valid, new, and never reset with `-B` or `--force`.
- Repositories using Beads must resolve the same live `bd context --json`
  identity from primary and linked worktrees.
- Missing repository, remote, source, safety, Git, or required Beads evidence
  fails closed.
- Hook parsing is an accidental-bypass guardrail; malformed or unsupported
  shell text fails open and the hook performs no mutation.
- Canonical files remain under 500 lines where practical; generated JSON,
  Markdown, and plugin copies remain renderer-owned.
- Historical specs, archived OpenSpec changes, and explicit legacy fixtures are
  evidence, not active policy, and are not rewritten merely to remove old text.
- Do not weaken any existing positive or negative control to make the change
  pass.

---

### Task 1: Establish the Behavioral Oracle and Complete the Mutation Challenge

**Files:**

- Create: `tests/worktree_fixtures.py`
- Create: `tests/test_escapement_worktree_validation.py`
- Create: `tests/test_escapement_worktree_sources.py`
- Create: `tests/test_escapement_worktree_transaction.py`
- Create: `claude/hooks/tests/test_worktree_entrypoint_guard.py`
- Create: `claude/hooks/tests/test_codex_worktree_entrypoint_guard.py`
- Create: `tests/test_worktree_policy_surfaces.py`
- Read: `docs/superpowers/specs/2026-07-29-escapement-owned-worktree-creation-design.md`

**Interfaces:**

- Consumes: the approved design's public command
  `escapement-worktree create --repo PATH --name NAME --branch BRANCH
  [--source REV]`.
- Produces: reusable external Git fixtures and the complete behavioral/static
  oracle that all later tasks must satisfy.

- [ ] **Step 1: Enter the implementation branch without leaving the isolated worktree**

Run:

```bash
git switch -c fix/escapement-ra0g
git status --short --branch
```

Expected: current branch is `fix/escapement-ra0g`; the committed design and
plan remain ancestors; the worktree is clean.

- [ ] **Step 2: Add repository fixtures whose truth does not come from the implementation**

Create `tests/worktree_fixtures.py` with a subprocess-only Git fixture. Do not
import the production transaction module here.

```python
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitScenario:
    primary: Path
    remote: Path
    remote_default_ref: str
    remote_head_sha: str
    stale_primary_sha: str


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_AUTHOR_NAME": "Oracle", "GIT_AUTHOR_EMAIL": "oracle@example.test",
             "GIT_COMMITTER_NAME": "Oracle", "GIT_COMMITTER_EMAIL": "oracle@example.test"},
    )
    if check and result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result


def rev(cwd: Path, ref: str = "HEAD") -> str:
    return git(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def make_remote_scenario(tmp_path: Path, *, default_branch: str = "trunk") -> GitScenario:
    remote = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    primary = tmp_path / "primary"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "--initial-branch", default_branch, str(seed))
    (seed / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    (seed / "oracle.txt").write_text("stale-primary\n", encoding="utf-8")
    git(seed, "add", ".gitignore", "oracle.txt")
    git(seed, "commit", "-m", "old primary fixture")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", default_branch)
    git(remote, "symbolic-ref", "HEAD", f"refs/heads/{default_branch}")
    git(tmp_path, "clone", str(remote), str(primary))
    stale_primary_sha = rev(primary)
    git(primary, "switch", "-c", "root-main")
    (seed / "oracle.txt").write_text("remote-default\n", encoding="utf-8")
    git(seed, "add", "oracle.txt")
    git(seed, "commit", "-m", "advance remote default fixture")
    git(seed, "push", "origin", default_branch)
    remote_head_sha = git(
        remote, "rev-parse", "--verify", f"{default_branch}^{{commit}}"
    ).stdout.strip()
    return GitScenario(
        primary=primary,
        remote=remote,
        remote_default_ref=f"refs/heads/{default_branch}",
        remote_head_sha=remote_head_sha,
        stale_primary_sha=stale_primary_sha,
    )
```

The committed helper must contain the full setup, including:

- a bare remote whose symbolic `HEAD` points to the requested default branch;
- an old commit cloned into the primary;
- a primary checkout switched to `root-main` at the old commit;
- a second remote-default commit containing an `oracle.txt` value known only to
  the fixture; and
- `.worktrees/` in the primary's committed `.gitignore`.

The fixture's `remote_head_sha` must come from:

```bash
git --git-dir <bare-remote> rev-parse '<default-branch>^{commit}'
```

not from production output.

- [ ] **Step 3: Write validation tests**

Create `tests/test_escapement_worktree_validation.py`. Invoke the executable as
a subprocess through a shared `run_cli(primary, *args, env=None)` helper.

Required tests are
`test_rejects_non_primary_repo_path`,
`test_rejects_existing_target_without_modifying_it`,
`test_rejects_symlinked_worktrees_directory`,
`test_rejects_nonignored_target_and_leaves_no_branch`,
`test_rejects_invalid_branch_name`,
`test_rejects_preexisting_branch_without_moving_it`, and
`test_plain_git_repository_does_not_require_beads`.

The unsafe-target test must assert all external postconditions:

```python
result = run_cli(
    scenario.primary,
    "create", "--repo", str(scenario.primary),
    "--name", "unsafe", "--branch", "feature/unsafe",
)
assert result.returncode != 0
assert not (scenario.primary / ".worktrees" / "unsafe").exists()
assert git(
    scenario.primary, "show-ref", "--verify", "--quiet",
    "refs/heads/feature/unsafe", check=False,
).returncode != 0
```

- [ ] **Step 4: Write source-resolution tests**

Create `tests/test_escapement_worktree_sources.py`.

Required tests are
`test_default_uses_fetched_remote_head_not_stale_primary_head`,
`test_default_branch_name_is_discovered_not_hardcoded_main`,
`test_main_may_be_checked_out_in_another_worktree`,
`test_explicit_source_uses_exact_local_commit_without_remote_refresh`,
`test_missing_origin_fails_without_creating_target_or_branch`,
`test_missing_remote_head_fails_closed`,
`test_remote_head_race_retries_once_then_uses_matching_sha`, and
`test_second_remote_head_race_fails_without_residue`.

The default-source assertion must compare independent SHAs:

```python
assert rev(created) == scenario.remote_head_sha
assert rev(created) != scenario.stale_primary_sha
assert (created / "oracle.txt").read_text(encoding="utf-8") == "remote-default\n"
```

For the race cases, place a test-only `git` proxy earlier on `PATH`. The proxy
delegates to the real Git executable but advances the fixture's bare remote
after the first `ls-remote --symref origin HEAD`. This changes external remote
state rather than mocking a production helper. The one-race fixture must
converge; the two-race fixture must fail closed.

Pre-create the race commits in the bare repository and pass their SHAs through
`RACE_SHAS`. The proxy body is:

```python
#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

real_git = os.environ["REAL_GIT"]
args = sys.argv[1:]
result = subprocess.run([real_git, *args], capture_output=True, check=False)
if "ls-remote" in args and args[-1] == "HEAD":
    counter_path = Path(os.environ["RACE_COUNTER"])
    count = int(counter_path.read_text() or "0") if counter_path.exists() else 0
    shas = json.loads(os.environ["RACE_SHAS"])
    if count < len(shas):
        subprocess.run(
            [
                real_git,
                "--git-dir",
                os.environ["RACE_REMOTE"],
                "update-ref",
                os.environ["RACE_REF"],
                shas[count],
            ],
            check=True,
        )
    counter_path.write_text(str(count + 1), encoding="utf-8")
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
```

- [ ] **Step 5: Write transaction, Beads, and rollback tests**

Create `tests/test_escapement_worktree_transaction.py`.

Required tests are
`test_success_reports_repo_branch_sha_source_kind_and_beads_status`,
`test_created_common_directory_matches_requested_repository`,
`test_beads_context_identity_matches_primary`,
`test_mismatched_beads_context_rolls_back_target_and_branch`,
`test_branch_moved_during_failure_survives_guarded_rollback`,
`test_partial_git_creation_failure_is_inspected_and_cleaned`, and
`test_cleanup_failure_reports_exact_residue`.

Use a fake `bd` executable only as an external dependency fixture. It must emit
JSON from its process cwd and may deliberately return different stable identity
fields in the target. Do not mock or assert private production helper calls.

For the moved-branch control, the fake `bd` process moves the newly created
branch to a separately prepared commit before returning mismatched context.
Assert that rollback does not delete the moved ref:

```python
assert result.returncode != 0
assert rev(scenario.primary, "refs/heads/feature/moved") == moved_sha
assert "refused to delete moved branch" in result.stderr
```

- [ ] **Step 6: Write new hook fixtures**

Create `claude/hooks/tests/test_worktree_entrypoint_guard.py` with a local module
loader and host payload driver. Cover
`test_git_worktree_add_is_redirected_in_plain_git_repo`,
`test_bd_worktree_create_is_redirected_in_beads_repo`,
`test_literal_cd_routes_repair_to_target_repo_not_payload_cwd`,
`test_git_dash_c_routes_repair_to_target_repo`,
`test_quoted_git_worktree_text_is_allowed`,
`test_quoted_bd_worktree_text_is_allowed`,
`test_malformed_shell_text_fails_open`,
`test_cli_invocation_is_allowed`,
`test_missing_bundled_cli_keeps_direct_creation_denied`, and
`test_non_creation_git_and_bd_commands_are_allowed`.

The cross-repository test must use two real temporary Git repositories:

```python
command = f"cd {dashboards} && git worktree add .worktrees/x -b feature/x"
_, output = run_hook(command, cwd=cake)
reason = output["hookSpecificOutput"]["permissionDecisionReason"]
assert f"--repo {dashboards}" in reason
assert f"--repo {cake}" not in reason
```

Create `claude/hooks/tests/test_codex_worktree_entrypoint_guard.py` using the
current Codex payload shape. It must independently prove a real direct creation
is denied, quoted text is allowed, and the repair command names a bundled CLI
that exists in the rendered Codex plugin.

- [ ] **Step 7: Write active-policy and generated-surface checks**

Create `tests/test_worktree_policy_surfaces.py` with exact active-source lists:

```python
ACTIVE_POLICY_FILES = [
    Path("agent-surfaces/onboarding/beads.md"),
    Path("claude/rules/worktree-discipline.md"),
    Path("claude/rules/agent-teams-default.md"),
    Path("claude/skills/beads-worktree/SKILL.md"),
    Path("claude/skills/beads-execution/SKILL.md"),
    Path(".agents/skills/beads-execution/SKILL.md"),
    Path("claude/hooks/escapement_session_context.py"),
    Path("claude/hooks/root_checkout_guard.py"),
    Path("harness/bin/session_isolation.py"),
    Path("harness/README.md"),
    Path("README.md"),
]
```

Required assertions:

- active policy contains `escapement-worktree`;
- active policy contains no `cake-worktree` or `.agents/worktree-entrypoint`;
- active policy contains no instruction to create via `bd worktree create`;
- historical specs and legacy fixtures are not included in the static scan;
- renderer targets contain the executable and importable module in both plugins;
- plugin guard copies are byte-equal to the canonical guard;
- plugin CLI/module copies are byte-equal to canonical `bin/` sources; and
- the obsolete generated `beads_worktree_location_guard.py` copies do not exist.

- [ ] **Step 8: Run the red oracle**

Run:

```bash
pytest -q \
  tests/test_escapement_worktree_validation.py \
  tests/test_escapement_worktree_sources.py \
  tests/test_escapement_worktree_transaction.py \
  claude/hooks/tests/test_worktree_entrypoint_guard.py \
  claude/hooks/tests/test_codex_worktree_entrypoint_guard.py \
  tests/test_worktree_policy_surfaces.py
```

Expected: FAIL because `bin/escapement-worktree` and the new behavior do not
exist. Inspect collection first; no test may be skipped or xfailed.

- [ ] **Step 9: Dispatch the required mutation challenger**

Dispatch an independent agent with this exact responsibility:

```text
Read the approved design and the new oracle tests. Do not write production
code. Invent at least five plausible bad implementations, including:
(1) deleting marker support and merely documenting fetch-first,
(2) creating from primary HEAD,
(3) hardcoding origin/main,
(4) using payload cwd instead of --repo,
(5) unconditional rollback deletion.
For each, say whether the current behavioral, contract, architecture, or static
checks fail it. If a bad implementation can pass, strengthen only the owned
test files and report the new rejecting assertion. DO NOT summarize unresolved
work and stop: finish when every named bad implementation is rejected or
escalate one precise blocker.
```

Ownership is limited to the seven new test/helper files from this task. The
challenger must not edit production, generated, policy, or design files.

Implementation remains blocked until the challenger confirms every named bad
implementation fails at least one check.

- [ ] **Step 10: Commit the reviewed red oracle**

```bash
git add \
  tests/worktree_fixtures.py \
  tests/test_escapement_worktree_validation.py \
  tests/test_escapement_worktree_sources.py \
  tests/test_escapement_worktree_transaction.py \
  claude/hooks/tests/test_worktree_entrypoint_guard.py \
  claude/hooks/tests/test_codex_worktree_entrypoint_guard.py \
  tests/test_worktree_policy_surfaces.py
git commit -m "test: define Escapement worktree transaction oracle"
```

---

### Task 2: Implement Repository Validation and Exact Source Resolution

**Files:**

- Create: `bin/escapement-worktree`
- Create: `bin/escapement_worktree.py`
- Test: `tests/test_escapement_worktree_validation.py`
- Test: `tests/test_escapement_worktree_sources.py`
- Reuse: `tests/worktree_fixtures.py`

**Interfaces:**

- Consumes: `GitScenario`, `git`, and `rev` from
  `tests/worktree_fixtures.py`.
- Produces:

```python
@dataclass(frozen=True)
class WorktreeRequest:
    repo: Path
    name: str
    branch: str
    source: str | None

@dataclass(frozen=True)
class RepositoryContext:
    primary: Path
    common_dir: Path

@dataclass(frozen=True)
class ResolvedSource:
    sha: str
    kind: Literal["remote-default", "explicit"]
    display_ref: str

class WorktreeError(RuntimeError):
    pass
```

The module exposes these exact functions:

- `resolve_repository(path: Path) -> RepositoryContext`
- `validate_request(ctx: RepositoryContext, request: WorktreeRequest) -> Path`
- `resolve_default_source(ctx: RepositoryContext) -> ResolvedSource`
- `resolve_explicit_source(ctx: RepositoryContext, source: str) -> ResolvedSource`

- [ ] **Step 1: Add the executable launcher**

Create `bin/escapement-worktree`:

```python
#!/usr/bin/env python3
from escapement_worktree import main

if __name__ == "__main__":
    raise SystemExit(main())
```

Make it executable. It contains no policy logic.

- [ ] **Step 2: Add the typed request, repository, and subprocess core**

Create `bin/escapement_worktree.py` with the types above and:

```python
def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=None if env is None else dict(env),
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise WorktreeError(f"{command[0]} failed: {detail}")
    return result


def git(
    ctx_or_repo: RepositoryContext | Path,
    *args: str,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    repo = ctx_or_repo.primary if isinstance(ctx_or_repo, RepositoryContext) else ctx_or_repo
    return run(("git", "-C", str(repo), *args), env=env, check=check)
```

Never use `shell=True`, shell interpolation, or `os.system`.

- [ ] **Step 3: Implement primary-repository validation**

`resolve_repository` must:

1. expand and resolve the requested path;
2. run `rev-parse --show-toplevel`;
3. run `rev-parse --path-format=absolute --git-common-dir`;
4. require `<top-level>/.git` to be a real directory, not a symlink;
5. require the resolved common directory to equal `<top-level>/.git`; and
6. return `RepositoryContext`.

Run:

```bash
pytest -q tests/test_escapement_worktree_validation.py -k "repo_path" -vv
```

Expected: repository-path tests PASS; other tests may remain red.

- [ ] **Step 4: Implement target and branch validation**

`validate_request` must validate the safe basename with:

```python
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
```

It must reject `"."`, `".."`, symlinked `.worktrees`, existing targets,
invalid/existing branches, and any `git check-ignore --quiet --no-index
<relative-target>` return code other than zero. A check-ignore error is unsafe,
not allowed.

Run:

```bash
pytest -q tests/test_escapement_worktree_validation.py -vv
```

Expected: validation tests pass except success paths that still require
creation.

- [ ] **Step 5: Implement remote-default discovery and fetch**

Parse `git ls-remote --symref origin HEAD` into one default branch ref and SHA.
Reject missing/multiple/malformed results.

Fetch with an explicit refspec:

```python
remote_ref = f"refs/heads/{branch_name}"
tracking_ref = f"refs/remotes/origin/{branch_name}"
git(
    ctx,
    "fetch", "--no-tags", "--prune", "origin",
    f"+{remote_ref}:{tracking_ref}",
    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
)
fetched_sha = git(
    ctx, "rev-parse", "--verify", f"{tracking_ref}^{{commit}}"
).stdout.strip()
```

If `fetched_sha != advertised_sha`, repeat discovery and fetch once. If the
second pair differs, raise `WorktreeError` without creating a target or branch.

- [ ] **Step 6: Implement explicit source resolution**

Resolve exactly one local commit:

```python
sha = git(
    ctx,
    "rev-parse",
    "--verify",
    "--end-of-options",
    f"{source}^{{commit}}",
).stdout.strip()
return ResolvedSource(sha=sha, kind="explicit", display_ref=source)
```

Do not fetch in this path.

- [ ] **Step 7: Run source-resolution controls**

Run:

```bash
pytest -q \
  tests/test_escapement_worktree_validation.py \
  tests/test_escapement_worktree_sources.py -vv
```

Expected: remote discovery, stale-primary, non-`main`, explicit-source, and race
controls pass. Transaction tests remain red until Task 3.

- [ ] **Step 8: Commit validation and source resolution**

```bash
git add bin/escapement-worktree bin/escapement_worktree.py
git commit -m "feat: resolve safe worktree repositories and sources"
```

---

### Task 3: Implement Transactional Creation, Verification, and Guarded Rollback

**Files:**

- Modify: `bin/escapement_worktree.py`
- Test: `tests/test_escapement_worktree_transaction.py`
- Test: `tests/test_escapement_worktree_validation.py`
- Test: `tests/test_escapement_worktree_sources.py`

**Interfaces:**

- Consumes: `WorktreeRequest`, `RepositoryContext`, `ResolvedSource`,
  `resolve_repository`, `validate_request`, `resolve_default_source`, and
  `resolve_explicit_source` from Task 2.
- Produces:

```python
@dataclass(frozen=True)
class CreationResult:
    repo: Path
    target: Path
    branch: str
    source_sha: str
    source_kind: Literal["remote-default", "explicit"]
    beads_verified: bool

```

The module exposes these exact functions:

- `beads_context(path: Path) -> dict[str, str] | None`
- `verify_created_worktree(ctx: RepositoryContext, request:
  WorktreeRequest, target: Path, source: ResolvedSource, root_beads:
  dict[str, str] | None) -> bool`
- `rollback_created_artifacts(ctx: RepositoryContext, target: Path, branch:
  str, expected_sha: str) -> list[str]`
- `create_worktree(request: WorktreeRequest) -> CreationResult`
- `main(argv: Sequence[str] | None = None) -> int`

- [ ] **Step 1: Implement Beads-context observation**

Detect Beads from `<primary>/.beads`. When absent, return `None`.

When present, run `bd context --json` from the supplied path and require a JSON
object with non-empty:

```python
BEADS_IDENTITY_FIELDS = ("project_id", "database", "beads_dir", "repo_root")
```

Do not read `.beads/issues.jsonl` or accept a known issue record as identity.

- [ ] **Step 2: Implement per-repository locking**

Add:

```python
@contextmanager
def creation_lock(ctx: RepositoryContext) -> Iterator[None]:
    lock_path = ctx.common_dir / "escapement-worktree.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
```

All validation, source resolution, creation, verification, and rollback happen
inside this context.

- [ ] **Step 3: Implement exact-SHA native Git creation**

Within `create_worktree`:

```python
ctx = resolve_repository(request.repo)
with creation_lock(ctx):
    target = validate_request(ctx, request)
    source = (
        resolve_explicit_source(ctx, request.source)
        if request.source is not None
        else resolve_default_source(ctx)
    )
    root_beads = beads_context(ctx.primary)
    git(
        ctx,
        "worktree", "add",
        "-b", request.branch,
        str(target),
        source.sha,
    )
```

Record that target and branch were absent before invoking Git. Treat any
nonzero Git result as potentially partial and enter guarded cleanup.

- [ ] **Step 4: Implement external outcome verification**

`verify_created_worktree` must compare:

- target `HEAD^{commit}` with `source.sha`;
- `symbolic-ref --quiet --short HEAD` with `request.branch`;
- absolute target common directory with `ctx.common_dir`;
- `git worktree list --porcelain` path/branch association;
- final resolved target parent with the non-symlinked ignored `.worktrees`; and
- Beads identity with `root_beads` when applicable.

Return `True` only when Beads was present and matched; return `False` for a
plain Git repository. Raise on any required mismatch.

- [ ] **Step 5: Implement ownership-proving rollback**

Rollback must first inspect, never assume.

If the target exists, remove it only after proving its common directory and
branch match the transaction. Use:

```bash
git -C <primary> worktree remove --force <target>
```

Delete the ref only through:

```bash
git -C <primary> update-ref -d refs/heads/<branch> <expected-sha>
```

If the ref has moved, preserve it and return a residue message. Aggregate
cleanup errors with the creation/verification failure.

- [ ] **Step 6: Implement the CLI contract**

Use `argparse` subparsers with required `create`, `--repo`, `--name`, and
`--branch`, plus optional `--source`.

On success, print one JSON object:

```json
{
  "beads_verified": true,
  "branch": "feature/example",
  "repo": "/repo",
  "source_kind": "remote-default",
  "source_sha": "0123456789abcdef0123456789abcdef01234567",
  "target": "/repo/.worktrees/example"
}
```

Use sorted keys for deterministic output. On `WorktreeError`, print
`escapement-worktree: <message>` to stderr and return 1.

- [ ] **Step 7: Run the complete CLI oracle**

Run:

```bash
pytest -q \
  tests/test_escapement_worktree_validation.py \
  tests/test_escapement_worktree_sources.py \
  tests/test_escapement_worktree_transaction.py
```

Expected: all CLI tests PASS, including rollback and partial-failure controls.

- [ ] **Step 8: Check module size and style**

Run:

```bash
wc -l bin/escapement_worktree.py
python3 -m py_compile bin/escapement_worktree.py bin/escapement-worktree
ruff check bin/escapement_worktree.py bin/escapement-worktree \
  tests/worktree_fixtures.py \
  tests/test_escapement_worktree_validation.py \
  tests/test_escapement_worktree_sources.py \
  tests/test_escapement_worktree_transaction.py
```

Expected: transaction module is at most 500 lines; compilation and Ruff pass.
If it exceeds 500 because subprocess parsing and transaction ownership have
become separate responsibilities, extract only the subprocess/remote parser
into `bin/escapement_worktree_git.py` and update imports/tests.

- [ ] **Step 9: Commit the complete transaction**

```bash
git add bin/escapement_worktree.py bin/escapement-worktree
git commit -m "feat: create and verify worktrees transactionally"
```

---

### Task 4: Replace Repository-Specific Routing with the Thin Escapement Guard

**Files:**

- Create: `claude/hooks/_worktree_cli.py`
- Modify: `claude/hooks/beads_worktree_guard.py`
- Delete: `claude/hooks/beads_worktree_location_guard.py`
- Delete: `claude/hooks/tests/test_beads_worktree_guard.py`
- Delete: `claude/hooks/tests/test_codex_beads_worktree_guard.py`
- Test: `claude/hooks/tests/test_worktree_entrypoint_guard.py`
- Test: `claude/hooks/tests/test_codex_worktree_entrypoint_guard.py`

**Interfaces:**

- Consumes: bundled logical executable `bin/escapement-worktree` and its
  `create --repo --name --branch [--source]` interface.
- Produces shared runtime discovery:

```python
def bundled_cli_path(hook_file: Path) -> Path | None:
    """Resolve the canonical CLI beside a source, Codex, or flat Claude hook."""

def bundled_cli_prefix(hook_file: Path) -> tuple[str, str, str] | None:
    """Return the Python command prefix when the CLI is packaged correctly."""
```

The guard produces:

```python
@dataclass(frozen=True)
class LiteralCreation:
    kind: Literal["git", "bd"]
    repo: Path
    name: str | None
    branch: str | None
    source: str | None

```

The guard exposes these exact functions:

- `literal_creations(command: str, payload_cwd: Path) ->
  Iterator[LiteralCreation]`
- `repair_command(create: LiteralCreation, cli_path: Path) -> str`

- [ ] **Step 1: Remove marker and location-policy ownership**

Delete:

- `_MARKER_RELATIVE_PATH`;
- `_repository_entrypoint`;
- `_deny_managed`;
- marker validation;
- imports from `beads_worktree_location_guard`; and
- Beads-only conditional routing.

Do not weaken non-creation pass-through behavior.

- [ ] **Step 2: Replace regex separator splitting with bounded shell-aware tokenization**

Use `shlex.shlex(command, posix=True, punctuation_chars=";&|")` with whitespace
splitting and comments disabled. Group punctuation tokens into command
separators while preserving quoted punctuation as argument content.

Track only safe literal forms:

- `cd <one-literal-path>` followed by `&&` or `;`;
- `git [<global-options>] worktree add <arguments>`; and
- `bd [<global-options>] worktree create <arguments>`.

Unsupported/dynamic segments yield no denial. A real creation detected before
an unsupported segment is still denied.

- [ ] **Step 3: Resolve the bundled CLI without a global PATH assumption**

Implement the shared path contract in `claude/hooks/_worktree_cli.py`. Search
these source/plugin-relative candidates from the caller's `hook_file`:

```python
def bundled_cli_path(hook_file: Path) -> Path | None:
    here = hook_file.resolve()
    candidates = (
        here.parents[2] / "bin" / "escapement-worktree",  # source + Codex plugin
        here.parents[1] / "bin" / "escapement-worktree",  # flat Claude plugin
    )
    return next((path for path in candidates if path.is_file()), None)
```

The guard imports this helper and quotes every emitted path with `shlex.join`.
No policy consumer reimplements plugin-layout traversal.

- [ ] **Step 4: Emit concrete repair and fail closed on broken packaging**

For every real direct creation, deny in both plain and Beads repositories.
Preserve parsed name/branch/source when available:

```bash
python3 -B <bundled-cli> create \
  --repo <resolved-repo> \
  --name <name> \
  --branch <branch> \
  [--source <source>]
```

If required arguments cannot be inferred, use visible placeholders in the
message without executing them. If the companion CLI is missing, continue to
deny and instruct the user to repair/update Escapement.

- [ ] **Step 5: Run the new guard suites**

Run:

```bash
pytest -q \
  claude/hooks/tests/test_worktree_entrypoint_guard.py \
  claude/hooks/tests/test_codex_worktree_entrypoint_guard.py
```

Expected: all real-create, quoted-content, cross-repository, missing-companion,
and host-payload controls PASS.

- [ ] **Step 6: Prove the obsolete implementation and oversized tests are gone**

Run:

```bash
test ! -e claude/hooks/beads_worktree_location_guard.py
test ! -e claude/hooks/tests/test_beads_worktree_guard.py
test ! -e claude/hooks/tests/test_codex_beads_worktree_guard.py
wc -l claude/hooks/beads_worktree_guard.py \
  claude/hooks/tests/test_worktree_entrypoint_guard.py \
  claude/hooks/tests/test_codex_worktree_entrypoint_guard.py
```

Expected: guard and each fixture are below 500 lines.

- [ ] **Step 7: Commit the guard replacement**

```bash
git add -A \
  claude/hooks/_worktree_cli.py \
  claude/hooks/beads_worktree_guard.py \
  claude/hooks/beads_worktree_location_guard.py \
  claude/hooks/tests
git commit -m "feat: route worktree creation through Escapement"
```

---

### Task 5: Replace Active Beads-Owned Worktree Policy and Repair Guidance

**Files:**

- Modify: `agent-surfaces/onboarding/beads.md`
- Modify: `claude/rules/worktree-discipline.md`
- Modify: `claude/rules/agent-teams-default.md`
- Modify: `claude/skills/beads-worktree/SKILL.md`
- Modify: `claude/skills/beads-execution/SKILL.md`
- Modify: `.agents/skills/beads-execution/SKILL.md`
- Modify: `claude/hooks/escapement_session_context.py`
- Modify: `claude/hooks/root_checkout_guard.py`
- Modify: `claude/hooks/tests/test_escapement_session_context.py`
- Modify: `claude/hooks/tests/test_root_checkout_guard.py`
- Modify: `harness/bin/session_isolation.py`
- Modify: `harness/tests/test_session_isolation.py`
- Modify: `harness/README.md`
- Modify: `README.md`
- Modify: `docs/deck.html`
- Test: `tests/test_worktree_policy_surfaces.py`

**Interfaces:**

- Consumes: the CLI contract and repair command established in Tasks 2-4.
- Produces: one active policy statement across onboarding, rules, skills,
  session context, root-checkout repair, and isolation steering.

- [ ] **Step 1: Update the source-of-truth onboarding boundary**

Replace the Beads-owned creation statement in
`agent-surfaces/onboarding/beads.md` with:

```markdown
Escapement owns worktree creation policy. Use the concrete bundled
`escapement-worktree create` command injected into session context so the
source commit, target repository, isolation, and optional Beads context are
verified together. Beads remains task state and is checked after native Git
creation when present.
```

- [ ] **Step 2: Update one-writer rules and skills**

Every active worktree-creation example must use the generic transaction. Preserve
the one-writer-one-worktree rule and native agent isolation affordances.

The rule's core example becomes:

```bash
python3 -B <injected-bundled-cli-path> create \
  --repo "$(git rev-parse --show-toplevel)" \
  --name <task> \
  --branch <branch>
```

The angle-bracket value is descriptive static policy: each live SessionStart
context supplies the concrete absolute value for the current source, Claude
plugin, or Codex plugin layout.

Remove claims that ordinary native Git worktrees create a broken Beads skeleton.
Retain `bd ready`, `bd show`, `bd update --claim`, and `bd close` as tracker
operations.

- [ ] **Step 3: Update hook repair and session context**

`escapement_session_context.py` must describe `escapement-worktree` as
Escapement Git policy, not include `bd worktree create` in the small Beads
command set. It imports `bundled_cli_prefix` from `_worktree_cli.py` and injects
the actual source/plugin command path into session context. A missing companion
is reported as a broken Escapement installation, never replaced with direct Git
or Beads creation.

`root_checkout_guard.py` must steer to the same CLI with explicit `--repo`,
`--name`, and `--branch`. Update its behavioral assertion so a root mutation is
still denied and the repair path is concrete.

- [ ] **Step 4: Update continuation-harness collision steering**

Change `build_isolation_steer` to name the generic CLI. Keep the behavioral
outcome unchanged: only colliding live sessions receive a steer; isolated and
stale-peer controls remain negative controls.

Update exact harness tests and README examples.

- [ ] **Step 5: Update current public documentation**

Replace current README/deck claims that `bd worktree create` is the worktree
integration. Do not rewrite:

- archived OpenSpec changes;
- the approved historical 2026-07-24 design/plan;
- `docs/analysis/` decision records; or
- `tests/legacy_codex_skill_fixture.py`.

Those remain provenance evidence and are excluded explicitly from the active
policy oracle.

- [ ] **Step 6: Run focused policy, root-guard, session-context, and isolation tests**

Run:

```bash
pytest -q \
  tests/test_worktree_policy_surfaces.py \
  claude/hooks/tests/test_escapement_session_context.py \
  claude/hooks/tests/test_root_checkout_guard.py \
  harness/tests/test_session_isolation.py
```

Expected: all PASS; no active policy file instructs direct Beads or Git
creation.

- [ ] **Step 7: Commit policy convergence**

```bash
git add \
  agent-surfaces/onboarding/beads.md \
  claude/rules/worktree-discipline.md \
  claude/rules/agent-teams-default.md \
  claude/skills/beads-worktree/SKILL.md \
  claude/skills/beads-execution/SKILL.md \
  .agents/skills/beads-execution/SKILL.md \
  claude/hooks/escapement_session_context.py \
  claude/hooks/root_checkout_guard.py \
  claude/hooks/tests/test_escapement_session_context.py \
  claude/hooks/tests/test_root_checkout_guard.py \
  harness/bin/session_isolation.py \
  harness/tests/test_session_isolation.py \
  harness/README.md README.md docs/deck.html
git commit -m "docs: make Escapement authoritative for worktrees"
```

---

### Task 6: Package the CLI and Render Host-Parity Surfaces

**Files:**

- Modify: `agent-surfaces/manifest.json`
- Modify: `tools/render_agent_surfaces.py`
- Test: `tests/test_worktree_policy_surfaces.py`
- Generate: `AGENTS.md`
- Generate: `CLAUDE.md`
- Generate: `.codex/hooks.json`
- Generate: `plugins/escapement/bin/escapement-worktree`
- Generate: `plugins/escapement/bin/escapement_worktree.py`
- Generate: `plugins/escapement/claude/hooks/_worktree_cli.py`
- Generate: `plugins/escapement/claude/hooks/beads_worktree_guard.py`
- Generate: `plugins/escapement/hooks/hooks.json`
- Generate: `plugins/escapement/skills/beads-execution/SKILL.md`
- Generate: `plugins/escapement-claude/bin/escapement-worktree`
- Generate: `plugins/escapement-claude/bin/escapement_worktree.py`
- Generate: `plugins/escapement-claude/hooks/_worktree_cli.py`
- Generate: `plugins/escapement-claude/hooks/beads_worktree_guard.py`
- Generate: `plugins/escapement-claude/hooks/hooks.json`
- Generate: `plugins/escapement-claude/rules/worktree-discipline.md`
- Generate: `plugins/escapement-claude/rules/agent-teams-default.md`
- Generate: `plugins/escapement-claude/skills/beads-worktree/SKILL.md`
- Generate: `plugins/escapement-claude/skills/beads-execution/SKILL.md`

**Interfaces:**

- Consumes: canonical `bin/` sources, canonical guard, active policy, and new
  host-specific fixtures.
- Produces: byte-identical runtime CLI and guard behavior in both supported
  plugins.

- [ ] **Step 1: Update the neutral hook manifest**

Keep the hook id `beads_worktree_guard` for migration compatibility, but change
its description to Escapement-owned worktree routing.

Replace fixtures with:

```json
"fixtures": [
  "claude/hooks/tests/test_codex_worktree_entrypoint_guard.py::test_codex_direct_creation_is_denied",
  "claude/hooks/tests/test_codex_worktree_entrypoint_guard.py::test_codex_quoted_creation_text_is_allowed"
]
```

for Codex and:

```json
"fixtures": [
  "claude/hooks/tests/test_worktree_entrypoint_guard.py"
]
```

for Claude.

- [ ] **Step 2: Teach the renderer about shared runtime support**

Add:

```python
SHARED_RUNTIME_SUPPORT = {
    "bin/escapement-worktree",
    "bin/escapement_worktree.py",
}
```

In `rendered_targets`, copy each source to the same relative path under both
`CODEX_PLUGIN_ROOT` and `CLAUDE_PLUGIN_ROOT`. Remove
`claude/hooks/beads_worktree_location_guard.py` from `SHARED_HOOK_SUPPORT` and
add `claude/hooks/_worktree_cli.py`.

Do not add the worktree CLI to `harness/bin`; it is not continuation-harness
state.

- [ ] **Step 3: Render every generated surface**

Run:

```bash
python3 tools/render_agent_surfaces.py
```

Do not hand-edit anything under `plugins/`, `AGENTS.md`, or `CLAUDE.md`.

- [ ] **Step 4: Verify renderer determinism and packaging**

Run:

```bash
python3 tools/render_agent_surfaces.py --check
pytest -q tests/test_agent_surfaces.py tests/test_worktree_policy_surfaces.py
```

Expected: renderer check and tests PASS. Confirm no obsolete generated location
guard remains:

```bash
find plugins -path '*beads_worktree_location_guard.py' -print
```

Expected: no output.

- [ ] **Step 5: Run all focused worktree and affected-surface suites**

Run:

```bash
pytest -q \
  tests/test_escapement_worktree_validation.py \
  tests/test_escapement_worktree_sources.py \
  tests/test_escapement_worktree_transaction.py \
  claude/hooks/tests/test_worktree_entrypoint_guard.py \
  claude/hooks/tests/test_codex_worktree_entrypoint_guard.py \
  tests/test_worktree_policy_surfaces.py \
  tests/test_agent_surfaces.py \
  claude/hooks/tests/test_escapement_session_context.py \
  claude/hooks/tests/test_root_checkout_guard.py \
  harness/tests/test_session_isolation.py
```

Expected: all PASS.

- [ ] **Step 6: Commit canonical and generated distribution changes**

```bash
git add \
  agent-surfaces/manifest.json \
  tools/render_agent_surfaces.py \
  AGENTS.md CLAUDE.md .codex/hooks.json \
  plugins/escapement plugins/escapement-claude
git commit -m "build: distribute Escapement worktree tooling"
```

---

### Task 7: Review, Run Real Outcome Probes, Land, and Verify Deployment

**Files:**

- Modify only if review finds a defect in an in-scope file.
- Read: `.escapement/repo.json`
- Read: `scripts/plugin-update.sh`
- Read: `scripts/codex-plugin-update.sh`

**Interfaces:**

- Consumes: fully rendered feature branch from Tasks 1-6.
- Produces: verified CAKE/dashboard behavior, merged pull request, updated live
  Claude/Codex plugins, and no probe residue.

- [ ] **Step 1: Run static and focused verification from a clean feature worktree**

Run:

```bash
git status --short --branch
git diff --check "$(git merge-base HEAD origin/main)"..HEAD
python3 tools/render_agent_surfaces.py --check
pytest -q \
  tests/test_escapement_worktree_validation.py \
  tests/test_escapement_worktree_sources.py \
  tests/test_escapement_worktree_transaction.py \
  claude/hooks/tests/test_worktree_entrypoint_guard.py \
  claude/hooks/tests/test_codex_worktree_entrypoint_guard.py \
  tests/test_worktree_policy_surfaces.py \
  tests/test_agent_surfaces.py \
  claude/hooks/tests/test_escapement_session_context.py \
  claude/hooks/tests/test_root_checkout_guard.py \
  harness/tests/test_session_isolation.py
```

The known full-suite collection dependency on the absent legacy
`~/.claude/hooks/tdd-gate.py` must not be represented as a product failure. Run
the full suite only after resolving that environment dependency without adding
user-local paths back to repository tests.

- [ ] **Step 2: Request independent code review**

Use `superpowers:requesting-code-review`. The reviewer must inspect:

- exact remote-default semantics;
- no mutation of the primary checkout;
- race retry and fail-closed behavior;
- target symlink/ignore checks;
- Beads identity as a postcondition;
- rollback expected-old-value deletion;
- literal shell parsing and quoted-content negatives;
- renderer ownership and host parity; and
- preservation of active one-writer policy.

Fix every in-scope finding and rerun Step 1. Do not summarize unresolved work
and stop.

- [ ] **Step 3: Run the real dashboards probe from CAKE cwd**

Before mutation, choose unique names and prove absence:

```bash
if git -C /Users/alexandervyhmeister/GitHub/dashboards \
  show-ref --verify --quiet refs/heads/probe/escapement-ra0g-dashboards
then
  echo "probe branch already exists" >&2
  exit 1
fi
test ! -e /Users/alexandervyhmeister/GitHub/dashboards/.worktrees/escapement-ra0g-dashboards
```

From `/Users/alexandervyhmeister/GitHub/cake`, run the feature-branch CLI by
absolute path and preserve its JSON:

```bash
python3 -B \
  /Users/alexandervyhmeister/GitHub/escapement/.worktrees/escapement-ra0g-design/bin/escapement-worktree \
  create \
  --repo /Users/alexandervyhmeister/GitHub/dashboards \
  --name escapement-ra0g-dashboards \
  --branch probe/escapement-ra0g-dashboards \
  | tee /private/tmp/escapement-ra0g-dashboards-result.json
DASHBOARDS_SOURCE_SHA="$(
  jq -r .source_sha /private/tmp/escapement-ra0g-dashboards-result.json
)"
```

Verify independently:

```bash
git -C /Users/alexandervyhmeister/GitHub/dashboards/.worktrees/escapement-ra0g-dashboards \
  rev-parse HEAD
git -C /Users/alexandervyhmeister/GitHub/dashboards/.worktrees/escapement-ra0g-dashboards \
  rev-parse --path-format=absolute --git-common-dir
bd -C /Users/alexandervyhmeister/GitHub/dashboards context --json
bd -C /Users/alexandervyhmeister/GitHub/dashboards/.worktrees/escapement-ra0g-dashboards \
  context --json
```

Expected: HEAD equals `source_sha`; common directory is dashboards `.git`; the
stable Beads identity fields match; CAKE is never named as owner.

- [ ] **Step 4: Run the CAKE explicit-source probe**

Resolve a stable explicit commit before invocation:

```bash
CAKE_SOURCE_SHA="$(
  git -C /Users/alexandervyhmeister/GitHub/cake \
    rev-parse 'HEAD^{commit}'
)"
python3 -B \
  /Users/alexandervyhmeister/GitHub/escapement/.worktrees/escapement-ra0g-design/bin/escapement-worktree \
  create \
  --repo /Users/alexandervyhmeister/GitHub/cake \
  --name escapement-ra0g-cake \
  --branch probe/escapement-ra0g-cake \
  --source "$CAKE_SOURCE_SHA"
```

Verify the created HEAD equals `CAKE_SOURCE_SHA` and the Beads contexts match.

- [ ] **Step 5: Remove all probe residue with ownership checks**

For each target, first verify its common directory and branch. Then run:

```bash
git -C /Users/alexandervyhmeister/GitHub/dashboards \
  worktree remove --force \
  /Users/alexandervyhmeister/GitHub/dashboards/.worktrees/escapement-ra0g-dashboards
git -C /Users/alexandervyhmeister/GitHub/dashboards \
  update-ref -d \
  refs/heads/probe/escapement-ra0g-dashboards \
  "$DASHBOARDS_SOURCE_SHA"
git -C /Users/alexandervyhmeister/GitHub/cake \
  worktree remove --force \
  /Users/alexandervyhmeister/GitHub/cake/.worktrees/escapement-ra0g-cake
git -C /Users/alexandervyhmeister/GitHub/cake \
  update-ref -d \
  refs/heads/probe/escapement-ra0g-cake \
  "$CAKE_SOURCE_SHA"
```

Verify:

```bash
test ! -e /Users/alexandervyhmeister/GitHub/dashboards/.worktrees/escapement-ra0g-dashboards
test ! -e /Users/alexandervyhmeister/GitHub/cake/.worktrees/escapement-ra0g-cake
if git -C /Users/alexandervyhmeister/GitHub/dashboards \
  show-ref --verify --quiet refs/heads/probe/escapement-ra0g-dashboards
then
  echo "dashboards probe branch remains" >&2
  exit 1
fi
if git -C /Users/alexandervyhmeister/GitHub/cake \
  show-ref --verify --quiet refs/heads/probe/escapement-ra0g-cake
then
  echo "CAKE probe branch remains" >&2
  exit 1
fi
```

Expected: target absent; `show-ref` returns nonzero. Do not run `worktree prune`
or clean unrelated existing worktree residue.

- [ ] **Step 6: Dispatch the required outcome verifier**

Give an independent verifier the approved design, CLI JSON, and commands from
Steps 3-5. It must independently inspect:

- the actual dashboards and CAKE common directories;
- expected versus actual source SHAs;
- primary checkout branches unchanged;
- matching live Beads contexts;
- direct-command guard output; and
- zero named probe paths/branches afterward.

The verifier may not accept tests or code inspection as sufficient. It must not
write production code. If it finds residue or a failed outcome, fix it or
escalate one precise blocker.

- [ ] **Step 7: Push and open the pull request**

Run:

```bash
git status --short --branch
git push -u origin fix/escapement-ra0g
gh pr create \
  --base main \
  --head fix/escapement-ra0g \
  --title "Replace repository-specific worktree routing" \
  --body "## Outcome
Escapement now owns one repository-neutral, exact-source worktree creation transaction.

## Oracle
Tests reject stale primary HEAD, hardcoded main, wrong-repository routing, unsafe targets, mismatched Beads context, quoted-command false positives, and destructive rollback.

## Verification
- Focused CLI, guard, policy, renderer, root-checkout, and isolation suites pass.
- Real CAKE and dashboards probes matched expected SHAs, Git common directories, and Beads contexts.
- Named probe worktrees and branches were removed after verification."
```

The PR body must summarize the business outcome, mutation controls, focused
test results, real CAKE/dashboard probes, and cleanup proof. Do not put memory
citations in the PR.

- [ ] **Step 8: Carry the PR through green checks and merge**

Run:

```bash
PR_NUMBER="$(gh pr view --json number --jq .number)"
gh pr checks "$PR_NUMBER" --watch
gh pr merge "$PR_NUMBER" --auto --squash
gh pr view "$PR_NUMBER" --json state,mergedAt,mergeCommit,statusCheckRollup
```

Address review and CI failures; do not weaken the oracle. Expected: PR state is
`MERGED` and all required checks are successful.

- [ ] **Step 9: Refresh the live Claude and Codex plugins from merged main**

Use a clean deployment checkout on merged `main`; do not deploy the feature
worktree. Fast-forward the configured pinned checkout under the existing
Escapement authorization, then run:

```bash
./scripts/plugin-update.sh
./scripts/codex-plugin-update.sh
```

These commands mutate user-level installed plugin state and must run only after
merge under the existing deployment authorization.

- [ ] **Step 10: Verify installed runtime behavior**

Resolve the installed Claude and Codex plugin roots through their authoritative
registries. Verify each contains:

```text
bin/escapement-worktree
bin/escapement_worktree.py
```

Drive each installed `beads_worktree_guard.py` with its real host payload shape:

- a quoted search expression produces no denial;
- a real `git worktree add` produces a denial whose repair command points at
  the installed bundled CLI; and
- the installed CLI `--help` exits zero.

Open fresh Claude and Codex sessions after refresh so they do not retain old
plugin roots. Confirm their injected policy names `escapement-worktree`, not
`cake-worktree` or `bd worktree create`.

- [ ] **Step 11: Close the tracked outcome and verify final residue**

Only after merge and installed-plugin verification:

```bash
bd close escapement-ra0g
git status --short --branch
git -C /Users/alexandervyhmeister/GitHub/cake worktree list --porcelain
git -C /Users/alexandervyhmeister/GitHub/dashboards worktree list --porcelain
```

Confirm no named probe path/branch remains. The design worktree may be removed
only after its branch is merged and its clean state is verified.
