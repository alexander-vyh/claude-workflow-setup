<!-- Spec: action-local-wait-continuity -->

## Purpose

Prove that Escapement can distinguish action-local attention from true global input
requirements and preserve one controlled independent execution path.

## ADDED Requirements

### Requirement: Represent pending attention without global suspension

The Phase 1 controlled harness SHALL classify a session as `running_with_attention` while
a bound intervention is pending and an explicitly disjoint sibling action remains
runnable. It SHALL classify the affected action as `waiting_human` without resolving or
suppressing the native intervention.

#### Scenario: Sibling completes during informational wait

- **WHEN** native evidence proves a controlled informational intervention is visible and
  unresolved before a deterministic disjoint reversible sibling becomes ready
- **THEN** the same Escapement supervisor dispatches the sibling and independent evidence
  shows its ready-to-running transition, first tool invocation, and effect all occur
  before native resolution while state reports `running_with_attention`; at sibling
  completion, independent evidence still shows the card visible, no submitted decision,
  and the affected action waiting

#### Scenario: No runnable work requires input

- **WHEN** every remaining action depends on an unresolved intervention
- **THEN** the observer reports `input_required` rather than inventing independent work

### Requirement: Preserve consequential blocking control

The liveness probe MUST include an unresolved consequential action that remains
unexecuted.

#### Scenario: Blocking control does not execute

- **WHEN** the controlled consequential action lacks delegated authority and human
  resolution
- **THEN** native evidence shows no corresponding effect occurred while unrelated
  reversible work may continue

### Requirement: Limit continuation enforcement to the controlled slice

Phase 1 SHALL enable action-local dispatch only in the explicit controlled liveness
harness. Normal-session behavior remains observer-only until Phase 1 validates.

#### Scenario: Normal sessions remain unchanged

- **WHEN** Phase 1 observer mode is active outside the controlled harness
- **THEN** Escapement does not alter native continuation or resolve a pending request

### Requirement: Enforce action-local continuation broadly

[DEFERRED: pending Phase 1 validation] Escapement SHALL later resume, pause, or cancel only
the bound action and its dependent edges in opted-in normal sessions.

#### Scenario: Deferred broad behavior stays disabled

- **WHEN** Phase 1 completes only the controlled liveness slice
- **THEN** no normal-session adapter claims broad continuation enforcement readiness
