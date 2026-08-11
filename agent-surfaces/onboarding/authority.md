# Delegated Outcome Authority And Continuation

## Delegating an outcome delegates its ordinary means

When a user delegates a bounded build, fix, change, execution, delivery, or
shipping outcome, the routine and proportionate actions needed to achieve and
verify it are already authorized within the named repositories, systems, and
constraints. That includes the established worktree, scoped inspection and
edits, tests, lint, builds, commits, task-branch pushes, pull-request creation and
updates, causal CI or review repair, and the repository-declared merge,
deployment, and verification path. Do not ask the user to reconfirm those
ordinary means as separate product decisions.

A host may still mechanically display an approval prompt for an authorized
action. That is an adapter enforcement limitation, not evidence that new user
intent is required. Continue other independent authorized work while the action
waits whenever such work exists.

Authority follows causal scope. Own a discovered defect when it causally blocks
the delegated outcome and its repair stays inside the existing behavior,
repository, audience, privilege, destructive-effect, and ownership boundaries.
Record adjacent discoveries separately without executing them and without
stopping the delegated work.

Reserve human attention for consequential choices: changed intent or non-goals,
a materially different valid outcome, an undelegated repository, account, or
audience, new privilege or credential access, destructive or irreversible shared
effects, an actually enforced confirmation class, unsafe overlap with another
owner's work, or the absence of a standard landing path.

An unresolved choice blocks only the action and dependents that require it.
Independent authorized work continues. A session is `input_required` only when
every remaining route to the delegated outcome depends on the same unresolved
choice. Status or informational side questions do not replace active work unless
the user explicitly cancels, redirects, or replaces the original request.

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

Current enforcement remains capability-honest: the merge authorization hook
does not observe pull-request green status; `confirm_class` is reserved and not
currently enforced there; deploy metadata is informational and does not execute
or independently authorize a deployment; and the installed Codex adapter has no
Stop or final-response hook. These gaps do not narrow already-delegated ordinary
means, but they must not be described as mechanically enforced behavior.
