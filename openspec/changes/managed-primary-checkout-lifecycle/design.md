## Context

Escapement currently treats a non-bare primary checkout as the repository control
plane: it owns the common Git directory, `.worktrees/`, repository policy, and
Beads discovery. Worktree creation nevertheless sources new branches directly
from the exact advertised `origin/HEAD`, so the primary checkout can remain
behind while linked work continues. The finish supervisor removes receipt-backed
worktrees but does not reconcile the primary checkout afterward.

The lifecycle must preserve the primary checkout's index and files. Updating a
branch ref behind a checked-out worktree, resetting user state, or making task
creation depend on an unsafe root repair would violate that boundary.

## Goals / Non-Goals

**Goals:**

- Diagnose primary-checkout eligibility using actual Git topology and worktree
  state.
- Fast-forward an eligible checked-out default branch to the exact SHA
  advertised by the remote default branch.
- Reuse the existing per-common-directory transaction lock.
- Attempt synchronization during default-source worktree creation and after a
  completed finish without blocking those lifecycle outcomes when the root is
  merely ineligible.
- Package the public command and implementation identically for source, Claude,
  and Codex installations.

**Non-Goals:**

- Supporting a true bare repository as the primary control plane.
- Resetting, stashing, cleaning, switching, rebasing, merging divergent work, or
  resolving user changes.
- Keeping a long-running background process solely to poll remote state.
- Replacing the existing exact-remote source selection for task worktrees.

## Decisions

### Synchronize through the checked-out worktree

Escapement will run `git merge --ff-only <advertised-sha>` in the primary
checkout after proving that HEAD is symbolic on the advertised default branch,
the worktree is clean, and HEAD is an ancestor of the advertised SHA. It will
then verify branch, HEAD, and cleanliness again. This updates the branch, index,
and files as one normal Git worktree operation. Direct `update-ref`, hard reset,
and branch switching are rejected because they can desynchronize or destroy the
checked-out state.

### Separate remote resolution from root eligibility

The existing remote-default resolver remains authoritative and returns the
fetched exact SHA plus tracking ref. Root synchronization consumes that resolved
source; it never substitutes a stale local branch. Explicit-source worktree
creation does not synchronize the root because an explicit task source says
nothing about the repository default.

### Use structured non-mutating dispositions

The root operation returns `synchronized`, `up_to_date`, or `ineligible` with a
stable reason. The explicit `sync-root` command exits unsuccessfully for an
ineligible root so automation cannot confuse refusal with synchronization.
Create and finish surface the same disposition but continue when the reason is a
safe refusal. Remote resolution and Git execution failures remain hard errors for
the explicit command; creation retains its existing failure behavior when it
cannot establish a safe task source.

### Integrate at serialized lifecycle boundaries

Default-source creation attempts root synchronization after resolving the exact
remote source and before creating the task branch, within the existing
repository transaction lock. Receipt-backed finish attempts it after local
cleanup completes. Both paths use the same implementation and report the
result. No separate daemon or duplicate lock is introduced.

### Preserve generated host parity

The new module is added to the renderer's staged worktree runtime. Existing
generated-surface checks and installed-update tests prove that source, Claude,
and Codex packages carry the same executable command. Root mutation protection
remains limited to host hook payloads that expose enough path context; lifecycle
synchronization does not claim to make arbitrary shell mutation preventable.

## Risks / Trade-offs

- [Remote moves between advertisement and fetch] -> Reuse the resolver's
  advertise/fetch/recheck protocol and accept only the exact matched SHA.
- [State changes between eligibility checks and merge] -> Hold the common-dir
  lock and verify branch, HEAD, and cleanliness after the fast-forward.
- [Dirty or divergent primary stays stale] -> Return a stable ineligible reason,
  preserve all user state, and keep task worktree creation independent.
- [Finish succeeds but remote lookup is unavailable] -> Treat post-finish root
  sync as a reported best-effort lifecycle result rather than undoing completed
  cleanup.
- [Installed surfaces drift] -> Renderer checks, package fixture tests, and a
  post-merge installed command probe gate completion.

## Migration Plan

1. Add behavioral Git fixtures and package-parity checks.
2. Ship the root lifecycle module and public command through generated packages.
3. Integrate non-blocking attempts into create and completed finish receipts.
4. Merge on green, refresh Claude and Codex plugins, and run the installed
   command against a disposable real remote/primary topology.

Rollback removes the command and integrations; no repository migration or
persistent schema change is required.

## Open Questions

None. Continuous polling can be proposed separately if event-boundary
synchronization proves insufficient.
