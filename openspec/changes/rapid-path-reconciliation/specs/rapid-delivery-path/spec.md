## ADDED Requirements

### Requirement: Full and rapid oracle forms
The oracle gate SHALL continue to accept a semantically valid nine-section full
brief and SHALL also accept the documented three-section rapid brief when its
compact semantic and eligibility contract is complete.

#### Scenario: Existing full brief remains valid
- **WHEN** a behavior-bearing edit or landing command has a semantically valid
  nine-section brief
- **THEN** the gate accepts it without requiring rapid eligibility fields

#### Scenario: Complete rapid brief permits an edit
- **WHEN** a three-section brief contains the outcome, independent truth, binding
  constraints, fail-closed eligibility decisions, named shortcut, discriminating
  controls, missing-data disposition, and exact planned user-facing proof
- **THEN** the edit gate classifies it as a valid rapid brief

### Requirement: Fail-closed rapid eligibility
The rapid form SHALL be valid only when every protected surface is explicitly absent,
root cause has observed executable evidence, and exact planned proof establishes an
executable outcome oracle. Missing, affirmative, unknown, or non-executable evidence
MUST require the full form.

#### Scenario: Protected surface cannot use rapid form
- **WHEN** any protected-surface decision is affirmative, unknown, missing, or
  supplied more than once with conflicting or boilerplate values
- **THEN** the three-section brief is rejected as invalid rapid evidence and the
  full lane is required

#### Scenario: Unresolved cause or missing oracle cannot use rapid form
- **WHEN** root cause is unresolved or the user-facing outcome oracle is missing or
  non-executable
- **THEN** the three-section brief is rejected and the full lane is required

### Requirement: Compact form preserves oracle substance
The rapid form SHALL preserve independent truth, binding constraints, a named fragile
implementation, a discriminating negative control, positive-output disposition,
missing/unresolved disposition, and exact final proof inside its three headings.

#### Scenario: Drop-all shortcut is rejected
- **WHEN** empty or suppressed output could make the observed failure disappear
- **THEN** the compact challenge includes executable planned proof of a valid result
  and rejects expected output described as empty, dropped, suppressed, or absent

#### Scenario: Inapplicability does not become a prose bypass
- **WHEN** a rapid brief claims that positive-output or missing-data controls are not
  applicable
- **THEN** the rapid brief is rejected and the full lane handles that judgment

#### Scenario: Missing compact semantic field fails closed
- **WHEN** any required compact field is absent, trivial, duplicated boilerplate, or
  under the wrong heading
- **THEN** edit and landing gates reject the rapid brief

#### Scenario: Contradictory disposition fails closed
- **WHEN** observed root-cause or outcome evidence differs from its expected result,
  or objective blockers is anything other than exactly `none`
- **THEN** the compact brief is rejected and the full lane is required

### Requirement: Proof requirements follow delivery stage
The rapid proof SHALL name the exact user-facing command, query, report, API call, UI
flow, or workflow before implementation. Durable artifacts may precede final proof;
review inventory and final landing require progressively stronger observed evidence.

#### Scenario: Planned proof is enough for implementation edits
- **WHEN** a valid rapid brief names an exact executable user-facing proof but has no
  observed result yet
- **THEN** the edit gate may accept the brief

#### Scenario: Planned proof permits a durable recovery artifact
- **WHEN** a valid rapid brief reaches a task-branch commit or push before final
  outcome proof exists
- **THEN** the durable stage may accept it without claiming delivery is complete

#### Scenario: Unready work cannot enter human review inventory
- **WHEN** a rapid brief reaches PR creation without a focused observed result, or
  with focused proof unrelated to the planned outcome, an objective blocker, a known
  limitation, or absent remaining landing proof
- **THEN** the review stage rejects it while leaving branch and commit durability
  available

#### Scenario: Review-ready work may open a PR
- **WHEN** bounded behavior works, focused proof passes, no objective blocker remains,
  and limitations plus remaining landing proof are stated
- **THEN** the review stage accepts PR creation without claiming final delivery

#### Scenario: Planned-only proof cannot complete delivery
- **WHEN** the same rapid brief reaches merge or task closure without a substantive
  final observed result
- **THEN** the final stage rejects it

#### Scenario: Observed proof can land
- **WHEN** the rapid brief records both the exact proof and its substantive observed
  result
- **THEN** the final stage accepts the oracle requirement

#### Scenario: Non-observations do not satisfy final proof
- **WHEN** the observed-result field is empty, placeholder text, future-tense intent,
  a command echo, or only says that tests passed without stating the user-facing result
- **THEN** the final stage rejects it

#### Scenario: Proof has explicit expected and actual evidence
- **WHEN** planned proof reaches any stage or observed proof reaches review/final
- **THEN** planned proof names a Command, Query, API, Report, or UI action plus its
  expected result, observed proof records expected and actual as the same result, and
  final observed proof matches the planned expected result

#### Scenario: Sentence-shaped fictional command is not executable proof
- **WHEN** Command or Report evidence is prose without a resolvable executable and
  parseable argv containing an option, path, or qualified argument
- **THEN** the rapid brief is rejected and requires executable proof or the full lane

#### Scenario: Query label does not launder prose
- **WHEN** Query evidence contains words such as select and from but is not wholly a
  supported SQL select form
- **THEN** the rapid brief is rejected as non-executable proof

#### Scenario: Compound command uses strongest stage
- **WHEN** one shell command contains durable, review, and/or final actions separated
  by control operators, background separators, pipe-stderr operators, literal
  newlines, or dynamic `eval`
- **THEN** the gate applies the strongest recognized proof stage to the whole command

### Requirement: Rapid independent review is conditional
The rapid workflow SHALL always verify the actual user-facing outcome and SHALL NOT
unconditionally dispatch an adversarial reviewer. It MUST call for independent review
when a specialty boundary, task-maturity gap, or named failure mechanism requires a
second evidence source, and MUST escalate protected or unresolved work to the full
lane.

#### Scenario: Bounded mature work avoids mandatory review ceremony
- **WHEN** rapid eligibility remains valid and no independent-review trigger exists
- **THEN** the workflow completes user-facing verification without requiring an
  adversarial-reviewer dispatch

#### Scenario: Leading trigger escalates the current run
- **WHEN** a protected surface is discovered, the boundary expands, reversibility
  becomes uncertain, controls cannot discriminate, root cause remains unresolved,
  or the outcome oracle is missing
- **THEN** the workflow stops rapid execution and requires the full lane

#### Scenario: Distinct review need remains available
- **WHEN** a specialty boundary, task-maturity gap, or named failure mechanism would
  benefit from independent evidence
- **THEN** the workflow requests a reviewer with that explicit purpose

### Requirement: Durable artifact precedes review inventory
The rapid workflow SHALL permit a branch, commit, or vertical slice as the early
durable artifact and SHALL describe a PR as review-ready only after bounded behavior
and focused proof are complete.

#### Scenario: Incomplete work remains durable without entering review
- **WHEN** work needs a recovery boundary but still has objective-blocking behavior or
  proof work
- **THEN** it may persist a branch, commit, or vertical slice without opening a PR

#### Scenario: Review-ready work opens a PR
- **WHEN** bounded behavior works, focused proof passes, no objective-blocking work
  remains, and limitations and remaining landing proof are stated
- **THEN** the workflow may open the PR for review
