#!/bin/bash
# INSTALL.sh — Compatibility installer for non-plugin Escapement auxiliaries.
#
# Usage:
#   ./INSTALL.sh              # refresh Claude plugin + install pinned auxiliaries
#   ./INSTALL.sh --dev        # refresh plugin + link auxiliaries to this checkout
#   ./INSTALL.sh --update     # refresh plugin + pinned auxiliary checkout
#   ./INSTALL.sh --uninstall  # remove auxiliary symlinks (plugin is untouched)
#   ./INSTALL.sh --dry-run    # show what would happen, change nothing
#
# The native Claude plugin is the sole owner of hooks, skills, agents, commands,
# rules, bootstrap, and harness code. This script retains only assets no plugin
# can install: ~/.claude/bin plus Beads formulas/status. Those auxiliaries use a
# branch-safe pinned checkout by default.
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

# B egk: resolve the EFFECTIVE pin dir for --update mode.
# If the caller explicitly set ESCAPEMENT_PIN_DIR, use that (B2 override wins).
# Otherwise read the sentinel symlink to find which checkout is actually live.
# Falls back to the default ESCAPEMENT_PIN_DIR if no sentinel exists (B3 fresh).
#
# Defined before the pre-flight banner on purpose: bash resolves functions at
# call time, so the banner can only report the effective pin if this is already
# in scope. It used to sit further down, which left the banner printing the
# unresolved default — a path the run never touched.
resolve_effective_pin_dir() {
  if [[ "$_PIN_DIR_EXPLICIT" == "set" ]]; then
    echo "$ESCAPEMENT_PIN_DIR"
    return
  fi
  # claude/bin is retained as the pinned auxiliary sentinel after plugin cutover.
  local sentinel="$CLAUDE_DIR/bin"
  if [[ -L "$sentinel" ]]; then
    local target
    target="$(readlink "$sentinel")"
    local checkout_root
    checkout_root="${target%%/claude/*}"
    if [[ -n "$checkout_root" && -d "$checkout_root/.git" ]]; then
      echo "$checkout_root"
      return
    fi
  fi
  # No sentinel or unresolvable — fall through to the default.
  echo "$ESCAPEMENT_PIN_DIR"
}

# --- Pre-flight ---
echo "==> Escapement installer"
echo "    repo:   $REPO_DIR"
echo "    claude: $CLAUDE_DIR"
echo "    beads:  $BEADS_DIR"
echo "    deploy: $([ "$DEV_MODE" == true ] && echo "live working tree (--dev)" || echo "pinned checkout ($(resolve_effective_pin_dir) @ $ESCAPEMENT_PIN_REF)")"
echo "    mode:   $MODE$([ "$DRY_RUN" == true ] && echo ' (dry-run)')"
echo

for tool in openspec bd direnv python3 jq git bash; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "WARN: '$tool' not found on PATH — some features will not work"
  fi
done

# --- Auxiliary symlink plan: (source_relative_to_repo, dest_absolute) ---
declare -a PLAN=(
  # Invokable auxiliary scripts. The plugin does not package this directory.
  "claude/bin|$CLAUDE_DIR/bin"

  # Beads is a third tool outside both host plugin systems.
  "beads/formulas/mol-feature.formula.json|$BEADS_DIR/formulas/mol-feature.formula.json"
  "beads/formulas/mol-rapid.formula.json|$BEADS_DIR/formulas/mol-rapid.formula.json"
  "beads/mol-status.sh|$BEADS_DIR/mol-status.sh"
)

is_managed_aux_target() {
  local target="$1"
  local src_rel="$2"
  case "$target" in
    "$REPO_DIR/$src_rel" | \
    "$CLAUDE_DIR"/.escapement-pinned*/"$src_rel" | \
    "$CLAUDE_DIR"/.cws-pinned*/"$src_rel")
      return 0
      ;;
  esac
  return 1
}

validate_plan_slots() {
  local entry src_rel dest target
  for entry in "${PLAN[@]}"; do
    src_rel="${entry%|*}"
    dest="${entry#*|}"
    if [[ -L "$dest" ]]; then
      target="$(readlink "$dest")"
      if ! is_managed_aux_target "$target" "$src_rel"; then
        echo "FATAL: refusing to replace unrelated auxiliary link: $dest -> $target" >&2
        return 1
      fi
    fi
  done
}

prepare_auxiliary_dirs() {
  run mkdir -p "$CLAUDE_DIR" "$CLAUDE_DIR/harness/threads" "$BEADS_DIR/formulas"
}

backup_if_exists() {
  local dest="$1"
  local src_rel="$2"
  if [[ -L "$dest" ]]; then
    local target
    target="$(readlink "$dest")"
    if ! is_managed_aux_target "$target" "$src_rel"; then
      echo "FATAL: refusing to replace unrelated auxiliary link: $dest -> $target" >&2
      return 1
    fi
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

    backup_if_exists "$dest" "$src_rel"
    run ln -s "$src_abs" "$dest"
    installed=$((installed + 1))
    echo "    link:   $dest -> $src_rel"
  done
  echo
  echo "==> installed $installed symlinks"
}

uninstall_plan() {
  local removed=0
  validate_plan_slots
  for entry in "${PLAN[@]}"; do
    local src_rel="${entry%|*}"
    local dest="${entry#*|}"
    if [[ -L "$dest" ]]; then
      is_managed_aux_target "$(readlink "$dest")" "$src_rel" || continue
      run rm "$dest"
      removed=$((removed + 1))
      echo "    unlink: $dest"
    fi
  done
  echo
  echo "==> removed $removed symlinks"
  echo "    (any .backup-* files are left alone — rename manually to restore)"
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

converge_plugin() {
  echo "==> refreshing native Claude plugin before auxiliary deployment"
  if [[ "$DRY_RUN" == true ]]; then
    bash "$REPO_DIR/scripts/plugin-update.sh" --dry-run
  else
    bash "$REPO_DIR/scripts/plugin-update.sh"
  fi
  echo
}

# --- Execute ---
if [[ "$MODE" == "update" ]]; then
  # Plugin authority is validated and converged before the auxiliary checkout is
  # touched, so an unresolved install cannot partially deploy.
  validate_plan_slots
  converge_plugin
  _effective_pin_dir="$(resolve_effective_pin_dir)"
  ensure_pinned_checkout "$_effective_pin_dir"
  DEPLOY_SRC="$_effective_pin_dir"
  prepare_auxiliary_dirs
  install_plan
  echo
  echo "==> native plugin and pinned auxiliary checkout are current."
elif [[ "$MODE" == "install" ]]; then
  validate_plan_slots
  converge_plugin
  if [[ "$DEV_MODE" == true ]]; then
    echo "==> --dev: auxiliary links use the LIVE working tree"
  else
    ensure_pinned_checkout
  fi
  prepare_auxiliary_dirs
  install_plan
  echo
  echo "==> next steps"
  echo "    1. Restart Claude Code so it loads the refreshed versioned plugin root."
  echo "    2. Open Claude Code in a git repo and verify SessionStart behavior."
  echo "    The native plugin owns workflow surfaces; this installer owns only"
  echo "    ~/.claude/bin and Beads auxiliary assets."
  echo
  echo "    See README.md for full details."
else
  uninstall_plan
fi
