## 1. Walking Skeleton — validate human-gate independence (riskiest assumption / Risk B)

- [x] 1.1 Assemble a blinded probe set: collect 5–8 real already-merged refactor diffs the reviewer has not closely reviewed; inject ≥1 planted transcription error (e.g. `>`→`>=` in a moved body); record the planted set out of the reviewer's view (spec: human-gate-independence / Empirical validation before build-out)
- [x] 1.2 Run the gate review in two conditions — agent-framing-only, then with an independent reference (pre-move behavior / spec) added — and record catch vs. miss per condition; interpret with the contamination caveat noted (spec: human-gate-independence / Empirical validation before build-out)

## 2. Future increments — NOT yet designed (gated on the skeleton result)

- [ ] 2.1 [PLACEHOLDER] Productionize landing-relocation-proof: AST body-identity + call-site-arity check, shipped first as a sound-but-noisy async tripwire (spec: landing-relocation-proof / Relocation identity proof). Designed after the skeleton; covers cake's class regardless of Risk B's outcome.
- [ ] 2.2 [PLACEHOLDER] If Risk B survives: build the independent-reference presentation into the live design/review gate — the mol-feature beads gate resolved via `bd gate resolve` (per molecule-awareness.md), NOT review_gate.py (which is a separate in-session bd-close nudge and a live instance of the Non-Goal #3 rejected pattern) (spec: human-gate-independence / Independent reference at the gate)
- [ ] 2.3 [PLACEHOLDER] If Risk B survives: enabling-entry-routing classifier spike (Risk A) — measure fire-rate/error-rate over real session-opening prompts before building any routing (spec: enabling-entry-routing / Classifier viability is a precondition)
