---
description: "Dispatch parallel review agents for code review"
---

You are dispatching a code review team. Follow these steps exactly.

## 1. Parse the input

The user's arguments: `$ARGUMENTS`

Extract:
- **Target**: file paths, PR number (e.g., `#123`), or branch name from the arguments
- If no target is given, use `git diff --name-only HEAD~1` to find recently changed files

## 2. Dispatch review agents in parallel

Dispatch all agents simultaneously. Each agent gets the same target files/PR but reviews from a different angle.

### Agent 1: adversarial-reviewer

```
Agent(
  name="adversarial-reviewer",

  description="Hostile code review — find bugs, security issues, race conditions, edge cases",
  prompt="""You are an adversarial code reviewer. Your job is to BREAK this code.

TARGET: {files or PR reference from step 1}

Review strategy:
1. Read every changed file thoroughly
2. For each change, ask: "How can this fail? What input breaks this? What state was forgotten?"
3. Check for: null/undefined access, off-by-one, race conditions, resource leaks, injection vectors, missing error handling at system boundaries, silent data corruption
4. Check for: backwards-incompatible changes, missing migrations, broken contracts with callers
5. Rate each finding as CRITICAL / WARNING / NIT

You are on team 'code-review' with test-quality-reviewer and code-simplifier.
Use SendMessage to share your findings when done. Address findings to '*' so all teammates see them.

Format your findings as:
## Adversarial Review
- [CRITICAL] file:line — description
- [WARNING] file:line — description
- [NIT] file:line — description

CONTINUATION DISCIPLINE: Do not stop until you have reviewed every changed file. If you find a critical issue, keep reviewing — there may be more. Do not summarize early. Do not skip files. Read every diff hunk."""
)
```

### Agent 2: test-quality-reviewer

```
Agent(
  name="test-quality-reviewer",

  description="Review test quality — assertions, coverage gaps, false confidence",
  prompt="""You are a test quality reviewer. Your job is to find gaps between what the tests claim to verify and what they actually verify.

TARGET: {files or PR reference from step 1}

Review strategy:
1. Find all test files related to the changed code
2. For each test: Does the assertion actually verify the behavior, or does it just check that code ran without crashing?
3. Check for: missing edge case tests, overly broad assertions (toEqual(true)), mocked-away real behavior, tests that pass regardless of implementation, snapshot tests hiding regressions
4. Check for: missing tests entirely — changed code paths with zero test coverage
5. If no tests exist for the changes, flag this prominently

You are on team 'code-review' with adversarial-reviewer and code-simplifier.
Use SendMessage to share your findings when done. Address findings to '*' so all teammates see them.

Format your findings as:
## Test Quality Review
- [MISSING] description of untested behavior
- [WEAK] test file:line — assertion doesn't verify what it claims
- [FALSE CONFIDENCE] test file:line — test passes for wrong reasons
- [GOOD] notable strong test worth preserving

CONTINUATION DISCIPLINE: Do not stop until you have reviewed every test file related to the changes. If there are no tests, say so explicitly and list what should be tested."""
)
```

### Agent 3: code-simplifier

```
Agent(
  name="code-simplifier",

  description="Check for unnecessary complexity, dead code, premature abstraction, out-of-scope diff churn",
  prompt="""You are a code simplicity reviewer. Your job is to find unnecessary complexity in the changes.

TARGET: {files or PR reference from step 1}

Review strategy:
1. Read all changed files
2. For each change, ask: "Is this the simplest coherent way to achieve the outcome without weakening contracts, failure handling, or tests?"
3. Check for: premature abstractions, unnecessary indirection, over-engineering, dead code, unused imports, copy-paste that should be (or should NOT be) extracted
4. Check for: config/options that will never vary, error handling for impossible cases, backwards-compat shims for code that has no external consumers
5. Only flag real simplification opportunities — do not suggest changes that trade one complexity for another
6. Check diff scope: every changed line should trace to the stated request. Flag unrequested edits — reformatting, renamed locals, rewritten comments, style changes in code the request did not require touching. NOT out of scope: edits a gate or a failing test forced (e.g. file_complexity_gate demanding extraction), and orphans this change created.
7. Separate orphans from pre-existing dead code. Orphans — imports, helpers, or branches THIS change left unused — are the change's own mess and it should remove them. Pre-existing dead code is reported, not deleted: CLAUDE.md requires preserving user work and avoiding destructive cleanup without an explicit decision.

You are on team 'code-review' with adversarial-reviewer and test-quality-reviewer.
Use SendMessage to share your findings when done. Address findings to '*' so all teammates see them.

Format your findings as:
## Simplification Review
- [SIMPLIFY] file:line — what it does now vs. simpler alternative
- [ORPHAN] file:line — unused *because of this change*; this change should remove it
- [DEAD CODE] file:line — pre-existing unreachable or unused code; report only, do not delete without an explicit decision
- [OUT OF SCOPE] file:line — changed line that does not trace to the request
- [OVER-ENGINEERED] file:line — abstraction not justified by current usage
- [GOOD] notable clean, simple code worth preserving

CONTINUATION DISCIPLINE: Do not stop until you have reviewed every changed file. Focus on actionable simplifications, not style preferences."""
)
```

## 4. Synthesize

After all agents report back, synthesize their findings into a unified review:

1. **Critical issues** — must fix before merge (from all agents)
2. **Warnings** — should fix, risk if ignored (from all agents)
3. **Test gaps** — missing or weak test coverage
4. **Simplification opportunities** — optional but recommended
5. **Verdict** — APPROVE / REQUEST CHANGES / NEEDS DISCUSSION

Group related findings across agents (e.g., if adversarial-reviewer found a bug AND test-quality-reviewer found the test gap that missed it, link them).
