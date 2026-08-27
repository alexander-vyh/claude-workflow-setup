# Test Oracle Brief: Scoped Agent Delegation

## 1. Business invariant

An unmanaged Claude Agent or Explore call succeeds on its first attempt without
Escapement preparation, denial, ledger creation, or bookkeeping-only Beads. In a
managed task session, Escapement records and verifies the real child lifecycle
automatically, but bookkeeping failure never removes native Agent capacity.
An ordinary copy from session scratch space to `~/Downloads` is allowed without
a waiver or root-checkout hook invocation. Explicit path-bearing edit tools
remain denied when they target a managed primary checkout.

## 2. Independent source of truth

- Raw PreToolUse and PostToolUse payloads captured from the installed Claude
  version.
- Structured Agent tool results in the trusted parent transcript.
- Distinct native child transcripts and timestamps.
- Peer-message delivery and terminal/idle records.
- Trusted `session_mode.json` created by a real `bd update <id> --claim` flow.
- The public Agent result, harness verification decision, and installed plugin
  registration, not private helper calls.
- Installed hook registration plus explicit edit-tool destination paths. An
  arbitrary shell command has no sound PreTool destination oracle.

## 3. Solution constraints

- Beads remains work state only.
- Unmanaged Agent capacity fails open.
- Managed completion verification fails closed.
- No prompt parsing, synthetic child IDs, generated-ID identity assertions,
  ledger deletion, or archive-as-resolution.
- Host parsing stays in a Claude adapter; the ledger consumes normalized events.
- Root-checkout hard enforcement is limited to Claude's built-in path-bearing
  Write, Edit, NotebookEdit, and MultiEdit payloads. The hook must not register
  for or classify Bash:
  predicting arbitrary shell effects would require executing the command or
  reimplementing shell state, expansion, control flow, redirection, and dynamic
  evaluation, neither of which is an acceptable gate.
- The hook must not register for Serena symbol edits. Serena `relative_path` is
  relative to Serena's independently activated project, and the Claude hook
  payload does not expose that root. Claude cwd is not a valid substitute.
- The same Serena boundary applies to advisory path-classifying gates such as
  TDD and Test Oracle Brief enforcement. An `ask` decision is still global
  friction when its path classification is not trustworthy.
- Trusted task mode is an atomic first-claim-wins record. A successful claim
  remains unresolved at completion if mode persistence fails or the hook exits
  before persistence; the trusted transcript is the independent fallback.
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
- Expanding a quoted or escaped literal `~` that the shell leaves unchanged.
- Injecting an expanded home path into raw command text without shell quoting.
- Treating `mv --target-directory` as destination-only and ignoring its sources.
- Treating Codex session cwd as `exec_command.workdir` and denying a linked-
  worktree mutation as if it targeted the primary checkout.
- Blocking because an unrelated scratchpad path contains a repository-shaped
  name.
- Registering a hard Bash root-checkout gate backed by a partial shell parser.
- Leaving the Bash event registered while returning allow in the hook, which
  preserves global overhead and misleading enforcement without protection.
- Registering Serena symbol edits while resolving `relative_path` against
  Claude cwd instead of Serena's independently active project root.
- Trusting a line-separated or carriage-return-separated shell sequence as an
  exact `bd update <id> --claim` invocation.
- Selecting the first generic path-shaped field instead of the registered
  built-in tool's canonical destination field, so a surplus decoy field can
  hide the real primary-checkout destination.

## 5. Fragile implementation to reject

The tempting shortcut is to change the current denial to unconditional allow and
leave the rest of the lifecycle untouched. It would remove the visible error but
managed executions would be unrecorded, unbound, and unverifiable. The managed
first-attempt workflow test must fail that shortcut.

For the path guard, the tempting shortcut is to keep Bash registered and add a
`cp` or `Downloads` exemption. The manifest no-Bash assertion rejects the
remaining bureaucracy, while direct managed Edit/Write denial proves that the
explicit-path containment boundary remains real.

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
  it must allow without creating execution state or consulting Beads, and that
  untrusted path alone must not block completion when no trusted execution or
  claim evidence exists.
- Quoted claim prose, failed claim results, chained fail-open claim commands,
  and commands containing LF, CR, or CRLF shell line controls must not create
  trusted task mode through either PostToolUse or transcript recovery; a
  successful exact claim result must.
- Concurrent successful claims must all observe the same first persisted scope.
- A successful exact claim followed by mode-write failure or hook loss must
  remain managed-unresolved through a trusted incident or transcript witness.
- An untrusted task-mode incident path alone is not authority and must remain
  unmanaged; the same invalid path alongside trusted task or transcript
  evidence must fail closed as a participating managed artifact.
- A ledger entry sharing only a tool-use ID with an expectation must not cover
  a different task, Agent name, or host.
- A managed Agent payload missing its name or tool-use ID must record unresolved
  incident evidence before allowing native capacity.
- Unknown PostToolUse result must not bind, abort, or terminate a child.
- Spawn result without structured `agent_id` must not fabricate identity.
- Terminal and aborted executions beginning with sticky `reconcile_due` must
  finish with all deadline and claim residue cleared.
- Active managed execution with missing ledger must block completion.
- Three sequential children fail the overlap oracle.
- Three isolated children without a delivered peer dependency fail the
  collaboration oracle.
- Injected post-enable failure restores the exact disabled plugin generation.
- Claude root-checkout hook registration must contain no Bash matcher.
- A Bash payload sent directly to the source hook must return untouched without
  invoking any shell-classification helper or recording a gate signal.
- Write, Edit, NotebookEdit, and MultiEdit targeting a managed primary checkout
  must deny; every one targeting an outside path or linked worktree must allow.
- Each registered built-in tool must classify its canonical destination field
  even when a surplus noncanonical path field points outside the repository.
- Claude root-checkout hook registration must contain no Serena matcher, and a
  Serena payload sent directly to the source hook must return untouched without
  inspecting `tool_input` or recording a gate signal.
- No Claude path-classifying gate may register Serena until the host supplies
  and a captured fixture proves the active Serena project root.

## 7. Positive controls

- Fresh unmanaged Agent succeeds on its first call.
- Fresh managed Agent succeeds on its first call and records the real tool-use
  identity without a prepare command.
- A real successful Claude Bash PostToolUse claim fixture creates trusted task
  mode; its PostToolUseFailure twin does not.
- Structured spawn binds the exact native child ID.
- Three managed children overlap, communicate, terminate honestly, and release
  completion.
- A passing global canary finalizes the plugin transaction and removes rollback
  residue.
- From a primary-checkout cwd, the reported scratchpad copy to
  `~/Downloads/name.pdf` executes successfully with no root-checkout hook event.

## 8. Missing and unresolved handling

Missing state fails open for Agent capacity. It fails closed for managed
completion only when trusted task or execution-expectation evidence says the
ledger participates in the outcome. No expectation plus no ledger is explicitly
valid and silent.

## 9. Final outcome verification

1. Render and install containment; run a fresh unmanaged Claude Agent smoke and
   a fresh normal Codex session.
2. Verify the installed root-checkout registration excludes Bash, execute the
   reported scratchpad-to-`~/Downloads` command successfully, and replay the
   managed direct-Edit destination control to verify denial.
3. Run focused behavioral, fixture, state-machine, reconciliation, completion,
   renderer, package, and rollback tests.
4. Run the full repository suite.
5. Run an isolated real unmanaged smoke and managed three-agent canary.
6. Install the enabled candidate transactionally and rerun both workflows.
7. Inject canary failure and prove byte-exact restoration of the disabled
   generation.

Tests pass is component evidence only. The fresh installed workflows and public
completion decision are the final oracle.
