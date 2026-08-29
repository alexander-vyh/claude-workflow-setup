# Worktree Discipline — One Writer, One Worktree (Global Rule)

## The unit of isolation is the WRITER, not the agent

A **writer** is anything that will run `git add` / `commit` / `checkout` / `stash` /
`rebase` / branch-switch in a checkout: an interactive session, a headless/cron session, a
dispatched subagent, or the user. Two writers in one working tree is a race — and on this
machine **multiple concurrent sessions are the default state**, so a repo's root checkout
must always be presumed contended. Sessions are writers; the rule covers them exactly as
it covers subagents.

## The rule

1. **Claim a worktree before write work.** Before the first git-mutating action of any
   task, create your own worktree + branch and do ALL write work there, using the bundled
   command injected into live session context:

   ```bash
   python3 -B <injected-bundled-cli-path> create \
     --repo "$(git rev-parse --show-toplevel)" \
     --name <task> \
     --branch <branch>
   ```

   This is the `escapement-worktree create` transaction: it resolves and verifies source,
   location, isolation, and tracker context together. "I'm only making one small commit"
   is not an exemption — the wrong-branch incidents were single commits.

2. **The root checkout is a shared surface — treat it as read-only.** Its job is to sit on
   `main` and host worktrees. Never `checkout`, `stash`, `commit`, `rebase`, or
   branch-switch there while another session may be running. Uncommitted WIP you find
   there belongs to someone else; leave it.

3. **Two or more writing agents → one worktree and branch each** — one
   `escapement-worktree create` per agent, or `isolation: "worktree"` on dispatch.
   Prompt-level "you own these files" lanes are merge-planning notes, **never** the
   isolation mechanism; compliance-based lanes have leaked in practice.

4. **Merges are deliberate.** The session owning the feature branch merges writer branches
   back explicitly. No writer merges into, or rebases, a branch another writer stands on.

5. **Verify location before every git op** (defense-in-depth, not a substitute for 1–4):
   `git rev-parse --abbrev-ref HEAD && git status --short`. If the branch or tree state is
   not what you left, STOP — another writer moved it.

## Repair — when a shared tree moved under you

Your commits are safe git objects even if the tree changed. Recover without touching the
shared tree:

1. Find your commit: `git log --all --oneline` / `git branch --contains <sha>`; confirm
   its parent is what you based on (`git rev-parse <sha>~1`).
2. Land it by ref-manipulation: `git branch -f <your-branch> <sha>`, then
   `git push origin <your-branch>`, then PR.
3. **Never** `git stash`, `git checkout`, `git clean`, or discard when the tree holds WIP
   you did not write — that destroys another writer's work.

## Exemptions (Flexibility)

- **Read-only work** (investigation, review, search) needs no worktree.
- **Genuinely single-writer repos** — but the burden is to *know* (you created the
  checkout this session; nothing else runs there), never to assume.
- **User-directed edits in the root checkout** when explicitly asked — still run the
  rule-5 verification first and surface any foreign WIP before proceeding.

Related: `agent-teams-default.md` (dispatch mechanics), `beads-worktree` skill (creation
and tracker verification), `continuation-harness.md` (delivery from the worktree branch).
