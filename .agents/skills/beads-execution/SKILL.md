---
name: "beads-execution"
description: "Use only when the user explicitly asks to execute, work on, run, or start a tracked Beads task, normally identified by a task ID."
---

# Beads Execution

Use this skill for execution intent, not keyword recognition.

Do not select this skill for informational, diagnostic, comparison, status, or
historical questions about Beads. A task ID by itself is not execution intent.
If the user says not to execute, answer directly and do not claim or mutate work.

Negative routing examples:

- "did Beads add back PR guidance?"
- "what changed in Beads?"
- "explain bead ESC-123, but do not execute it."

Positive routing examples:

- "execute bead ESC-123"
- "work on task ESC-123"
- "run the ready Beads tasks"
- "start the next tracked task"

## Execution contract

1. Run `bd prime` after session start or context recovery.
2. Resolve the requested task with `bd show <id>`. If the user explicitly asks
   to run ready work without naming an ID, inspect `bd ready`.
3. Claim the task before implementation with `bd update <id> --claim`.
4. Use the session-injected `escapement-worktree create` transaction when
   isolated implementation work is needed; Beads remains task state only.
5. Follow the repository's outcome-and-oracle discipline before production
   code: define the outcome, independent oracle, constraints, controls, and
   final verification.
6. Implement the smallest coherent change and verify the actual requested
   outcome, not only test status.
7. Update or close the Beads task only after the outcome and repository state
   satisfy its acceptance criteria.
8. After verified merge or deployment from an Escapement-created worktree, run
   the session-supplied `escapement-worktree finish` command. A `pending` result
   is a safe handoff to the existing supervisor, not a completed deletion.

Preserve user work. Do not delete, reset, merge, push, or close unrelated work
merely because it appears in Beads output.
