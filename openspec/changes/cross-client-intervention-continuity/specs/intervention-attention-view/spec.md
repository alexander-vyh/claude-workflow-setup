<!-- Spec: intervention-attention-view -->

## Purpose

Define the Phase 1 attention list as a rebuildable projection over canonical observed
requests rather than a second workflow authority.

## ADDED Requirements

### Requirement: Derive pending attention from canonical events

The Phase 1 attention view SHALL derive unresolved requests from canonical observed
lifecycle records. It MUST expose request identity, native provenance, affected action,
wait scope, requested time, and current known status without storing an independent
mutable decision.

#### Scenario: Rebuilding the view preserves results

- **WHEN** the attention projection is deleted and rebuilt from canonical journals
- **THEN** it returns the same pending request identities and statuses

#### Scenario: Resolved request leaves pending view

- **WHEN** a fixture-proven native resolution is observed for a pending request
- **THEN** the request remains queryable historically but no longer appears as pending

### Requirement: Avoid duplicate human attention

Observer mode MUST NOT create a second user notification for a native prompt.

#### Scenario: Native card remains sole notification

- **WHEN** a native client already displays an approval or question during Phase 1
- **THEN** Escapement records and lists it without emitting another card, email, or To-Do

### Requirement: Resolve from the attention view

[DEFERRED: pending Phase 1 validation] A later attention surface SHALL bind an authenticated
resolution to the canonical request and re-evaluate current authority before resuming.

#### Scenario: Phase 1 view is read-only

- **WHEN** a caller lists pending attention in Phase 1
- **THEN** no interface exposed by the view can approve, deny, or resume the request
