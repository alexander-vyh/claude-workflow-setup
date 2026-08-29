# Escapement Shared Workflow

<!-- escapement:core-identity:start -->
<!-- escapement:mission:start -->
Escapement converts available agent capacity plus delegated authority into verified, delivered outcomes while reserving human attention for consequential choices.
<!-- escapement:mission:end -->

Build the smallest thing that satisfies the outcome and can be verified. Prefer
deleting machinery to adding it. If a rule below could be a mechanism that just
does the work, make it one.

Invariants, whatever the host:

- track work in `bd`; state outcome and oracle before non-trivial implementation;
- test behavior, not implementation echoes; verify the user-facing outcome before closing;
- never destroy user work without an explicit decision;
- keep files lean — past 500 lines a hook nudges, past 1000 it blocks (waiver-overridable).
  Line count is a proxy; the real concerns are coupling and mixed responsibility.

Its durable capabilities form one closed loop:

<!-- escapement:capabilities:start -->
1. Intent and authority
2. Design and specification
3. Executable dependency-aware work breakdown
4. Capacity allocation
5. Isolated execution
6. Action-local continuation and repair
7. Independent outcome verification
8. Authorized landing and delivery
9. Learning and feedback
<!-- escapement:capabilities:end -->

Those capabilities define Escapement. Clients, planners, task stores, hosts, and
hook mechanisms are replaceable adapters; their availability may differ without
changing the mission.
<!-- escapement:core-identity:end -->

Current adapter mapping, explicit but non-defining:

<!-- escapement:adapter-mapping:start -->
- Design and specification | OpenSpec
- Executable dependency-aware work breakdown | Beads
- Isolated execution | Git worktrees
- Capacity allocation | Claude Code, Codex
- Authorized landing and delivery | GitHub
<!-- escapement:adapter-mapping:end -->
