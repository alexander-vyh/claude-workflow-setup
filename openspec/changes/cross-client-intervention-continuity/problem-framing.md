# Problem Framing — cross-client-intervention-continuity

## Problem

Claude Code, Codex, Pi, and other coding-agent clients repeatedly stop productive
work to solicit human input for routine actions already authorized by the task and
repository outcome. The same failure appears as command approvals, edit approvals,
clarifying questions, credential requests, and client-specific permission cards.
Today Escapement has no host-neutral inventory of those interventions, no durable
cross-session attention queue, and no mechanical distinction between “this action
must wait” and “the whole session must stop.” As a result, an informational side
question or one blocked child action can strand otherwise independent work until the
user notices and responds.

## Why Now

The user observed and captured repeated examples across several repositories during
the last 48 hours, including approvals for verification commands, isolated-worktree
Git operations, PR creation, and agent-authored edits. These interruptions are no
longer isolated annoyances: they are a recurring cross-repository loss of unattended
execution. Public client documentation now exposes enough lifecycle and permission
hooks to address much of the problem, and GitLab’s current Agent Platform work
independently validates durable approval memory, typed wait states, asynchronous
attention queues, and preapproved privileges as the appropriate product shape.

## Decision Authority

The user owns the what and why. Escapement owns workflow policy and repository-local
authorization through `.escapement/repo.json`; Beads owns task state only. Individual
clients and their adapters do not acquire independent authority to broaden or rewrite
those decisions.

## Behavioral Population

Claude Code, Codex, Pi, and future coding-agent sessions operating through
Escapement must change behavior. Host adapters must normalize native approval and
input events into one Escapement lifecycle; parent sessions and sibling agents must
continue independent work while one action awaits input; already-authorized routine
actions must stop re-prompting merely because their command text changed. The user
must receive one durable, queryable attention surface for genuinely unresolved
decisions across all repositories and sessions.

## Riskiest Assumption + Liveness

We are betting that a deliberately small semantic action model derived from explicit
task intent and `.escapement/repo.json`, combined with action-local intervention state,
can remove the majority of repeated human interruptions without suppressing a
genuinely necessary decision. The bet is wrong if controlled live sessions either
continue surfacing duplicate prompts for already-authorized actions, still suspend
unrelated runnable work, or silently execute a negative-control action that required
human confirmation. We will know within two weeks through replay of the captured
prompt classes, live Claude/Codex/Pi adapter probes, reconciliation of normalized
events against client-visible prompts, and observation of sibling tool activity while
an intervention remains pending.

## Success Criteria

- A single query returns every normalized human-intervention event observed across
  supported clients, repositories, sessions, and agents for a requested time window,
  including requested, surfaced, resolved, and waited durations where the client
  exposes them.
- In a controlled multi-actor run, an informational question or one action awaiting
  approval leaves independent sibling and parent work runnable and visibly active.
- Repository- and task-authorized worktree creation, editing, verification, commit,
  branch synchronization, push, PR creation, merge, and deployment actions do not
  re-prompt solely because dynamic command arguments changed.
- A declared confirmation class, missing credential, material scope expansion,
  destructive shared-state mutation, or otherwise unresolved consequential action
  still produces a durable intervention and never becomes silently authorized.
- Unsupported client capabilities remain explicit and observable; Escapement never
  claims interception, resumption, or prompt suppression that the installed host
  cannot behaviorally prove.
