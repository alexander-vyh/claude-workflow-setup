## 1. Oracle and Regression Boundary

- [x] 1.1 Produce the full Test Oracle Brief and pass the mutation challenge for dual-form classification, fail-closed eligibility, and stage-aware proof.
- [x] 1.2 Add failing behavioral and contract tests for valid full/rapid briefs; every protected-field yes/unknown/missing/duplicate case; compact-field omission, wrong-section placement, placeholder, boilerplate, and duplicate cases; exact command/query/API/report/UI proof controls; edit/durable/review/final proof stages and signal categories; observed-result non-evidence; clause-bound conditional review and escalation; and review-ready artifacts.

## 2. Executable Oracle Policy

- [x] 2.1 Implement full-versus-rapid brief classification and compact labeled-field validation in the canonical policy module.
- [x] 2.2 Make edit and landing gates apply stage-appropriate proof requirements and report actionable full-or-rapid repair guidance.

## 3. Rapid Workflow Contract

- [x] 3.1 Update the canonical TDD rule with the semantically complete compact form, fail-closed exclusions, and current-run escalation triggers.
- [x] 3.2 Update `mol-rapid` to remove unconditional adversarial review, preserve conditional independent review, and separate durable artifacts from review-ready PRs.

## 4. Generated Surfaces and Delivery

- [x] 4.1 Regenerate Claude and Codex plugin surfaces and verify source/rendered parity.
- [x] 4.2 Run focused hook/formula tests, mutation controls, generated-surface checks, and the relevant full test suite.
- [ ] 4.3 Verify installed hook behavior for valid and invalid compact briefs, then carry the change through PR, merge, and declared deployment verification.
