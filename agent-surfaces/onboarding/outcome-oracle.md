# Outcome And Oracle Discipline

"Outcome" throughout means the **user or business outcome** — the change someone
outside this repository can observe. A passing test run, a merged PR, or a green
pipeline are evidence about an outcome; none of them is one.

Before non-trivial implementation, state that outcome, the independent source of
truth, the constraints, and what would falsify it — negative and positive
controls, invalid solution classes, missing-data handling.

Tests must reject plausible bad implementations. Green is not enough when the
tests only echo private helpers, constants, generated IDs, or the shape the code
already has. Never weaken an oracle to make a change pass: fix the code, or
change the spec by explicit decision.

## Minimum Verified Delivery

Escapement optimizes for minimum verified delivery: the smallest coherent
solution that delivers the intended user or business outcome and its
constraints — not the fewest lines or files, and never a green test run standing
in for the outcome itself. YAGNI forbids speculative structure; it never weakens
the outcome oracle. A YAGNI decision is valid only when the current
user/business outcome still passes its independent verification, controls remain
intact, and the skipped work has an observable trigger for adding it later.

DRY targets duplicated authority, not similar text. Reuse an owner only when its
contract matches the invariant; centralize when duplication causes drift,
competing source-of-truth claims, or repeated synchronized edits. Preserve
independent corroborating checks across implementation, tests, review, and
outcome verification.

Add gates only for repeated or high-severity failures with a replayable oracle
that catches bad cases and allows good ones. Prefer a mechanism that does the
work over a rule that asks an agent to remember it. Do not add workflow
machinery just to prove that less workflow machinery should exist.
