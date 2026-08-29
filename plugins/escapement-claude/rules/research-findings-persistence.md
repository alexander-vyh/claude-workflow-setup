# Durable Artifacts — Persist Before You Point (Global Rule)

Applies to **any** multi-agent dispatch — research, review roundtables, debug
fan-outs, everything. This is general agent-team hygiene, not research-specific.
Home: `agent-teams-default.md` + the `dispatching-parallel-agents` skill.

## The principle: nothing load-bearing on the wire

Anything load-bearing that exists **only** in a `SendMessage` is lost the moment
the sending agent shuts down. This is not hypothetical — in every recovered
multi-agent session, agents delivered findings via SendMessage and then ended at
"Approving shutdown"; the only durable copy lived in the *lead's* transcript,
which compaction erases. Findings had to be reconstructed forensically from
SendMessage tool-call payloads. Silent, total, unrecoverable loss of dispatched
work.

## The rule

**Every dispatched agent writes its complete artifact to a file BEFORE sending
its pointer message.** The `SendMessage` is a pointer — "done, findings at
`<path>`" — **never the payload.** Write off the wire, at the source: the agent
that *has* the work persists it, instead of relying on the lead to catch a
fleeting message. This survives both agent shutdown and lead-transcript
compaction.

- **Where:** a **gitignored** `.research/<topic>-<date>/<NN>-<agent>.md`. The
  dispatch's **first action ensures `.research/` is in `.gitignore`** (add if
  absent). NOT `docs/` — that commits PII-bearing output into a product repo
  behind the gitleaks hook (would trade data-loss for a secret-leak). NOT `/tmp`
  — it vanishes (the exact durability failure this fixes).
- **Format contract:** each file carries a mandated `## Findings` header (the
  dispatch prompt instructs every agent to emit it). Provenance / uncertainty
  tags live **inline in the file**, never only in the message.
- **Retention:** on completion the lead **prints the path and offers cleanup** —
  no auto-delete (matches the repo's "no automated deletes; archive/flag only"
  convention).

<!-- escapement:detail:start -->

## Enforcement — at the consumer's gate, not the producer's

The **lead's existing continuation-harness contract** is the enforcement — no new
machinery, no agent-side shutdown hook (coercive at the worst layer; can't know
which file was owed; no escape path for a legit write failure). Make the lead's
`--verify` blocking and substance-checking (value-not-presence — a touched empty
file must fail):

```sh
set -euo pipefail
shopt -s nullglob                      # empty dir → loop body skips, no literal-glob
D=.research/<topic>-<date>; N=<agents-dispatched>
count=$(ls "$D"/*.md 2>/dev/null | wc -l | tr -d ' ')
test "$count" -eq "$N" || { echo "FAIL count: $count/$N files in $D"; exit 1; }
for f in "$D"/*.md; do
  grep -q '^## Findings' "$f" || { echo "FAIL no-Findings-header: $f"; exit 1; }
  test "$(sed -n '/^## Findings/,$p' "$f" | grep -cve '^[[:space:]]*$')" -ge 5 \
    || { echo "FAIL stub-under-header: $f"; exit 1; }
done
echo "OK: $N substantive findings files in $D"
```

The count check is the headline guard, so it **must** carry its own `|| exit 1`
(do not rely on `set -e` for a bare `test`). Without it, a run with 3 of 10 files
present would fall through to the loop and pass — the gate that doesn't fire.

If a file is missing or a stub, **re-dispatch or ping that agent** — do not
synthesize from the transcript.

**Day-2 escalation (only if the observe phase shows agents skipping the file):**
a producer-side hook with an escape path in the denial (`write file OR send
inline + --persist-waiver "<why>"`), `_gate_signal.record(gate=
'research-persistence', …)`, and the same substance check. Behavior precedes
belief — ship the rule + the lead-side contract first.


<!-- escapement:detail:end -->
## Status

Promoted from a real near-miss (3 recovered sessions). Severity (silent total
loss) makes the lead-side contract **mandatory**; the proportional-enforcement
principle keeps the producer-side hook **off** until evidence demands it.
