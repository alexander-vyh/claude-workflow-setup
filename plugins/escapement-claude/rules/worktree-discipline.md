# Worktree Discipline — One Writer, One Worktree (Global Rule)

## The unit of isolation is the WRITER, not the agent

A **writer** is anything that will run `git add` / `commit` / `checkout` /
`stash` / `rebase` / branch-switch in a checkout: an interactive Claude
session, a headless/cron session, a dispatched subagent, or the user. Two
writers in one working tree is a race — and on this machine, **multiple
concurrent sessions are the default state**, so a repo's root checkout must
always be presumed contended.

The earlier form of this rule ("two or more writing *agents* means one
worktree each") was scoped to subagents inside one session. That scoping was
proven insufficient the same night it shipped: a session following it was
still clobbered by *other concurrent sessions* sharing the root checkout
(2026-07-08). Sessions are writers. The rule covers them equally.

## The rule

1. **Sessions claim a worktree before write work.** Before the first
   git-mutating action of any task, create your own worktree + branch and do
   ALL write work there. Use the concrete bundled command injected into live
   session context:

   ```bash
   python3 -B <injected-bundled-cli-path> create \
     --repo "$(git rev-parse --show-toplevel)" \
     --name <task> \
     --branch <branch>
   ```

   This is the `escapement-worktree create` transaction: Escapement resolves
   and verifies source, location, isolation, and optional tracker context
   together.

   "I'm only making one small commit" is not an exemption — the wrong-branch
   incidents were single commits.

2. **The root checkout is a shared surface — treat it as read-only.** Its job
   is to sit on `main` and host worktrees. Never `checkout`, `stash`,
   `commit`, `rebase`, or branch-switch there while any other session may be
   running. Uncommitted WIP you find there belongs to someone else; leave it.

3. **Two or more writing agents → one worktree and branch each** —
   one `escapement-worktree create` transaction per agent, or
   `isolation: "worktree"` on the Agent dispatch. Prompt-level "you own these
   files" lanes are merge-planning notes, **never** the isolation mechanism;
   compliance-based lanes leaked twice in one evening (2026-07-08: one agent's
   commit swept a sibling's uncommitted edits; another agent drifted into the
   root checkout and committed onto an unrelated in-flight branch).

4. **Merges are deliberate.** The session that owns the feature branch merges
   writer branches back explicitly. No writer merges into, or rebases, a
   branch another writer is standing on.

5. **Verify location before every git op** (defense-in-depth, not a
   substitute for 1–4): `git rev-parse --abbrev-ref HEAD && git status
   --short`. If the branch or tree state is not what you left, STOP — another
   writer moved it.

## Repair — when a shared tree moved under you

Your commits are safe git objects even if the tree changed. Recover without
touching the shared tree:

1. Find your commit: `git log --all --oneline` / `git branch --contains <sha>`;
   confirm its parent is what you based on (`git rev-parse <sha>~1`).
2. Land it by ref-manipulation: `git branch -f <your-branch> <sha>`, then
   `git push origin <your-branch>`, then PR.
3. **Never** `git stash`, `git checkout`, `git clean`, or discard when the
   tree holds WIP you didn't write — that destroys another writer's work.

(Live incident this pattern comes from: 2026-06-18, escapement — another
session checked out a different branch mid-task; a commit landed under the
wrong branch label and `gh pr create` reported "No commits between main and
branch".)

## Exemptions (Flexibility)

- **Read-only work** (investigation, review, search) needs no worktree.
- **Repos that are genuinely single-writer** — but the burden is to *know*
  (you created the checkout this session; nothing else runs there), never to
  assume. Multi-session is this machine's default.
- **User-directed edits in the root checkout** when the user explicitly asks
  for them there — still run the rule-5 verification first and surface any
  foreign WIP before proceeding.

## Why (Internal transparency)

Git isolation is mechanical; file lanes and carefulness are compliance-based.
Every observed clobbering incident — 2026-06-18 (branch switched under a
session), 2026-07-08 ×2 (lane leaks between agents), 2026-07-08 (session
clobbered by concurrent sessions) — is impossible by construction under
worktree-per-writer. The rule converts a recurring recovery burden into a
one-command setup cost.

Related: `agent-teams-default.md` (dispatch mechanics),
`beads-worktree` skill (Escapement creation and tracker verification),
`continuation-harness.md` (outcome delivery from the worktree's branch).
