<!-- Spec: host-intervention-adapters -->

## Purpose

Constrain client adapters to evidence-backed normalization while preserving explicit
capability gaps.

## ADDED Requirements

### Requirement: Adapter readiness requires installed-version fixtures

An adapter capability SHALL be marked ready only when evidence captured outside the
adapter records the installed binary version, redacted native event reference, fixture
hash, separately frozen expected labels, and controlled live result. The fixture MUST
exercise its observer path and reconcile to native evidence. Public documentation or an
adapter-authored version field alone MUST NOT establish readiness.

#### Scenario: Documented hook absent from installed client

- **WHEN** public documentation describes an event but the installed client cannot emit
  or fixture it
- **THEN** the manifest reports that capability partial or unsupported and the query
  exposes the coverage gap

#### Scenario: Proven observer path becomes ready

- **WHEN** independently captured installed-version evidence and its hashed redacted
  fixture exercise a manifest-owned adapter and produce the separately frozen result
- **THEN** only the proven observer capability may be marked ready

### Requirement: Adapters do not own policy

Host adapters MUST translate payloads and render supported responses; they MUST NOT
invent authority or silently rewrite unrelated personal client configuration.

#### Scenario: Equivalent hosts produce common semantics

- **WHEN** two fixture-proven hosts surface semantically equivalent intervention events
- **THEN** their adapters preserve host provenance while producing the same core semantic
  action and intervention kind
