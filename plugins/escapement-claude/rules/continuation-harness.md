# continuation-harness — outcome-or-resumption gate (live)

A new deterministic Stop gate runs alongside `validate_no_shirking.py`. Both can block; they are additive. The harness gate enforces **outcome-bias over action-bias**: stopping is forbidden unless you've demonstrated completion, scheduled your own resumption, or been explicitly released by the user.

## The three Stop-permission paths

You may Stop iff one of these is true:

1. **Verification passed this turn.** You ran `~/.claude/harness/bin/verify`, it exited 0, and the run happened within the last 5 minutes.
2. **Wakeup registered.** You called `ScheduleWakeup` for a future-dated check-in. The wakeup entry exists in your session's thread dir (`~/.claude/harness/threads/{session_id}/scheduled.json`, keyed by `CLAUDE_CODE_SESSION_ID`).
3. **User released.** The user typed `stop`, `end here`, `done for now`, `that's enough`, `we're done`, `halt`, etc.

If none of these holds, Stop is blocked with a constructive resumption prompt. This is
a real control transition: the attempted turn ends and the agent must resume through a
new turn. It can therefore consume time, tokens, and user attention; do not describe it
as harmless noise or use repeated blocks as a continuation strategy.

## Action-local waits

An unresolved consequential choice **blocks only that action and its dependents**;
**independent authorized work continues**. Preserve the blocked dependency, run other
ready work, and return to the decision only when it becomes the last remaining route.
The session is `input_required` only when no authorized path toward the delegated
outcome remains runnable. Informational side questions do not cancel the active outcome;
answer them and resume unless the user explicitly redirects or stops the work.

<!-- escapement:detail:start -->

## How to declare a contract

### Preferred: declare the oracle on the bead, once

If the work is tracked by a bead, put the oracle in the bead's acceptance criteria as
a fenced `verify` block. `harness/bin/derive_contract.py` reads it and builds the
contract — `goal` from the bead title, `verification_command` from the block,
`source: bead-derived`. No second authoring step.

The acceptance criteria carry prose plus one fenced block tagged `verify`:

~~~text
<what a user must be able to observe>

```verify
<shell command whose exit 0 proves that outcome>
```
~~~

Pass that whole string to `bd create --acceptance=...` (or `bd update <id> --acceptance=...`
to add one to an existing bead). A concrete example:

~~~text
The Finance close line reads plain language and states whether the close is
authoritative or degraded. No rendered close-status string contains an
underscore-derived enum, including for an unmapped basis value.

```verify
cd src/dashboards/frontend && npx vitest run outcome/outcome-1940.test.jsx
```
~~~

Only a fence tagged `verify` counts, so an illustrative ``` block in the criteria is
never mistaken for an oracle. Absent or trivial oracles (`true`, `:`, `echo x`) are
rejected — the deriver is fail-closed, and **a bead with no verify block yields no
contract at all**, which silently degrades "done" to whatever is mechanically
checkable (a green suite, clean lint) rather than to the outcome the bead exists to
produce.

Write the oracle against the surface a reader actually sees — render the component,
call the endpoint, query the report — not against a helper function. "A test exists"
is not an outcome; "the line on the page reads X" is.

### Fallback: hand-author when there is no bead

```bash
python3 ~/.claude/harness/bin/init_contract.py \
  --goal "<one sentence: what 'done' means for the user>" \
  --verify "<shell command whose exit 0 proves done>"
```

The `--verify` command is the **oracle**. It must be something whose exit code
mechanically demonstrates the outcome — an end-to-end behavioral check, a report/query
assertion, or a public workflow state check. Task closure such as `bd close <id>` is
tracking state, not an independent outcome oracle. Sloppy contracts (`--verify "true"`)
get caught at first-run human review for novel/`agent-declared` sources.

### Contracts for config work (you still owe one)

Config/docs work being TDD-exempt does NOT exempt it from a continuation-harness contract. TDD-exemption means "no unit test"; the harness still wants proof the outcome happened. The right `--verify` for config is the appropriate rung of the behavioral-config ladder (see `tdd-enforcement.md` § "Behavioral config is not exempt"), NOT a parse check, and NOT `true`:

| Config kind | `--verify` oracle |
|-------------|-------------------|
| Passive config / docs (data an app reads, prose) | parse check is fine: `python3 -c "import yaml; yaml.safe_load(open('f.yml'))"` / `python3 -c "import json; json.load(open('f.json'))"` |
| GitHub workflow YAML (routine) | `actionlint .github/workflows/<f>.yml` (mandatory floor) |
| GitHub workflow YAML (trigger / auth / deploy-gating) | `gh workflow run <f>.yml --ref <test-branch> && gh run watch <id> --exit-status` + assert the *downstream* run started. Lint alone is forbidden here. If unreproducible locally, register a **waiver** (see below) instead of a fake green. |
| Terraform / OpenTofu (config-authoring) | `tofu validate` then a deterministic plan assertion, e.g. `tofu plan -out=tfplan && tofu show -json tfplan \| jq -e '<assertion about resource_changes>'` |
| Kubernetes manifests | `kubeconform <f>` then `kubectl apply --dry-run=server -f <f>` |

If the real behavior can only be observed after merge (platform semantics, no sandbox), do NOT register a passing parse-check as the contract. Instead declare the contract's verification as the **post-merge observation command** and register a `ScheduleWakeup` to run it after merge, or surface a waiver to the user. A green parse check standing in for an unverified trigger change is exactly the oracle-downgrade the harness exists to prevent.

## How to verify

When you consider the task done:

```bash
~/.claude/harness/bin/verify
```

The script runs your contract's `verification_command`, captures the result back to
`contract.json#/last_run`, and exits with the same code. If exit 0 and within the
current-turn window, the gate allows Stop next time. If it fails, fix the underlying
issue. If an unresolved consequential choice blocks that action, persist the dependency
and continue independent authorized work; documenting a failure is durable state, not
completion.

## How to schedule resumption

If your work is genuinely waiting on something external (CI, merge queue, DAG run, an external agent), use the `ScheduleWakeup` tool. Don't write "I'll check back" as prose and end the turn — prose-as-polling is the largest measured stall class (30%) and is exactly what this gate exists to prevent.

### Task-mode gate + external-event wait: use ScheduleWakeup, not task pickup

When the task-mode gate blocks with `tasks_remain_in_queue` but your **session goal** is blocked on an external event (CI finishing, a merge completing, a scheduled dbt/DAG run, an external agent completing its work), the correct response is:

```
ScheduleWakeup(delaySeconds=<when the event will complete>, reason="<what you're waiting for>", prompt="<same loop prompt>")
```

Do **not** pick up unrelated ready tasks from `bd ready` to drain the queue and satisfy the gate. That is scope creep, not progress — you are doing work the user did not ask for in this session, and the session's actual outcome remains unverified.

The three release paths from a task-mode block are:
1. **Finish the actual session work** — drain the tasks that belong to this session's goal, verify the outcome.
2. **ScheduleWakeup** — register a future check-in for when the external blocker clears.
3. **User release** — the user says `stop` or `end here`.

Picking up unrelated backlog items is not a fourth path. If `bd ready` shows tasks outside the current session's scope, ignore them — they belong to a different session.

### Background-workflow watchdog (long runs die silently at the host timeout)

A background `Workflow` run is killed at the Claude Code host's task timeout (~13 min, observed 2026-05-29) with **no completion notification** — the parent is silently stranded mid-run. That timeout is a platform limit this repo cannot reconfigure; the mitigation is to make the death *observable* and *recoverable* instead of silent. When you launch a `Workflow` that may exceed ~13 min of wall-clock:

1. **Register a fallback wakeup for it.** `ScheduleWakeup(delaySeconds=<~run estimate + buffer>, reason="watchdog: workflow <runId>", prompt="<resume/check prompt>")`. Since the ScheduleWakeup→Stop-gate bridge now works (bead `escapement-0wg`), this both releases the Stop gate while you wait and re-invokes you when the timer fires.
2. **On re-invocation, classify the run mechanically — do NOT do manual `ps`/file-activity forensics:**
   ```bash
   python3 ~/.claude/harness/bin/workflow_status.py --run <runId>
   ```
   Exit 0 = `completed` (collect the result). Non-zero = actionable: `running` (re-arm the wakeup and wait longer), `no_signal` (silently died — resume), `ended_incomplete` (errored — inspect).
3. **Resume a dead run** with `Workflow({scriptPath, resumeFromRunId: "<runId>"})` — completed agents return from cache; only the killed/edited call onward re-runs. If a run dies repeatedly at the same boundary, decompose the script into smaller phases (each phase a separate background run) so no single run approaches the timeout.

The residual platform fix (the runtime emitting its own death signal / raising the timeout) is tracked outside this repo; the harness-side mitigation above turns silent stranding into a scheduled, mechanical re-check.


<!-- escapement:detail:end -->
## Rule: outcome-bias

If you are not done and not scheduled to return, you are not stopping. More tool
calls, dispatches, or bead-claims are not proof of completion or resumption.

## Completion target: ship it live

Done means merged and deployed where the change actually runs — not "PR opened" or
"committed locally." There is no git completion ceiling. Commits, task-branch pushes,
pull-request updates, and a repository's standard declared landing path are authorized
ordinary means, not categorically human-only. If an action genuinely crosses delegated
authority (a new credential, an undelegated irreversible shared effect), name that exact
dependency and continue the rest.

### Per-repo outcome authorization — the durable authorization the base prompt defers to

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

The base prompt confirms hard-to-reverse actions *unless durably authorized*. A repo's
committed `.escapement/repo.json` **is** that authorization. Read it before merging
(`harness/bin/repo_outcome.py` resolves it):

- `intended_outcome` at or above `merged` **and** `auto_merge_on_green: true` →
  pre-authorized for the declared landing path. On green evidence, **merge and ship it
  live. Do NOT ask "want me to merge it now, or review the PR first?"** — that
  solicitation is the exact anti-pattern this authorization removes. If the repo declares
  a `deploy` surface, name it in your report ("now live at X") rather than ask.
- No declaration, malformed, or `auto_merge_on_green: false` → stop at `pr-opened`, may
  ask. Never assume authorization a repo did not grant.

**The "irreversible external action" carve-out does NOT cover a merge that triggers
auto-deploy.** It covers steps you genuinely cannot perform without a human — typing a
credential, clicking an external approval. A `gh pr merge` you can run is not one — do
not stretch the carve-out to re-introduce the ask the declaration removed.

Support boundary, narrower than the configuration vocabulary: the merge hook does not
observe green status; `confirm_class` is reserved and not currently enforced; `deploy`
metadata is informational. Do not claim stronger enforcement until a point-of-effect fixture proves it.

**Attempt the merge; do not pre-judge repository authorization in conversation.** Run
`gh pr merge <PR> --squash` and let `merge_authorization_gate.py` (PreToolUse on
`Bash(gh pr merge:*)`) return the actual verdict. If it denies, report its
`permissionDecisionReason` — verbatim in substance, never a guess dressed up as an
external constraint. (An agent once invented a "platform-level gate" that branch
protection confirmed did not exist.) To configure a repo, offer
`harness/bin/set_repo_outcome.py` rather than asking the user to hand-write JSON.
