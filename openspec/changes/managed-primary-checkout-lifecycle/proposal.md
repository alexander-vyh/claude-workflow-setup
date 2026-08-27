## Why

Escapement creates, bootstraps, finishes, and cleans linked worktrees, but it does not own the health or synchronization of the primary checkout that anchors those worktrees. Primary checkouts can therefore remain stale or become structurally invalid while task worktrees continue from fresh `origin/HEAD`, leaving repository tools and Beads discovery inconsistent at the control-plane boundary.

## What Changes

- Add an Escapement-owned primary-checkout lifecycle that diagnoses anchor integrity and safely fast-forwards an eligible default-branch checkout to the exact advertised remote default.
- Add a public `escapement-worktree sync-root` transaction and integrate safe root synchronization with worktree creation and receipt-backed finishing without making task work depend on an unsafe root repair.
- Fail closed without modifying dirty, divergent, bare, detached, or non-default primary checkouts; never reset, stash, switch branches, or rewrite refs behind the checked-out worktree.
- Surface root-health and synchronization outcomes consistently through source, generated Claude/Codex packages, and the installed lifecycle supervisor.
- Reconcile root-checkout protection so installed host surfaces actually invoke the supported guard behavior rather than merely shipping an unwired hook file.

## Capabilities

### New Capabilities
- `primary-checkout-lifecycle`: Diagnose and safely synchronize the non-bare primary checkout that acts as Escapement's repository control plane.

### Modified Capabilities
- `agent-surface-parity`: Require packaged Claude and Codex lifecycle surfaces to carry the same runnable root synchronization and supported protection behavior.

## Impact

- Extends `escapement-worktree` and its Git transaction modules, lifecycle finish/supervisor integration, generated plugin packaging, and repository-policy parsing.
- Adds behavioral fixtures for clean fast-forward, dirty/divergent/non-default/bare controls, source freshness independence, installed package parity, and root-guard registration.
- Repositories retain ownership of bootstrap commands and landing policy; Beads remains task state only.
