# Oracle-Independence Skeleton Probe

> **Current status:** the recorded A/B result is an AI-only pilot. It is not a
> human result. The ready-to-run human handoff is
> [`human-scoring-packet.md`](human-scoring-packet.md).

This probe tests the riskiest assumption behind oracle-independence: **does
having the independent reference (the original pre-extraction code) actually
change a reviewer's ability to catch a behavior-changing transcription error in
an "extracted verbatim" diff — versus reviewing the extraction on the framing
alone?**

Each `diff-NN/` directory is one extracted-collaborator seam (a single function
or method moved out of a large file during a decomposition refactor). For each
seam:

- `framing.md` — the extracted code body plus the one-line frame the implementing
  agent would write ("verbatim extraction of X; byte-identical; tests green").
  This is the **only** information available in condition A.
- `reference.md` — the same extracted body, PLUS the original pre-extraction
  version of that same function (recovered independently from the commit's
  parent). This is the added information available in condition B.

Some diffs are faithful extractions. Some contain a single subtle
behavior-changing transcription error introduced during the "move." This README
does **not** say which, how many, or where — that is the point of the experiment.

## Protocol (run in order; do not skip ahead)

### Condition A — framing only

1. Read **only** `diff-NN/framing.md` for all six diffs, in order 01→06.
2. For each diff, record a verdict: **CLEAN** (faithful extraction) or
   **PLANTED** (contains a behavior-changing error). If PLANTED, note where.
3. Do **not** open any `reference.md` yet. Do **not** open the answer key.
4. Save your six condition-A verdicts.

### Condition B — framing + independent reference

5. Now read each `diff-NN/reference.md`, comparing the moved body against the
   original pre-extraction body line by line. The mechanical extraction
   differences (receiver renames, dropped docstring sections) are listed at the
   bottom of each `reference.md` so you can factor them out and focus on logic.
6. Record a second verdict per diff: CLEAN or PLANTED (+ location).
7. Save your six condition-B verdicts.

### Scoring

8. Open the sealed answer key (path provided separately by the planter — NOT in
   this directory). For each diff compute catch/miss under each condition.
9. Report the **delta**: how many planted errors did condition A miss that
   condition B caught (and vice versa)? That delta is the skeleton's result —
   it measures whether the independent reference changes detection, which is the
   assumption oracle-independence rests on.

## Blinding rules

- The diff numbers are shuffled; ordering carries no information about planted
  vs clean.
- Record condition-A verdicts **before** reading any `reference.md`. Reading the
  reference first collapses the two conditions and destroys the measurement.
- Do not open the answer key until both verdict sets are written down.
