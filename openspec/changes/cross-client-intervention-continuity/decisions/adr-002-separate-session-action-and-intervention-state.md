# ADR-002: Separate Session, Action, and Intervention State

## Status

Accepted

## Context

Current client surfaces commonly collapse a pending approval or question into a session
that is globally waiting for user input. That loses the distinction between the affected
action, dependent actions, and independent work owned by the same session or siblings.
A single status cannot represent both pending attention and ongoing useful work.

## Decision

Escapement will model session state, action state, and intervention state as independent
axes joined by stable identifiers and dependency bindings. A pending intervention moves
its bound action to `waiting_human`. The aggregate session becomes
`running_with_attention` while any independent action remains runnable, and becomes
`input_required` only when no independent work can proceed.

## Consequences

One human question no longer has to stop a parent or sibling. Attention, progress, and
blocking telemetry become honest. The model requires explicit dependency handling and
conservative behavior when dependency information is missing; this is more work than a
single status enum but is necessary to prevent global-pause semantics from reappearing.
