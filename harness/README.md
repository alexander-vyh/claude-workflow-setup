# continuation-harness

Deterministic Stop-gate harness for Claude Code (and, via adapters, other agent CLIs). Targets the stall classes diagnosed in the 14-day session-miner analysis (May 2026): announced-poll-then-waited, narrate-then-stop, phase-complete-then-stop.

Design lives in `../openspec/changes/continuation-harness/`.

## Source vs. runtime state (the install model)

This directory is **source only** — code, schemas, tests. `INSTALL.sh` symlinks `bin/` and `schemas/` into `~/.claude/harness/`, and runtime state lives there too. **Nothing is ever written into a repo working tree**, and the code carries **no hardcoded clone path** (it self-locates and reads state from `~/.claude/harness`, overridable via `CONTINUATION_HARNESS_HOME`). So you can clone this repo to any path, run `INSTALL.sh`, and every Claude session in any repo uses the same decoupled harness.

```
# Source (this repo — symlinked into ~/.claude/harness by INSTALL.sh):
harness/
├── bin/
│   ├── would_block_stop.py     # pure gate fn + thread_dir_for_session + harness_home
│   ├── verify                  # agent affordance — runs verification_command, writes last_run
│   ├── stop_hook.py            # Claude Code Stop-hook adapter (self-locating)
│   ├── wakeup_waker.py         # scheduled.json firing layer; dry-run by default
│   ├── wakeup_dispatch.py      # pure check/resume dispatch logic
│   ├── init_contract.py        # scaffold a contract
│   └── baseline.py             # baseline metrics scanner
├── schemas/{contract,scheduled}.schema.json
└── tests/test_gate.py          # gate + session-isolation + portability tests

# Runtime state (~/.claude/harness — real dirs, NOT this repo, NOT symlinked):
~/.claude/harness/
├── bin -> <repo>/harness/bin           # symlink (code)
├── schemas -> <repo>/harness/schemas   # symlink (code)
├── threads/{session_id}/               # per-session contract.json + scheduled.json
└── incidents.jsonl                     # append-only gate-decision log
```

## How it works (v0)

1. Agent declares its outcome via `contract.json` (or invokes `bin/init_contract.py` to scaffold).
2. When the agent considers the task done, it runs `bin/verify` — this executes `verification_command`, captures the exit code, writes `last_run` back to `contract.json`.
3. At Stop, the Claude Code adapter (`bin/stop_hook.py`) calls `would_block_stop(thread_state)`:
   - **allow** if `last_run.exit_code` matches `expected_exit` within the current turn
   - **allow** if `scheduled.json` has a future-dated wakeup entry
   - **allow** if the user typed an explicit-stop word
   - **block** otherwise
4. Block decisions return a constructive resumption prompt; the agent receives it and continues.

`bin/wakeup_waker.py` is the firing layer for `scheduled.json`. By default it is a
dry-run and must not execute check commands, rewrite schedules, or spawn Claude.
With `--fire`, it claims each schedule file, polls due `kind="check"` entries,
re-arms not-ready checks, and prunes only wakeups whose handoff/resume was spawned
successfully. Long waits should use `kind="check"` so polling stays in cheap shell
work and the eventual wake is a fresh cheap-model handoff, not a large-context
`--resume`.

Coexists with `~/.claude/hooks/validate_no_shirking.py` — both fire on Stop, both can block. Additive coverage.

## Session isolation (v0.1 — 2026-05-20)

Thread directories are keyed by session id: `harness/threads/{CLAUDE_CODE_SESSION_ID}/`. Every Claude Code session — including each named subagent, which is its own session — gets an isolated thread dir, so concurrent agents working the same repo never clobber each other's `contract.json`. Resolution is identical across the three touch points (`would_block_stop.thread_dir_for_session()`, the `verify` shell script, and `init_contract.py`):

1. `HARNESS_THREAD_DIR` env override (tests / special cases)
2. sanitized `CLAUDE_CODE_SESSION_ID` → `threads/{session_id}` (in-session tools) / `payload.session_id` (Stop hook) — these are the same value
3. fallback → `threads/current` (single-session / non-Claude contexts)

`threads/current/` remains as the fallback bucket only; it is no longer the default for live Claude Code sessions.

### Working-tree isolation — detect & steer (Move 3, bead e9v.4 — 2026-06-18)

Thread *state* is per-session (above), but `verify` runs the contract command
against the **shared working tree**. When two live sessions share one
non-isolated checkout, session B's verify picks up session A's in-flight
breakage — so B's red is actually A's. The 2026-06-17 root-cause (UDE-7 /
BLOCK-5) concluded the fix is **isolation, not result-state gating**: a session's
finishing boundary must reflect only its own work, and the repo already mandates
the session-injected `escapement-worktree create` transaction (CLAUDE.md).

`session_isolation.py` implements *detect → steer* (state-only, agent-asserts
nothing):

- Each session stamps `{thread_dir}/checkout.json` =
  `{session_id, worktree_root, git_common_dir, is_linked_worktree, heartbeat}`.
  Written at SessionStart (`session_watermark.py`) and refreshed each Stop
  (`stop_hook.py`). Outside a git repo nothing is written (no checkout concept).
- **Collision** = ≥2 *live* records (heartbeat within `LIVENESS_WINDOW_SECONDS`,
  30 min) whose `worktree_root` is the same on-disk tree, distinct session ids.
  It keys on `worktree_root`, NOT `git_common_dir` — two sessions in separate
  linked worktrees of one repo share the common dir but are **isolated**, so they
  do not collide (that is the success state the steer points toward).
- On collision the harness surfaces the worktree escape path: proactively at
  SessionStart, and appended to the `no_completion_or_resumption_proof` Stop
  block (the BLOCK-5 red-boundary case). A solo session is never nagged.

The steer is advisory — like the other Stop-block messages, the mechanical
guard is the *detection* (unit-tested with positive/negative/staleness controls
in `tests/test_session_isolation.py`); the de-collision itself happens when the
agent runs the concrete bundled command emitted by SessionStart. The static
reference template is:

```bash
python3 -B <injected-bundled-cli-path> create \
  --repo <colliding-checkout> \
  --name <task> \
  --branch <branch>
```

Ultimate proof is observational (does the cross-session red-boundary deadlock
stop recurring), named not hidden.

**Known limits (named, not hidden — tracked as follow-up beads):**

- *Idle-peer staleness.* Heartbeats stamp only at SessionStart and each Stop, so
  a peer parked idle >`LIVENESS_WINDOW_SECONDS` (30 min) ages out and stops being
  detected. The *actively-editing* peer — the one whose WIP actually reddens the
  tree — ends turns frequently and stays fresh; the gap is a long-idle peer.
  Closing it fully needs a heartbeat refresh on tool-use (a frequently-firing
  hook), deferred to bead `escapement-e9v.9`.
- *Unresolved session id.* When `CLAUDE_CODE_SESSION_ID` is absent, two sessions
  both fall back to `threads/current/` and `colliding_sessions` skips
  falsy-id records — detection goes blind exactly when id resolution fails. This
  is inherited `thread_dir_for_session` fragility; robustifying it is bead
  `escapement-e9v.10`.

## Still v0.1+

Full 57-stall regression test, launchd installation for `wakeup_waker.py`, bead-derived contracts, human-readable `(team_id, agent_name)` naming layered on top of session-id keying, and the supervisor daemon.
