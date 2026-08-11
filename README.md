# Escapement

<!-- escapement:core-identity:start -->
> Escapement converts available agent capacity plus delegated authority into verified, delivered outcomes while reserving human attention for consequential choices.

Escapement is a client-neutral workflow and control layer for agentic delivery. It keeps a bounded outcome moving from intent through design, execution, verification, and landing. The tools used at each stage are adapters: useful today, replaceable tomorrow.

> This repository is a snapshot of a working system, not a universal product. Its opinions are strong; its claims are limited to behavior that the current adapters can prove.

## Why "Escapement"?

An escapement turns available energy into controlled motion. It gives the mechanism enough impulse to continue, meters progress, and prevents the train from running free.

The software analogy is deliberate. Agent capacity is the available energy; delegated authority defines where it may act; design and independent verification regulate the motion; landing turns progress into a delivered result. Escapement is not a lock or a judge. It is the control mechanism that keeps authorized work moving toward its intended outcome.

See [docs/NAMING.md](docs/NAMING.md) for the full naming rationale.
<!-- escapement:core-identity:end -->

## Durable capability chain

These capabilities define Escapement independently of any current tool or client:

1. **Intent and authority**
2. **Design and specification**
3. **Executable dependency-aware work breakdown**
4. **Capacity allocation**
5. **Isolated execution**
6. **Action-local continuation and repair**
7. **Independent outcome verification**
8. **Authorized landing and delivery**
9. **Learning and feedback**

Design and work breakdown are not ceremony around the “real” work. They make intent, constraints, dependencies, and the independent oracle explicit enough that capacity can be allocated safely and parallel work can continue without repeatedly returning product decisions to the user.

## Operating model

Escapement uses mission command and leverage to define its purpose: human-chosen intent directs available agent capacity within delegated authority. Its operating loop is closed-loop control:

```text
intent and authority
  → design and specification
  → executable work graph
  → capacity allocation
  → isolated execution
  → continuation and repair
  → independent verification
  → authorized landing
  → learning
```

Flow and constraint measures show where delivery stalls. Enabling-bureaucracy principles determine whether a rule or gate supplies repair, transparency, and flexibility—or merely consumes attention.

## Delegated outcomes include their ordinary means

Delegating a bounded build, fix, change, execution, delivery, or shipping outcome delegates the routine, proportionate actions needed to achieve and verify it within the named repository, systems, and constraints. That normally includes:

- creating the established isolated worktree;
- scoped inspection and editing;
- running tests, lint, builds, and other verification;
- committing and pushing the declared task branch;
- creating or updating its pull request;
- repairing causally necessary CI or review failures; and
- following the repository-declared merge, deployment, and outcome-verification path.

Those are ordinary delivery means, not fresh product decisions. A client may still present a mechanical approval prompt when its sandbox or hook model cannot express the existing authority; that is an adapter limitation, not a request for new intent.

Authority remains bounded. Escapement requests human attention when progress requires changed intent or non-goals, a material trade-off between valid outcomes, an undelegated repository/account/audience, new privilege or credentials, destructive or irreversible shared effects, an actually enforced confirmation class, unsafe overlap with another owner, or a missing standard landing path.

An unresolved consequential choice blocks only the dependent action. Independent authorized work continues. A session is genuinely `input_required` only when no authorized route toward the delegated outcome remains runnable.

## Current adapters

The current implementation maps replaceable tools to the durable capabilities:

<!-- escapement:adapter-mapping:start -->
| Durable capability | Current adapter |
|---|---|
| Design and specification | OpenSpec |
| Executable dependency-aware work breakdown | Beads |
| Isolated execution | Git worktrees |
| Capacity allocation | Claude Code, Codex |
| Authorized landing and delivery | GitHub |
<!-- escapement:adapter-mapping:end -->

Independent verification currently uses test-oracle briefs, behavioral checks,
mutation challenge, and live outcome checks. Continuation and learning use durable
work state, supported lifecycle hooks, wakeups where available, and retrospective
signals.

The current mapping uses OpenSpec artifacts and change contracts; Beads for task
state only; Escapement-created Git worktrees; Claude Code and Codex host adapters;
and GitHub plus repository policy for landing.

The supported isolated-creation transaction is `escapement-worktree create`;
session context supplies its concrete bundled path and repository-scoped
arguments. Direct Git or Beads worktree creation is not an equivalent adapter.

Replacing an adapter must not require changing the mission or capability chain. A formal adapter framework should be added only when a real replacement creates a concrete interface to generalize.

## Supported hosts and truthful limits

Host-specific surfaces are rendered from authored sources under `agent-surfaces/` by `tools/render_agent_surfaces.py`. Generated `AGENTS.md`, `CLAUDE.md`, plugin metadata, and compatibility surfaces must not be edited directly.

| Surface | Claude Code | Codex |
|---|---|---|
| Instructions | `CLAUDE.md` | `AGENTS.md` |
| Installed hooks | Escapement Claude plugin | Escapement Codex plugin |
| Skills | Claude plugin skill tree | `.agents/skills/` and Codex plugin skills |
| Package metadata | Claude plugin manifest | Codex plugin manifest |

<!-- escapement:support-claims:start
merge-green-status=unsupported
merge-green-status-reason=The merge authorization hook resolves repository-declared merge authority but does not observe pull-request check or green status.
confirm-class-enforcement=reserved
confirm-class-enforcement-reason=Repository confirmation classes are stored but are not currently enforced by the merge authorization hook.
deploy-execution=informational
deploy-execution-reason=Repository deploy metadata is surfaced as outcome context and does not execute or independently authorize a deployment command.
codex-final-response-interception=guidance-only
codex-final-response-interception-reason=The installed Codex adapter exposes no Stop or final-response hook; durable work state and SessionStart guidance support continuation without native interception.
-->
<!-- escapement:support-claims:end -->

Capabilities are enabled only where the adapter and a fixture prove the current payload and point-of-effect behavior. Current limitations are explicit:

- merge authorization resolves repository policy; it does not itself observe whether a pull request is green;
- `confirm_class` is reserved configuration and is not currently enforced by the merge gate;
- deployment metadata is informational to the outcome resolver and does not itself execute or authorize a deployment command; and
- Codex exposes supported startup and tool-use lifecycle events, but no Stop/final-response hook. Its final-response continuation discipline is guidance-only and relies on explicit durable work state.

Support in one host is never inferred from another host's lifecycle model.

## Install current adapters

### Prerequisites

Install the tools used by the capabilities you intend to adopt. The current complete workflow expects `openspec`, `bd`, `git`, `python3`, and `jq`; `direnv` and Serena are optional integrations.

### Codex

Install the Escapement plugin from its marketplace:

```bash
codex plugin marketplace add https://github.com/alexander-vyh/escapement
codex plugin add escapement@escapement
```

For an existing checkout, update it and refresh the effective plugin source:

```bash
git pull --ff-only
./scripts/codex-plugin-update.sh
```

The updater refuses to overwrite unrecognized user-authored skill content and verifies the effective installed source before reporting success.

### Claude Code

Install the native plugin from inside Claude Code:

```text
/plugin marketplace add alexander-vyh/escapement
/plugin install escapement@escapement
```

Then use the authoritative updater from a checkout:

```bash
mkdir -p "$HOME/src"
git clone https://github.com/alexander-vyh/escapement "$HOME/src/escapement"
cd "$HOME/src/escapement"
./scripts/plugin-update.sh
```

The native plugin owns workflow hooks, skills, agents, commands, rules, bootstrap, and harness code. Restart Claude Code after an upgrade because an already-running process may retain an older versioned plugin root.

`INSTALL.sh` is an optional compatibility installer only for assets the native plugin cannot install, including Beads formulas and selected stable auxiliary wrappers. It is not the primary workflow installer.

## What the current workflow looks like

For non-trivial work, the present adapters commonly follow this path:

```text
bounded outcome
  → adversarial discovery and OpenSpec change
  → dependency-aware Beads graph
  → isolated task worktree
  → independent test-oracle review and mutation challenge
  → walking skeleton, then remaining implementation
  → outcome verification
  → repository-declared landing and delivery
  → retrospective learning
```

The molecule formulas encode reusable work graphs. Skills explain how to perform work; rules state policy; hooks enforce only the lifecycle events their host actually exposes. Each layer can evolve independently as long as the capability contract and user-visible outcome remain intact.

## Repository anatomy

| Area | Purpose |
|---|---|
| `agent-surfaces/` | Authored identity, onboarding, host capability evidence, and render manifest |
| `openspec/changes/` | Current design, requirements, decisions, and implementation artifacts |
| `beads/formulas/` | Reusable task-graph templates |
| `claude/skills/`, `.agents/skills/` | Current workflow procedures exposed to supported clients |
| `claude/rules/` | Escapement policy and operating doctrine |
| `claude/hooks/`, `harness/` | Fixture-backed enforcement and continuation machinery |
| `tools/render_agent_surfaces.py` | Generated-surface renderer and consistency validator |

See [docs/VOCABULARY.md](docs/VOCABULARY.md) for the client-neutral mechanics behind these terms.

## Warnings

### Gates must enable the work

A gate is useful only when the failure is repeated or severe, the oracle is replayable, valid work can pass, and the denial explains repair. Stored configuration and prose are not enforcement evidence.

### Hooks are capability-specific

Skills and rules can guide behavior; hooks can mechanically constrain only events that the current client exposes. Install and evaluate each adapter as a capability bundle rather than assuming symmetry.

### Bootstrap scope

The bootstrap script runs in any Git repository by default. Set `ESCAPEMENT_BOOTSTRAP_ROOTS` to a colon-separated allowlist when machine-wide bootstrap should be constrained:

```bash
ESCAPEMENT_BOOTSTRAP_ROOTS="$HOME/src:$HOME/work"
```

### Personal state is not bundled

Authentication, permissions, per-project memory, and Beads databases remain user- or repository-owned state. The plugin must not replace unrelated personal configuration.

## Credits

The current adapters build on [OpenSpec](https://github.com/Fission-AI/OpenSpec), [Beads](https://github.com/steveyegge/beads), Git, GitHub, and optional [Serena](https://github.com/oraios/serena). The operating doctrine draws from mission command, Grove's leverage, closed-loop control, Lean flow, constraint management, enabling bureaucracy, walking-skeleton development, and independent test-oracle practice.

## License

Escapement is licensed under the **GNU General Public License v3.0 or later** (`GPL-3.0-or-later`). See [`LICENSE`](LICENSE).
