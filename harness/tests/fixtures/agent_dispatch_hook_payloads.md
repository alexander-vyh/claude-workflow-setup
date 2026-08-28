# Agent dispatch hook payloads — capture method

`agent_dispatch_hook_payloads.json` holds real Claude Code hook payloads captured
for **escapement-g27c**, which asked whether a hook can observe (a) that a
subagent was dispatched and its native child identifier, and (b) that subagent's
final output. Both are **yes**. This file records how the capture was produced so
the answer can be re-derived against a future host version instead of re-argued.

Asserted by `harness/tests/test_agent_dispatch_capability.py`.

## Why a positive test

The prior belief was inference: `strings` on the Claude binary showed an
`Agent` entry near `PostToolUse` in the hook event registry. Per the
`absence-of-declaration-is-not-absence-of-capability` rule, neither the presence
nor the absence of a declaration establishes a mechanism — only an installed
payload capture does. Everything in the fixture was observed.

## Host

Claude Code **2.1.248**, darwin, captured 2026-08-28.

## Method

1. A throwaway probe hook writes its **entire verbatim stdin** as one JSON
   record per invocation, then exits 0 with empty stdout so it never alters host
   behavior. Its `argv[1]` is the event name it was registered under, so a
   payload can be attributed even if `hook_event_name` were missing.

   ```python
   label = sys.argv[1]
   raw = sys.stdin.read()
   record = {"label": label, "ts": ..., "payload": json.loads(raw)}
   out.open("a").write(json.dumps(record) + "\n")
   ```

2. It was registered for **ten** event names in a standalone settings file:
   `PreToolUse`, `PostToolUse`, `PostToolUseFailure` (each `matcher: ".*"`), and
   `PostToolBatch`, `SubagentStart`, `SubagentStop`, `TaskCreated`,
   `TaskCompleted`, `TeammateIdle`, `Stop`.

3. Each trial ran a nested headless session from a scratch directory **outside
   any repo**, so no other session's settings were touched or applied:

   ```
   claude -p --restricted --settings <probe.json> --model haiku \
          --allowedTools 'Task' 'Agent'
   ```

   `--restricted` drops the built-in project and local settings files while
   still honoring `--settings`, which is what isolates the probe. Pass the
   prompt on **stdin** — `--allowedTools` is variadic and will otherwise swallow
   a trailing prompt argument.

4. Each trial dispatched one real `general-purpose` subagent told to reply with
   a unique sentinel word. **The sentinel is the oracle**: a field only counts as
   carrying the subagent's output if the sentinel round-trips through it
   verbatim.

## Trials

| Trial | Dispatch | Sentinel | Observed order |
|---|---|---|---|
| `background_dispatch` | `run_in_background: true` | `MANGO` | PreToolUse → SubagentStart → **PostToolUse** → PostToolBatch → **SubagentStop** → Stop → Stop |
| `foreground_dispatch` | `run_in_background: false` | `PAPAYA` | PreToolUse → SubagentStart → **SubagentStop** → **PostToolUse** → PostToolBatch → Stop |
| `dispatch_failure` | unknown `subagent_type` | — | PreToolUse → PostToolUseFailure → PostToolBatch → Stop |

## What the capture establishes

- `PostToolUse` **does** fire for `tool_name: "Agent"` and **does** carry
  `tool_response`.
- The native child identifier is `tool_response.agentId`, and it equals
  `agent_id` on both `SubagentStart` and `SubagentStop`.
- `SubagentStop.last_assistant_message` carries the subagent's final text under
  **both** dispatch modes — the only single-code-path verdict source.
- `SubagentStart` is real and fires, despite being absent from the public hook
  event table.
- A rejected dispatch fires `PostToolUseFailure` (with `error` and
  `tool_use_id`) and fires **no** subagent event, so it strands no execution.

## Traps this capture exists to prevent

- **PostToolUse is not a completion signal.** For a backgrounded dispatch it is
  a launch receipt: `status: "async_launched"`, no `content`, `duration_ms` in
  single digits.
- **Order is dispatch-mode dependent.** `PostToolUse` precedes `SubagentStop`
  when backgrounded and follows it when synchronous. Consumers must be
  order-tolerant.
- **`tool_use_id` cannot join the subagent events.** It is stable across
  `PreToolUse`/`PostToolUse`/`PostToolUseFailure`/`PostToolBatch` but is absent
  from `SubagentStart`/`SubagentStop`. Join on `agent_id`.
- **`background_tasks[].status` is not terminality.** It read `"running"` for an
  agent inside that agent's own `SubagentStop` payload.
- **`tool_response` echoes the dispatch prompt.** `tool_response.prompt` and
  `.description` repeat what the *dispatcher* asked for, and are populated
  before the subagent has produced anything. Read verdict text only from a named
  reply field (`tool_response.content[].text` or
  `SubagentStop.last_assistant_message`), never by substring search over the
  payload.
- **`TaskCreated`, `TaskCompleted`, `TeammateIdle` are not subagent signals.**
  They were registered, the settings file was accepted without error, and none
  of them ever fired for a subagent dispatch.

## Re-running

Change one sentinel per trial, re-run the three commands above, and re-run
`python3 -m pytest harness/tests/test_agent_dispatch_capability.py -q`. If a
future host version moves a field, that test fails with the specific claim that
stopped being true.
