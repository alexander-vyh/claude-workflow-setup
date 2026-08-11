<!-- Spec: semantic-preauthorization -->

## Purpose

Test whether routine authority can be described semantically without enabling it during
the observer phase.

## ADDED Requirements

### Requirement: Record expected semantic authority independently

The Phase 1 corpus SHALL label expected authority from explicit task scope,
`.escapement/repo.json`, prerequisites, and confirmation carve-outs before the normalizer
is evaluated. Command strings, prompt prose, Beads records, and adapter assertions MUST
NOT determine the expected label.

#### Scenario: Equivalent routine commands retain one expected authority

- **WHEN** independently frozen labels include two different command shapes for the same
  routine action and, after normalizer implementation, the oracle generates a runtime
  metamorphic variant that changes only pre-declared irrelevant dynamic fields
- **THEN** their corpus labels identify the same semantic authority and preserve their
  distinct concrete event identity

#### Scenario: Scope expansion still requires a decision

- **WHEN** two actions share a command prefix but one introduces a target, effect, or
  confirmation class outside the delegated task and repo outcome
- **THEN** its expected label requires human intervention even if its command prefix
  matches a previously routine action

### Requirement: Exclude adapter and raw command verdicts from authority input

The Phase 1 architecture SHALL keep independently frozen expected-authority labels
outside host adapters. Any future authority evaluation boundary MUST accept normalized
semantic action plus current task/repository authority and MUST NOT accept a raw command,
prompt prose, or adapter-provided allow verdict as authority.

#### Scenario: Adapter cannot mark its own event allowed

- **WHEN** a host adapter emits a normalized event containing native command evidence
- **THEN** the adapter output cannot set or override the independently expected authority
  label

### Requirement: Apply semantic preauthorization

[DEFERRED: pending Phase 1 validation] Escapement SHALL later suppress a native approval
only when the installed host can accept the decision and current semantic authority
matches at point of action.

#### Scenario: Phase 1 never auto-allows

- **WHEN** an observed routine action has an expected `allow` label
- **THEN** the Phase 1 observer records the label but does not answer or suppress the
  native approval
