# Escapement-Owned Session Context Design

## Outcome

Escapement, not Beads, determines how agents commit, push, open pull requests,
merge, deploy, and decide that work is complete. Beads remains the task-state
system.

Every repository using Escapement defaults to feature branch, push, and pull
request. A committed `.escapement/repo.json` may explicitly select another
supported landing outcome. When no declaration exists, Escapement presents the
available outcome choices while retaining the pull-request default.

## Current Failure

Escapement runs `bd prime` automatically at SessionStart and PreCompact.
`bd prime` injects upstream Beads prose controlled by the installed Beads
version, global Beads configuration, and stored Beads memories. A global
`no-git-ops: true` setting therefore tells agents not to perform Git operations,
contradicting Escapement's repository outcome contract.

## Design

1. Remove automatic `bd prime` commands from the shared hook manifest and every
   generated Claude and Codex hook surface.
2. Add an Escapement-owned SessionStart/PreCompact context hook. Its output:
   - defines Beads as task tracking only;
   - names the small operational command set: `bd ready`, `bd show`,
     `bd update --claim`, `bd close`, and `bd worktree create`;
   - states that Beads-provided Git, landing, completion, and memory policy is
     non-authoritative;
   - reports the landing outcome from `.escapement/repo.json`, defaulting to
     `pr-opened`;
   - instructs the agent to offer the supported landing choices when the
     declaration is absent.
3. Update Escapement's generated onboarding text so it no longer calls
   `bd prime` a workflow source of truth.
4. Keep the existing outcome resolver and authorization gates authoritative for
   merge and deployment decisions; the new context hook reports their contract
   rather than creating a second authorization implementation.

## Failure Handling

Missing or malformed `.escapement/repo.json` fails closed to `pr-opened` and
auto-merge disabled. Missing Beads or unavailable `bd` does not suppress the
Escapement landing policy; tracker commands simply remain unavailable until the
tracker is repaired.

## Test Oracle Brief

1. **Business invariant:** Automatic agent context always follows Escapement's
   repository landing policy and never lets Beads override it.
2. **Independent source of truth:** The committed `.escapement/repo.json`,
   interpreted by the existing outcome contract, plus the generated hook
   artifacts users actually install.
3. **Solution constraints:** Host-neutral behavior; manifest-driven generated
   surfaces; no upstream Beads prose; conservative `pr-opened` default; existing
   merge authorization semantics remain unchanged.
4. **Invalid solution classes:** Filtering a few current `bd prime` strings;
   changing only checked-in `AGENTS.md`; fixing only one host; depending on the
   user's global Beads configuration; duplicating merge authorization logic.
5. **Fragile implementation to reject:** Continue running `bd prime` and append
   stronger Escapement prose afterward.
6. **Negative control:** With global Beads `no-git-ops: true`, generated and
   installed startup context must contain no `bd prime`, `stealth mode`, or
   Beads-authored Git policy.
7. **Positive control:** With no repo declaration, startup context still
   requires feature branch, push, and pull request and offers the outcome
   choices. With a valid declaration, it reports that declaration.
8. **Missing/unresolved handling:** Missing or malformed policy fails closed to
   `pr-opened`; missing tracker state never changes the landing policy.
9. **Final outcome verification:** Render every agent surface, run the focused
   behavioral tests, execute the actual startup hook against absent and
   configured repo policies while hostile Beads configuration is present, and
   inspect the emitted context.

## Deployment Extension: Legacy Settings Migration

The first live deployment exposed a second path by which the removed command
could remain active: a direct registration in `~/.claude/settings.json`.
`INSTALL.sh --update` must find the actually installed Escapement plugin and
use its hook inventory to remove both duplicate script registrations and the
legacy command replaced by `escapement_session_context.py`.

1. **Business invariant:** After updating an installed Escapement plugin, the
   effective user settings contain no automatic Beads policy injection.
2. **Independent source of truth:** The real Claude plugin-cache layout, the
   installed plugin's `hooks/hooks.json`, and the resulting user
   `settings.json`.
3. **Solution constraints:** Migration must be idempotent, preserve unrelated
   user hooks and all non-hook settings, and remain non-destructive when the
   plugin is genuinely absent.
4. **Invalid solution classes:** Hardcoding one version directory; deleting
   every lifecycle hook; editing only the current user's settings; finding the
   plugin but failing to recognize the obsolete non-script command.
5. **Fragile implementation to reject:** Increase the plugin-cache search depth
   while leaving the legacy command untouched because it has no script basename.
6. **Negative control:** A settings fixture containing the obsolete command must
   lose it after installation.
7. **Positive control:** A personal `PreCompact` hook in the same fixture must
   survive byte-for-byte.
8. **Missing/unresolved handling:** If no installed plugin hook inventory is
   found, make no settings change and report that pruning was skipped.
9. **Final outcome verification:** Run the installer against an isolated home
   with the real nested cache shape, then update the live plugin and confirm the
   effective settings and installed hook output.
