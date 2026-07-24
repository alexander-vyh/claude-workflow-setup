---
name: behavioral-test-oracle-review
description: Use when implementing, writing tests, modifying tests, reviewing tests, fixing bugs, or changing business logic, reports, data models, APIs, auth, UI flows, jobs, or integrations where tests must prove user or business behavior rather than implementation details.
---

# Behavioral Test Oracle Review

Use this before implementation for non-trivial changes where a superficial test could pass a fragile implementation.

## Required Output

Produce a Test Oracle Brief:
1. Business invariant: what user/business outcome must be true?
2. Independent source of truth: what proves correctness without copying the implementation?
3. Solution constraints: what repo, language, framework, security, ownership, or maintainability constraints must hold?
4. Invalid solution classes: what kinds of solutions are disallowed even if they produce today's output?
5. Fragile implementation to reject: name at least one tempting shortcut.
6. Negative control: what fixture, row, request, role, input, or scenario should fail if the code is wrong?
7. Positive control: what proves valid output is not accidentally dropped?
8. Missing/unresolved handling: should missing lookup/source data fail closed, fail open, or be explicitly allowed?
9. Final outcome verification: what command, query, report, API call, UI flow, or workflow proves the actual result?

## Review Rule

Reject the test plan if the named fragile implementation would pass every relevant behavioral, fixture, contract, architecture, and static check.

## Common Oracle Smells

- Same magic constant in code and test
- Test duplicates the implementation algorithm
- Mock interaction is asserted without an outcome assertion
- Private helper is asserted instead of public behavior
- Generated ID is used as business identity
- Snapshot has no semantic assertion
- No negative control
- No positive control, so an empty result could pass
- Final output is not checked
- Intermediate artifact is tested when the user cares about downstream output
- Reported path is fixed while another consumer of the same invariant remains broken

## Handoff

Before implementation begins, state:
- the business invariant
- the independent oracle
- the invalid solution classes
- the fragile implementation that must fail
- the tests/checks that reject it
- the final outcome verification surface
