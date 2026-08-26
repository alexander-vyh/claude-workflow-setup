# Test Oracle Brief: Scoped Agent Delegation

## 1. Business invariant

An unmanaged Claude Agent or Explore call succeeds on its first attempt without
Escapement preparation, denial, ledger creation, or bookkeeping-only Beads. In a
managed task session, Escapement records and verifies the real child lifecycle
automatically, but bookkeeping failure never removes native Agent capacity.
An ordinary copy from session scratch space to `~/Downloads` is allowed without
a waiver, while an actual write into a managed primary checkout remains denied.

## 2. Independent source of truth

- Raw PreToolUse and PostToolUse payloads captured from the installed Claude
  version.
- Structured Agent tool results in the trusted parent transcript.
- Distinct native child transcripts and timestamps.
- Peer-message delivery and terminal/idle records.
- Trusted `session_mode.json` created by a real `bd update <id> --claim` flow.
- The public Agent result, harness verification decision, and installed plugin
  registration, not private helper calls.
- The shell-expanded destination path and resulting filesystem location, not a
  substring search over the raw command or the session cwd.

## 3. Solution constraints

- Beads remains work state only.
- Unmanaged Agent capacity fails open.
- Managed completion verification fails closed.
- No prompt parsing, synthetic child IDs, generated-ID identity assertions,
  ledger deletion, or archive-as-resolution.
- Host parsing stays in a Claude adapter; the ledger consumes normalized events.
- Root-checkout containment must use actual write targets with shell-compatible
  `~` expansion and preserve real primary-checkout denial.
- A host must not register root-checkout blocking unless its fixture proves the
  effective per-command working directory. Codex currently stays partial.
- Codex remains unsupported until its lifecycle boundary is independently
  proven.
- Global enable requires a fresh installed canary and recoverable plugin update
  transaction.

## 4. Invalid solution classes

- Teaching agents to call `prepare` before every Agent invocation.
- Creating a child Bead for every research helper.
- Returning allow while silently dropping all managed execution evidence.
- Treating spawn as terminal completion.
- Fabricating native child identity.
- Warning whenever any session lacks `executions.json`.
- Leaving a host-incompatible denying hook registered.
- Enabling globally based only on unit tests or generated-file parity.
- Treating an unexpanded `~/...` destination as relative to the repository cwd.
- Treating Codex session cwd as `exec_command.workdir` and denying a linked-
  worktree mutation as if it targeted the primary checkout.
- Blocking because an unrelated scratchpad path contains a repository-shaped
  name.

## 5. Fragile implementation to reject

The tempting shortcut is to change the current denial to unconditional allow and
leave the rest of the lifecycle untouched. It would remove the visible error but
managed executions would be unrecorded, unbound, and unverifiable. The managed
first-attempt workflow test must fail that shortcut.

For the path guard, the tempting shortcut is to exempt `cp` or all paths
containing `Downloads`. The real in-repo destination negative control must fail
either shortcut.

## 6. Negative controls

- Unmanaged session with no ledger must not emit a warning or create state.
- Managed Agent call whose state write fails must still reach Claude while the
  completion decision remains unresolved.
- Managed expectation-path failure must fall back to a distinct trusted incident
  path, skip ledger registration, allow Agent capacity, and keep completion
  unresolved.
- If both expectation and incident persistence fail, the public Agent hook must
  still allow with unresolved evidence and must not attempt ledger registration.
- Symlinked, world-writable, malformed, or foreign-session task mode is unmanaged:
  it must allow without creating execution state or consulting Beads.
- Unknown PostToolUse result must not bind, abort, or terminate a child.
- Spawn result without structured `agent_id` must not fabricate identity.
- Terminal and aborted executions beginning with sticky `reconcile_due` must
  finish with all deadline and claim residue cleared.
- Active managed execution with missing ledger must block completion.
- Three sequential children fail the overlap oracle.
- Three isolated children without a delivered peer dependency fail the
  collaboration oracle.
- Injected post-enable failure restores the exact disabled plugin generation.
- From a managed primary-checkout cwd, copying a scratch file to an explicit path
  under that checkout must still deny.
- Home expansion controls run under both `Downloads` and an unrelated home child
  so a directory-specific resolver cannot satisfy the oracle.

## 7. Positive controls

- Fresh unmanaged Agent succeeds on its first call.
- Fresh managed Agent succeeds on its first call and records the real tool-use
  identity without a prepare command.
- Structured spawn binds the exact native child ID.
- Three managed children overlap, communicate, terminate honestly, and release
  completion.
- A passing global canary finalizes the plugin transaction and removes rollback
  residue.
- From the same primary-checkout cwd, copying a scratch file to
  `~/Downloads/name.pdf` is allowed without a waiver and resolves to the user's
  home directory.

## 8. Missing and unresolved handling

Missing state fails open for Agent capacity. It fails closed for managed
completion only when trusted task or execution-expectation evidence says the
ledger participates in the outcome. No expectation plus no ledger is explicitly
valid and silent.

## 9. Final outcome verification

1. Render and install containment; run a fresh unmanaged Claude Agent smoke and
   a fresh normal Codex session.
2. Replay the reported scratchpad-to-`~/Downloads` command through the installed
   root-checkout hook and verify it produces no denial; replay the in-repo
   destination control and verify denial.
3. Run focused behavioral, fixture, state-machine, reconciliation, completion,
   renderer, package, and rollback tests.
4. Run the full repository suite.
5. Run an isolated real unmanaged smoke and managed three-agent canary.
6. Install the enabled candidate transactionally and rerun both workflows.
7. Inject canary failure and prove byte-exact restoration of the disabled
   generation.

Tests pass is component evidence only. The fresh installed workflows and public
completion decision are the final oracle.
