## Context

The canonical TDD rule permits a compact three-section Test Oracle Brief for
low-blast-radius work, but `test_oracle_brief_policy.py` recognizes only the nine
full-form headings. Both edit and landing gates call the same `brief_status`
function, so they also apply the same proof threshold even though an observed
result cannot exist before implementation. Separately, `mol-rapid` always requires
an adversarial-reviewer and describes a PR neither as a review-ready boundary nor
as distinct from an earlier durable branch, commit, or slice.

The canonical hook policy is copied to both installed host surfaces by
`tools/render_agent_surfaces.py`. The implementation must remain in those canonical
sources and preserve host parity. The active `oracle-independence` change owns how
truth is independent; this change only compresses the presentation of that truth.

## Goals / Non-Goals

**Goals:**

- Make the documented compact form executable without weakening its oracle.
- Fail closed to the full form when rapid eligibility or required semantic content
  is absent, unknown, or protected.
- Require planned user-facing proof before edits and an observed result before
  landing.
- Remove unconditional independent review from `mol-rapid` while retaining
  explicit triggers for review or full-lane escalation.
- Distinguish an early durable artifact from a review-ready PR.
- Keep the implementation small, renderer-owned, and behaviorally tested.

**Non-Goals:**

- Build a semantic risk classifier or inspect arbitrary diffs to infer business
  consequence.
- Change the full nine-section brief or weaken final outcome verification.
- Merge this capability into `oracle-independence` or claim that another agent is
  itself an independent oracle.
- Change global agent allocation, PR authorization, merge, or deployment policy.
- Add a new ledger, gate, file format, or cross-session experiment framework.

## Decisions

### Support two explicit brief forms

The policy module will classify a brief as full or rapid. A substantively valid
nine-section brief remains the default and requires no rapid eligibility
attestation. A brief using only the existing load-bearing headings—`Business
invariant`, `Negative control`, and `Final outcome verification`—is a rapid brief
and must satisfy the compact semantic contract below. A partial hybrid is invalid.

Alternative considered: replace the nine headings globally. Rejected because full
work still needs the explicit reasoning surface and the evidence does not justify a
global assurance reduction.

### Put structured rapid evidence inside the three existing sections

The rapid form will use labeled fields rather than a fourth heading or separate
eligibility artifact:

- `Business invariant`: outcome, independent source of truth, binding constraints,
  and each rapid exclusion decision.
- `Negative control`: named fragile implementation, discriminating negative
  control, positive-control disposition, and missing/unresolved disposition.
- `Final outcome verification`: exact user-facing command/query/flow and, at
  landing time, its observed result.

Proof uses compact structured value forms so the hook does not guess from free-form
assurances: planned proof is `<Command|Query|API|Report|UI>: <action>; Expected:
<result>`, while focused/final observation is `Expected: <result>; Actual: <same
result>; Match: yes`. Root-cause evidence combines both forms:
`<Kind>: <action>; Expected: <cause>; Actual: <same cause>; Match: yes`.

Every protected-surface field must explicitly be `no`; root cause must have observed
executable evidence; and the exact planned user-facing proof is
itself the executable outcome-oracle attestation. This avoids a second yes/no field
that could contradict the proof. Missing, `unknown`, affirmative protected values,
non-executable proof, or contradictory observed values make the rapid brief invalid
and require the full form. Rapid requires concrete positive and missing-data
controls; inapplicability claims and placeholder `N/A` use the full lane.

Command proof must resolve an executable, parse as argv, and include an option, path,
or qualified argument; report proof begins with `Run` or `Generate` followed by that
command shape. Query proof uses a conservative complete `SELECT ... FROM ...` shape
rather than accepting a SQL-looking prefix or finding SQL words in prose. More
complex query runners can use Command proof. API and UI proof retain their explicit
lexical contracts. These are executability checks, not claims that the hook ran
arbitrary brief content.

The positive control reuses planned-proof syntax and rejects expected results that
are empty, dropped, suppressed, absent, or negated. Focused proof must equal the
planned expected outcome, not an unrelated fact. Rapid review readiness uses exact
`none` values for objective blockers and known limitations; work with either moves
to the full lane while remaining landing proof can still name the unexecuted final
check.

Alternative considered: YAML frontmatter. Rejected because it would create a new
mini-format and separate the eligibility claim from the invariant it constrains.

### Make proof validation stage-aware

`brief_status` will accept a proof stage so the durable-artifact boundary does not
pretend unfinished work is already delivered:

| Stage | Examples | Rapid proof required |
| --- | --- | --- |
| Edit | supported behavior-bearing edit tools | exact planned user-facing verification |
| Durable | `git commit`, task-branch `git push` | exact planned user-facing verification |
| Review | `gh pr create` | planned proof, focused observed result, no objective blocker, known limitations, remaining landing proof |
| Final | `gh pr merge`, `bd close` | exact user-facing verification and substantive observed result |

The existing host hook registration remains unchanged; unsupported stages are not
described as mechanically enforced. Signal categories distinguish valid full and
rapid briefs without claiming that a host approval occurred.

For compound shell commands, the landing parser chooses the strongest recognized
stage across every shell boundary, including newlines, background separators, and
pipe-stderr operators. Dynamic `eval` is final/fail-closed, as is unparseable
finishing syntax.

Alternative considered: require the final observed result before the first edit or
durable commit. Rejected because that is temporally impossible and would encourage
fabricated proof or prevent useful recovery artifacts.

### Treat the checklist as fail-closed attestation, not inferred truth

The hook can mechanically prove that every required decision was made and that
unknown/protected values do not use the rapid path. It cannot infer whether a
business claim is truthful from arbitrary source paths. Rules and molecule prompts
therefore require reclassification during the current run whenever a protected
surface, boundary expansion, uncertain reversibility, non-discriminating control,
unresolved root cause, or missing oracle is discovered.

Alternative considered: filename/path heuristics for auth, money, schemas, or
infrastructure. Rejected because path names are neither complete nor an independent
business-risk oracle.

### Make independent review conditional in `mol-rapid`

The verify step will always exercise the user-facing outcome. It will request
independent review only for a protected exclusion, specialty boundary, task-maturity
gap, or named failure mechanism that benefits from a second evidence source. A
protected exclusion or unresolved uncertainty escalates to the full lane; another
agent's presence alone never proves independence.

### Separate durability from review inventory

The rapid prompt will encourage an early branch, commit, or vertical slice as the
durable recovery boundary. It will call a PR review-ready only after bounded behavior
works, focused proof passes, objective-blocking work is absent, and limitations and
remaining landing proof are stated.

## Risks / Trade-offs

- **Self-attestation can be inaccurate** → Fail closed on missing/unknown values,
  require current-run reclassification, and do not describe the hook as proving
  real-world risk absence.
- **Three headings can become dense boilerplate** → Require concise labeled fields
  and reject duplicate/placeholder bodies rather than prescribing prose length.
- **A stale observed result could be copied forward** → Require it at landing and
  keep exact user-facing proof visible; do not claim cryptographic coupling between
  proof and commit.
- **Conditional review can be under-requested** → Keep explicit specialty, maturity,
  protected-surface, and named-failure triggers in the molecule and tests.
- **Canonical/rendered drift can preserve the old behavior** → Extend renderer
  parity tests and run the generated-surface check before landing.

## Migration Plan

1. Add failing policy and formula contract tests for the compact form, exclusions,
   stage-aware proof, conditional review, escalation, and review-ready PR boundary.
2. Implement the dual-form policy and update the gate messages.
3. Update the canonical rule and `mol-rapid` formula.
4. Regenerate host surfaces and run focused plus full verification.
5. Install/update the merged plugin through the repository-declared path and verify
   canonical and installed hook behavior with valid and invalid rapid briefs.

Rollback is a source revert followed by regeneration and plugin refresh; the full
nine-section path remains valid throughout.

## Open Questions

None. The hook deliberately validates explicit evidence shape rather than attempting
semantic risk inference.
