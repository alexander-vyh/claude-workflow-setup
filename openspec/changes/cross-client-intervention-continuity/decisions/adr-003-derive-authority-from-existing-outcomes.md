# ADR-003: Derive Routine Authority From Existing Outcomes

## Status

Accepted

## Context

Repeated native approvals are often keyed to exact commands, arguments, or prose even
when the user has already delegated an outcome. Dynamic revisions, test selectors, pull
request bodies, and generated worktree names then create effectively identical prompts.
Escapement already owns task/outcome workflow policy through explicit user intent and
`.escapement/repo.json`.

## Decision

Future preauthorization will use a small Escapement-owned semantic action vocabulary
derived from explicit task scope, repository outcome, prerequisites, and explicit
confirmation carve-outs. Command strings, shell comments, native prompt prose, adapter
claims, Beads records, and analytics events are evidence only. Escapement will not add a
general policy engine, policy DSL, or arbitrary-shell approval wrapper.

Phase 1 records expected authority for analysis but cannot allow, deny, or suppress an
action.

## Consequences

Routine delegated work can eventually survive harmless argument changes while real
scope expansion still asks. The vocabulary stays inspectable and matches existing repo
ownership. Adding a new semantic action requires a real unmatched event and contract
review; unsupported or ambiguous actions remain explicit rather than falling through to
an allow.
