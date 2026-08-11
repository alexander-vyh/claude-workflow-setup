# Beads

Beads is the task-state system, not the workflow-policy authority.
Git, pull-request, merge, deployment, completion, memory, and agent-behavior
policy come from Escapement and the repository's `.escapement/repo.json`.

Issues live in the local Dolt database under `.beads`; `.beads/issues.jsonl` is
a passive export and must not be treated as the wire protocol. Use `bd ready`,
`bd show <id>`, `bd update <id> --claim`, and `bd close <id>` for work state.

Escapement owns worktree creation policy. Use the concrete bundled
`escapement-worktree create` command injected into session context so the
source commit, target repository, isolation, and optional Beads context are
verified together. Beads remains task state and is checked after native Git
creation when present.

Any repository may opt into post-creation setup by committing a generic
bootstrap declaration to `.escapement/repo.json`:

```json
{
  "worktree": {
    "bootstrap": {
      "argv": ["tools/setup-worktree", "--non-interactive"],
      "timeout_seconds": 300
    }
  }
}
```

Escapement executes that exact argv in the verified target without a shell,
then re-verifies the transaction before returning success. Without the
declaration, repository provisioning is not applicable.

Do not use TodoWrite, TaskCreate, or markdown TODO lists for project work
tracking. If follow-up work is discovered, create or update a bead.
