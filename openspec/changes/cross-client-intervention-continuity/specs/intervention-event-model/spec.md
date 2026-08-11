<!-- Spec: intervention-event-model -->

## Purpose

Define the observer-only common envelope used to reconcile native human-attention events
without changing client behavior.

## ADDED Requirements

### Requirement: Normalize fixture-proven native interventions

The Phase 1 observer SHALL emit one normalized event for each fixture-proven native
approval, edit confirmation, clarification, credential request, informational question,
or equivalent action-required event. Each event MUST include stable event identity,
host/version provenance, session and actor identity, repository/worktree identity when
available, native kind, semantic action, intervention kind, wait scope, lifecycle
timestamps, and a redacted native reference.

#### Scenario: Dynamic command variants share semantic identity

- **WHEN** two native approvals perform the same routine verification action but differ
  in generated revisions, selectors, or prose
- **THEN** the observer records distinct event ids with the same semantic action and does
  not use the complete command string as authority

#### Scenario: Unknown payload remains explicit

- **WHEN** an installed host emits a payload that does not satisfy a fixture-proven
  classifier
- **THEN** the observer records it as unknown or unsupported and does not infer an allow,
  resolution, or session wait scope

### Requirement: Keep normalization non-authoritative

The Phase 1 model MUST NOT suppress, answer, allow, deny, or resume any native event.

#### Scenario: Observer failure cannot release an action

- **WHEN** normalization fails or storage is unavailable for a consequential native
  approval
- **THEN** native client behavior remains unchanged and the affected action is not
  released by Escapement
