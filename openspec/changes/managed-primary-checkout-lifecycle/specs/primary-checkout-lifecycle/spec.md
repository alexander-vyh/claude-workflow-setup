## ADDED Requirements

### Requirement: Exact remote default is the synchronization authority

Escapement SHALL synchronize a primary checkout only to the commit SHA that is
both advertised by `origin/HEAD` and fetched from its advertised branch.

#### Scenario: Remote default advances

- **WHEN** the remote default branch advertises a commit newer than the clean primary checkout
- **THEN** synchronization targets that exact advertised and fetched commit

#### Scenario: Advertisement changes during resolution

- **WHEN** the advertised remote default cannot be matched to the fetched commit after the bounded retry
- **THEN** synchronization fails without moving the primary branch, index, or files

### Requirement: Eligible primary checkout fast-forwards safely

Escapement SHALL update an eligible primary checkout through a normal fast-forward
of its checked-out default branch and SHALL verify the resulting branch, commit,
worktree cleanliness, and file state.

#### Scenario: Clean default branch is behind

- **WHEN** the primary checkout is clean, on the advertised default branch, and its HEAD is an ancestor of the exact remote default SHA
- **THEN** its branch, HEAD, index, and files advance to that exact SHA

#### Scenario: Clean default branch is current

- **WHEN** the eligible primary checkout already equals the exact remote default SHA
- **THEN** synchronization reports `up_to_date` without creating a commit or changing files

### Requirement: Unsafe primary state is preserved

Escapement MUST NOT reset, stash, clean, switch, merge divergent history, or
update a checked-out branch ref behind its worktree.

#### Scenario: Primary checkout is dirty

- **WHEN** tracked or untracked primary-checkout state is present
- **THEN** synchronization reports `ineligible` and preserves HEAD, index, and every file

#### Scenario: Primary checkout is divergent

- **WHEN** primary HEAD is not an ancestor of the exact remote default SHA
- **THEN** synchronization reports `ineligible` and preserves the local commit and files

#### Scenario: Primary checkout is not on the remote default

- **WHEN** primary HEAD is detached or names a different branch
- **THEN** synchronization reports `ineligible` without switching branches

#### Scenario: Repository is bare or linked-only

- **WHEN** the requested repository is not the non-bare primary checkout that owns the common Git directory
- **THEN** the public command fails closed without modifying repository refs

### Requirement: Worktree lifecycle remains available when root is ineligible

Escapement SHALL serialize root synchronization with existing repository
transactions and SHALL NOT make an ineligible primary checkout prevent safe task
worktree creation or completed receipt cleanup.

#### Scenario: Dirty root creates fresh task worktree

- **WHEN** default-source worktree creation resolves the exact remote default but root synchronization is ineligible
- **THEN** creation succeeds at the exact remote SHA and reports the root refusal

#### Scenario: Explicit task source is requested

- **WHEN** worktree creation uses `--source`
- **THEN** Escapement creates from that explicit source without treating it as authority for root synchronization

#### Scenario: Finish completes with ineligible root

- **WHEN** receipt-backed worktree cleanup completes while the primary checkout is ineligible
- **THEN** cleanup remains completed and the result reports the root refusal

### Requirement: Public root synchronization is machine-readable

The `escapement-worktree sync-root --repo <path>` command SHALL emit a stable JSON
result containing repository, status, reason, previous SHA, and target SHA, and
SHALL return non-zero when safe synchronization did not occur due to ineligible
state or a hard error.

#### Scenario: Automation synchronizes an eligible root

- **WHEN** automation invokes `sync-root` for an eligible primary checkout
- **THEN** it receives a zero exit and a JSON `synchronized` or `up_to_date` result matching the verified Git state

#### Scenario: Automation encounters dirty root

- **WHEN** automation invokes `sync-root` for a dirty primary checkout
- **THEN** it receives a non-zero exit and a JSON `ineligible` result without mutation
