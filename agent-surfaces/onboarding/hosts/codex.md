# Codex Adapter Notes

Codex reads repository instructions from `AGENTS.md` and repo skills from
`.agents/skills`. The installed Escapement plugin is the sole owner of Codex hook
registration; the generated project `.codex/hooks.json` remains empty to prevent
the same lifecycle event from executing twice.

Codex hooks in this repo must use repository-relative commands and must not call
through user-local Claude paths. A Codex hook is marked blocking only when a
fixture proves the current Codex payload shape exercises the intended behavior.
Unsupported Claude-only behavior stays explicit rather than being copied into a
Codex surface as prose.

Informational or diagnostic questions about Beads are bounded read-only work.
Answer them directly with only the inspection needed for the question. Do not
invoke `beads-execution` merely because a prompt mentions Beads, `bd`, a bead, or
a task ID. Invoke that skill only when the user explicitly asks to execute, work
on, run, or start a tracked task. An explicit instruction not to execute always
wins, even when a task ID is present.
