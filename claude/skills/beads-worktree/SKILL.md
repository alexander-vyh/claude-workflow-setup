---
name: beads-worktree
description: Use when creating an isolated worktree or checking Beads task-state resolution inside one. Escapement owns creation policy; Beads remains tracker state.
---

# Beads + Git Worktrees

Create worktrees with the concrete bundled `escapement-worktree create`
transaction injected into live session context:

```bash
python3 -B <injected-bundled-cli-path> create \
  --repo "$(git rev-parse --show-toplevel)" \
  --name <task> \
  --branch <branch>
```

Escapement owns source selection, target location, creation, and verification.
When `.beads/` exists, the transaction also verifies that the new checkout
resolves the same tracker state. Beads does not own creation policy.

## Existing linked worktrees

Beads resolves a linked worktree's tracker through Git's common directory; a
`.beads/redirect` file is not required. Once a linked worktree exists, normal
Git operations such as commit, push, merge, and rebase must be allowed. Check
task state with:

```bash
git rev-parse --path-format=absolute --git-common-dir
bd context --json
bd show <known-issue-id>
```

The last command should return the same issue state from the primary checkout
and the linked worktree. **Do not run `bd init` inside a worktree.**

## If tracker resolution fails

1. Run `bd context --json` from the linked worktree and inspect the reported
   tracker root.
2. Compare `bd show <known-issue-id>` from the linked worktree and primary
   checkout.
3. If the results differ, stop and investigate the actual Beads/Git layout;
   do not create a new database with `bd init` or remove `.beads/` blindly.
