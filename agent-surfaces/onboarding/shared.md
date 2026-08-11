# Escapement Shared Workflow

<!-- escapement:core-identity:start -->
<!-- escapement:mission:start -->
Escapement converts available agent capacity plus delegated authority into verified, delivered outcomes while reserving human attention for consequential choices.
<!-- escapement:mission:end -->

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

These capabilities define Escapement. Current clients, planning systems, task
stores, source-control hosts, and hook mechanisms are replaceable adapters. Their
availability and enforcement may differ without changing the mission.
<!-- escapement:core-identity:end -->

The host adapter may change which hooks, tools, and config files are available.
The workflow invariants do not change:

- use `bd` for task tracking;
- make outcome and oracle explicit before non-trivial implementation;
- prefer behavioral checks over implementation echoes;
- verify the real user-facing outcome before closing work;
- preserve user work and avoid destructive cleanup without an explicit decision;
- keep files lean: a PreToolUse hook gives soft guidance past 500 lines and hard-blocks past 1000 (waiver-overridable) — extract a cohesive responsibility into a sibling module rather than growing a file. Line count is a weak proxy; the real concerns are complexity and coupling (multiple responsibilities, long/deeply-nested functions, near-duplicate blocks), framed for both human reviewability and agent edit-reliability.

Current adapter mapping is explicit but non-defining:

<!-- escapement:adapter-mapping:start -->
- Design and specification | OpenSpec
- Executable dependency-aware work breakdown | Beads
- Isolated execution | Git worktrees
- Capacity allocation | Claude Code, Codex
- Authorized landing and delivery | GitHub
<!-- escapement:adapter-mapping:end -->
