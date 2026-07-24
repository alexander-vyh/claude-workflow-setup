# Outcome And Oracle Discipline

For non-trivial implementation, state the business outcome, the independent
source of truth, constraints, invalid solution classes, negative controls,
positive controls, missing-data handling, and final outcome verification before
writing production code.

Tests must reject plausible bad implementations. A passing test suite is not
enough when the tests only repeat private helpers, constants, generated IDs, or
intermediate implementation details. Do not weaken an oracle to make a change
pass; fix the implementation or update the spec with an explicit decision.

## Minimum Verified Delivery

Escapement optimizes for minimum verified delivery: the smallest coherent
solution that satisfies the current outcome and its constraints, not the
fewest lines or files. YAGNI forbids speculative structure; it never weakens
the outcome oracle. A YAGNI decision is valid only when the current
user/business outcome still passes its independent verification, controls
remain intact, and the skipped work has an observable trigger for adding it
later.

DRY targets duplicated authority, not similar text. Reuse or extend an existing
owner only when its contract matches the invariant. Do not force centralization
across consumers whose semantics differ. Centralize when duplication creates
drift, competing source-of-truth claims, repeated synchronized edits, reviewer
confusion, repeated decision cost, or high-severity deterministic risk. Preserve
independent corroborating checks, especially across implementation, tests,
oracle review, mutation challenge, and outcome verification.

Add gates only for repeated or high-severity failures with a replayable oracle
that catches bad cases and allows good ones. Do not add workflow machinery just
to prove that less workflow machinery should exist.
