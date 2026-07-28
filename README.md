# Escapement

Escapement is an agentic workflow system built **on top of [OpenSpec](https://github.com/Fission-AI/OpenSpec)**, adding:

- **Adversarial overlays** on OpenSpec's discovery step (riskiest-assumption, pre-mortem, red/blue team, walking-skeleton sections injected as internal prompts — not visible in the output docs).
- **A bridge to [Beads](https://github.com/steveyegge/beads)** so OpenSpec's `tasks.md` becomes a real task graph (`bd create --spec-id ...`) that agents can execute against.
- **A molecule formula** (`mol-feature`) that orchestrates the full brainstorm → discovery → skeleton → build → retro lifecycle with two human gates.
- **Hooks, rules, and skills** that enforce the workflow at the tool-call level: TDD-first, named-agent teams, outcome-verification, OpenSpec init checks, spec-ID linkage, never-suppress discipline, Serena-first navigation. **Claude Code and Codex are supported adapters** over the same workflow core (see [Hosts](#supported-hosts)).

**This does not replace OpenSpec — it uses it.** The `/discovery` skill calls `openspec init`, `openspec instructions`, and `openspec status` under the hood. The `proposal.md` / `design.md` / `specs/*.md` files written are standard OpenSpec artifacts. Engineers comfortable with bare OpenSpec can read the changes a teammate produced through this workflow with no extra context.

> ⚠️ This is a snapshot of one working setup, not a product. Read, adapt, cherry-pick. The opinions are strong.

## Why "Escapement"?

`Escapement` is named for the clock mechanism that turns stored energy into measured motion. It restrains runaway movement, gives the oscillator enough impulse to continue, and advances the train one tick at a time. This repo applies that model to agentic work: OpenSpec, Beads, hooks, test oracles, verification, and wakeups convert model effort into controlled, outcome-verified progress.

See [docs/NAMING.md](docs/NAMING.md) for the naming rationale.

---

## What you get

### The core pattern

```
you say "build X"
        │
        ▼
┌───────────────────────────┐
│ mol-feature (beads)       │  root epic + 9 step tasks (2 are gates)
└──────────┬────────────────┘
           │ step: discovery
           ▼
┌───────────────────────────┐
│ /discovery skill          │  adversarial overlay on OpenSpec CLI
│   └─ writes ──────────────┼──▶ openspec/changes/{name}/
│                           │     ├─ proposal.md   ← standard OpenSpec
│                           │     ├─ design.md     ← + adversarial sections
│                           │     ├─ specs/*.md    ← requirement IDs
│                           │     └─ tasks.md
└──────────┬────────────────┘
           │ step: work-breakdown
           ▼
┌───────────────────────────┐
│ /work-breakdown skill     │  reads openspec, writes beads
│   └─ creates ─────────────┼──▶ bd spec issues (--acceptance fields)
│                           │    bd task issues (--spec-id links)
└──────────┬────────────────┘
           │ step: execute-skeleton → review → execute-full
           ▼
       parallel named agents implement tasks,
       reading --acceptance via --spec-id
```

### Layers (adopt one, some, or all)

The paths below are the source and Claude Code adapter paths. The [host mapping](#supported-hosts) shows the equivalent Codex surfaces.

| Layer | Files | What it buys you |
|------|------|------------------|
| **1. Formulas** | `beads/formulas/*.json` | 10-step workflow definition for `bd mol pour` |
| **2. Skills** | `claude/skills/*/` | The "how" for each step — discovery, work-breakdown, execution, oracle review |
| **3. Commands** | `claude/commands/*.md` | Slash-command shims (`/discovery`, `/work-breakdown`, `/brainstorm`, `/review`) |
| **4. Rules** | `claude/rules/*.md` | Global discipline — TDD, agent teams, outcome ownership, never-suppress, Serena-first |
| **5. Hooks** | `claude/hooks/*.py` | Tool-call-level enforcement (see below) |
| **6. Bootstrap** | `scripts/project-bootstrap.sh` | SessionStart hook auto-inits openspec/beads/serena per repo |

Each layer adds value without requiring the ones above. You can install just the skills, or skills + formulas without hooks. Start small.

---

## Supported hosts

Escapement is **host-neutral at the core** — beads, OpenSpec, test-oracle discipline, and outcome verification don't depend on which agent runs them. The host-specific surfaces are *generated* from one neutral manifest (`agent-surfaces/manifest.json`) by `tools/render_agent_surfaces.py`, and a `--check` mode fails CI if they drift, so the two hosts stay in sync:

| Surface | Claude Code | Codex |
|---------|-------------|-------|
| Instructions | `CLAUDE.md` | `AGENTS.md` *(generated)* |
| Project hooks | Escapement Claude plugin | Escapement Codex plugin |
| Skills | `claude/skills/` | `.agents/skills/` |
| Plugin packaging | `.claude-plugin/plugin.json` | `plugins/escapement/.codex-plugin/` *(generated)* |

The portable core runs on both: Escapement-owned task-state and landing-policy
context and the verified test-oracle and outcome gates are packaged in each
host's plugin and fixture-tested against that host's payload shape. For Codex,
the plugin is the sole hook owner; the generated repo `.codex/hooks.json` is
intentionally empty so the same hook cannot fire once from the checkout and
again from the installed plugin. The write/edit TDD gate remains Claude
Code-specific because its Serena tool matching is not portable to Codex.

The adapters expose different native capabilities. Per the `agent-surface-parity` spec, Claude Code-specific features — multi-agent `TeamCreate` teams and `ScheduleWakeup` continuation — are *excluded* from Codex surfaces rather than faked: a Codex hook is marked blocking only when a fixture proves it works against the current Codex payload. New shared behavior is added to the manifest first, then rendered to whichever host surface can actually enforce it.

---

## Prerequisites

| Tool | Install | Verify |
|------|---------|--------|
| `openspec` | `brew install openspec` (or `npm i -g @fission-ai/openspec`) | `openspec --version` |
| `bd` (beads) | [github.com/steveyegge/beads](https://github.com/steveyegge/beads) | `bd --version` |
| `direnv` | `brew install direnv` | `direnv version` |
| `python3` | usually present | `python3 --version` (3.9+) |
| `jq` | `brew install jq` | `jq --version` |
| `git`, `bash` | usually present | — |

Install either supported host: Claude Code or Codex. The Claude steps below
configure Claude Code machine-wide. Codex uses the Escapement plugin for hooks
and packaged skills while still reading a checkout's generated `AGENTS.md`.
[Serena MCP](https://github.com/oraios/serena) is optional but the navigation
hooks are silent unless `.serena/memories` exists in a project.

## Install the Codex adapter

```bash
codex plugin marketplace add https://github.com/alexander-vyh/escapement
codex plugin add escapement@escapement
```

For later upgrades, update the checkout and run the authoritative updater:

```bash
git pull --ff-only
./scripts/codex-plugin-update.sh
```

The updater refreshes the plugin, then backs up and replaces only exact known
Escapement legacy versions of
`~/.agents/skills/beads-execution/SKILL.md`. It refuses to overwrite an
unrecognized or customized user-authored skill, and verifies the effective
installed source before reporting success.

---

## Install the Claude Code adapter

First install the native plugin from inside Claude Code:

```text
/plugin marketplace add alexander-vyh/escapement
/plugin install escapement@escapement
```

Then clone the repository and run the authoritative updater:

```bash
mkdir -p "$HOME/src"
git clone https://github.com/alexander-vyh/escapement "$HOME/src/escapement"
cd "$HOME/src/escapement"
./scripts/plugin-update.sh
```

The plugin is the sole owner of Claude workflow surfaces: hooks, skills,
agents, commands, rules, bootstrap, and harness code. The updater refreshes the
versioned plugin cache, preserves the configured model and enabled state,
removes recognized legacy Escapement symlinks, prunes duplicate settings hooks,
and maintains the stable `~/.claude/harness/{bin,schemas}` wrappers. It refuses
to replace unknown user-owned files or unrelated symlinks.

For later upgrades, land or pull the desired revision and rerun:

```bash
git pull --ff-only
./scripts/plugin-update.sh
```

Restart Claude Code after an upgrade. Already-running processes may retain the
old versioned plugin root.

### Optional compatibility auxiliaries

```bash
./INSTALL.sh
```

`INSTALL.sh` remains only for assets the native plugin cannot install:
`~/.claude/bin`, Beads formulas, and `~/.beads/mol-status.sh`. Those auxiliaries
point into a branch-safe pinned checkout by default. The script delegates
workflow convergence to `scripts/plugin-update.sh` first, so a later
`INSTALL.sh --update` cannot restore legacy pin-owned hooks, skills, commands,
agents, bootstrap, or harness code. `--dev` changes only the auxiliary links.

---

## First run with Claude Code

After installing:

1. Open Claude Code in a fresh git repo anywhere on disk.
2. On SessionStart, the bootstrap script runs (idempotent, fail-open):
   - `direnv allow` on any `.envrc`
   - `openspec init --tools claude` if `openspec/` is missing
   - `bd init --prefix <repo-name>` if `.beads/` is missing
   - Prompts for Serena onboarding (interactive)
3. Say: *"let's build a small feature to validate the setup — add a /status slash command"*
4. Claude should respond by pouring `mol-feature` and walking you through: brainstorm → discovery → review → breakdown → skeleton → build → retro.

If that flow happens end-to-end, everything is wired correctly.

---

## Anatomy

### The molecule formula (`beads/formulas/mol-feature.formula.json`)

Nine steps, two human gates, four phases (Design / Validate / Build / Learn):

```
brainstorm ──▶ discovery ──▶ [GATE: review-discovery] ──▶ work-breakdown
                                                                │
                                                                ▼
                              execute-skeleton ──▶ [GATE: review-skeleton]
                                                                │
                                                                ▼
                              execute-full ──▶ ceremony-retro ──▶ outcome-check
```

Each step's `description` tells Claude what to do — often "run /discovery" or "dispatch named agents via TeamCreate". The formula is the script; the skills are the subroutines.

### The skills

```
build                              ← front-door router; pours the right molecule
  └─ brainstorming                 ← "should we build this at all?" pre-filter
       └─ discovery                ← adversarial wrapper on openspec CLI
            └─ work-breakdown      ← openspec → beads spec+task issues
                 └─ beads-execution  ← dispatches agents for bd ready
                      ├─ behavioral-test-oracle-review  ← brief before each code change
                      ├─ dispatching-parallel-agents
                      └─ subagent-driven-development
```

### The hooks (what the rules can't enforce alone)

Grouped by what they protect:

**OpenSpec workflow**
- `openspec_init_guard.py` — blocks `openspec` commands when `openspec/` doesn't exist
- `design_doc_location_guard.py` — warns when design docs go to `docs/plans/` instead of `openspec/changes/`
- `discovery-gate.py` — blocks `bd create` of features/epics without a design doc
- `discovery-nudge.py` — nudges `/discovery` when a prompt looks like feature work
- `discovery-close-gate.py` — on `bd close`, surfaces proof-of-delivery + anti-metrics
- `spec_id_enforcement.py` — blocks `bd create --type task` under `mol-feature` without `--spec-id`
- `mol_status_check.py` — SessionStart, surfaces active molecules

**TDD + test oracle discipline**
- `tdd-gate.py` — requires test file modification before implementation
- `test_oracle_brief_gate.py` — requires `.agent/runtime/test-oracle-brief.md` for behavioral code changes
- `test_reminder.py` — PostToolUse nudge to run tests after edits
- `implementation_echo_test_gate.py` — rejects tests that echo the implementation
- `oracle_downgrade_warning_gate.py` — warns on test-oracle weakening in diffs
- `outcome_assertion_gate.py` — on `gh pr create`, blocks tests with only structural assertions

**Outcome verification + shirking**
- `validate_no_shirking.py` — blocks "pre-existing failure" evasion at commit/PR/Stop
- `review_gate.py` — soft gate on `bd close` if no review agent was dispatched
- `review_nudge.py` — UserPromptSubmit, nudges `/review` on review-intent prompts

**Agent discipline**
- `enforce_named_agents.py` — blocks anonymous agents and multi-agent dispatch without `TeamCreate`
- `context_burn_detector.py` — nudges agent dispatch after excessive inline research
- `session_cleanup.py` — SessionStart, cleans /tmp state from the above

**Serena navigation discipline** (silent unless `.serena/memories` exists)
- `serena_preference_gate.py` — blocks full-file Read on code when Serena is available
- `serena_preference_injection.py` — UserPromptSubmit, steers toward Serena symbol tools
- `serena_onboarding_check.sh` — SessionStart, nudges Serena onboarding when missing

Each hook's docstring at the top of the file is authoritative — read it before editing.

---

## ⚠️ Warnings

### This is opinionated

The `rules/` directory encodes strong opinions:

- **`planning-discipline.md`** — `mol-feature` on any non-trivial work
- **`tdd-enforcement.md`** — failing test FIRST in any test-capable repo
- **`agent-teams-default.md`** — `TeamCreate` + named agents for anything multi-step
- **`outcome-ownership.md`** — done = verified end-to-end, not "my change compiles"
- **`molecule-awareness.md`** — surface active molecules on every session start
- **`beads-worktree-integration.md`** — `bd worktree create` instead of `git worktree add`
- **`never-suppress.md`** — no `# noqa`, no `--no-verify`, no test downgrades — fix the underlying issue
- **`serena-first.md`** — symbol tools over full-file Read when Serena is onboarded

Read the rules BEFORE installing. Edit to match your philosophy. These are not universally applicable — the TDD and never-suppress rules in particular will feel restrictive if your project has no test infrastructure or relies on legacy suppression patterns.

### Hooks are load-bearing

Skills produce guidance. Hooks produce enforcement. If you install skills without hooks, Claude can and will drift from the workflow. If you install hooks without skills, the hooks will block things without offering a path forward.

Install both, or neither.

### Bootstrap scope

The bootstrap script runs in any git repo by default. To constrain machine-wide bootstrap to known roots, set `ESCAPEMENT_BOOTSTRAP_ROOTS` to a colon-separated allowlist, for example:

```bash
ESCAPEMENT_BOOTSTRAP_ROOTS="$HOME/src:$HOME/work"
```

Some maintenance utilities still default to scanning `~/GitHub/` unless their own root variables are set, such as `BEADS_BACKUP_ROOTS` for beads backup discovery.

### Not shared

Deliberately excluded from this bundle:
- `~/.claude/settings.json` (personal auth + permissions) — only the `hooks` block is templated
- `~/.claude/projects/*/memory/` (per-project memory)
- General-utility hooks unrelated to the workflow (statusline, transcript backup, IDE wrappers, context injection)
- `~/.beads/` databases

---

## Uninstall

```bash
./INSTALL.sh --uninstall
```

Removes all symlinks. Your `.backup-<timestamp>` files are left alone — rename them back manually to restore your previous config.

---

## Credits

The substance comes from elsewhere; this repo is the glue.

- **[OpenSpec](https://github.com/Fission-AI/OpenSpec)** — the structured change-management framework this workflow runs on top of. The discovery skill is an opinionated wrapper, not a replacement.
- **[Beads](https://github.com/steveyegge/beads)** — graph-based task tracker designed for AI agents, with molecule formulas as a workflow templating layer.
- **[Serena](https://github.com/oraios/serena)** — LSP-backed semantic code navigation; the Serena rule + hooks make it the default during the build phase.
- **Walking-skeleton thinking** (Steven Gong et al.) — riskiest assumption first, 1–3 tasks, 30–60 min each.
- **Manager Tools, Radical Candor, Playing to Win, Lencioni, Grove, Brené Brown** — the rules' flavor on outcome ownership, feedback, and psychological safety.

The glue — molecule formulas, skill overlays, hook enforcement, settings template — is my own iteration, not a product. Expect to modify before adopting.

## License

Escapement is licensed under the **GNU General Public License v3.0 or later**
(`GPL-3.0-or-later`). See [`LICENSE`](LICENSE) for the full text. You may
redistribute and/or modify it under the terms of the GPL as published by the
Free Software Foundation, either version 3 of the License, or (at your option)
any later version.
