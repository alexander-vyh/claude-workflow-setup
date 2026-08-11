#!/usr/bin/env bash
# Behavioral installation oracle for the continuation supervisor.
#
# This test never touches the caller's real HOME or launchd domain. A recording
# launchctl replacement parses the installed plist and executes its advertised
# ProgramArguments, so a plist-only implementation cannot pass.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$REPO/scripts/continuation-supervisor-install.sh"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
BIN="$ROOT/bin"
mkdir -p "$BIN"

fail=0
ok()  { printf '  ok: %s\n' "$*"; }
bad() { printf '  FAIL: %s\n' "$*"; fail=1; }

tree_manifest() {
  python3 - "$1" <<'PY'
import base64
import json
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
entries = []


def visit(path):
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
    elif stat.S_ISREG(metadata.st_mode):
        kind = "file"
    elif stat.S_ISLNK(metadata.st_mode):
        kind = "symlink"
    else:
        kind = "other"
    record = {
        "path": "." if path == root else str(path.relative_to(root)),
        "type": kind,
        "mode": f"{mode:04o}",
        "inode": metadata.st_ino,
        "mtime_ns": metadata.st_mtime_ns,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "link_target": os.readlink(path) if kind == "symlink" else None,
        "content": (
            base64.b64encode(path.read_bytes()).decode("ascii")
            if kind == "file"
            else None
        ),
    }
    entries.append(record)
    if kind == "directory":
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            visit(child)


visit(root)
print(json.dumps(entries, sort_keys=True, separators=(",", ":")))
PY
}

cat > "$BIN/uname" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "${FAKE_UNAME:-Darwin}"
STUB

cat > "$BIN/launchctl" <<'STUB'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "$LAUNCHCTL_LOG"
STATE="${LAUNCHCTL_STATE:-$HOME/launchctl.loaded}"
mkdir -p "$(dirname "$STATE")"
touch "$STATE"

label_from_plist() {
  python3 - "$1" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as fh:
    print(plistlib.load(fh)["Label"])
PY
}

remove_loaded() {
  local label="$1"
  grep -Fxq "$label" "$STATE" || return 3
  grep -Fvx "$label" "$STATE" > "$STATE.next" || true
  mv -f "$STATE.next" "$STATE"
}

case "${1:-}" in
  bootout)
    remove_loaded "${2##*/}"
    exit $?
    ;;
  unload)
    label="$(label_from_plist "${2:-}")" || exit 3
    remove_loaded "$label"
    exit $?
    ;;
  bootstrap)
    plist="${3:-}"
    ;;
  load)
    plist="${@: -1}"
    ;;
  *)
    exit 0
    ;;
esac
label="$(label_from_plist "$plist")" || exit 4
if grep -Fxq "$label" "$STATE"; then
  echo "duplicate loaded label: $label" >&2
  exit 72
fi
if [ "${LAUNCHCTL_BOOTSTRAP_FAIL:-0}" = 1 ]; then
  echo "injected launchctl load failure: $label" >&2
  exit 75
fi
printf '%s\n' "$label" >> "$STATE"
python3 - "$plist" <<'PY'
import datetime as dt
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

with open(sys.argv[1], "rb") as fh:
    job = plistlib.load(fh)

if job.get("RunAtLoad") is not True:
    raise SystemExit("launchctl fixture refuses a job without RunAtLoad")
if os.environ.get("ASSERT_SAFE_SCHEDULES") == "1":
    unsafe_marker = Path(os.environ["HOME"]) / "unsafe-load-attempted"

    def unsafe(message):
        unsafe_marker.write_text(message + "\n", encoding="utf-8")
        raise SystemExit(message)

    now = dt.datetime.now(dt.timezone.utc)
    for scheduled in (Path(os.environ["HOME"]) / ".claude" / "harness" / "threads").glob(
        "*/scheduled.json"
    ):
        try:
            entries = json.loads(scheduled.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            unsafe(f"launch refused malformed active schedule: {scheduled}: {exc}")
        if not isinstance(entries, list):
            unsafe(f"launch refused non-list active schedule: {scheduled}")
        for entry in entries:
            if not isinstance(entry, dict):
                unsafe(f"launch refused malformed active entry: {scheduled}")
            raw = entry.get("wake_at")
            try:
                wake_at = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError) as exc:
                unsafe(f"launch refused unparseable active wake: {scheduled}: {exc}")
            if wake_at.tzinfo is None:
                wake_at = wake_at.replace(tzinfo=dt.timezone.utc)
            if wake_at <= now:
                unsafe(f"launch refused overdue active wake: {scheduled}")
argv = job.get("ProgramArguments")
if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
    raise SystemExit("invalid ProgramArguments")

environment = {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"]}
environment.update(job.get("EnvironmentVariables") or {})
if os.environ.get("ASSERT_SAFE_SCHEDULES") == "1":
    environment["ASSERT_SAFE_SCHEDULES"] = "1"
stdout_path = job.get("StandardOutPath")
stderr_path = job.get("StandardErrorPath")
with open(stdout_path, "a", encoding="utf-8") as stdout, open(
    stderr_path, "a", encoding="utf-8"
) as stderr:
    result = subprocess.run(argv, env=environment, stdout=stdout, stderr=stderr)
raise SystemExit(result.returncode)
PY
STUB
chmod +x "$BIN/uname" "$BIN/launchctl"
export ESCAPEMENT_TEST_LAUNCHCTL_STUB="$BIN/launchctl"
launchctl() (
  # Keep the fixture in-process so Codex's launchctl sandbox policy cannot
  # mistake this isolated state machine for a real launchd mutation.
  state="${LAUNCHCTL_STATE:-$HOME/launchctl.loaded}"
  log="${LAUNCHCTL_LOG:-$HOME/launchctl.log}"
  label="com.escapement.continuation-supervisor"
  printf '%s\n' "$*" >> "$log"
  : > "$state.next"
  [[ -e "$state" ]] || : > "$state"
  case "${1:-}" in
    print)
      while IFS= read -r loaded; do
        [[ "$loaded" != "$label" ]] || return 0
      done < "$state"
      return 113
      ;;
    bootout|unload)
      found=0
      while IFS= read -r loaded; do
        if [[ "$loaded" == "$label" ]]; then
          found=1
        elif [[ -n "$loaded" ]]; then
          printf '%s\n' "$loaded" >> "$state.next"
        fi
      done < "$state"
      [[ "$found" -eq 1 ]] || return 3
      remaining="$(<"$state.next")"
      : > "$state"
      [[ -z "$remaining" ]] || printf '%s\n' "$remaining" > "$state"
      return 0
      ;;
    bootstrap) plist="${3:-}" ;;
    load) plist="${@: -1}" ;;
    *) return 0 ;;
  esac
  content="$(<"$plist")"
  [[ "$content" == *"<string>$HOME/.claude/harness/bin/wakeup_waker.py</string>"* ]]
  [[ "$content" == *"<string>--fire</string>"* ]]
  while IFS= read -r loaded; do
    [[ "$loaded" != "$label" ]] || return 72
  done < "$state"
  [[ "${LAUNCHCTL_BOOTSTRAP_FAIL:-0}" != 1 ]] || return 75
  if [[ "${ASSERT_SAFE_SCHEDULES:-0}" == 1 ]]; then
    shopt -s nullglob
    for scheduled in "$HOME/.claude/harness/threads"/*/scheduled.json; do
      scheduled_content="$(<"$scheduled")"
      if [[ "$scheduled_content" != \[* || "$scheduled_content" == *2020-01-01* ]]; then
        printf 'unsafe fixture state\n' > "$HOME/unsafe-load-attempted"
        return 76
      fi
    done
  fi
  printf '%s\n' "$label" >> "$state"
  ASSERT_SAFE_SCHEDULES=0 source "$HOME/.claude/harness/bin/wakeup_waker.py" --fire
)
export -f launchctl
cat > "$BIN/claude" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$BIN/claude"
TEST_PATH="$BIN:/usr/bin:/bin"

if [ -x "$INSTALLER" ]; then
  ok "supervisor installer is an executable deployment entrypoint"
else
  bad "missing executable supervisor installer: $INSTALLER"
  echo
  echo "FAIL: see above"
  exit 1
fi
if grep -En '/opt/|/usr/local/|(^|[^[:alnum:]_])(brew|jq|plutil|pip3?|npm|node|ruby|perl)([^[:alnum:]_]|$)' \
  "$INSTALLER" >/dev/null 2>&1
then
  bad "supervisor installer introduces a non-Bash/non-stdlib runtime dependency"
else
  ok "installer declares no package-manager or third-party runtime dependency"
fi

HOME_DIR="$ROOT/home"
STABLE_BIN="$HOME_DIR/.claude/harness/bin"
PLIST="$HOME_DIR/Library/LaunchAgents/com.escapement.continuation-supervisor.plist"
LAUNCHCTL_LOG="$HOME_DIR/launchctl.log"
WAKER_LOG="$HOME_DIR/waker.argv"
mkdir -p "$STABLE_BIN"
cat > "$STABLE_BIN/wakeup_waker.py" <<'WAKER'
#!/usr/bin/env bash
set -euo pipefail
if [ "${ASSERT_SAFE_SCHEDULES:-0}" = 1 ]; then
  python3 - "$HOME" <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path

home = Path(sys.argv[1])
now = dt.datetime.now(dt.timezone.utc)
for scheduled in (home / ".claude" / "harness" / "threads").glob("*/scheduled.json"):
    unsafe = False
    try:
        entries = json.loads(scheduled.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            unsafe = True
        else:
            for entry in entries:
                raw = entry.get("wake_at") if isinstance(entry, dict) else None
                wake_at = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if wake_at.tzinfo is None:
                    wake_at = wake_at.replace(tzinfo=dt.timezone.utc)
                unsafe = unsafe or wake_at <= now
    except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
        unsafe = True
    if unsafe:
        (home / "unsafe-waker-attempted").write_text(
            f"unsafe waker invocation with active state: {scheduled}\n",
            encoding="utf-8",
        )
        raise SystemExit(76)
PY
fi
printf '%s\n' "$*" >> "$HOME/waker.argv"
WAKER
chmod +x "$STABLE_BIN/wakeup_waker.py"

KEY_FILE="$ROOT/judge.key"
printf '%s\n' 'file-secret-must-not-leak' > "$KEY_FILE"
chmod 600 "$KEY_FILE"

if HOME="$HOME_DIR" PATH="$TEST_PATH" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
  ESCAPEMENT_LOCAL_JUDGE_API_KEY='env-secret-must-not-leak' \
  ESCAPEMENT_LOCAL_JUDGE_API_KEY_FILE="$KEY_FILE" \
  bash "$INSTALLER" > "$ROOT/install.out" 2> "$ROOT/install.err"
then
  ok "first install succeeds in an isolated launchd domain"
else
  sed -n '1,160p' "$ROOT/install.err"
  bad "first install failed"
fi

[ -f "$PLIST" ] && ok "install creates the named LaunchAgent" \
  || bad "install did not create the named LaunchAgent"

if [ -f "$PLIST" ] && python3 - \
  "$PLIST" \
  "$STABLE_BIN/wakeup_waker.py" \
  "$HOME_DIR" \
  "$REPO" \
  'env-secret-must-not-leak' \
  'file-secret-must-not-leak' <<'PY'
import base64
import os
import plistlib
import sys

plist_path, stable_waker, home, repo, *secrets = sys.argv[1:]
with open(plist_path, "rb") as fh:
    job = plistlib.load(fh)
argv = job["ProgramArguments"]
assert job["Label"] == "com.escapement.continuation-supervisor"
assert job["AbandonProcessGroup"] is True
assert job["RunAtLoad"] is True
assert type(job["StartInterval"]) is int
assert job["StartInterval"] == 60
assert argv == [stable_waker, "--fire"]
environment = job.get("EnvironmentVariables") or {}
assert isinstance(environment, dict)
assert set(environment) <= {"HOME", "PATH", "CONTINUATION_HARNESS_HOME"}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


serialized = "\n".join(strings(job))
for secret in secrets:
    encodings = {
        secret,
        secret[::-1],
        secret.encode().hex(),
        base64.b64encode(secret.encode()).decode(),
        base64.urlsafe_b64encode(secret.encode()).decode(),
        base64.b32encode(secret.encode()).decode(),
    }
    encodings |= {encoded.rstrip("=") for encoded in encodings}
    assert not any(encoded and encoded in serialized for encoded in encodings)
assert repo not in serialized
for key in ("StandardOutPath", "StandardErrorPath"):
    path = job[key]
    assert os.path.isabs(path)
    assert os.path.commonpath((home, path)) == home
PY
then
  ok "plist names the stable installed waker, --fire, load-on-start, bounded cadence, and absolute logs"
else
  bad "plist does not express the required stable execution contract"
fi

[ "$(wc -l < "$WAKER_LOG" 2>/dev/null || echo 0)" -eq 1 ] \
  && [ "$(sed -n '1p' "$WAKER_LOG" 2>/dev/null)" = "--fire" ] \
  && ok "launchd load actually invokes the stable waker with --fire" \
  || bad "launchd load did not execute one stable --fire invocation"

if ! grep -F 'env-secret-must-not-leak' \
  "$PLIST" "$LAUNCHCTL_LOG" "$WAKER_LOG" "$ROOT/install.out" "$ROOT/install.err" >/dev/null 2>&1 \
  && ! grep -F 'file-secret-must-not-leak' \
  "$PLIST" "$LAUNCHCTL_LOG" "$WAKER_LOG" "$ROOT/install.out" "$ROOT/install.err" >/dev/null 2>&1
then
  ok "judge secrets are absent from plist, process argv records, installer output, and supervisor logs"
else
  bad "judge secret leaked into a deployment artifact or process record"
fi

if HOME="$HOME_DIR" PATH="$TEST_PATH" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
  bash "$INSTALLER" > "$ROOT/reinstall.out" 2> "$ROOT/reinstall.err"
then
  ok "idempotent reinstall/reload succeeds"
else
  sed -n '1,160p' "$ROOT/reinstall.err"
  bad "idempotent reinstall/reload failed"
fi

[ "$(awk '/^bootstrap / {count++} END {print count+0}' "$LAUNCHCTL_LOG" 2>/dev/null)" -eq 2 ] \
  && [ "$(wc -l < "$WAKER_LOG" 2>/dev/null || echo 0)" -eq 2 ] \
  && ok "each install converges to one loaded job and one RunAtLoad fire" \
  || bad "reinstall duplicated, skipped, or failed to reload the job"

for pass in first second; do
  if HOME="$HOME_DIR" PATH="$TEST_PATH" LAUNCHCTL_LOG="$LAUNCHCTL_LOG" \
    bash "$INSTALLER" --uninstall > "$ROOT/uninstall-$pass.out" 2> "$ROOT/uninstall-$pass.err"
  then
    ok "$pass uninstall succeeds"
  else
    sed -n '1,160p' "$ROOT/uninstall-$pass.err"
    bad "$pass uninstall failed"
  fi
done
[ ! -e "$PLIST" ] && ok "idempotent uninstall leaves no LaunchAgent plist" \
  || bad "uninstall left the LaunchAgent plist behind"
[ ! -s "$HOME_DIR/launchctl.loaded" ] \
  && ok "double uninstall leaves the stateful launchd domain empty" \
  || bad "double uninstall left a loaded or duplicated launchd label"

MISSING_HOME="$ROOT/missing-waker"
MISSING_LOG="$MISSING_HOME/launchctl.log"
mkdir -p "$MISSING_HOME"
if HOME="$MISSING_HOME" PATH="$TEST_PATH" LAUNCHCTL_LOG="$MISSING_LOG" \
  bash "$INSTALLER" > "$ROOT/missing.out" 2> "$ROOT/missing.err"
then
  bad "install should fail when the stable deployed waker is absent"
else
  ok "missing stable waker fails installation"
fi
[ ! -e "$MISSING_HOME/Library/LaunchAgents/com.escapement.continuation-supervisor.plist" ] \
  && [ ! -s "$MISSING_LOG" ] \
  && ok "missing-waker failure occurs before plist or launchctl mutation" \
  || bad "missing-waker failure partially mutated launchd state"

LOAD_FAIL_HOME="$ROOT/bootstrap-failure"
LOAD_FAIL_BIN="$LOAD_FAIL_HOME/.claude/harness/bin"
LOAD_FAIL_LOG="$LOAD_FAIL_HOME/launchctl.log"
mkdir -p "$LOAD_FAIL_BIN"
cp -f "$STABLE_BIN/wakeup_waker.py" "$LOAD_FAIL_BIN/wakeup_waker.py"
if HOME="$LOAD_FAIL_HOME" PATH="$TEST_PATH" LAUNCHCTL_LOG="$LOAD_FAIL_LOG" \
  LAUNCHCTL_BOOTSTRAP_FAIL=1 bash "$INSTALLER" \
    > "$ROOT/bootstrap-failure.out" 2> "$ROOT/bootstrap-failure.err"
then
  bad "deployment should fail when launchctl cannot load the job"
else
  ok "launchctl bootstrap/load failure propagates as deployment failure"
fi
if [ ! -s "$LOAD_FAIL_HOME/launchctl.loaded" ] \
  && [ ! -e "$LOAD_FAIL_HOME/waker.argv" ] \
  && ! grep -Eqi '^installed([^[:alnum:]_]|$)|^==> (installed|OK)([^[:alnum:]_]|$)|successfully installed' \
    "$ROOT/bootstrap-failure.out" "$ROOT/bootstrap-failure.err"
then
  ok "failed launch never runs the waker or claims installed success"
else
  bad "failed launch left a loaded job, ran the waker, or claimed success"
fi

# Incident replay: real pre-install homes can contain years of old due schedules
# plus malformed siblings. The first RunAtLoad must not blindly execute them.
HAZARD_HOME="$ROOT/legacy-state"
HAZARD_BIN="$HAZARD_HOME/.claude/harness/bin"
HAZARD_THREADS="$HAZARD_HOME/.claude/harness/threads"
HAZARD_PLIST="$HAZARD_HOME/Library/LaunchAgents/com.escapement.continuation-supervisor.plist"
HAZARD_LOG="$HAZARD_HOME/launchctl.log"
mkdir -p \
  "$HAZARD_BIN" \
  "$HAZARD_THREADS/legacy-due" \
  "$HAZARD_THREADS/legacy-malformed" \
  "$HAZARD_THREADS/future-valid"
cp -f "$STABLE_BIN/wakeup_waker.py" "$HAZARD_BIN/wakeup_waker.py"
cat > "$HAZARD_THREADS/legacy-due/scheduled.json" <<'JSON'
[
  {
    "wake_at": "2020-01-01T00:00:00+00:00",
    "prompt": "legacy work must not fire during installation",
    "thread_id": "legacy-due",
    "created_by": "legacy-fixture",
    "crash_count": 0
  }
]
JSON
printf '{malformed legacy schedule\n' > "$HAZARD_THREADS/legacy-malformed/scheduled.json"
cat > "$HAZARD_THREADS/future-valid/scheduled.json" <<'JSON'
[
  {
    "wake_at": "2999-01-01T00:00:00+00:00",
    "prompt": "future valid work must remain active",
    "thread_id": "future-valid",
    "created_by": "fixture",
    "crash_count": 0
  }
]
JSON
cp -f "$HAZARD_THREADS/legacy-due/scheduled.json" "$ROOT/legacy-due.before"
cp -f "$HAZARD_THREADS/legacy-malformed/scheduled.json" "$ROOT/legacy-malformed.before"
cp -f "$HAZARD_THREADS/future-valid/scheduled.json" "$ROOT/future-valid.before"

hazard_installed=false
if HOME="$HAZARD_HOME" PATH="$TEST_PATH" LAUNCHCTL_LOG="$HAZARD_LOG" \
  ASSERT_SAFE_SCHEDULES=1 bash "$INSTALLER" > "$ROOT/hazard.out" 2> "$ROOT/hazard.err"
then
  hazard_installed=true
fi

if [ "$hazard_installed" = false ]; then
  sed -n '1,160p' "$ROOT/hazard.err"
  bad "installer refused migratable trusted legacy state instead of quarantining and loading safely"
else
  if python3 - \
    "$HAZARD_HOME" \
    "$ROOT/legacy-due.before" \
    "$ROOT/legacy-malformed.before" \
    "$ROOT/future-valid.before" \
    "$HAZARD_THREADS/legacy-due/scheduled.json" \
    "$HAZARD_THREADS/legacy-malformed/scheduled.json" \
    "$HAZARD_THREADS/future-valid/scheduled.json" <<'PY'
import json
import hashlib
import sys
from pathlib import Path

(
    home,
    due_before,
    malformed_before,
    future_before,
    due_source,
    malformed_source,
    future_source,
) = map(Path, sys.argv[1:])
quarantine = home / ".claude" / "harness" / "quarantine"
manifests = list(quarantine.rglob("manifest.json"))
assert len(manifests) == 1, "expected one inspectable quarantine transaction manifest"
manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
records = manifest.get("entries") if isinstance(manifest, dict) else None
assert isinstance(records, list), "manifest entries must be a list"
expected = {
    str(due_source): (due_before.read_bytes(), "due"),
    str(malformed_source): (malformed_before.read_bytes(), "malformed"),
}
assert {record.get("source") for record in records} == set(expected), (
    "manifest must map exactly the unsafe active sources"
)
for record in records:
    source = record["source"]
    expected_bytes, expected_reason = expected[source]
    assert record.get("reason") == expected_reason
    archive = Path(record["archive"])
    assert archive.is_file(), f"record archive does not exist: {archive}"
    assert archive.resolve().is_relative_to(quarantine.resolve())
    assert archive.read_bytes() == expected_bytes
    expected_hash = hashlib.sha256(expected_bytes).hexdigest()
    assert record.get("sha256") == expected_hash
assert future_source.read_bytes() == future_before.read_bytes(), (
    "safe future schedule was removed or rewritten"
)
assert str(future_source) not in {record["source"] for record in records}
PY
  then
    [ -s "$HAZARD_LOG" ] \
      && ok "unsafe legacy schedules are preserved with a manifest before safe first load" \
      || bad "quarantined legacy state never reached the launch boundary"
  else
    bad "successful legacy-state install did not preserve inspectable byte-exact quarantine"
  fi
fi

if [ "$(wc -l < "$HAZARD_HOME/waker.argv" 2>/dev/null || echo 0)" -eq 1 ] \
  && [ "$(sed -n '1p' "$HAZARD_HOME/waker.argv" 2>/dev/null)" = "--fire" ] \
  && [ "$(awk '/^bootstrap / {count++} END {print count+0}' "$HAZARD_LOG" 2>/dev/null)" -eq 1 ] \
  && [ "$(sed -n '1p' "$HAZARD_HOME/launchctl.loaded" 2>/dev/null)" = "com.escapement.continuation-supervisor" ] \
  && [ "$(wc -l < "$HAZARD_HOME/launchctl.loaded" 2>/dev/null || echo 0)" -eq 1 ] \
  && [ ! -e "$HAZARD_HOME/unsafe-load-attempted" ] \
  && [ ! -e "$HAZARD_HOME/unsafe-waker-attempted" ]
then
  ok "exactly one independently safe --fire invocation occurs after quarantine"
else
  bad "legacy state reached load/waker early, duplicated fire, or did not load once"
fi

run_untrusted_case() {
  local kind="$1"
  local case_home="$ROOT/untrusted-$kind"
  local case_bin="$case_home/.claude/harness/bin"
  local case_threads="$case_home/.claude/harness/threads"
  local case_thread="$case_threads/session"
  local case_schedule="$case_thread/scheduled.json"
  local case_target="$case_home/symlink-target.json"
  local case_plist="$case_home/Library/LaunchAgents/com.escapement.continuation-supervisor.plist"
  local case_log="$case_home/launchctl.log"
  mkdir -p "$case_bin" "$case_thread"
  cp -f "$STABLE_BIN/wakeup_waker.py" "$case_bin/wakeup_waker.py"

  case "$kind" in
    world-file)
      cp -f "$ROOT/legacy-due.before" "$case_schedule"
      chmod 666 "$case_schedule"
      ;;
    world-directory)
      cp -f "$ROOT/legacy-due.before" "$case_schedule"
      chmod 644 "$case_schedule"
      chmod 777 "$case_thread"
      ;;
    symlink)
      cp -f "$ROOT/legacy-due.before" "$case_target"
      chmod 600 "$case_target"
      ln -s "$case_target" "$case_schedule"
      tree_manifest "$case_target" > "$ROOT/untrusted-$kind-target.before"
      ;;
  esac

  tree_manifest "$case_threads" > "$ROOT/untrusted-$kind.before"
  if HOME="$case_home" PATH="$TEST_PATH" LAUNCHCTL_LOG="$case_log" \
    ASSERT_SAFE_SCHEDULES=1 bash "$INSTALLER" \
      > "$ROOT/untrusted-$kind.out" 2> "$ROOT/untrusted-$kind.err"
  then
    bad "$kind scheduled state should fail closed"
  else
    ok "$kind scheduled state independently fails closed"
  fi
  tree_manifest "$case_threads" > "$ROOT/untrusted-$kind.after"
  if [ "$kind" = symlink ]; then
    tree_manifest "$case_target" > "$ROOT/untrusted-$kind-target.after"
  fi

  if [ ! -e "$case_plist" ] \
    && [ ! -s "$case_log" ] \
    && [ ! -s "$case_home/launchctl.loaded" ] \
    && [ ! -e "$case_home/unsafe-load-attempted" ] \
    && [ ! -e "$case_home/.claude/harness/quarantine" ] \
    && cmp -s "$ROOT/untrusted-$kind.before" "$ROOT/untrusted-$kind.after" \
    && { [ "$kind" != symlink ] \
      || cmp -s "$ROOT/untrusted-$kind-target.before" "$ROOT/untrusted-$kind-target.after"; }
  then
    ok "$kind preserves bytes, modes, links, and unloaded state"
  else
    bad "$kind refusal mutated scheduled or launchd state"
  fi
}

run_untrusted_case world-file
run_untrusted_case world-directory
run_untrusted_case symlink

UNSUPPORTED_HOME="$ROOT/unsupported"
UNSUPPORTED_LOG="$UNSUPPORTED_HOME/launchctl.log"
mkdir -p "$UNSUPPORTED_HOME/.claude/harness/bin"
cp -f "$STABLE_BIN/wakeup_waker.py" "$UNSUPPORTED_HOME/.claude/harness/bin/wakeup_waker.py"
if HOME="$UNSUPPORTED_HOME" PATH="$TEST_PATH" LAUNCHCTL_LOG="$UNSUPPORTED_LOG" \
  FAKE_UNAME=Linux bash "$INSTALLER" > "$ROOT/unsupported.out" 2> "$ROOT/unsupported.err"
then
  if grep -Eqi 'unsupported|not supported|no-op' "$ROOT/unsupported.out" "$ROOT/unsupported.err"; then
    ok "unsupported host reports an explicit non-installed result"
  else
    bad "unsupported host exited successfully without an explicit unsupported result"
  fi
else
  bad "unsupported host should degrade portably rather than fail deployment"
fi
[ ! -e "$UNSUPPORTED_HOME/Library/LaunchAgents/com.escapement.continuation-supervisor.plist" ] \
  && [ ! -s "$UNSUPPORTED_LOG" ] \
  && ok "unsupported host does not mutate launchd state" \
  || bad "unsupported host falsely installed or loaded launchd state"

DRY_HOME="$ROOT/dry-run"
DRY_CACHE="$DRY_HOME/.claude/plugins/cache/escapement/escapement/test-cache"
DRY_LAUNCH_LOG="$DRY_HOME/launchctl.log"
DRY_SCHEDULE="$DRY_HOME/.claude/harness/threads/legacy/scheduled.json"
mkdir -p "$DRY_CACHE" "$DRY_HOME/.claude/plugins" "$DRY_HOME/Library" "$(dirname "$DRY_SCHEDULE")"
cp -R "$REPO/plugins/escapement-claude/." "$DRY_CACHE/"
cp -f "$ROOT/legacy-due.before" "$DRY_SCHEDULE"
cp -f "$DRY_SCHEDULE" "$ROOT/dry-schedule.before"
cat > "$DRY_HOME/.claude/settings.json" <<'JSON'
{"enabledPlugins":{"escapement@escapement":true}}
JSON
cat > "$DRY_HOME/.claude/plugins/installed_plugins.json" <<JSON
{"version":2,"plugins":{"escapement@escapement":[
  {"scope":"user","installPath":"$DRY_CACHE","version":"test-cache"}
]}}
JSON
tree_manifest "$DRY_HOME" > "$ROOT/dry.before"
if HOME="$DRY_HOME" PATH="$TEST_PATH" LAUNCHCTL_LOG="$DRY_LAUNCH_LOG" \
  bash "$REPO/scripts/plugin-update.sh" --dry-run > "$ROOT/dry.out" 2> "$ROOT/dry.err"
then
  ok "plugin deployment dry-run succeeds"
else
  sed -n '1,160p' "$ROOT/dry.err"
  bad "plugin deployment dry-run failed"
fi
tree_manifest "$DRY_HOME" > "$ROOT/dry.after"
if cmp -s "$ROOT/dry.before" "$ROOT/dry.after"; then
  ok "plugin dry-run preserves every HOME path, type, mode, link target, and file byte"
else
  bad "plugin deployment dry-run mutated the isolated HOME tree"
fi
if grep -Eq 'com\.escapement\.continuation-supervisor' "$ROOT/dry.out" \
  && grep -Eq 'wakeup_waker\.py.*--fire|--fire.*wakeup_waker\.py' "$ROOT/dry.out"; then
  ok "plugin deployment dry-run reports the supervisor execution plan"
else
  bad "plugin deployment dry-run did not report the supervisor --fire plan"
fi

if python3 - "$ROOT" "$KEY_FILE" \
  'env-secret-must-not-leak' 'file-secret-must-not-leak' <<'PY'
import base64
import sys
from pathlib import Path

root = Path(sys.argv[1])
excluded = Path(sys.argv[2]).resolve()
secrets = sys.argv[3:]
tokens = set()
for secret in secrets:
    encoded = {
        secret,
        secret[::-1],
        secret.encode().hex(),
        base64.b64encode(secret.encode()).decode(),
        base64.urlsafe_b64encode(secret.encode()).decode(),
        base64.b32encode(secret.encode()).decode(),
    }
    tokens |= encoded | {value.rstrip("=") for value in encoded}

violations = []
for path in root.rglob("*"):
    if not path.is_file() or path.is_symlink() or path.resolve() == excluded:
        continue
    data = path.read_bytes()
    for token in tokens:
        if token and token.encode() in data:
            violations.append((str(path), token))
assert not violations, f"reversible judge-secret encoding leaked: {violations}"
PY
then
  ok "all lifecycle artifacts and logs reject raw or reversibly encoded judge secrets"
else
  bad "judge secret or reversible encoding leaked into a lifecycle artifact"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "PASS: continuation supervisor installation is executable, stable, idempotent, and secret-free"
  exit 0
fi
echo "FAIL: see above"
exit 1
