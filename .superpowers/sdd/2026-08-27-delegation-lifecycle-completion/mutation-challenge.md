# Mutation Challenge: Delegation Lifecycle Completion

## Verdict

**APPROVED — 0 blocking oracle gaps.**

QC commit `a2f0e43` closes all four blockers from the first mutation review with
executable tests/checks. The controls were observed RED before production
implementation, the captured fixtures were independently reproduced from raw
Claude 2.1.247 records, and every named fragile implementation fails a public
behavioral, fixture-contract, state-machine, or updater-transaction check.

This approval opens production implementation. It is not evidence that the
feature is implemented, enabled, installed, or live-verified.

## Oracle basis

- **Business invariant:** unmanaged native Agent/Explore capacity remains
  available without Escapement state; a managed task may claim completion only
  after exact native child lifecycle evidence is durably observed and honestly
  resolved.
- **Independent source of truth:** installed Claude 2.1.247 raw records, trusted
  parent/child transcripts, durable ledger state, public completion decisions,
  and the selected installed plugin generation. Private adapter calls and prompt
  claims are not accepted as outcome proof.
- **Invalid solution classes:** unconditional allow without observation,
  fabricated child identity, terminal-on-spawn, idle-as-terminal,
  replay-renewed deadlines, standalone roots treated as broken, and updater
  commit before a rollback-capable live canary.
- **Missing-data policy:** native capacity fails open; trusted managed evidence
  remains unresolved and fails closed at completion.
- **Final outcome oracle after implementation:** fresh unmanaged Claude/Codex
  smokes; an installed managed three-child canary with distinct native IDs,
  overlap, peer dependency, honest terminal/abort cleanup, and public completion
  release; injected live-canary failure with exact rollback; source/rendered/
  selected-cache/active-hook parity.

## Evidence reviewed

### Commit boundary

`a2f0e43` adds only sanitizer, sanitized fixtures/provenance, and test files. It
does not add `harness/bin/claude_agent_lifecycle.py` or
`scripts/delegation-canary.py`, and it does not change production behavior.
Therefore the oracle commit precedes the implementation it constrains.

### Independent fixture reproduction

The sanitizer was run in `--check` mode against the raw terminal stream, raw
no-spawn stream, and historical Claude transcript referenced by the provenance
sidecar. It exited 0 and reproduced both committed artifacts byte-for-byte:

```text
python3 tools/sanitize_claude_lifecycle_fixtures.py \
  --terminal-stream /private/tmp/escapement-xncx-capture.ckwON8/provenance-terminal-stream.jsonl \
  --no-spawn-stream /private/tmp/escapement-xncx-capture.ckwON8/provenance-no-spawn-stream.jsonl \
  --historical-transcript /Users/alexandervyhmeister/.claude/projects/-Users-alexandervyhmeister-GitHub-dashboards/953720f5-bcc5-44ca-8c85-1d8869be0e79.jsonl \
  --fixture harness/tests/fixtures/claude-agent-lifecycle-2.1.247.jsonl \
  --provenance harness/tests/fixtures/claude-agent-lifecycle-2.1.247.provenance.json \
  --check
```

The raw stream init records report `claude_code_version == "2.1.247"`; the
three historical witness records report `version == "2.1.247"`. The sanitizer
copies an explicit JSON-pointer allowlist and imports no adapter or lifecycle
event constants. The sidecar records raw-record SHA-256 digests, capture source,
line, timestamp, sanitizer version/command, and retained pointers.

The positive fixture relationships are independently witnessed: dispatch
tool-use ID links to task-start, async result, and terminal; native task ID links
to async result, task-start, terminal, and peer origin; no-spawn has an exact
error result and no identity; interactive spawn has equal non-empty structured
`agent_id`/`teammate_id`; the historical content-only idle record is followed by
later child activity and is deliberately a negative parsing control.

Ruling: the sanitized fixtures are capture-derived independent evidence, not an
adapter echo. The eventual installed canary must still consume live raw host
output rather than replay these fixtures.

### Executable RED

Focused command:

```text
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider \
  harness/tests/test_claude_agent_lifecycle.py \
  harness/tests/test_claude_agent_lifecycle_public.py \
  harness/tests/test_execution_standalone_parent.py \
  tests/test_delegation_canary.py \
  tests/test_plugin_update_canary_transaction.py \
  harness/tests/test_execution_ledger.py \
  harness/tests/test_execution_validation.py \
  harness/tests/test_execution_reconcile.py
```

Observed: **36 failed, 101 passed**.

The failures are causal and expected: adapter/canary files are absent; public
PostToolUse remains unresolved; `dispatch_aborted` is unknown; terminal cleanup
retains residue; replay renews activity; aborted schema validation is absent;
standalone roots remain unresolved; and the updater neither invokes the canary
nor rolls back on injected canary failure. The fixture/provenance contract tests
pass before implementation.

## Prior blocker closure

### 1. Synthetic identity negatives

**Closed.**

`test_invalid_interactive_spawn_identity_shapes_produce_no_events_or_mutation`
parameterizes agent-only, teammate-only, unequal, empty, prose-only, and surplus
invented-native-child shapes. Every case requires literal `events == []`,
byte-identical ledger state, no bound child, blocked managed completion, and no
deny decision. Mismatched agent name, tool-use ID, task ID, and late generation
also require no event or mutation.

This rejects accepting one child-looking field, copying it into a missing field,
parsing identity prose, or binding a surplus invented field.

### 2. Public prefix completion oracle

**Closed.**

`test_background_spawn_prefix_binds_starts_and_blocks_completion` requires the
captured spawn prefix to emit exactly `child_bound`, `child_started`, persist a
running state with no terminal fields, and return public completion block.

`test_historical_idle_text_is_nonterminal_and_later_peer_activity_is_accepted`
requires the content-only idle record to emit no event or mutation, remain
running and completion-blocked, then accept separately observed peer activity.

`test_matching_peer_activity_and_terminal_are_separate_prefixes` requires peer
activity to remain running, a separate exact terminal record to close, complete
deadline/claim cleanup, and result application before public completion release.

This rejects terminal-on-spawn, bare idle-as-terminal, and completion release
from terminal state without applied outcome.

### 3. Updater ordering and single transaction ownership

**Closed.**

`test_single_transaction_commits_only_after_parity_and_live_canary` records the
transaction helper, parity, and canary calls. It requires exactly one begin,
candidate selection and installed parity while the original journal and backup
state remain live, one canary, and one later commit with no rollback.

`test_canary_failure_uses_original_rollback_and_is_byte_exact` requires exactly
one begin and one rollback, no commit, journal presence during canary, nonzero
updater status, restoration of settings, registry, wrapper targets, executable
modes, supervisor marker/plist/load state, removal of rollback residue, and
idempotent later recovery behavior.

The call trace plus live snapshot rejects both early commit and the forbidden
"commit, then open a second transaction" workaround.

### 4. Independent sanitized fixture provenance

**Closed.**

The raw sources are available for QC, their Claude version is independently
visible, and sanitizer `--check` proves the committed fixture and sidecar are
mechanical allowlist projections of those exact records. The sanitizer predates
and does not import the missing adapter. Fixture tests assert the witnessed
identity/temporal relationships rather than only file existence, key presence,
or hash formatting.

## Named mutation matrix

| Bad implementation | Check that fails it | Why it fails |
| --- | --- | --- |
| Unconditional allow without observation | `test_public_posttool_durably_applies_captured_interactive_spawn_prefix`; managed canary outcome test | Requires durable running child state and blocked completion, not merely a successful hook return. |
| Synthetic child binding | Parameterized invalid interactive spawn identity test | One-sided, unequal, empty, prose-only, and invented identities must emit no event and leave the ledger byte-identical. |
| Terminal-on-spawn | Background spawn prefix test | Spawn must produce exactly bind/start, remain running, have no terminal fields, and block completion. |
| Bare idle-as-terminal | Historical idle/later-activity test | Idle emits no terminal event, leaves state running and blocked, and permits later activity. |
| Replay-renewed deadlines | Identical replay no-op, conflicting replay rejection, and new-identity positive control in `test_execution_ledger.py` | Identical replay must be byte-stable; reused identity with changed semantics must reject; only a genuinely new event may advance idle time and never hard deadline. |
| Standalone parent treated as broken | Standalone SessionStart and supervisor tests | Absent/null is silent standalone with no parent lookup; malformed values remain unresolved. |
| Updater commit before live canary/rollback | Single-transaction success/failure tests | Canary observes original uncommitted journal and live candidate; commit must follow success, while failure must use the same rollback authority. |

## Gate decision

No named fragile implementation can pass every relevant committed check. The
oracle is approved for production implementation under the updated plan. Tests
must not be weakened: implementation must make these exact controls GREEN, then
the outcome verifier must exercise the real installed workflows and rollback
before global enable or Bead closure.
