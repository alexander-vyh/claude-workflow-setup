#!/bin/bash
# plugin-update.sh — refresh the installed Escapement Claude plugin and
# converge legacy symlink deployments to plugin-only ownership.
#
# Usage:
#   ./scripts/plugin-update.sh
#   ./scripts/plugin-update.sh --dry-run
#
# The user-scope entry in ~/.claude/plugins/installed_plugins.json is the sole
# deployment authority. The updater preserves settings state across Claude's
# uninstall/reinstall cycle, removes only provenance-recognized legacy links,
# and maintains stable harness wrappers into the current versioned plugin cache.
#
# Fail-fast.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_DIR="$HOME/.claude"
SETTINGS="$CLAUDE_DIR/settings.json"
PLUGIN_ID="escapement@escapement"
INSTALLED="$CLAUDE_DIR/plugins/installed_plugins.json"
TRANSACTION_HELPER="$REPO_DIR/scripts/plugin-update-transaction.py"
WRAPPER_TARGET_HELPER="$REPO_DIR/scripts/plugin-wrapper-target.py"
TRANSACTION_JOURNAL="$CLAUDE_DIR/.plugin-update-transaction.json"
TRANSACTION_GUARD="$TRANSACTION_JOURNAL.commit-guard"
TRANSACTION_LOCK="$CLAUDE_DIR/harness/.continuation-supervisor-install.lock"
SUPERVISOR_LABEL="com.escapement.continuation-supervisor"
SUPERVISOR_MARKER="$CLAUDE_DIR/harness/continuation-supervisor-installed.json"
SUPERVISOR_PLIST="$HOME/Library/LaunchAgents/$SUPERVISOR_LABEL.plist"
SUPERVISOR_DOMAIN="gui/$UID"
supervisor_installer="$REPO_DIR/scripts/continuation-supervisor-install.sh"
supervisor_state_helper="$REPO_DIR/scripts/continuation-supervisor-state.py"

DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --help|-h) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

[[ -x "$TRANSACTION_HELPER" ]] || {
  echo "FATAL: plugin cutover transaction helper is missing: $TRANSACTION_HELPER" >&2
  exit 1
}
[[ -x "$WRAPPER_TARGET_HELPER" ]] || {
  echo "FATAL: plugin wrapper target helper is missing: $WRAPPER_TARGET_HELPER" >&2
  exit 1
}
[[ -x "$supervisor_installer" ]] || {
  echo "FATAL: continuation supervisor installer is missing: $supervisor_installer" >&2
  exit 1
}
[[ -x "$supervisor_state_helper" ]] || {
  echo "FATAL: continuation supervisor state helper is missing: $supervisor_state_helper" >&2
  exit 1
}

# Resolve only the installed user-scope plugin. Historical cache siblings and
# project-scope entries are not deployment authority.
resolve_install_path() {
  [[ -f "$INSTALLED" ]] || return 0
  python3 - "$INSTALLED" <<'PY'
import json
import os
import sys

try:
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
except Exception:
    sys.exit(0)

plugins = data.get("plugins", data)
if not isinstance(plugins, dict):
    sys.exit(0)
entries = plugins.get("escapement@escapement", [])
if not isinstance(entries, list):
    sys.exit(0)

for entry in entries:
    if not isinstance(entry, dict) or entry.get("scope") != "user":
        continue
    path = entry.get("installPath")
    if isinstance(path, str) and path and os.path.isdir(path):
        print(path)
        break
PY
}

validate_plugin_root_for_update() {
  local plugin_root="$1"
  local required
  for required in \
    harness/bin \
    harness/schemas \
    skills \
    agents \
    commands \
    rules \
    hooks \
    hooks/hooks.json
  do
    if [[ ! -e "$plugin_root/$required" ]]; then
      echo "FATAL: installed plugin is incomplete: $plugin_root/$required" >&2
      return 1
    fi
  done
}

validate_plugin_root() {
  local plugin_root="$1"
  local required
  validate_plugin_root_for_update "$plugin_root" || return 1
  for required in \
    hooks/magic_number_echo.py \
    hooks/oracle_reason_validation.py
  do
    if [[ ! -e "$plugin_root/$required" ]]; then
      echo "FATAL: installed plugin is incomplete: $plugin_root/$required" >&2
      return 1
    fi
  done
}

classify_managed_cache_wrapper_target() {
  python3 -B "$WRAPPER_TARGET_HELPER" "$1" "$2" "$3" "$4"
}

is_managed_wrapper_target() {
  local target="$1"
  local component="$2"
  local status
  local codex_cache="${CODEX_HOME:-$HOME/.codex}/plugins/cache/escapement/escapement"
  local claude_cache="$CLAUDE_DIR/plugins/cache/escapement/escapement"

  if classify_managed_cache_wrapper_target \
    "$target" "$component" "$codex_cache" versioned-cache
  then
    return 0
  else
    status=$?
    [[ "$status" -eq 1 ]] || return 1
  fi
  [[ "$target" == "$REPO_DIR/harness/$component" ]] && return 0
  if classify_managed_cache_wrapper_target \
    "$target" "$component" "$CLAUDE_DIR" pinned
  then
    return 0
  else
    status=$?
    [[ "$status" -eq 1 ]] || return 1
  fi
  if classify_managed_cache_wrapper_target \
    "$target" "$component" "$claude_cache" versioned-cache
  then
    return 0
  else
    status=$?
    [[ "$status" -eq 1 ]] || return 1
  fi
  return 1
}

# Validate both wrapper slots before Claude or filesystem state is changed.
validate_wrapper_slot() {
  local dest="$1"
  local component="$2"
  if [[ -L "$dest" ]]; then
    local target
    target="$(readlink "$dest")"
    if ! is_managed_wrapper_target "$target" "$component"; then
      echo "FATAL: refusing to replace unrelated harness wrapper: $dest -> $target" >&2
      return 1
    fi
  elif [[ -e "$dest" ]]; then
    echo "FATAL: refusing to replace non-symlink harness content: $dest" >&2
    return 1
  fi
}

validate_current_deployment() {
  command -v claude >/dev/null || {
    echo "FATAL: 'claude' CLI not on PATH" >&2
    return 1
  }
  [[ -f "$SETTINGS" ]] || {
    echo "FATAL: no settings.json at $SETTINGS" >&2
    return 1
  }
  [[ -f "$INSTALLED" ]] || {
    echo "FATAL: no valid user-scope $PLUGIN_ID installPath in $INSTALLED" >&2
    return 1
  }
  python3 -B "$TRANSACTION_HELPER" validate-authority \
    --journal "$TRANSACTION_JOURNAL" \
    --path "$SETTINGS" \
    --path "$INSTALLED"
  current_path="$(resolve_install_path)"
  if [[ -z "$current_path" ]]; then
    echo "FATAL: no valid user-scope $PLUGIN_ID installPath in $INSTALLED" >&2
    return 1
  fi
  validate_plugin_root_for_update "$current_path"
  validate_wrapper_slot "$CLAUDE_DIR/harness/bin" bin
  validate_wrapper_slot "$CLAUDE_DIR/harness/schemas" schemas
}

# A clean invalid deployment is rejected before the shared lock creates any
# compatibility state. Interrupted transactions acquire the lock first because
# their durable journal, not the half-cut-over live files, is authoritative.
if [[ "$DRY_RUN" == true ]]; then
  validate_current_deployment
elif ! python3 -B "$supervisor_state_helper" lock-held \
  --path "$TRANSACTION_LOCK" \
  --fd "${ESCAPEMENT_SUPERVISOR_LOCK_FD:--1}"
then
  if [[ ! -e "$TRANSACTION_JOURNAL" && ! -e "$TRANSACTION_GUARD" ]]; then
    validate_current_deployment
  fi
  exec python3 -B "$supervisor_state_helper" lock-run \
    --path "$TRANSACTION_LOCK" \
    bash "$0" "$@"
fi

if [[ "$DRY_RUN" != true \
  && ( -e "$TRANSACTION_JOURNAL" || -e "$TRANSACTION_GUARD" ) ]]
then
  if ! python3 -B "$TRANSACTION_HELPER" recover \
    --journal "$TRANSACTION_JOURNAL" \
    --supervisor-installer "$supervisor_installer"
  then
    echo "FATAL: prior plugin cutover could not be recovered" >&2
    exit 1
  fi
  echo "    recovered interrupted plugin cutover"
fi
if [[ "$DRY_RUN" != true ]]; then
  validate_current_deployment
fi

pre_enabled="$(python3 - "$SETTINGS" "$PLUGIN_ID" <<'PY'
import json
import sys

with open(sys.argv[1]) as fh:
    data = json.load(fh)
enabled = data.get("enabledPlugins", {})
if sys.argv[2] not in enabled:
    print("__MISSING__")
else:
    print("true" if enabled[sys.argv[2]] else "false")
PY
)"
pre_model="$(python3 - "$SETTINGS" <<'PY'
import json
import sys

with open(sys.argv[1]) as fh:
    print(json.load(fh).get("model") or "")
PY
)"

fail_after_refresh() {
  local message="$1"
  local rollback_status
  trap - ERR
  set +e
  python3 -B "$TRANSACTION_HELPER" rollback \
    --journal "$TRANSACTION_JOURNAL" \
    --supervisor-installer "$supervisor_installer"
  rollback_status=$?
  set -e
  case "$rollback_status" in
    0)
      echo "FATAL: $message; prior deployment generation restored" >&2
      exit 1
      ;;
    3)
      echo "    plugin update committed; interrupted cleanup deferred"
      exit 0
      ;;
    *)
      echo "FATAL: $message; prior deployment generation rollback FAILED" >&2
      exit 2
      ;;
  esac
}

echo "==> escapement plugin-update"
echo "    pre-state: enabled=$pre_enabled  model='${pre_model:-<unset>}'"
echo "    installed: $current_path"

if [[ "$DRY_RUN" == true ]]; then
  echo "    [dry-run] begin recoverable plugin cutover transaction"
  echo "    [dry-run] claude plugin update $PLUGIN_ID"
  new_path="$current_path"
else
  set +e
  launchctl print "$SUPERVISOR_DOMAIN/$SUPERVISOR_LABEL" >/dev/null 2>&1
  supervisor_status=$?
  set -e
  case "$supervisor_status" in
    0) supervisor_loaded=true ;;
    113) supervisor_loaded=false ;;
    *)
      echo "FATAL: could not determine prior supervisor runtime state (exit $supervisor_status)" >&2
      exit "$supervisor_status"
      ;;
  esac
  if [[ "$supervisor_loaded" == true ]]; then
    if [[ ! -e "$SUPERVISOR_PLIST" && ! -L "$SUPERVISOR_PLIST" ]]; then
      echo "FATAL: loaded continuation supervisor has no trusted plist" >&2
      exit 1
    fi
    python3 -B "$supervisor_state_helper" validate-file --path "$SUPERVISOR_PLIST"
  fi
  python3 -B "$TRANSACTION_HELPER" begin \
    --journal "$TRANSACTION_JOURNAL" \
    --settings "$SETTINGS" \
    --registry "$INSTALLED" \
    --wrapper "$CLAUDE_DIR/harness/bin" \
    --wrapper "$CLAUDE_DIR/harness/schemas" \
    --supervisor-marker "$SUPERVISOR_MARKER" \
    --supervisor-plist "$SUPERVISOR_PLIST" \
    --supervisor-loaded "$supervisor_loaded"
  trap 'fail_after_refresh "plugin update transaction failed"' ERR
  atomic_symlink() {
    local target="$1"
    local dest="$2"
    local parent staging temporary
    parent="$(dirname "$dest")"
    mkdir -p "$parent"
    staging="$(mktemp -d "$parent/.escapement-link.XXXXXX")"
    temporary="$staging/link"
    if ! ln -s "$target" "$temporary"; then
      rmdir "$staging" 2>/dev/null || true
      return 1
    fi
    if ! python3 - "$temporary" "$dest" <<'PY'
import os
import sys

os.replace(sys.argv[1], sys.argv[2])
descriptor = os.open(os.path.dirname(sys.argv[2]), os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
    then
      rm -f "$temporary"
      rmdir "$staging" 2>/dev/null || true
      return 1
    fi
    rmdir "$staging"
  }
  if ! claude plugin update "$PLUGIN_ID" >/dev/null; then
    fail_after_refresh "Claude plugin update failed"
  fi

  new_path="$(resolve_install_path)"
  if [[ -z "$new_path" ]]; then
    fail_after_refresh "could not resolve user-scope plugin installPath after update"
  fi
  if ! validate_plugin_root "$new_path"; then
    fail_after_refresh "updated plugin package is incomplete"
  fi
fi

# Restore settings state that Claude's update may change.
if [[ "$DRY_RUN" != true ]]; then
  if [[ "$pre_enabled" == "false" ]]; then
    if ! claude plugin disable "$PLUGIN_ID" >/dev/null; then
      fail_after_refresh "could not restore disabled plugin state"
    fi
  elif [[ "$pre_enabled" == "__MISSING__" ]]; then
    if ! python3 - "$SETTINGS" "$PLUGIN_ID" <<'PY'
import json
import sys

p, plugin_id = sys.argv[1], sys.argv[2]
with open(p) as fh:
    data = json.load(fh)
enabled = data.get("enabledPlugins")
if isinstance(enabled, dict):
    enabled.pop(plugin_id, None)
with open(p, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
    then
      fail_after_refresh "could not restore missing enabledPlugins state"
    fi
  fi

  now_model="$(python3 - "$SETTINGS" <<'PY'
import json
import sys

with open(sys.argv[1]) as fh:
    print(json.load(fh).get("model") or "")
PY
)"
  if [[ "$now_model" != "$pre_model" ]]; then
    if ! python3 - "$SETTINGS" "$pre_model" <<'PY'
import json
import sys

p, model = sys.argv[1], sys.argv[2]
with open(p) as fh:
    data = json.load(fh)
if model:
    data["model"] = model
else:
    data.pop("model", None)
with open(p, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
    then
      fail_after_refresh "could not restore model setting"
    fi
    echo "    restored model key: '${pre_model:-<unset>}' (reinstall had set '${now_model:-<unset>}')"
  fi
fi

# The plugin is the sole owner of hook registration. Prune before touching any
# wrapper/link/mode so a pruner failure can roll the refresh transaction back
# without undoing filesystem migration.
pruner="$REPO_DIR/scripts/prune_settings_hooks.py"
plugin_hooks="$new_path/hooks/hooks.json"
if [[ "$DRY_RUN" == true ]]; then
  python3 "$pruner" "$plugin_hooks" "$SETTINGS" --dry-run 2>&1 | sed 's/^/    /'
elif ! python3 "$pruner" "$plugin_hooks" "$SETTINGS" 2>&1 | sed 's/^/    /'; then
  fail_after_refresh "settings hook pruning failed"
fi

# Plugin caches preserve Git's non-executable mode for rendered harness files,
# while verify/workflow_status are documented as bare commands.
if [[ "$DRY_RUN" == true ]]; then
  echo "    [dry-run] chmod +x $new_path/harness/bin/*"
else
  python3 -B "$TRANSACTION_HELPER" record-modes \
    --journal "$TRANSACTION_JOURNAL" \
    --root "$new_path/harness/bin"
  chmod +x "$new_path"/harness/bin/*
  echo "    restored +x on vendored harness executables"
fi

replace_wrapper() {
  local component="$1"
  local dest="$CLAUDE_DIR/harness/$component"
  local target="$new_path/harness/$component"
  if [[ "$DRY_RUN" == true ]]; then
    echo "    [dry-run] ln -sfn $target $dest"
    return
  fi
  if ! atomic_symlink "$target" "$dest"; then
    fail_after_refresh "could not atomically replace harness wrapper $dest"
  fi
  echo "    wrapper: $dest -> $target"
}

remove_plugin_owned_legacy_link() {
  local dest="$1"
  local surface="$2"
  local relative="${dest#"$CLAUDE_DIR/$surface/"}"
  [[ "$relative" != "$dest" ]] || return 0
  [[ -e "$new_path/$surface/$relative" || -e "$REPO_DIR/claude/$surface/$relative" ]] || return 0
  [[ -L "$dest" ]] || return 0
  local target
  target="$(readlink "$dest")"
  case "$target" in
    "$REPO_DIR/claude/$surface/$relative" | \
    "$CLAUDE_DIR"/.escapement-pinned*/claude/"$surface/$relative" | \
    "$CLAUDE_DIR"/.cws-pinned*/claude/"$surface/$relative")
      ;;
    *) return 0 ;;
  esac
  if [[ "$DRY_RUN" == true ]]; then
    echo "    [dry-run] remove legacy link $dest -> $target"
  else
    rm -f "$dest"
    echo "    removed legacy link: $dest"
  fi
}

remove_plugin_owned_bootstrap_link() {
  local dest="$CLAUDE_DIR/project-bootstrap.sh"
  [[ -e "$new_path/hooks/project-bootstrap.sh" && -L "$dest" ]] || return 0
  local target
  target="$(readlink "$dest")"
  case "$target" in
    "$REPO_DIR/scripts/project-bootstrap.sh" | \
    "$CLAUDE_DIR"/.escapement-pinned*/scripts/project-bootstrap.sh | \
    "$CLAUDE_DIR"/.cws-pinned*/scripts/project-bootstrap.sh)
      ;;
    *) return 0 ;;
  esac
  if [[ "$DRY_RUN" == true ]]; then
    echo "    [dry-run] remove legacy link $dest -> $target"
  else
    rm -f "$dest"
    echo "    removed legacy link: $dest"
  fi
}

replace_wrapper bin
replace_wrapper schemas

# The stable harness wrapper is the launchd execution authority. Install only
# after that wrapper and every plugin-owned surface have converged; dry-run uses
# the same installer planner without touching launchd or HOME.
if [[ "$DRY_RUN" == true ]]; then
  "$supervisor_installer" --dry-run
elif ! "$supervisor_installer"; then
  fail_after_refresh "continuation supervisor installation failed"
fi

if [[ "$DRY_RUN" != true ]]; then
  python3 -B "$TRANSACTION_HELPER" commit --journal "$TRANSACTION_JOURNAL"
  trap - ERR
  if ! python3 -B "$TRANSACTION_HELPER" recover \
    --journal "$TRANSACTION_JOURNAL" \
    --supervisor-installer "$supervisor_installer"
  then
    echo "WARNING: plugin update committed; cleanup deferred" >&2
  fi
fi

# Native plugin discovery owns these surfaces. Defer destructive legacy-link
# cleanup until the new supervisor and stable wrappers are successfully live,
# so a failed cutover needs no broad filesystem reconstruction.
for surface in skills agents commands rules hooks; do
  root="$CLAUDE_DIR/$surface"
  [[ -d "$root" ]] || continue
  while IFS= read -r legacy_link; do
    remove_plugin_owned_legacy_link "$legacy_link" "$surface"
  done < <(find "$root" -type l -print 2>/dev/null)
done
remove_plugin_owned_bootstrap_link

if [[ "$DRY_RUN" != true ]]; then
  canary="$new_path/harness/bin/stop_hook.py"
  repo_canary="$REPO_DIR/plugins/escapement-claude/harness/bin/stop_hook.py"
  if [[ -f "$canary" && -f "$repo_canary" ]]; then
    if diff -q "$canary" "$repo_canary" >/dev/null; then
      echo "==> OK: plugin cache refreshed to match this checkout."
    else
      echo "WARN: plugin cache differs from THIS checkout's plugin source." >&2
      echo "      (expected when this checkout is not deployed main)" >&2
    fi
  fi
fi

echo "==> done."
