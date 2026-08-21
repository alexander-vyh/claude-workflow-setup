# System-wide Codex Hook Fan-out Repair Design

## Outcome

An ordinary Codex Bash tool call starts one Escapement-owned PreToolUse process,
continues to enforce every ready gate, and does not surface 5-second or 10-second
hook failures under normal concurrent use. A plugin refresh removes only
recognized legacy Escapement registrations from the machine-wide Codex hook
file and preserves unrelated user and repository policy hooks.

## Current failure

The installed plugin renders every ready Bash gate as a separate command hook.
The live global `~/.codex/hooks.json` also retains older Escapement commands.
One Bash tool call therefore starts 21 processes on this machine. Parallel Codex
sessions multiply that fan-out, and host deadlines expire even though each gate
finishes quickly when executed alone.

The updater's "sole owner" check covers the repository compatibility surface and
the plugin inventory, but not the global Codex hook file. That is the oracle gap
which allowed the duplicate installation to survive.

## Considered approaches

1. **Increase timeouts.** Rejected: it leaves the process storm intact and only
   delays its visible failure.
2. **Delete all global Codex hooks.** Rejected: the file contains unrelated Sifi
   and PR-policy hooks owned outside Escapement.
3. **One in-process dispatcher plus provenance-aware migration.** Selected: it
   reduces ordinary Escapement Bash fan-out to one process while keeping the
   existing gate implementations and manifest as the policy authority.

## Architecture

`agent-surfaces/manifest.json` remains the canonical inventory. The renderer
collects ready Codex `PreToolUse`/`Bash` events into one command for
`codex_pretool_dispatch.py`, passing each gate's plugin-relative path as a
`--gate` argument. Non-Bash matchers remain independent because they do not
contribute to ordinary shell-command fan-out.

The dispatcher reads the hook payload once, executes the named Python gate
scripts in the same interpreter, and combines their public hook results.
`deny` outranks `ask`, which outranks `allow`; distinct reasons, advisory
contexts, and system messages are retained in manifest order. A broken child
gate becomes an explicit system warning while later gates still run, preserving
the current fail-open-on-hook-error behavior without producing a host-level
process storm.

The Codex updater invokes a new conservative pruner against
`~/.codex/hooks.json`. It recognizes only gate paths rooted directly beneath the
current user's legacy `~/.codex/hooks` or `~/.claude/hooks` directories and only
when the dispatcher declares the same basename. It backs up the original file,
preserves non-hook keys, group metadata, ordering, and unrelated hook objects,
and is idempotent.

## Test Oracle Brief

1. **Business invariant:** unrelated Codex work must not be interrupted by
   Escapement hook timeouts, and all intended gates must still affect the public
   PreToolUse decision.
2. **Independent source of truth:** the effective installed plugin and global
   hook file, plus concurrent execution of the installed dispatcher over real
   hook payloads.
3. **Solution constraints:** the neutral manifest remains authoritative; Claude
   behavior is unchanged; user hooks are preserved; no private Codex registry
   mutation; repository-generated surfaces remain deterministic; Python 3 and
   the existing plugin installer are retained.
4. **Invalid solution classes:** raising deadlines; deleting all global hooks;
   dropping gates; spawning each gate as a subprocess behind one wrapper;
   swallowing deny/ask/advisory outputs; matching unrelated hooks by basename
   alone.
5. **Fragile implementation to reject:** one wrapper command that still launches
   fifteen child Python processes.
6. **Negative control:** a deny gate combined with an allow/advisory gate must
   produce deny; a same-named script outside the recognized legacy directories
   must survive pruning.
7. **Positive control:** multiple advisory contexts survive aggregation, and the
   unrelated Sifi and PR-policy hooks remain after migration.
8. **Missing/unresolved handling:** malformed dispatcher configuration fails
   visibly; an individual gate exception is reported as a system warning while
   remaining gates continue, matching current host behavior.
9. **Final outcome verification:** run
   `python3 scripts/verify_codex_hook_runtime.py --codex-home /Users/alexandervyhmeister/.codex --require-installed`,
   then execute a fresh Codex Bash command and inspect fresh runtime telemetry for
   one Escapement PreToolUse start and no hook failure.

## Delivery

Land through a feature branch and pull request, merge, refresh the Codex plugin
from merged `main`, run the installed-runtime verifier, and exercise a fresh
Codex session. The legacy global file is changed only by the merged updater and
is backed up before the first migration.
