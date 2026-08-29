# Beads

Beads is the task-state system, not the workflow-policy authority.
Git, pull-request, merge, deployment, completion, memory, and agent-behavior
policy come from Escapement and the repository's `.escapement/repo.json`.

Issues live in the Dolt database under `.beads`; `.beads/issues.jsonl` is a
passive export, not the wire protocol. Use `bd ready`, `bd show <id>`,
`bd update <id> --claim`, and the matching close command for work state.

Escapement owns worktree creation. The worktree guard names the exact
`escapement-worktree create` command at the moment you need it — no need to
memorize it here.

Do not use TodoWrite, TaskCreate, or markdown TODO lists for project work.
