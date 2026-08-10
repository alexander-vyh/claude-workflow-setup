#!/bin/bash
# Install the deterministic continuation reconciler as a per-user LaunchAgent.
#
# Usage:
#   continuation-supervisor-install.sh
#   continuation-supervisor-install.sh --dry-run
#   continuation-supervisor-install.sh --uninstall

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_HELPER="$SCRIPT_DIR/continuation-supervisor-state.py"
LABEL="com.escapement.continuation-supervisor"
HARNESS_HOME="$HOME/.claude/harness"
WAKER="$HARNESS_HOME/bin/wakeup_waker.py"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PENDING_PLIST="$HOME/Library/LaunchAgents/.$LABEL.pending.plist"
INSTALL_MARKER="$HARNESS_HOME/continuation-supervisor-installed.json"
LOCK_PATH="$HARNESS_HOME/.continuation-supervisor-install.lock"
LOG_DIR="$HARNESS_HOME/logs"
STDOUT_LOG="$LOG_DIR/continuation-supervisor.stdout.log"
STDERR_LOG="$LOG_DIR/continuation-supervisor.stderr.log"
INTERVAL=60
DOMAIN="gui/$UID"

MODE="install"
for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    --uninstall) MODE="uninstall" ;;
    --quiesce) MODE="quiesce" ;;
    --restore-loaded) MODE="restore-loaded" ;;
    --restore-unloaded) MODE="restore-unloaded" ;;
    --help|-h)
      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "FATAL: unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "==> unsupported host: continuation supervisor installation is a no-op"
  exit 0
fi

[[ -x "$STATE_HELPER" ]] || {
  echo "FATAL: continuation supervisor state helper is missing: $STATE_HELPER" >&2
  exit 1
}
if [[ "$MODE" == "install" && ! -x "$WAKER" ]]
then
  echo "FATAL: stable installed waker is missing or not executable: $WAKER" >&2
  exit 1
fi

# Metadata trust is checked before launchctl mutation. Uninstall deliberately
# remains available even when the scheduled state it is meant to stop is bad.
if [[ "$MODE" == "install" || "$MODE" == "dry-run" ]]; then
  python3 -B "$STATE_HELPER" validate \
    --threads "$HARNESS_HOME/threads" \
    --quarantine "$HARNESS_HOME/quarantine"
fi

if [[ "$MODE" == "dry-run" ]]; then
  echo "    [dry-run] launchd $LABEL: $WAKER --fire"
  echo "    [dry-run] RunAtLoad=true StartInterval=$INTERVAL plist=$PLIST"
  exit 0
fi

# One inherited, inode-validated flock serializes install, uninstall, and the
# encompassing plugin cutover. Caller-controlled environment booleans are not
# accepted as proof that the lifecycle lock is held.
if ! python3 -B "$STATE_HELPER" lock-held \
  --path "$LOCK_PATH" \
  --fd "${ESCAPEMENT_SUPERVISOR_LOCK_FD:--1}"
then
  exec python3 -B "$STATE_HELPER" lock-run \
    --path "$LOCK_PATH" \
    bash "$0" "$@"
fi

bootout_job() {
  local status
  set +e
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1
  status=$?
  set -e
  if [[ "$status" -ne 0 && "$status" -ne 3 ]]; then
    echo "FATAL: launchctl could not quiesce $LABEL (exit $status)" >&2
    return "$status"
  fi
  return "$status"
}

fsync_parent() {
  python3 -B "$STATE_HELPER" fsync-parent --path "$1"
}

if [[ "$MODE" == "uninstall" ]]; then
  if bootout_job; then
    :
  else
    status=$?
    [[ "$status" -eq 3 ]] || exit "$status"
  fi
  rm -f "$PLIST" "$PENDING_PLIST" "$INSTALL_MARKER"
  [[ ! -d "$(dirname "$PLIST")" ]] || fsync_parent "$PLIST"
  [[ ! -d "$(dirname "$INSTALL_MARKER")" ]] || fsync_parent "$INSTALL_MARKER"
  echo "==> uninstalled $LABEL"
  exit 0
fi

if [[ "$MODE" == "quiesce" ]]; then
  if bootout_job; then
    :
  else
    status=$?
    [[ "$status" -eq 3 ]] || exit "$status"
  fi
  echo "==> quiesced $LABEL"
  exit 0
fi

if [[ "$MODE" == "restore-loaded" || "$MODE" == "restore-unloaded" ]]; then
  set +e
  launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1
  runtime_status=$?
  set -e
  [[ "$runtime_status" -eq 0 || "$runtime_status" -eq 113 ]] || {
    echo "FATAL: prior supervisor runtime state is unresolved (exit $runtime_status)" >&2
    exit "$runtime_status"
  }
  if [[ "$MODE" == "restore-loaded" ]]; then
    if [[ -e "$PLIST" || -L "$PLIST" ]]; then
      python3 -B "$STATE_HELPER" validate-file --path "$PLIST"
      if [[ "$runtime_status" -eq 0 ]]; then
        if bootout_job; then
          :
        else
          status=$?
          [[ "$status" -eq 3 ]] || exit "$status"
        fi
      fi
      if ! launchctl bootstrap "$DOMAIN" "$PLIST"; then
        echo "FATAL: prior loaded supervisor could not be restored" >&2
        exit 1
      fi
    elif [[ "$runtime_status" -ne 0 ]]; then
      echo "FATAL: prior loaded supervisor has neither a live job nor a trusted plist" >&2
      exit 1
    fi
  elif [[ "$runtime_status" -eq 0 ]]; then
    if bootout_job; then
      :
    else
      status=$?
      [[ "$status" -eq 3 ]] || exit "$status"
    fi
  fi
  echo "==> restored $LABEL runtime: ${MODE#restore-}"
  exit 0
fi

marker_status="$(
  python3 -B "$STATE_HELPER" marker-status --path "$INSTALL_MARKER" --label "$LABEL"
)"
first_install=0
[[ "$marker_status" == "valid" ]] || first_install=1
marker_backup=""
if [[ "$marker_status" == "valid" ]]; then
  marker_backup="$(python3 -B "$STATE_HELPER" backup --source "$INSTALL_MARKER")"
fi

promote_file() {
  python3 -B "$STATE_HELPER" promote --source "$1" --destination "$2"
}

# Snapshot the exact pre-install runtime and filesystem authority before any
# quiesce. A loaded job without a trusted plist cannot be reconstructed, so it
# must fail before the installer touches launchd or plugin state.
set +e
launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1
runtime_status=$?
set -e
case "$runtime_status" in
  0) was_loaded=1 ;;
  113) was_loaded=0 ;;
  *)
    echo "FATAL: prior supervisor runtime state is unresolved (exit $runtime_status)" >&2
    exit "$runtime_status"
    ;;
esac

backup=""
if [[ "$was_loaded" -eq 1 ]]; then
  if [[ ! -e "$PLIST" && ! -L "$PLIST" ]]; then
    echo "FATAL: loaded supervisor has no trusted plist to restore" >&2
    exit 1
  fi
  python3 -B "$STATE_HELPER" validate-file --path "$PLIST"
fi
if [[ -e "$PLIST" || -L "$PLIST" ]]; then
  python3 -B "$STATE_HELPER" validate-file --path "$PLIST"
  backup="$(python3 -B "$STATE_HELPER" backup --source "$PLIST")"
fi

restore_previous() {
  local status=0
  if bootout_job; then
    :
  else
    status=$?
    [[ "$status" -eq 3 ]] || return "$status"
  fi
  rm -f "$PENDING_PLIST"
  if [[ -n "$backup" && -f "$backup" ]]; then
    promote_file "$backup" "$PLIST" || return $?
  else
    rm -f "$PLIST"
    [[ ! -d "$(dirname "$PLIST")" ]] || fsync_parent "$PLIST"
  fi
  if [[ -n "$marker_backup" && -f "$marker_backup" ]]; then
    promote_file "$marker_backup" "$INSTALL_MARKER" || return $?
  else
    rm -f "$INSTALL_MARKER"
    [[ ! -d "$(dirname "$INSTALL_MARKER")" ]] || fsync_parent "$INSTALL_MARKER"
  fi
  if [[ "$was_loaded" -eq 1 ]]; then
    if ! launchctl bootstrap "$DOMAIN" "$PLIST" >/dev/null 2>&1; then
      echo "FATAL: prior loaded supervisor could not be restored" >&2
      return 1
    fi
  fi
}

rollback_armed=1
rollback_on_exit() {
  local status=$?
  local restore_status=0
  trap - EXIT
  if [[ "$status" -ne 0 && "$rollback_armed" -eq 1 ]]; then
    restore_previous || restore_status=$?
    if [[ "$restore_status" -ne 0 ]]; then
      echo "FATAL: supervisor installation failed and prior state could not be restored" >&2
      exit "$restore_status"
    fi
  fi
  exit "$status"
}
trap rollback_on_exit EXIT

# Quiesce before reading active schedule contents. Exit 3 is the fake/real
# launchctl absent result used as a proven-safe no-op; other failures abort.
pre_quiesced=0
if [[ -d "$HARNESS_HOME/threads" ]]; then
  if [[ "$was_loaded" -eq 1 ]]; then
    if bootout_job; then
      :
    else
      status=$?
      [[ "$status" -eq 3 ]] || exit "$status"
      was_loaded=0
    fi
  fi
  pre_quiesced=1
fi

migration_args=(
  migrate
  --threads "$HARNESS_HOME/threads"
  --quarantine "$HARNESS_HOME/quarantine"
)
[[ "$first_install" -eq 0 ]] || migration_args+=(--first-install)
python3 -B "$STATE_HELPER" "${migration_args[@]}"

if [[ ! -e "$HARNESS_HOME/threads" && ! -L "$HARNESS_HOME/threads" ]]; then
  mkdir -m 700 "$HARNESS_HOME/threads"
  fsync_parent "$HARNESS_HOME/threads"
fi

mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
python3 -B "$STATE_HELPER" write-plist \
  --destination "$PENDING_PLIST" \
  --label "$LABEL" \
  --waker "$WAKER" \
  --home "$HOME" \
  --path-value "${PATH:-/usr/bin:/bin}" \
  --harness-home "$HARNESS_HOME" \
  --stdout-log "$STDOUT_LOG" \
  --stderr-log "$STDERR_LOG" \
  --interval "$INTERVAL"

# With no schedule tree, bootstrapping the pending candidate first is a
# non-destructive loaded-state probe: success loads it; exit 72 means the old
# job is still loaded and must be quiesced before promotion.
loaded_candidate=0
if [[ "$pre_quiesced" -eq 0 ]]; then
  set +e
  launchctl bootstrap "$DOMAIN" "$PENDING_PLIST"
  status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    loaded_candidate=1
  elif [[ "$status" -eq 72 ]]; then
    if bootout_job; then
      :
    else
      status=$?
      if [[ "$status" -eq 3 ]]; then
        was_loaded=0
      else
        exit "$status"
      fi
    fi
  else
    echo "FATAL: launchctl could not load $LABEL" >&2
    exit "$status"
  fi
fi

if [[ "$loaded_candidate" -eq 1 ]]; then
  if ! promote_file "$PENDING_PLIST" "$PLIST"; then
    echo "FATAL: loaded candidate could not be promoted to the stable plist" >&2
    exit 1
  fi
else
  if ! promote_file "$PENDING_PLIST" "$PLIST"; then
    echo "FATAL: candidate LaunchAgent could not be installed" >&2
    exit 1
  fi
  if ! launchctl bootstrap "$DOMAIN" "$PLIST"; then
    echo "FATAL: launchctl could not load $LABEL" >&2
    exit 1
  fi
fi
if ! python3 -B "$STATE_HELPER" write-marker \
  --path "$INSTALL_MARKER" \
  --label "$LABEL"
then
  echo "FATAL: installed supervisor marker could not be persisted" >&2
  exit 1
fi
rollback_armed=0
trap - EXIT
rm -f "$backup" "$marker_backup"
echo "==> installed $LABEL"
