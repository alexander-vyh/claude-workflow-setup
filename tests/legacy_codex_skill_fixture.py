"""Deterministically reconstruct the legacy Codex beads skill regression fixture."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT_CLAUDE_SKILL = ROOT / "claude" / "skills" / "beads-execution" / "SKILL.md"
HISTORICAL_CRLF_SHA256 = (
    "2096820ff0d7a712aa4b58ca2590979f80f2d0fd168a23bda788a024f47792e0"
)
HISTORICAL_LF_SHA256 = (
    "c65855a32ece63079c692332a968174748187b9fb8e1a57bf3803dcc76beb402"
)

_REPLACEMENTS = (
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
- **Isolated workspace** — REQUIRED: create the worktree with `bd worktree create <path> -b <branch>`. This is a beads project; never use `git worktree add` (it leaves a broken `.beads/` skeleton — see `beads-worktree-integration.md`).
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
