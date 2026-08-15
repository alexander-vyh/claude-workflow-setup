# Test Oracle Brief: Minimum Worktree Lifecycle

## Outcome

Every Escapement-created worktree gets one durable receipt. After its exact
change is merged to the live GitHub default branch, `escapement-worktree finish`
removes only the clean, unlocked, inactive local worktree, its exact local ref,
and the receipt. The existing external supervisor retries receipts an agent did
not finish.

## Independent oracle

Use the real Git worktree registry, current full ref and HEAD, ignored-inclusive
status, Git worktree lock, supported lease/process/CWD observations, authenticated
GitHub repository/default-branch and exact-head merged PR evidence, repository
policy at that live commit, and final path/registry/ref/receipt observations.

## Constraints

- Receipt data selects a candidate; it never overrides observed Git/GitHub facts.
- Hold one receipt lock and the existing repository lock through the final local
  recheck and first mutation.
- The repository lock coordinates Escapement writers. Recheck observable local
  state immediately before removal; arbitrary same-user writes after that final
  observation are outside this cooperative boundary.
- Remove the worktree without force; delete the ref with expected-old SHA; delete
  the receipt last. Never delete a remote branch.
- An in-worktree call records `pending`; the external supervisor finishes later.
- Missing GitHub, activity, Git, or registry evidence preserves the candidate.
- Reuse the existing supervisor and install paths. Add no new daemon, health
  framework, retry database, inspection CLI, legacy sweep, or non-macOS claim.

## Invalid implementation and controls

Reject ordinary `git status` without ignored files, ancestry-only landing,
receipt/source-SHA authorization, force removal, unconditional ref deletion,
self-activity exemption, and inspect-then-unlock deletion. Positive control is a
clean exact-head merged worktree. Negative controls are ignored content, lock or
active CWD, moved ref, wrong/unmerged PR, and unavailable GitHub. Creation failure
must not leave a false receipt; finish failure must leave the candidate intact.

## Final verification

Run focused receipt/finish tests, existing worktree transaction/bootstrap tests,
one supervisor tick test, generated-surface check, and isolated Claude/Codex
package execution. Then exercise a real created worktree through pending handoff
and external finish, independently proving local path/registration/ref/receipt
absence and remote ref survival.
