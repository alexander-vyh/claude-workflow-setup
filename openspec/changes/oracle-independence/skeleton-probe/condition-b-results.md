# Condition B Results — Framing Plus Independent Reference

- Reviewer: **AI subagent**. This is pilot evidence, not a human-authored review.
- Evidence boundary: compared each moved body only with its case's tracked `reference.md`; no answer key was opened.
- Review order: `diff-01` through `diff-06`, sequentially.
- Verdict meaning: **CLEAN** means the extraction preserves the original body after factoring out the reference's listed mechanical differences; **PLANTED** means a behavior-changing transcription difference was found.
- Saved at: `2026-08-09T20:37:20Z`

| Order | Case | Reference reviewed (UTC) | Verdict | Rationale / location |
|---:|---|---|---|---|
| 1 | `diff-01` | `2026-08-09T20:36:47Z` | **PLANTED** | The moved body changes the original `while "__" in sanitized` loop to a single `if`. After one replacement pass, an input containing three or more consecutive underscores can still contain `"__"`, so the extracted sanitizer no longer fully collapses repeated underscores. |
| 2 | `diff-02` | `2026-08-09T20:36:53Z` | **CLEAN** | The moved control flow and upload arguments match the original. Receiver, cached-value, and logging-label substitutions are the listed mechanical changes. The absent generation-match precondition is already absent from the original body, so it is not a transcription error in this extraction. |
| 3 | `diff-03` | `2026-08-09T20:36:58Z` | **CLEAN** | The moved and original bodies are identical, including token validation, endpoint construction, request method, response keys, defaults, and every `ReportMetadata` field mapping. |
| 4 | `diff-04` | `2026-08-09T20:37:02Z` | **PLANTED** | In the nested-key branch, the original `blob.name.endswith("_SUCCESS")` predicate became `blob.name.startswith("_SUCCESS")`. Since the full object name begins with its path prefix, valid nested success markers are omitted. |
| 5 | `diff-05` | `2026-08-09T20:37:09Z` | **CLEAN** | Every manifest key and value, sorting/count behavior, JSON indentation, and content type match the original after the listed receiver and manifest-path substitutions. |
| 6 | `diff-06` | `2026-08-09T20:37:13Z` | **CLEAN** | The module-level helper is identical to the original, including its empty-string predicate, `None`/value branches, and iteration over `row.items()`. |

This artifact records Condition B only. Answer-key scoring was intentionally not performed.
