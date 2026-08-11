# ADR-005: Keep Canonical State in Escapement and Derive Attention Views

## Status

Accepted

## Context

Intervention records must span repositories, sessions, agents, and clients. Beads owns
task state, native clients own their own prompt rendering, and gate-signal JSONL is
fail-soft analytics. Making any of those the authority would create competing truth or
let an agent-authored record authorize work. A separately mutable inbox would introduce
the same duplication.

## Decision

Canonical intervention request and lifecycle state will live in Escapement-owned runtime
storage keyed by host-neutral session and actor identity. Native cards and gate signals
are adapter projections. Cross-session lists, inboxes, notifications, and reports are
rebuildable read models derived from canonical pending requests; they never own status
or resolution. Phase 1 uses simple per-session atomic files and journals and adds no
daemon or external database.

## Consequences

All clients can share one lifecycle without surrendering native rendering. Attention
views can be rebuilt and audited. The core must define stable identity, atomic writes,
redaction, retention, and replay semantics. A stronger transactional store remains a
future option only if measured concurrency requires it.
