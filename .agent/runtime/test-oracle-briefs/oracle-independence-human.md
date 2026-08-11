# Test Oracle Brief — oracle-independence human handoff

## Business invariant
The recorded A/B judgments remain identified as an AI pilot. A human result exists
only after a person completes both ordered conditions.

## Independent source of truth
The six committed pilot rows determine pilot arithmetic; the future person's two
responses—not actor labels or commit metadata—determine human completion.

## Solution constraints
Preserve the pilot, correct its timestamp, provide one concise blinded scoring
packet, and do not invent human responses, signatures, or receipt machinery.

## Invalid solution classes
Reject relabeling AI output as human, exposing Condition B references before
Condition A, copying pilot verdicts as human answers, or changing the six rows.

## Fragile implementation to reject
Inferring human authorship from a name, role string, or commit author.

## Negative control
No human response means the human-only outcome remains explicitly blocked.

## Positive control
The six pilot rows still reconcile to 4/6 versus 6/6, and the packet covers both
conditions without revealing planted changes early.

## Missing/unresolved handling
Missing human responses block only the human conclusion; they do not invalidate the
AI pilot or justify fabricated provenance.

## Final outcome verification
Run `pytest -q tests/test_oracle_independence_human_probe.py` and inspect the pilot
disclosure, scoring packet, timestamp, and bead state.
