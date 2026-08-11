# Test Oracle Brief — gate-decision replay (`escapement-uikk`)

## Business invariant

One deterministic command replays a stable 150–200-event, production-derived
corpus through the shipped gate surfaces and reports decision quality by gate
and host. A pass-rate-only report is insufficient: each supported cell must
include the complete `allow`/`ask`/`deny` matrix, binary confusion matrix,
false-positive and false-negative classes, and available repair cost.

## Independent source of truth

`source-events.jsonl` is a redacted selection from the repository's append-only
`.beads/.gate-signal.jsonl`, pinned by source line and source-file digest.
`labels.jsonl` is a separate review artifact whose receipt records the reviewer,
review method, source revision, and label-file digest. Replay never derives an
expected decision from gate output or from a historical decision field.

## Solution constraints

- Keep 150–200 unique historical events; the checked-in population is 180.
- Execute the public Claude and Codex hook files where the manifest says the
  gate is supported. Codex `tdd_gate` remains explicitly unsupported.
- Use Python's standard library and repository Git; no network, PKI, HMAC,
  process-witness framework, or home-directory state.
- Run in disposable repositories and leave the corpus unchanged.
- Report all nine nonnegative integer decision cells and exact case rows.
- Preserve nullable repair-cost fields; missing evidence is not measured zero.

## Invalid solution classes

- Vacuous, all-allow, all-ask, or all-deny runners.
- Copying expected or historical decisions into observed results.
- Hardcoding the checked-in report instead of executing selected hook files.
- Reporting only aggregate pass/intervention counts.
- Dropping sparse or zero matrix cells, host identity, policy revision,
  provenance, error classes, or missing-cost reasons.
- Accepting changed labels without failing the replay verification.

## Fragile implementation to reject

The tempting shortcut is `observed = expected`. The all-allow surface mutant
must change live observations and fail the replay. A runner that copies labels
or returns a saved report will stay green and therefore fails that control.

## Negative control

Run the corpus with one supported public surface replaced by an always-allow
hook: ask/deny cases become false negatives and replay exits nonzero. Separately,
invert one expected label: validation or live comparison must fail.

## Positive control

The canonical command returns 180 case rows, all five supported gate/host cells,
complete 3×3 and binary matrices, both allow and intervention decisions, and at
least one evidenced repair-cost row while retaining explicit missing reasons.

## Missing/unresolved handling

Missing/malformed source rows, labels, receipts, surfaces, policy revisions,
matrix fields, or joins fail closed. Unsupported host/gate cells are documented
and excluded rather than silently scored. Missing repair-cost evidence remains
`null` with a reason.

## Final outcome verification

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/evals/gate_decision_replay/replay.py \
  --result /private/tmp/gate-decision-replay.json
```

Then run `python3 -m pytest -q tests/test_gate_decision_replay.py` and inspect
the JSON report's five decision matrices, five binary matrices, error classes,
and repair-cost summary.
