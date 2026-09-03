# Test Oracle Brief: Worktree Bootstrap Lock Scope

## Business invariant

Repository-declared provisioning must not prevent an unrelated Escapement
worktree from being created in the same repository. A worktree is reported
ready only after its bootstrap succeeds and its exact Git identity is
reverified. Interrupted creation is recoverable without deleting replacement
or user-owned state.

## Independent source of truth

Two real public `escapement-worktree create` processes operate against a
disposable bare origin. Files written by their repository-declared bootstrap
commands establish the concurrency ordering independently of implementation
helpers. Git's NUL-delimited worktree registry, exact refs and HEADs, a durable
per-creation token in the Git administrative directory, and the lifecycle
receipt establish recovery ownership. Final path, ref, registry, receipt, and
public JSON observations determine the outcome.

## Solution constraints

- Keep the per-lifecycle lock across creation and provisioning.
- Hold the repository lock for shared source/ref/worktree transitions and their
  verification, never for the repository-declared bootstrap process.
- Persist and fsync a write-ahead receipt before the transaction creates its
  branch or worktree registry entry.
- Allocate the branch through Git's prepared ref-transaction protocol. While
  Git still holds the unpublished loose-ref lock, persist and fsync its exact
  identity, then commit that same inode to the public ref name. Suppress the
  new branch's reflog so rollback does not claim a file it could not journal
  before publication. SHA or pathname equality never substitutes for a missing
  creation identity; prepared, committed, and pre-identity crash windows fail
  closed.
- Bind recovery to an unguessable token persisted in both the private receipt
  and the created Git administrative directory.
- Move a rollback candidate to a token-derived claim path, verify the moved
  instance, and journal that claim before atomically detaching the exact
  worktree and administrative directories into a private disposal directory.
  Pin both public parents with directory descriptors. If the identity changes
  at the rename boundary, atomically restore the displaced replacement without
  overwriting a newly occupied public name.
- Never pass a rollback path to `git worktree remove`; delete only identities
  atomically detached and inode-checked inside the private disposal directory.
- Preserve a worktree created by a failed `git worktree add` when no exact
  token or controller-held administrative identity was established, while
  cleaning an owned branch if the add failed before creating any worktree.
- Delete an owned branch only while holding Git's exclusive loose-ref lock,
  after verifying the exact direct-ref content and journaling lock/ref identity.
  Atomically rename public loose-ref, reflog, and owned-lock names into
  token-private claims, verify the moved inode, and delete only those private
  claims. The claim directories must be owned by the current user and mode
  `0700`; this private namespace is the deletion trust boundary. A replacement
  installed at a public name after detachment must survive, while a private
  claim whose inode differs from the journal must be preserved. Packed,
  symbolic, changed, and unowned lock states fail closed; every crash after a
  durable detach replays only through the lifecycle-owned claim journal. A
  late Git owner restores the exact ref without overwriting a public name, and
  both the detached and restoring phases replay across crashes.
- Pin every component of nested loose-ref and reflog parents with no-follow
  directory descriptors. Inspection, locking, detachment, and restoration use
  those descriptors so a later parent rename or symlink cannot redirect a
  rollback into another directory.
- Replacement controls retain the original inode under a separate name before
  creating the replacement. They must not rely on `unlink` followed by create,
  because filesystems may immediately reuse the inode and make the negative
  control inert.
- Preserve the existing standard-library-only Python runtime, public create and
  finish contracts, source resolution, Beads verification, guarded rollback,
  and generated Claude/Codex parity.
- The existing supervisor remains the retry authority; add no daemon, database,
  generalized workflow engine, or repository-specific bootstrap behavior.

## Invalid solution classes

Reject releasing the repository lock without a durable receipt, writing the
receipt only after `git worktree add`, treating path/branch/SHA equality as
ownership, marking ready before locked re-verification, routing an incomplete
creation into finish, blocking the supervisor behind a live bootstrap, and
recovering with a check-then-path-delete race, force, or unconditional ref
deletion. Reject rollback state written only after worktree removal, any use of
`git worktree remove` during recovery, dereferencing ref deletion, and semantic
path/branch/SHA checks as authority after a partial add failure. Reject a
ceremonial ref lock released before detachment, packed-ref deletion, direct
unlink of a verified public pathname, and any replay that consumes a
replacement lock or cannot recover a token-private claim. Reject checking ref
parents once and later mutating through ordinary multi-component pathnames.
Reject first learning a ref or reflog inode during recovery, because a
same-content replacement may already occupy that pathname. Reject publishing a
new ref before its exact inode is durable in the receipt, and reject creating a
branch reflog that cannot be journaled before its public name appears.

## Fragile implementation to reject

Move only `run_bootstrap()` outside the current `with
repository_transaction_lock(...)` block while retaining the post-bootstrap-only
receipt. The concurrency test would pass, but the pre-mutation receipt and crash
recovery controls must fail it.

## Negative control

While bootstrap is blocked, a second public create must reach its own bootstrap.
An interrupted pending receipt whose target was replaced by a same-branch,
same-SHA Git worktree must be preserved. A moved ref must also survive recovery.
Calling recovery while the creator still holds its lifecycle lock must return
promptly without mutation. A replacement installed at the destructive boundary
and a branch whose target disappeared before any rollback claim must survive.
A symbolic branch replacement injected at the delete boundary must preserve
both the symbolic ref and its referent. A failed add that actually created an
unbound worktree must preserve it, while a failure before add must clean the
transaction-owned branch. Replacing a ref, reflog, or rollback lock after its
exact inode is detached must leave the replacement intact, and killing recovery
with only token-private claims remaining must be safely replayable. Replacing
an intermediate ref or reflog parent with a symlink at the mutation boundary
must preserve every file in the symlink target.
A same-SHA loose ref or arbitrary reflog installed after creation but before
recovery must survive by inode identity, with the receipt retained. If an
allocated ref disappears before rollback creates its exact private claim, its
absence must also retain the receipt rather than count as successful deletion.
Replacing either a claimed worktree directory or its Git administrative
directory at the exact detach boundary must restore that foreign directory to
its public name and retain the receipt. Killing recovery after the forward
rename but before its identity check must replay the same restoration from the
token-private disposal claim whether the replacement is a directory, regular
file, or symlink.
Killing recovery after a late-owner ref restoration publishes the exact inode
but before its private claim is removed must replay to one public ref, no
private claim, and a retained receipt naming the live owner.

## Positive control

Both concurrent creations finish with non-empty registered worktrees and
`created` receipts. An abandoned token-matching pending creation is removed
completely, while an ordinary successfully created lifecycle still completes
through the existing finish path.

## Missing and unresolved handling

Malformed receipts, missing required pending-creation tokens, unverifiable Git
identity, replacement targets, moved refs, and uncertain ownership fail closed
and retain an explicit `bootstrap_failed` receipt. A pending receipt with no
created target or prepared ref is safe to clear. A live lifecycle lock returns
`bootstrap-active` rather than waiting or mutating. A durable rollback claim is
replayable whether interruption occurs immediately before or after worktree
removal or ref/lock detachment; a merely missing target or public ref never
substitutes for that claim.

## Final outcome verification

Run:

```bash
pytest -q tests/test_escapement_worktree_two_phase.py \
  tests/test_escapement_worktree_transaction.py \
  harness/tests/test_worktree_lifecycle_supervisor.py \
  tests/test_worktree_policy_surfaces.py
```

Then render and refresh both installed plugin surfaces. Against an isolated
disposable repository, run two public installed creates with one bootstrap held
open; observe the second reach provisioning before releasing the first, both
return ready, and both receipts finish in `created` state.
