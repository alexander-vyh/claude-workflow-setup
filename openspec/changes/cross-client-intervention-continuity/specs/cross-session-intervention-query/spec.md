<!-- Spec: cross-session-intervention-query -->

## Purpose

Provide a deterministic, host-neutral answer to which human interventions occurred in a
requested time window.

## ADDED Requirements

### Requirement: Query all observed interventions in a time window

The public Phase 1 query SHALL return every normalized intervention whose lifecycle
intersects the requested interval across supported repositories, sessions, actors, and
hosts. Results MUST expose provenance, lifecycle status, wait duration where derivable,
and any declared coverage gaps.

#### Scenario: Forty-eight-hour cross-repository inventory

- **WHEN** the user requests the prior forty-eight hours and observed events exist in
  multiple repositories and hosts
- **THEN** the query returns each event exactly once without requiring the caller to know
  its repository or session in advance

#### Scenario: Coverage gap is not reported as zero events

- **WHEN** a host lacks a fixture-proven event source during part of the requested period
- **THEN** the query marks that host/time range incomplete instead of claiming no
  interventions occurred

### Requirement: Reconcile query results to native truth

Each controlled corpus and live event MUST be traceable from a query result to one
redacted native event reference, and duplicate ingestion MUST NOT create duplicate
results.

#### Scenario: Replayed native fixture is deduplicated

- **WHEN** the same stable native event is observed more than once
- **THEN** the query returns one intervention with replay provenance rather than two
  human-attention events
