# Human A/B Scoring Packet

**Status: BLOCKED — awaiting a human response.** The existing result was
produced by an AI subagent. This packet is the unscored handoff for a genuine
human run; it does not claim that one has occurred.

## Reviewer guardrails

Use the six existing `diff-01` through `diff-06` cases. Before completing both
conditions, **do not open** `condition-a-results.md`,
`condition-b-results.md`, or `result.md`; they disclose the AI pilot's judgments
and scoring. Do not open any `reference.md` before submitting Condition A.

The same person should complete both conditions in order. Return the JSON
directly as a user response. Replace every placeholder verdict and rationale;
do not reuse the AI result files.

## Condition A — framing only

Read, in order, only:

- `diff-01/framing.md`
- `diff-02/framing.md`
- `diff-03/framing.md`
- `diff-04/framing.md`
- `diff-05/framing.md`
- `diff-06/framing.md`

For each case, choose `CLEAN` if no behavior-changing transcription error is
apparent or `PLANTED` if one is present. Then return this completed payload:

```json
{
  "condition": "A",
  "verdicts": [
    {"case_id": "diff-01", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-02", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-03", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-04", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-05", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-06", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"}
  ]
}
```

Stop after submitting Condition A. The facilitator should preserve that user
response before releasing Condition B.

## Condition B — framing plus independent reference

After Condition A is recorded, read each corresponding `reference.md` and
compare the moved body with the original body. Return a new judgment for every
case:

```json
{
  "condition": "B",
  "verdicts": [
    {"case_id": "diff-01", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-02", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-03", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-04", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-05", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"},
    {"case_id": "diff-06", "verdict": "CLEAN or PLANTED", "rationale": "replace with rationale"}
  ]
}
```

## Scoring after both responses

Only after both user responses exist, compare them with the answer key named in
`result.md`. Report Condition A and B accuracy, planted-error detection, clean
case rejection, A-miss-to-B-catch transitions, and the reverse transitions.
Record a negative or unchanged result honestly. Until those two human responses
exist, the human-only outcome remains blocked.
