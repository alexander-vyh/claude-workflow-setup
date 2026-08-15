"""Deterministically reconstruct the legacy Codex beads skill regression fixture."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT_CLAUDE_SKILL = ROOT / "claude" / "skills" / "beads-execution" / "SKILL.md"
CURRENT_CODEX_SKILL = ROOT / ".agents" / "skills" / "beads-execution" / "SKILL.md"
PRE_FINISH_CODEX_SKILL_SHA256 = (
    "f08ccbb6c66668db81a8a1fe19e1f2302be17a4a204f11dc43522a0b2031f1cb"
)
PREVIOUS_CODEX_SKILL_SHA256 = (
    "d175cacf8aff932af013d0d410a2e8324a505c35b7d7fd5301c34d78c0d22bcc"
)

_FINISH_GUIDANCE = (
    "\n8. After verified merge or deployment from an Escapement-created worktree, run\n"
    "   the session-supplied `escapement-worktree finish` command. A `pending` result\n"
    "   is a safe handoff to the existing supervisor, not a completed deletion."
)
HISTORICAL_CRLF_SHA256 = (
    "2096820ff0d7a712aa4b58ca2590979f80f2d0fd168a23bda788a024f47792e0"
)
HISTORICAL_LF_SHA256 = (
    "c65855a32ece63079c692332a968174748187b9fb8e1a57bf3803dcc76beb402"
)

_REPLACEMENTS = (
    (
        """**If a specific task ID is provided as an argument**, skip `bd ready` and go
directly to that task. Claim it, execute it, and carry that task through the
repository-declared landing and verification outcome. Do not expand a bounded
invocation to unrelated ready beads; continue only causally in-scope work that
the completed task unblocks.
""",
        """**If a specific task ID is provided as an argument**, skip `bd ready` and go
directly to that task. Claim it and execute it. After it completes, ask the user
if they want to continue with the next ready task or stop.
""",
    ),
    (
        """3. Resolve the landing policy from `.escapement/repo.json` through
   `harness/bin/repo_outcome.py`, unless session context already provides the
   resolved policy. Treat malformed or absent policy as its conservative
   `pr-opened` default.
4. Confirm tests and quality gates pass, then follow the resolved outcome:
   - `committed`: leave the verified task commit on its isolated branch.
   - `pr-opened`: push the feature branch and create or update its pull request.
   - `merged`: push, create or update the pull request, repair CI/review failures,
     verify green status independently, and merge through the remote landing path
     when repository policy authorizes it.
   - `merged-and-deployed`: complete the `merged` path, then run the declared
     deployment or refresh workflow and verify the installed/user-facing outcome.
5. Never merge a feature branch into the local default-branch checkout. Landing
   happens through the repository's remote pull-request path. If one consequential
   authorization is genuinely missing, block only that action and continue every
   independent authorized verification or repair route.
""",
        """3. Finish the branch — **PR-only** (this repo never merges to main directly):
   - Confirm tests / quality gates pass.
   - `git push` the feature branch.
   - Open a PR with `gh pr create` (if it 403s on a read-only account, print the
     `compare/main...<branch>` URL instead — see the gh-account memory).
   - Do **not** merge to main locally. If not ready to PR: keep the branch, or discard it.
""",
    ),
    (
        """**If a discrepancy between the task description, design doc, validation findings,
or upstream results requires a product decision, block only the affected task and
its dependents. Preserve the decision and continue independent ready tasks.**

Ask the user only when every remaining route to the delegated outcome depends on
the same unresolved consequential choice. A discrepancy is not permission to guess,
but it is also not permission to stop unrelated authorized work.

Examples that require an action-local decision:
- Validation gate recommended changes that conflict with the task description
- Design doc says X but the codebase actually does Y
- An API doesn't work as the spec assumed
- Two sources of truth disagree about what to build

**The pattern:**
1. State the conflict clearly (source A says X, source B says Y)
2. Identify the affected task and dependent tasks
3. Continue independent ready tasks, causal verification, and repair
4. Ask which source governs only if the product decision is still required
5. Resume the affected branch when the answer arrives
""",
        """**If ANY discrepancy exists between the task description, the design doc,
validation findings, or upstream task results — STOP and ask the user.**

This is not optional. This is not "note it and keep going." This is a hard stop.

Examples that require stopping:
- Validation gate recommended changes that conflict with the task description
- Design doc says X but the codebase actually does Y
- An API doesn't work as the spec assumed
- Two sources of truth disagree about what to build

**The pattern:**
1. State the conflict clearly (source A says X, source B says Y)
2. Ask which to follow
3. Wait for the answer
4. Then implement
""",
    ),
    (
        """**Required workflow setup:**
- **Isolated workspace** — REQUIRED: use the concrete bundled `escapement-worktree create` transaction injected into session context. It verifies repository, source, location, branch, and shared Beads task state together.
- **Code review** — use the repo's own review template (the `adversarial-reviewer` agent / the `dispatching-parallel-agents` review prompt) for the quality gate (Step 2f).
- **Finish** — repository-outcome-driven branch finish, inline in Step 4 (commit,
  pull request, authorized remote merge, deployment/refresh, and verification as
  selected by `.escapement/repo.json`).
""",
        """**Required workflow setup:**
- **Isolated workspace** — REQUIRED: use the concrete bundled `escapement-worktree create` transaction injected into session context. It verifies repository, source, location, branch, and shared Beads task state together.
- **Code review** — use the repo's own review template (the `adversarial-reviewer` agent / the `dispatching-parallel-agents` review prompt) for the quality gate (Step 2f).
- **Finish** — PR-only branch finish, inline in Step 4 (push + open PR; never merge to main).
""",
    ),
    (
        """    "beads-execution" [shape=box, style=filled, fillcolor=lightgreen];
    "Run /work-breakdown first" [shape=box];

    "Project has beads graph?" -> "Tasks from /work-breakdown?" [label="yes"];
    "Project has beads graph?" -> "Run /work-breakdown first" [label="no"];
    "Tasks from /work-breakdown?" -> "beads-execution" [label="yes"];
    "Tasks from /work-breakdown?" -> "Run /work-breakdown first" [label="no — run it first"];
""",
        """    "beads-execution" [shape=box, style=filled, fillcolor=lightgreen];
    "superpowers:subagent-driven-development" [shape=box];
    "Run /work-breakdown first" [shape=box];

    "Project has beads graph?" -> "Tasks from /work-breakdown?" [label="yes"];
    "Project has beads graph?" -> "Run /work-breakdown first" [label="no"];
    "Tasks from /work-breakdown?" -> "beads-execution" [label="yes"];
    "Tasks from /work-breakdown?" -> "superpowers:subagent-driven-development" [label="no — use plan file"];
""",
    ),
    (
        '"Finish: push + open PR" [shape=box, style=filled, fillcolor=lightgreen];',
        '"superpowers:finishing-a-development-branch" [shape=box, style=filled, fillcolor=lightgreen];',
    ),
    (
        '"Dispatch final code reviewer" -> "Finish: push + open PR";',
        '"Dispatch final code reviewer" -> "superpowers:finishing-a-development-branch";',
    ),
    (
        """When dispatching multiple agents, give each a `name` — they are automatically on the
implicit team and can coordinate via `SendMessage`:

1. Spawn agents with a `name` parameter
2. Agents can use `SendMessage` for FYI coordination (not blocking questions)
3. Monitor agent completion via idle notifications
""",
        """When dispatching multiple agents, set up team coordination:

1. Use `TeamCreate` to create a team for this wave
2. Spawn agents with `team_name` and `name` parameters
3. Agents can use `SendMessage` for FYI coordination (not blocking questions)
4. Monitor agent completion via idle notifications
5. Shutdown the team after the wave completes
""",
    ),
    ("`~/.claude/agents/`", "`~/.Codex/agents/`"),
    (
        """Only after spec compliance passes. Dispatch a code-quality reviewer using the
repo's own review template (the `adversarial-reviewer` agent / the
`dispatching-parallel-agents` review prompt) with:
""",
        """Only after spec compliance passes. Dispatch using the
`superpowers:requesting-code-review` skill template with:
""",
    ),
    (
        """3. Finish the branch — **PR-only** (this repo never merges to main directly):
   - Confirm tests / quality gates pass.
   - `git push` the feature branch.
   - Open a PR with `gh pr create` (if it 403s on a read-only account, print the
     `compare/main...<branch>` URL instead — see the gh-account memory).
   - Do **not** merge to main locally. If not ready to PR: keep the branch, or discard it.
""",
        "3. Use `superpowers:finishing-a-development-branch` to complete the work\n",
    ),
    (
        """**Required workflow setup:**
- **Isolated workspace** — REQUIRED: use the concrete bundled `escapement-worktree create` transaction injected into session context. It verifies repository, source, location, branch, and shared Beads task state together.
- **Code review** — use the repo's own review template (the `adversarial-reviewer` agent / the `dispatching-parallel-agents` review prompt) for the quality gate (Step 2f).
- **Finish** — PR-only branch finish, inline in Step 4 (push + open PR; never merge to main).
""",
        """**Required workflow skills:**
- **superpowers:using-git-worktrees** — REQUIRED: set up isolated workspace
  before starting
- **superpowers:requesting-code-review** — code review template for quality gate
- **superpowers:finishing-a-development-branch** — complete development after all
  tasks
""",
    ),
    (
        "**Supersedes:** this skill is the beads-native execution loop — the role generic per-task subagent dispatch plays in non-beads projects. Beads is the source of truth, and this project always has a beads graph (it auto-installs everywhere), so there is no non-beads fallback path.",
        """**Replaces:**
- **superpowers:subagent-driven-development** — for beads-managed projects, use
  this skill instead. Falls back to subagent-driven-development if project has no
  beads graph.""",
    ),
)


def historical_legacy_skill_bytes(*, crlf: bool = True) -> bytes:
    """Return the exact known legacy deployment, independent of user-local files."""
    text = CURRENT_CLAUDE_SKILL.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    for current, historical in _REPLACEMENTS:
        assert text.count(current) == 1, f"legacy fixture source drifted: {current[:60]}"
        text = text.replace(current, historical)

    lf_bytes = text.encode("utf-8")
    assert hashlib.sha256(lf_bytes).hexdigest() == HISTORICAL_LF_SHA256
    if not crlf:
        return lf_bytes

    crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
    assert hashlib.sha256(crlf_bytes).hexdigest() == HISTORICAL_CRLF_SHA256
    return crlf_bytes


def pre_finish_codex_skill_bytes() -> bytes:
    """Return the canonical Codex skill from immediately before finish guidance."""
    current = CURRENT_CODEX_SKILL.read_text(encoding="utf-8")
    assert current.count(_FINISH_GUIDANCE) == 1, "pre-finish Codex fixture source drifted"
    previous = current.replace(_FINISH_GUIDANCE, "").encode("utf-8")
    assert hashlib.sha256(previous).hexdigest() == PRE_FINISH_CODEX_SKILL_SHA256
    return previous


def previous_codex_skill_bytes() -> bytes:
    """Return the canonical Codex skill before the worktree-create policy change."""
    current = pre_finish_codex_skill_bytes().decode("utf-8")
    new_policy = (
        "4. Use the session-injected `escapement-worktree create` transaction when\n"
        "   isolated implementation work is needed; Beads remains task state only."
    )
    old_policy = (
        "4. Use `bd worktree create` when isolated implementation work is needed."
    )
    assert current.count(new_policy) == 1, "previous Codex fixture source drifted"
    previous = current.replace(new_policy, old_policy).encode("utf-8")
    assert hashlib.sha256(previous).hexdigest() == PREVIOUS_CODEX_SKILL_SHA256
    return previous
