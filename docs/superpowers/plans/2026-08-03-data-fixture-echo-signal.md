# Data-Fixture Echo Signal

## Test Oracle Brief

1. **Business invariant**

   A changed inert textual fixture must never cause an unrepairable hard denial
   merely because it shares an opaque literal with changed production code.
   The possible implementation echo must remain visible as one nonblocking
   diagnostic and one persistent gate signal. Executable tests with the same
   echo must continue to deny finishing commands.

2. **Independent source of truth**

   The public PreToolUse hook contract is the oracle: execute the hook against
   real temporary Git repositories and inspect its JSON decision plus the real
   `.beads/.gate-signal.jsonl` side effect. A nonblocking fixture response has
   `systemMessage` and no deny decision; an executable-test echo has
   `permissionDecision="deny"`.

3. **Solution constraints**

   - Keep the existing landing-time changed-file scope.
   - Preserve explicit executable-test filename precedence even when the file
     also has a textual data extension (for example, `service.test.json`).
   - Preserve the canonical deny contract: one deny JSON document and exit 0.
   - Use the existing `_gate_signal` persistence owner and its
     `allow-with-warning` vocabulary.
   - Bound fixture bytes and matched-issue aggregation below the host's
     ten-second hook deadline, and retain truncation metadata in the persistent
     signal rather than reading arbitrarily large golden datasets into memory.
     A decision may read at most 9,000,000 fixture bytes and retain at most 128
     fixture issues across all changed fixtures; one additional byte may be
     read solely to detect truncation. The public subprocess oracle must finish
     within 7 seconds.
   - Keep executable test classification and oracle overrides unchanged.
   - Render the canonical hook into both installable plugin surfaces.
   - Do not introduce fixture attestations, sidecars, or consumer graph
     analysis without evidence that warning-only handling is insufficient.

4. **Invalid solution classes**

   - Silently excluding every data extension from analysis.
   - Downgrading executable-test echoes from deny to warning.
   - Warning on every changed fixture regardless of shared evidence.
   - Emitting user-visible text without a persistent signal.
   - Recording multiple fixture-warning signals for one hook decision.
   - Treating a fixture as production source after removing it from the test
     bucket.
   - Hardcoding the regression fixture's known opaque literal or recognizing
     only `tests/fixtures/` rather than the existing test-directory contract.
   - Reading every matching fixture completely or aggregating an unbounded
     number of advisory issues before the host deadline.

5. **Fragile implementation to reject**

   The recovered `NON_ASSERTING_DATA_EXTENSIONS` shortcut, which returned
   `False` from `is_test_file()` and dropped fixtures from the gate entirely.
   It must fail because a suspicious fixture is required to emit a warning and
   exactly one `allow-with-warning` signal.

6. **Negative controls**

   - An executable Python test repeating the same opaque production literal
     still denies.
   - An explicitly test-shaped data filename repeating the literal still
     denies rather than being reclassified as an inert fixture.
   - A textual fixture containing a different opaque literal stays silent.

7. **Positive control**

   A JSON/YAML/TOML/INI/CFG/CSV/TSV fixture sharing a production literal is
   allowed, emits one nonblocking message naming the fixture, and records one
   `allow-with-warning` signal. Valid unquoted scalars in YAML, INI/CFG, CSV,
   and TSV are covered as well as quoted values.

8. **Missing or unresolved handling**

   Unknown fixture provenance is explicitly allowed with a warning. Signal
   persistence is fail-soft as before and must never convert an advisory into
   a denial. Resource-budget truncation also fails open, but must be explicit
   in the persistent signal. Binary fixture formats are explicitly unchanged: they are not
   added to the textual fixture classifier or silently exempted here. A
   replayable binary-fixture false positive is the trigger for separate parser
   and policy work.

9. **Final outcome verification**

   Run the focused regression and rendered-surface suites, render all plugin
   copies, deploy merged main with both authoritative plugin updaters, then
   execute the installed Claude and Codex hook entrypoints against real
   temporary repositories for suspicious-fixture warning, unrelated-fixture
   silence, and executable-test denial.

## Mutation challenge

The pre-implementation challenger must verify that the proposed tests reject:

1. `is_test_file()` simply returning false for all listed fixture extensions.
2. Every fixture producing a warning even when no literal is shared.
3. Every shared echo, including executable tests, becoming nonblocking.
4. A warning response that never calls `_record_signal`.
5. One warning per matched literal or fixture instead of one decision-level
   warning.
6. Hardcoded recognition of the regression test's literal or fixture path.
7. Explicit `*.test.*` files being downgraded because extension checks run
   before executable-test naming checks.
8. Reusing the source-code quote parser so unquoted declarative scalars vanish
   from fixture analysis.
9. Reading a whole oversized fixture or collecting every match without
   exposing resource-budget truncation.
