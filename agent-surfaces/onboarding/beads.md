# Beads

Beads is the task-state system, not the workflow-policy authority.
Git, pull-request, merge, deployment, completion, memory, and agent-behavior
policy come from Escapement and the repository's `.escapement/repo.json`.

Issues live in the local Dolt database under `.beads`; `.beads/issues.jsonl` is
a passive export and must not be treated as the wire protocol. Use `bd ready`,
`bd show <id>`, `bd update <id> --claim`, and `bd close <id>` for work state.

Use `bd worktree create` instead of `git worktree add` in this repo so linked
worktrees share the correct beads database.

Do not use TodoWrite, TaskCreate, or markdown TODO lists for project work
tracking. If follow-up work is discovered, create or update a bead.
