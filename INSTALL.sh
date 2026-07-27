#!/bin/bash
# INSTALL.sh — Symlinks Escapement into ~/.claude/ and ~/.beads/
#
# Usage:
#   ./INSTALL.sh              # install: symlink ~/.claude into a PINNED checkout (default)
#   ./INSTALL.sh --dev        # install: symlink into THIS live working tree (instant edits)
#   ./INSTALL.sh --update     # refresh the pinned checkout to latest (deploy new main)
#   ./INSTALL.sh --uninstall  # remove symlinks (backups are left alone)
#   ./INSTALL.sh --dry-run    # show what would happen, change nothing
#
# Default deploy model (bead ft1): ~/.claude symlinks resolve into a pinned clone
# (ESCAPEMENT_PIN_DIR, default ~/.claude/.escapement-pinned) of this repo at
# ESCAPEMENT_PIN_REF (default main) — NOT the live working tree. This way a branch switch or mid-edit in this
# repo can't break hooks machine-wide across all your repos. The price: edits go
# live only after they reach main AND you run `./INSTALL.sh --update`. Use --dev to
# opt back into instant-edit-from-working-tree (the old, fragile-but-convenient model).
#
# Fail-fast. Backup-then-symlink — nothing is silently clobbered.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
BEADS_DIR="$HOME/.beads"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

# Pinned-checkout deploy (bead ft1). Overridable via env for testing/relocation.
# The old CWS_* variables remain accepted for existing scripts.
#
# B egk fix: track whether ESCAPEMENT_PIN_DIR was explicitly provided by the
# caller. In --update mode with no explicit override, we resolve the EFFECTIVE
# pin dir from where a deployed sentinel symlink actually points (so a CWS-era
# machine whose symlinks resolve into .cws-pinned gets THAT dir updated, not a
# freshly created .escapement-pinned that nothing links to). An explicit
# ESCAPEMENT_PIN_DIR always wins (B2), and no-symlinks falls back to the default.
_PIN_DIR_EXPLICIT="${ESCAPEMENT_PIN_DIR+set}"  # "set" if caller exported it; else ""
ESCAPEMENT_PIN_DIR="${ESCAPEMENT_PIN_DIR:-${CWS_PIN_DIR:-$CLAUDE_DIR/.escapement-pinned}}"
ESCAPEMENT_PIN_REF="${ESCAPEMENT_PIN_REF:-${CWS_PIN_REF:-main}}"
ESCAPEMENT_PIN_REMOTE="${ESCAPEMENT_PIN_REMOTE:-${CWS_PIN_REMOTE:-$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || echo "$REPO_DIR")}}"

# --- Arg parsing ---
MODE="install"
DRY_RUN=false
DEV_MODE=false
ALLOW_PINNED_DRIFT=false
for arg in "$@"; do
  case "$arg" in
    --uninstall) MODE="uninstall" ;;
    --update)    MODE="update" ;;
    --dev)       DEV_MODE=true ;;
    --dry-run)   DRY_RUN=true ;;
    --allow-pinned-drift) ALLOW_PINNED_DRIFT=true ;;
    --help|-h)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

# Where symlinks point: the pinned checkout (default) or the live working tree (--dev).
if [[ "$DEV_MODE" == true ]]; then DEPLOY_SRC="$REPO_DIR"; else DEPLOY_SRC="$ESCAPEMENT_PIN_DIR"; fi

run() {
  if [[ "$DRY_RUN" == true ]]; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

# --- Pre-flight ---
echo "==> Escapement installer"
echo "    repo:   $REPO_DIR"
echo "    claude: $CLAUDE_DIR"
echo "    beads:  $BEADS_DIR"
echo "    deploy: $([ "$DEV_MODE" == true ] && echo "live working tree (--dev)" || echo "pinned checkout ($ESCAPEMENT_PIN_DIR @ $ESCAPEMENT_PIN_REF)")"
echo "    mode:   $MODE$([ "$DRY_RUN" == true ] && echo ' (dry-run)')"
echo

for tool in openspec bd direnv python3 jq git bash; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "WARN: '$tool' not found on PATH — some features will not work"
  fi
done

run mkdir -p "$CLAUDE_DIR"/{skills,rules,commands,hooks,harness,agents} "$CLAUDE_DIR"/harness/threads "$BEADS_DIR"/formulas

# --- Symlink plan: (source_relative_to_repo, dest_absolute) ---
# Preserves directory structure. For skill directories, symlink the whole dir.
declare -a PLAN=(
  # Skills (whole directories)
  "claude/skills/discovery|$CLAUDE_DIR/skills/discovery"
  "claude/skills/work-breakdown|$CLAUDE_DIR/skills/work-breakdown"
  "claude/skills/brainstorming|$CLAUDE_DIR/skills/brainstorming"
  "claude/skills/build|$CLAUDE_DIR/skills/build"
  "claude/skills/beads-execution|$CLAUDE_DIR/skills/beads-execution"
  "claude/skills/behavioral-test-oracle-review|$CLAUDE_DIR/skills/behavioral-test-oracle-review"
  "claude/skills/dispatching-parallel-agents|$CLAUDE_DIR/skills/dispatching-parallel-agents"
  "claude/skills/subagent-driven-development|$CLAUDE_DIR/skills/subagent-driven-development"
  "claude/skills/beads-worktree|$CLAUDE_DIR/skills/beads-worktree"
  "claude/skills/gate-design|$CLAUDE_DIR/skills/gate-design"
  "claude/skills/vocab|$CLAUDE_DIR/skills/vocab"

  # Rules are NOT symlinked here (bead escapement-w4sn). The plugin's SessionStart
  # hook (plugins/escapement-claude/hooks/inject-rules.sh) is the SOLE injection
  # channel for the always-on rules. Symlinking claude/rules/*.md into
  # ~/.claude/rules/ ALSO loaded them as the native claudeMd block, so every
  # session carried all rule bodies TWICE (~20K tokens of pure duplicate on turn 0).
  # inject-rules.sh self-resolves ${CLAUDE_PLUGIN_ROOT} and is portable to
  # plugin-only installs, so it is the channel we keep. Do not re-add the
  # claude/rules/*.md -> $CLAUDE_DIR/rules/*.md entries; test_agent_surfaces.py
  # asserts they stay gone and that inject-rules.sh still delivers every rule.

  # Agents (workflow-integral only — personal advisor agents live in the user's
  # own config, not in this framework. adversarial-reviewer is dispatched by
  # subagent-driven-development; test-quality-reviewer is the operational
  # counterpart to tdd-enforcement + behavioral-test-oracle-review.)
  "claude/agents/adversarial-reviewer.md|$CLAUDE_DIR/agents/adversarial-reviewer.md"
  "claude/agents/test-quality-reviewer.md|$CLAUDE_DIR/agents/test-quality-reviewer.md"

  # Commands
  "claude/commands/discovery.md|$CLAUDE_DIR/commands/discovery.md"
  "claude/commands/work-breakdown.md|$CLAUDE_DIR/commands/work-breakdown.md"
  "claude/commands/brainstorm.md|$CLAUDE_DIR/commands/brainstorm.md"
  "claude/commands/review.md|$CLAUDE_DIR/commands/review.md"

  # Hooks: every claude/hooks/*.py and *.sh is symlinked by the glob below the
  # array (e9v.8) so new hooks/modules can't drift out of the deploy. The tests
  # dir is the one explicit entry.
  "claude/hooks/tests|$CLAUDE_DIR/hooks/tests"

  # Bin scripts (invokable from any repo cwd; resolve their own project root)
  "claude/bin|$CLAUDE_DIR/bin"

  # Continuation harness — code symlinked into ~/.claude/harness; runtime state
  # (threads/, incidents.jsonl) lives in ~/.claude/harness too (NOT the repo) and
  # is created by the mkdir below, so concurrent agents in any repo never write
  # into a project working tree.
  "harness/bin|$CLAUDE_DIR/harness/bin"
  "harness/schemas|$CLAUDE_DIR/harness/schemas"

  # Bootstrap
  "scripts/project-bootstrap.sh|$CLAUDE_DIR/project-bootstrap.sh"

  # Beads formulas
  "beads/formulas/mol-feature.formula.json|$BEADS_DIR/formulas/mol-feature.formula.json"
  "beads/formulas/mol-rapid.formula.json|$BEADS_DIR/formulas/mol-rapid.formula.json"
  "beads/mol-status.sh|$BEADS_DIR/mol-status.sh"
)

# Hooks (e9v.8): glob every hook + support module so new files auto-deploy and
# the install list can't drift. Per-file (not whole-dir) so a user's own hooks in
# ~/.claude/hooks/ coexist; the tests dir is added explicitly in PLAN above.
for _hook in "$REPO_DIR"/claude/hooks/*.py "$REPO_DIR"/claude/hooks/*.sh; do
  [[ -e "$_hook" ]] || continue
  _base="$(basename "$_hook")"
  PLAN+=("claude/hooks/$_base|$CLAUDE_DIR/hooks/$_base")
done

backup_if_exists() {
  local dest="$1"
  if [[ -L "$dest" ]]; then
    # Already a symlink — remove it (no backup needed, nothing real there)
    run rm "$dest"
  elif [[ -e "$dest" ]]; then
    local backup="${dest}.backup-${TIMESTAMP}"
    echo "    backup: $dest -> $backup"
    run mv "$dest" "$backup"
  fi
}

install_plan() {
  local installed=0
  for entry in "${PLAN[@]}"; do
    local src_rel="${entry%|*}"
    local dest="${entry#*|}"
    local src_abs="$DEPLOY_SRC/$src_rel"

    # Existence is checked against the repo (source of truth); the symlink itself
    # points at DEPLOY_SRC (pinned checkout by default). Decoupling lets --dry-run
    # report a real plan even before the pinned checkout is created.
    if [[ ! -e "$REPO_DIR/$src_rel" ]]; then
      echo "SKIP (source missing): $src_rel"
      continue
    fi

    backup_if_exists "$dest"
    run ln -s "$src_abs" "$dest"
    installed=$((installed + 1))
    echo "    link:   $dest -> $src_rel"
  done
  echo
  echo "==> installed $installed symlinks"
}

uninstall_plan() {
  local removed=0
  for entry in "${PLAN[@]}"; do
    local dest="${entry#*|}"
    if [[ -L "$dest" ]]; then
      run rm "$dest"
      removed=$((removed + 1))
      echo "    unlink: $dest"
    fi
  done
  echo
  echo "==> removed $removed symlinks"
  echo "    (any .backup-* files are left alone — rename manually to restore)"
}

# Verify the continuation-harness Stop gate is actually WIRED in the deployed
# settings — not merely present on disk. The harness code is symlinked above, but
# it does nothing unless ~/.claude/settings.json invokes stop_hook.py under Stop.
# This catches the distribution-drift bug where a user symlinks the harness but
# forgets to merge the Stop block (bead escapement-fxh.1).
verify_stop_gate_wired() {
  local settings="$CLAUDE_DIR/settings.json"
  if [[ ! -f "$settings" ]]; then
    echo "    ⚠  $settings not found — merge the template's Stop block (see step 1),"
    echo "       or the continuation-harness Stop gate will be inert."
    return 0
  fi
  if python3 - "$settings" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"    ⚠  could not parse settings.json ({e}) — verify the Stop block manually.")
    sys.exit(0)
cmds = [
    h.get("command", "")
    for grp in data.get("hooks", {}).get("Stop", [])
    for h in grp.get("hooks", [])
]
sys.exit(0 if any("stop_hook.py" in c for c in cmds) else 1)
PY
  then
    echo "    ✓  continuation-harness Stop gate is wired (stop_hook.py present under Stop)."
  else
    echo "    ⚠  continuation-harness Stop gate is NOT wired in $settings."
    echo "       The harness code is installed but DEAD until you add this under hooks.Stop:"
    echo '         { "hooks": [ { "type": "command",'
    echo '             "command": "python3 ~/.claude/harness/bin/stop_hook.py" } ] }'
    echo "       (additive — keep the existing validate_no_shirking.py entry too)."
  fi
}

# Create or refresh the pinned checkout that ~/.claude symlinks resolve into.
# Accepts an optional first arg: the pin dir to act on (defaults to
# $ESCAPEMENT_PIN_DIR). Idempotent: clone if absent, else fast-forward to
# ESCAPEMENT_PIN_REF. Never rewrites local edits (ff-only) — the pinned checkout
# is deploy state, not a dev tree.
ensure_pinned_checkout() {
  local pin_dir="${1:-$ESCAPEMENT_PIN_DIR}"
  if [[ -d "$pin_dir/.git" ]]; then
    # Guardrail: detect deploy-dir drift (uncommitted edits made directly in the
    # pinned checkout). These bypass review and are invisible until they conflict
    # with a future update — surface them with an escape path rather than silently
    # stranding them (recovered two such drifts on 2026-06-14).
    local _drift
    _drift="$(git -C "$pin_dir" status --porcelain 2>/dev/null)"
    if [[ -n "$_drift" ]]; then
      echo "" >&2
      echo "⚠  pinned checkout has uncommitted local edits (deploy-dir drift):" >&2
      printf '%s\n' "$_drift" | sed 's/^/       /' >&2
      echo "   Direct edits here bypass review and are invisible until they conflict." >&2
      echo "   Resolve one of:" >&2
      echo "     • upstream:  git -C '$pin_dir' diff <file>  → open a PR to $ESCAPEMENT_PIN_REF, then re-run --update" >&2
      echo "     • discard:   git -C '$pin_dir' checkout -- <file>" >&2
      echo "     • proceed:   re-run with --allow-pinned-drift (ff-only keeps non-conflicting edits)" >&2
      if [[ "$ALLOW_PINNED_DRIFT" != true ]]; then
        echo "   Aborting to avoid silently stranding the drift." >&2
        exit 1
      fi
      echo "   --allow-pinned-drift set: proceeding." >&2
    fi
    echo "==> refreshing pinned checkout: $pin_dir -> $ESCAPEMENT_PIN_REF"
    run git -C "$pin_dir" fetch --quiet "$ESCAPEMENT_PIN_REMOTE" "$ESCAPEMENT_PIN_REF"
    run git -C "$pin_dir" checkout --quiet "$ESCAPEMENT_PIN_REF"
    run git -C "$pin_dir" merge --ff-only FETCH_HEAD
  else
    echo "==> creating pinned checkout: clone $ESCAPEMENT_PIN_REMOTE -> $pin_dir"
    run git clone --quiet "$ESCAPEMENT_PIN_REMOTE" "$pin_dir"
    run git -C "$pin_dir" checkout --quiet "$ESCAPEMENT_PIN_REF"
  fi
}

# B egk: resolve the EFFECTIVE pin dir for --update mode.
# If the caller explicitly set ESCAPEMENT_PIN_DIR, use that (B2 override wins).
# Otherwise read the sentinel symlink to find which checkout is actually live.
# Falls back to the default ESCAPEMENT_PIN_DIR if no sentinel exists (B3 fresh).
resolve_effective_pin_dir() {
  if [[ "$_PIN_DIR_EXPLICIT" == "set" ]]; then
    echo "$ESCAPEMENT_PIN_DIR"
    return
  fi
  # Try to resolve from a deployed sentinel hook symlink.
  local sentinel="$CLAUDE_DIR/hooks/spec_id_enforcement.py"
  if [[ -L "$sentinel" ]]; then
    local target
    target="$(readlink "$sentinel")"
    # The target is something like <checkout>/<relative-path>. Strip the
    # relative suffix to get the checkout root. We look for the first component
    # that is a git checkout (contains /.git/).
    # Strategy: split on '/.git/' — everything before it is the checkout root.
    local checkout_root
    # Remove the path component from the sentinel's relative path inside the checkout.
    # The sentinel symlink points to: <pin_dir>/claude/hooks/spec_id_enforcement.py
    # We need to strip "/claude/hooks/spec_id_enforcement.py" to get <pin_dir>.
    # Use parameter substitution: strip from '/claude/' onward.
    checkout_root="${target%%/claude/*}"
    if [[ -n "$checkout_root" && -d "$checkout_root/.git" ]]; then
      echo "$checkout_root"
      return
    fi
  fi
  # No sentinel or unresolvable — fall through to the default.
  echo "$ESCAPEMENT_PIN_DIR"
}

# The Claude PLUGIN is the sole owner of hook registration (escapement-ptzz).
#
# Previously this MERGED claude/settings.template.json's hooks into the live
# settings.json — while the plugin registered the same scripts via its own
# hooks.json. Claude Code does not dedupe, so 38 gates fired TWICE per matching
# tool call. The template now registers nothing; this converges the live settings
# by REMOVING the duplicates instead of adding them.
#
# Safe by construction: it prunes against the hooks the INSTALLED plugin actually
# registers, so it can never remove a gate the live plugin does not supply. The
# user's own hooks (not shipped by escapement) are preserved verbatim.
merge_settings() {
  local live="$CLAUDE_DIR/settings.json"
  local plugin_hooks=""
  local plugin_registry="$CLAUDE_DIR/plugins/installed_plugins.json"
  local plugin_install=""
  local plugin_entry=""

  # Use Claude's installed-plugin registry rather than scanning cache siblings.
  # Multiple historical cache versions may coexist; only installPath identifies
  # the plugin version Claude actually loads.
  if [[ -f "$plugin_registry" ]]; then
    if ! plugin_entry=$(jq -c '
      (.plugins // .) as $plugins
      | if ($plugins | type) != "object" then
          error("plugin registry must contain an object")
        else
          ($plugins["escapement@escapement"] // null)
          | if . == null then
              null
            elif type != "array" then
              error("escapement plugin registry entry must be an array")
            else
              ([.[] | select(type == "object" and .scope == "user")] | first // null)
            end
        end
    ' "$plugin_registry" 2>&1); then
      echo "ERROR: cannot migrate settings hooks: invalid plugin registry: $plugin_entry" >&2
      return 1
    fi
    if [[ "$plugin_entry" != "null" ]]; then
      if ! plugin_install=$(jq -er '
        .installPath | select(type == "string" and length > 0)
      ' <<<"$plugin_entry" 2>/dev/null); then
        echo "ERROR: cannot migrate settings hooks: user plugin entry has no valid installPath." >&2
        return 1
      fi
      plugin_hooks="$plugin_install/hooks/hooks.json"
      if [[ ! -f "$plugin_hooks" ]]; then
        echo "ERROR: cannot migrate settings hooks: registered plugin hooks not found: $plugin_hooks" >&2
        return 1
      fi
    fi
  fi

  if [[ -z "$plugin_hooks" ]]; then
    echo "    ⚠  escapement plugin not installed — cannot prune settings hooks."
    echo "       Install it first:  /plugin marketplace add alexander-vyh/escapement"
    echo "                          /plugin install escapement@escapement"
    return 0
  fi
  if [[ "$DRY_RUN" == true ]]; then
    python3 "$REPO_DIR/scripts/prune_settings_hooks.py" "$plugin_hooks" "$live" --dry-run 2>&1 \
      | sed 's/^/    /'
    return 0
  fi
  python3 "$REPO_DIR/scripts/prune_settings_hooks.py" "$plugin_hooks" "$live" 2>&1 \
    | sed 's/^/    /' || echo "    ⚠  settings prune failed — remove duplicate hooks manually."
}

# --- Execute ---
if [[ "$MODE" == "update" ]]; then
  # Refresh the pinned checkout only; existing symlinks already point into it.
  # B egk fix: update the dir the deployed symlinks ACTUALLY point into, not the
  # default dir (which may differ on CWS-era machines whose symlinks point into
  # .cws-pinned rather than .escapement-pinned).
  _effective_pin_dir="$(resolve_effective_pin_dir)"
  ensure_pinned_checkout "$_effective_pin_dir"
  # e9v.8: re-run the symlink plan + settings merge so NEW hooks in the refreshed
  # pin actually deploy (previously --update moved the pin but left new hooks
  # un-symlinked and unregistered).
  DEPLOY_SRC="$_effective_pin_dir"
  install_plan
  merge_settings
  echo
  echo "==> pinned checkout now at $ESCAPEMENT_PIN_REF; ~/.claude reflects the update."
elif [[ "$MODE" == "install" ]]; then
  if [[ "$DEV_MODE" == true ]]; then
    echo "==> --dev: symlinking the LIVE working tree (instant edits; not branch-safe)"
  else
    ensure_pinned_checkout
  fi
  install_plan
  merge_settings
  echo
  echo "==> next steps"
  echo "    1. The escapement PLUGIN owns hook registration; duplicate entries were"
  echo "       pruned from ~/.claude/settings.json (backup written). Your own hooks"
  echo "       are untouched. Review env blocks manually."
  echo "    2. Read claude/rules/*.md and edit to match your philosophy."
  echo "    3. Open Claude Code in any git repo to trigger bootstrap."
  echo "       Optional: set ESCAPEMENT_BOOTSTRAP_ROOTS=/path/a:/path/b to limit it."
  [[ "$DEV_MODE" == true ]] || echo "    NOTE: harness/hook edits go live after they reach main + './INSTALL.sh --update'."
  echo
  echo "==> verifying continuation-harness Stop gate wiring"
  verify_stop_gate_wired
  echo
  echo "    See README.md for full details."
else
  uninstall_plan
fi
