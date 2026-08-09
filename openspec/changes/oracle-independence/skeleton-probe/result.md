# Oracle-Independence Skeleton Probe — Final Result

## Evidence and scoring rule

Condition A was recorded in commit
`2e6863ad2505702778011d7b3f3d31b1125cc24a` before Condition B was recorded in
commit `6edb0191d22b6e6427bcf046ffb8b794ec0e1fda`. The sealed answer key was opened
only after both verdict artifacts were committed.

For each case, **catch** means the condition's CLEAN/PLANTED verdict matches the
sealed key; **miss** means it does not. Planted-error detection is also reported
separately, because overall classification accuracy includes correct CLEAN
verdicts and false-positive corrections.

| Case | Sealed truth | Condition A | A score | Condition B | B score | Transition |
|---|---|---|---|---|---|---|
| `diff-01` | **PLANTED** — double-underscore collapse changed `while` to `if` | CLEAN | **MISS** — false negative | PLANTED | **CATCH** — true positive | **A miss -> B catch** |
| `diff-02` | **CLEAN** | PLANTED | **MISS** — false positive; the missing generation precondition was already absent from the original | CLEAN | **CATCH** — true negative | **A miss -> B catch** |
| `diff-03` | **CLEAN** | CLEAN | **CATCH** — true negative | CLEAN | **CATCH** — true negative | No change |
| `diff-04` | **PLANTED** — nested marker predicate changed `endswith` to `startswith` | PLANTED | **CATCH** — true positive | PLANTED | **CATCH** — true positive | No change |
| `diff-05` | **CLEAN** | CLEAN | **CATCH** — true negative | CLEAN | **CATCH** — true negative | No change |
| `diff-06` | **CLEAN** | CLEAN | **CATCH** — true negative | CLEAN | **CATCH** — true negative | No change |

## Totals

- Overall per-case catch rate: Condition A **4/6 (66.7%)**; Condition B **6/6
  (100%)**.
- Planted-error detection rate: Condition A **1/2 (50%)**; Condition B **2/2
  (100%)**.
- Clean-case correct-rejection rate: Condition A **3/4 (75%)**; Condition B
  **4/4 (100%)**.
- A-miss -> B-catch flips: **2 total** (`diff-01`, `diff-02`), including **1
  planted-error flip** (`diff-01`) and **1 false-positive correction**
  (`diff-02`).
- B-miss -> A-catch reverse flips: **0**.

The arithmetic was independently reconciled from the six verdict rows against
the key's two planted and four clean cases. Both Condition A misses became
Condition B catches; Condition B introduced no new miss.

## Interpretation

**Premise result under the design's stated binary rule: TRUE.** The
independent-reference condition caught at least one planted transcription error
that framing-only missed: `diff-01`. This is neither the FALSE outcome
(reference changes nothing) nor an inconclusive caught-in-both/missed-in-both
outcome for every planted case.

This is a **positive, suggestive skeleton result, not validation of the
human-gate-independence capability**. The reviewer knew a probe was running, so
vigilance was elevated; the experiment has one self-administered reviewer/trial
(`n=1`). That contamination can inflate both conditions and does not establish
the effect's repeatability or its size in the live gate.

## Sealed-key provenance

The scoring source was the external sealed key at
`/Users/alexandervyhmeister/GitHub/.oracle-probe-answer-key.md`, SHA-256
`18ed08412ccdd2cbe7322be069c2bab2bac14a86c372093b8210509ef0d6a818`. Its
filesystem birth/modified timestamp is `2026-06-12T01:27:01Z`, before the
Condition A and B commits on `2026-08-09`. The key names the same six seams and
the exact two corruptions present in the committed probe. Its header retains the
probe's pre-relocation `cake/...` locator; the case identities and contents, not
that stale locator, establish the match to this consolidated probe.

## Forward natural experiment

Before treating the positive result as validation or using it alone to justify
live-gate build-out, repeat prospectively across real eligible refactors with a
**different planter/reference custodian than the reviewer**:

1. Freeze the pre-change behavior/reference and preregister any known natural
   discrepancy (or seed a reversible discrepancy only on a disposable review
   branch that cannot land).
2. Preserve the same ordering: record framing-only verdicts before exposing the
   independent reference, then score both against the preregistered key.
3. Accumulate multiple independent review events and report both planted-error
   flips and false-positive corrections; never count tests-green or reviewer
   confidence as a catch.
4. Treat a repeat A-miss -> B-catch on a known error as corroboration; treat a
   with-reference miss as evidence against relying on the human-boundary route.
