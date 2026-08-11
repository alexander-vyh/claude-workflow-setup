# ADR-004: Observe Before Broad Enforcement

## Status

Accepted

## Context

Public client documentation does not prove the payload shape or lifecycle behavior of
the installed client. A shared normalizer could miss prompts, misclassify wait scope, or
claim progress after merely hiding a card. Changing permissions before measuring native
truth would make Escapement both implementation and oracle.

## Decision

Normal-session adapters are observer-only in Phase 1. They will reconcile redacted native
fixtures against a host-neutral journal and time-window query without suppressing prompts,
resolving interventions, or executing blocked actions. One explicitly controlled,
off-by-default liveness harness may use the shared supervisor to dispatch a proven-disjoint
reversible sibling after native evidence shows an informational intervention is pending.
Each adapter capability requires independently captured installed-version evidence and a
behavioral fixture; unavailable events remain partial or unsupported.

## Consequences

The first phase does not reduce prompt volume or preauthorize actions, but it proves one
real continuity path rather than merely observing native luck. Broad enforcement is
delayed until evidence exists. Adapter maintenance gains an explicit compatibility
contract and cannot claim readiness from documentation alone.
