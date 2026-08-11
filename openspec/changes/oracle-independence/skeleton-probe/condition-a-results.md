# Condition A Results — Framing Only

- Reviewer: **AI subagent**. This is pilot evidence, not a human-authored review.
- Evidence boundary: reviewed only each case's `framing.md`; no independent reference or answer key was opened.
- Review order: `diff-01` through `diff-06`, sequentially.
- Verdict meaning: **CLEAN** means no planted behavior-changing transcription error was detected from the framing-only evidence; it does not independently prove byte identity.
- Saved at: `2026-08-09T11:37:47Z`

| Order | Case | Framing reviewed (UTC) | Verdict | Rationale / location |
|---:|---|---|---|---|
| 1 | `diff-01` | `2026-08-09T11:37:00Z` | **CLEAN** | The replacement, underscore collapsing, and edge stripping form an internally coherent sanitizer; no behavior-changing transcription error is apparent from this body alone. |
| 2 | `diff-02` | `2026-08-09T11:37:07Z` | **PLANTED** | At `lock_blob.upload_from_string(...)`: the write has no generation-match precondition, so two contenders can both overwrite the blob and return success. The `PreconditionFailed` handler cannot protect this acquisition without an atomic create/update precondition. |
| 3 | `diff-03` | `2026-08-09T11:37:15Z` | **CLEAN** | Token validation, the describe endpoint, and the response-to-`ReportMetadata` mapping are internally consistent; no behavior-changing transcription error is apparent. |
| 4 | `diff-04` | `2026-08-09T11:37:22Z` | **PLANTED** | In the nested-key branch, `blob.name.startswith("_SUCCESS")` tests the full object name even though it begins with the nested path prefix. It should test the marker at the end (as the flat branch does), otherwise valid nested batches are dropped. |
| 5 | `diff-05` | `2026-08-09T11:37:27Z` | **CLEAN** | The manifest fields agree with the method inputs, the processed batches are serialized deterministically, and the count preserves input cardinality; no behavior-changing transcription error is apparent. |
| 6 | `diff-06` | `2026-08-09T11:37:31Z` | **CLEAN** | The comprehension preserves every non-empty CSV value as a string and maps only empty strings to `None`, matching the stated contract; no behavior-changing transcription error is apparent. |

This artifact records Condition A only. Condition B and scoring were intentionally not performed.
