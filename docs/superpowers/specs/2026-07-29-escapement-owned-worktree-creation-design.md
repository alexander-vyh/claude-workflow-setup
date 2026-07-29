# Escapement-Owned Worktree Creation Design

## Outcome

Escapement owns one repository-neutral worktree creation transaction for agent
sessions. From any session working directory, an agent can name the repository
that should own the new worktree and receive a linked worktree:

- under that repository's ignored `.worktrees/` directory;
- on a new, explicitly named feature branch;
- based by default on the freshly fetched commit at the remote's current
  default branch;
- based instead on an explicitly requested, exactly resolved source commit when
  the caller deliberately supplies one;
- connected to the target repository's Git common directory; and
- connected to the same Beads database when the repository uses Beads.

The transaction does not require the primary checkout to be clean, current, on
the default branch, or able to check out that branch. It never fast-forwards or
switches the primary checkout.

Beads remains task state only. Native Git creates and removes worktrees;
Escapement owns creation policy, verification, and repair guidance.

## Current Failure

The current `beads_worktree_guard.py` redirects worktree creation through
repository-specific `.agents/worktree-entrypoint` markers. CAKE declares
`cake-worktree`, whose implementation is intentionally tied to CAKE,
`origin/main`, and a primary checkout currently on `main`.

The guard also derives the effective repository from a bounded parse of the
host's shell payload. A CAKE-cwd session that changes directory before creating
a dashboards worktree can therefore be redirected to the CAKE-only executable.
The repository-specific redirection solves freshness for one repository by
making cross-repository operation incorrect.

Falling back to `bd worktree create` is not sufficient. Beads 1.1.0 creates a
branch from the invoking checkout's current `HEAD` and exposes no source-ref
argument. That couples task setup to stale or occupied primary-checkout state.

The real dashboards layout is the load-bearing outcome probe: its primary
checkout may be on `root-main`, while `main` is held by another linked worktree.
A correct creator must still start a new feature branch from the fetched remote
default commit without changing either checkout.

## Tooling Decision

Use a transactional Escapement CLI backed by native Git, plus a thin
PreToolUse guard that detects bypass attempts and redirects agents to the CLI.

The canonical executable is:

```text
bin/escapement-worktree
```

The executable is core Escapement policy. It does not belong under `claude/`,
because Claude is only one host adapter, and it does not belong under
`harness/`, whose primary responsibility is continuation state and outcome
verification.

`tools/render_agent_surfaces.py` vendors the canonical executable into both
generated plugin packages. The worktree guard locates the companion executable
in its actual source or plugin layout and emits that executable path in repair
guidance. This works in plugin-only installs without assuming a user-global
wrapper on `PATH`. A convenience installation on `PATH` may be added later, but
it is not part of the correctness contract.

### Rejected Alternatives

1. **Perform creation inside the hook.** Rejected because a PreToolUse gate
   should inspect and explain, not hide network and filesystem mutations inside
   command authorization.
2. **Publish a Git command recipe.** Rejected because separate fetch, resolve,
   create, and verify commands have no transaction owner and cannot guarantee
   ordering, rollback, or consistent cross-repository routing.
3. **Extend or wrap `bd worktree create`.** Rejected because upstream Beads
   supplies no source ref, Escapement has no practical control over its release
   cadence, and making Beads authoritative for Git policy violates the ownership
   boundary.
4. **Keep repository-specific entrypoints.** Rejected because they duplicate
   policy, drift by repository, and reproduce the cross-repository failure.

## Public Interface

The CLI exposes one creation operation:

```bash
escapement-worktree create \
  --repo /path/to/repository \
  --name escapement-ra0g \
  --branch fix/escapement-ra0g

escapement-worktree create \
  --repo /path/to/repository \
  --name dependent-change \
  --branch feature/dependent-change \
  --source feature/prerequisite
```

`--repo`, `--name`, and `--branch` are required. `--source` is optional.

- `--repo` must identify a non-bare primary checkout. The CLI normalizes it to
  the repository top level and verifies that its `.git` directory is the Git
  common directory.
- `--name` is one safe basename and determines
  `<repo>/.worktrees/<name>`. Arbitrary target paths are intentionally out of
  scope.
- `--branch` is a valid Git branch name that must not already exist.
- `--source` is an explicit local Git commit-ish resolved in the target
  repository. Supplying it replaces remote-default discovery and fetch. The
  CLI makes no freshness claim for an explicit source; it guarantees only that
  creation and final verification use the exact resolved commit.

The command prints a concise success record containing repository, worktree,
branch, resolved source commit, source kind (`remote-default` or `explicit`),
and Beads verification status. Failures go to stderr and return nonzero.

## Creation Transaction

### 1. Resolve and validate the target repository

The CLI uses argument-vector subprocess calls, never `shell=True`.

It resolves:

```bash
git -C <repo> rev-parse --show-toplevel
git -C <repo> rev-parse --path-format=absolute --git-common-dir
```

The normalized top level must equal the primary checkout that owns the common
`.git` directory. Missing repositories, bare repositories, linked-worktree
paths presented as the primary repository, and unresolved common directories
fail closed with actionable errors.

### 2. Lock creation for the repository

The CLI takes an exclusive `fcntl` lock at:

```text
<git-common-dir>/escapement-worktree.lock
```

The lock serializes Escapement creation transactions in the same repository.
Git's own reference locks remain the final concurrency authority for other Git
processes.

All precondition checks, source resolution, creation, verification, and
rollback occur while this lock is held.

### 3. Validate the target and branch

Before mutation:

- validate `--name` as one non-special basename;
- validate `--branch` with `git check-ref-format --branch`;
- prove the target path is exactly `<repo>/.worktrees/<name>`;
- reject a symlinked `.worktrees` directory;
- reject an existing target;
- prove the target is ignored with `git check-ignore`;
- fail closed if ignore status cannot be determined; and
- prove `refs/heads/<branch>` does not exist.

The creator never uses `-B`, `--force`, or a pre-existing branch. This preserves
the one-writer-one-worktree invariant and avoids silently resetting prior work.

### 4. Resolve the source commit

#### Default source

With no `--source`, the independent source of truth is the remote named
`origin`, not the primary checkout and not the local `origin/HEAD` symbolic ref.

The CLI:

1. runs `git ls-remote --symref origin HEAD`;
2. requires one advertised `HEAD` symref under `refs/heads/` and its commit SHA;
3. fetches that exact branch into
   `refs/remotes/origin/<default-branch>` using an explicit refspec;
4. resolves the fetched remote-tracking ref with `^{commit}`; and
5. compares the fetched SHA with the advertised remote `HEAD` SHA.

If the remote advances between discovery and fetch, the CLI repeats discovery
and fetch once. A second mismatch fails closed and tells the caller to retry.
Missing `origin`, authentication failure, network failure, missing remote HEAD,
invalid symref output, or an unresolvable fetched commit all fail closed.

`GIT_TERMINAL_PROMPT=0` prevents an agent session from hanging on credentials.

#### Explicit source

With `--source`, the CLI skips remote-default discovery and fetch, then resolves:

```bash
git -C <repo> rev-parse --verify --end-of-options '<source>^{commit}'
```

The argument is passed literally as one subprocess argument. Missing,
ambiguous, or non-commit sources fail closed. The resulting SHA becomes the
transaction oracle. Callers who need a freshly fetched remote source should
omit `--source`; explicit source means deliberate use of repository state that
already exists.

### 5. Create with native Git

After recording that the target and branch are absent, the CLI runs:

```bash
git -C <repo> worktree add \
  -b <branch> \
  <repo>/.worktrees/<name> \
  <resolved-source-sha>
```

The exact SHA, rather than a moving ref or primary-checkout branch, is passed to
Git.

### 6. Verify the outcome

Creation succeeds only when all checks pass:

1. `HEAD^{commit}` in the new worktree equals the resolved source SHA.
2. The new worktree's symbolic branch equals `--branch`.
3. Its absolute Git common directory equals the target repository's common
   directory.
4. `git worktree list --porcelain` associates the new path with the requested
   branch.
5. The final target remains inside the non-symlinked ignored `.worktrees`
   directory.
6. If the primary repository uses Beads, `bd context --json` succeeds in both
   locations and the stable identity fields (`project_id`, `database`,
   `beads_dir`, and `repo_root`) match.

Beads verification is a postcondition, not the creation mechanism. In a
Beads-managed repository, missing `bd`, invalid context output, or an identity
mismatch fails the transaction. In a repository without Beads, the CLI reports
that Beads verification was not applicable.

## Rollback and Residue

Any failure after mutation triggers guarded rollback.

The CLI may remove the target only when:

- the target did not exist before the transaction;
- it resolves as a linked worktree of the expected Git common directory; and
- its recorded branch is the branch requested by this transaction.

It removes the worktree with native Git. It then deletes the newly created
branch atomically:

```bash
git update-ref -d refs/heads/<branch> <resolved-source-sha>
```

The expected-old-value check refuses deletion if another process or user moved
the branch. The CLI never deletes a pre-existing branch, an unrelated path, or
a worktree whose ownership cannot be proven.

If rollback cannot prove ownership or cannot complete, the command remains
failed and reports the exact residue. It never hides cleanup failure behind the
original error.

## Guard Design

`beads_worktree_guard.py` becomes an Escapement worktree-entrypoint guard.

It detects ordinary literal invocations of:

- `git worktree add`; and
- `bd worktree create`.

Both are denied and redirected to the concrete bundled
`escapement-worktree create` command. Direct Git creation is guarded in plain
and Beads repositories because fresh-source and one-writer policy belongs to
Escapement everywhere. The CLI invocation itself is allowed.

The parser uses shell-aware tokenization for a bounded set of ordinary literal
commands:

- quoted command text remains argument content;
- Git and Beads global `-C`/directory options affect repository resolution;
- a simple literal `cd <path>` preceding `&&` or `;` updates the effective
  working directory for later segments;
- dynamic expansion, aliases, nested interpreters, and adversarial obfuscation
  remain outside the hook's contract; and
- malformed or unparseable shell text fails open because the hook is an
  accidental-bypass guardrail, not a shell security boundary.

The hook performs no fetch, creation, removal, or other repository mutation.
If the bundled CLI cannot be found, direct creation remains denied and the
message identifies the broken Escapement installation.

The separate `beads_worktree_location_guard.py` is removed after its target
safety invariant moves into the transactional CLI. Keeping a second parser and
location authority would create drift rather than defense in depth.

## Policy and Generated-Surface Changes

The implementation removes or supersedes:

- `.agents/worktree-entrypoint` marker handling;
- `cake-worktree` repair text and policy references;
- rules and skills that require `bd worktree create`;
- generated Claude and Codex plugin copies of the obsolete behavior; and
- tests whose oracle is specifically Beads-owned creation.

The source-of-truth onboarding text instead states:

- Escapement owns worktree policy;
- `escapement-worktree create` is the supported creation transaction;
- native Git owns linked-worktree mechanics;
- Beads owns task state and is verified after creation when present; and
- every writing session still requires its own feature branch and worktree.

Generated files are changed through `agent-surfaces/` and
`tools/render_agent_surfaces.py`, never edited as independent authorities.

## Test Oracle Brief

1. **Business invariant:** A new task worktree belongs to the explicitly named
   repository, starts from the intended exact source commit without changing
   the primary checkout, preserves one-writer isolation, and shares live Beads
   state when Beads is present.
2. **Independent source of truth:** For the default path, the remote's advertised
   `HEAD` symref and fetched commit; for an explicit source, Git's independently
   resolved commit SHA; for repository ownership, Git's absolute common
   directory; for tracker continuity, `bd context --json` identity.
3. **Solution constraints:** Host-neutral Escapement ownership; native Git
   creation; standard-library Python; no repository-specific executable; no
   primary-checkout branch mutation; ignored `.worktrees` target; explicit new
   feature branch; generated plugin parity; user work preserved.
4. **Invalid solution classes:** Creating from primary `HEAD`; hardcoding
   `main`; trusting local `origin/HEAD` without remote discovery; resolving
   before fetch; using payload cwd instead of `--repo`; allowing direct
   creation around the transaction; using Beads as the Git policy owner;
   accepting an unsafe target; verifying only copied tracker artifacts;
   destructive rollback.
5. **Fragile implementation to reject:** Delete the repository entrypoint
   marker, allow ordinary `git worktree add`, and document that agents should
   fetch first.
6. **Negative control:** A CAKE-cwd invocation targeting dashboards, whose
   primary checkout is stale and on `root-main` while `main` is held elsewhere,
   must not resolve CAKE, primary `HEAD`, or local `main`. Additional negative
   controls cover non-ignored targets, pre-existing branches, missing remote
   HEAD, mismatched Beads context, quoted command text, and rollback after a
   branch moves.
7. **Positive control:** Default creation produces a non-empty worktree at the
   fetched remote-default SHA; explicit-source creation produces a non-empty
   worktree at its independently resolved SHA; plain Git repositories work
   without Beads; real direct creation attempts are still denied with a usable
   repair command.
8. **Missing/unresolved handling:** Repository, remote, source, target-safety,
   branch, creation, and required Beads-verification failures fail closed.
   Unparseable hook command text fails open because only the transactional CLI,
   not the heuristic hook parser, is the policy authority.
9. **Final outcome verification:** Run focused CLI, guard, generated-surface,
   and architecture tests; render both plugins; then create real temporary CAKE
   and dashboards worktrees from a CAKE-cwd session. Compare expected and actual
   SHAs, branches, common directories, and Beads contexts. Exercise an explicit
   alternate source. Remove the temporary worktrees and branches and verify
   both repositories have no probe residue.

## Mutation Challenges

Before production implementation, a mutation challenger must prove the tests
reject at least these plausible bad implementations:

| Bad implementation | Required rejecting check |
|---|---|
| Use primary checkout `HEAD` as the source | Stale/non-default dashboards primary produces the wrong SHA |
| Replace remote discovery with hardcoded `origin/main` | Fixture whose remote default branch is `trunk` |
| Resolve the remote-tracking ref before fetch | Remote advances after the local tracking ref was recorded |
| Ignore `--repo` and operate from payload cwd | CAKE-cwd command targeting dashboards yields the wrong common directory |
| Allow direct `git worktree add` after deleting marker support | Claude and Codex guard fixtures deny a real invocation |
| Treat a copied Bead record as continuity proof | Mismatched `bd context` identities fail verification |
| Roll back with unconditional `branch -D` | Branch moved after creation survives cleanup and produces explicit residue |
| Accept inspection errors as safe target status | `git check-ignore` error fails closed |

Implementation remains blocked until every named bad implementation fails at
least one behavioral, fixture, contract, architecture, or static check.

## Final Delivery

The change is delivered only when:

1. focused tests and generated-surface checks pass;
2. the real CAKE and dashboards probes pass and leave no temporary residue;
3. the feature branch is pushed and reviewed through a pull request;
4. the pull request merges under Escapement's authorization policy;
5. the deployed Escapement plugin contains the new CLI and guard behavior; and
6. a fresh Claude and Codex session receives the new repair path rather than
   `cake-worktree` or `bd worktree create`.

