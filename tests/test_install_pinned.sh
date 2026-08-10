#!/usr/bin/env bash
# Test: INSTALL.sh retains a pinned deployment only for auxiliary assets that
# native plugins cannot ship, and cannot roll Claude workflow surfaces back to
# the legacy pin.
#
# Business invariant: after either install or update, claude/bin and Beads
# assets may resolve into the branch-safe pinned checkout, while workflow
# surfaces remain plugin-owned. Running INSTALL.sh --update after plugin-update
# must not recreate pin-owned hooks, skills, commands, agents, bootstrap, or
# harness code.
#
# Offline + isolated: runs against throwaway homes, a stub Claude CLI, and local
# Git remotes. It never touches the real ~/.claude or network.
# Run: bash tests/test_install_pinned.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_REF="$(git -C "$REPO" rev-parse HEAD)"
fail=0
ok()  { printf '  ok: %s\n' "$*"; }
bad() { printf '  FAIL: %s\n' "$*"; fail=1; }

[ -x "$REPO/INSTALL.sh" ] \
  && ok "documented installer entrypoint is executable" \
  || bad "INSTALL.sh is not executable despite documented ./INSTALL.sh invocation"

ROOT="$(mktemp -d)"; trap 'rm -rf "$ROOT"' EXIT
BIN="$ROOT/bin"
mkdir -p "$BIN"

cat > "$BIN/claude" <<'STUB'
#!/usr/bin/env bash
S="$HOME/.claude/settings.json"
set_json() { python3 - "$S" "$@" <<'PY'
import json,sys
p=sys.argv[1]; op=sys.argv[2]; d=json.load(open(p))
if op=="enable":
    d.setdefault("enabledPlugins",{})["escapement@escapement"]=True
    d.pop("model",None)
elif op=="disable":
    d.setdefault("enabledPlugins",{})["escapement@escapement"]=False
json.dump(d,open(p,"w"),indent=2)
PY
}
case "$1 $2" in
  "plugin update")    set_json enable ;;
  "plugin install")   set_json enable ;;
  "plugin uninstall") : ;;
  "plugin disable")   set_json disable ;;
esac
exit 0
STUB
chmod +x "$BIN/claude"

# Isolate the supervisor installer that plugin-update owns. The dedicated
# installer test executes plist argv; this integration stub records only that
# deployment reached launchd after the stable wrapper was converged.
cat > "$BIN/uname" <<'STUB'
#!/usr/bin/env bash
printf 'Darwin\n'
STUB
cat > "$BIN/launchctl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$HOME/launchctl.log"
exit 0
STUB
chmod +x "$BIN/uname" "$BIN/launchctl"
export ESCAPEMENT_TEST_LAUNCHCTL_STUB="$BIN/launchctl"
launchctl() (
  state="$HOME/launchctl.loaded"
  label="com.escapement.continuation-supervisor"
  printf '%s\n' "$*" >> "$HOME/launchctl.log"
  [[ -e "$state" ]] || : > "$state"
  case "${1:-}" in
    print)
      while IFS= read -r loaded; do
        [[ "$loaded" != "$label" ]] || return 0
      done < "$state"
      return 113
      ;;
    bootout)
      : > "$state"
      return 0
      ;;
    bootstrap)
      printf '%s\n' "$label" > "$state"
      return 0
      ;;
  esac
  return 0
)
export -f launchctl

setup_claude_home() {
  local home_dir="$1"
  local cache="$home_dir/.claude/plugins/cache/escapement/escapement/sha-current"
  local personal="$home_dir/personal"
  mkdir -p "$cache" "$home_dir/.claude/plugins" "$home_dir/.claude/hooks" "$personal"
  cp -R "$REPO/plugins/escapement-claude/." "$cache/"
  printf '#!/usr/bin/env python3\n' > "$personal/personal_hook.py"
  ln -s "$personal/personal_hook.py" "$home_dir/.claude/hooks/personal_hook.py"
  cat > "$home_dir/.claude/settings.json" <<JSON
{
  "model": "opus[1m]",
  "enabledPlugins": { "escapement@escapement": true },
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "python3 $personal/personal_hook.py" }
        ]
      }
    ]
  }
}
JSON
  cat > "$home_dir/.claude/plugins/installed_plugins.json" <<JSON
{ "version": 2, "plugins": { "escapement@escapement": [
  { "scope": "user", "installPath": "$cache", "version": "sha-current" }
] } }
JSON
}

assert_plugin_owned() {
  local home_dir="$1"
  local label="$2"
  local cache="$home_dir/.claude/plugins/cache/escapement/escapement/sha-current"
  local claude_dir="$home_dir/.claude"

  [ "$(readlink "$claude_dir/harness/bin" 2>/dev/null)" = "$cache/harness/bin" ] \
    && ok "$label: harness/bin targets installed plugin" \
    || bad "$label: harness/bin is not plugin-owned ($(readlink "$claude_dir/harness/bin" 2>/dev/null))"
  [ "$(readlink "$claude_dir/harness/schemas" 2>/dev/null)" = "$cache/harness/schemas" ] \
    && ok "$label: harness/schemas targets installed plugin" \
    || bad "$label: harness/schemas is not plugin-owned ($(readlink "$claude_dir/harness/schemas" 2>/dev/null))"

  for stale in \
    "$claude_dir/skills/discovery" \
    "$claude_dir/agents/adversarial-reviewer.md" \
    "$claude_dir/commands/discovery.md" \
    "$claude_dir/hooks/spec_id_enforcement.py" \
    "$claude_dir/project-bootstrap.sh"
  do
    [ ! -L "$stale" ] || bad "$label: legacy workflow link exists: $stale -> $(readlink "$stale")"
  done

  while IFS= read -r deployed_link; do
    deployed_target="$(readlink "$deployed_link")"
    case "$deployed_target" in
      *"/.escapement-pinned"*/*|*"/.cws-pinned/"*|"$REPO"/*)
        bad "$label: plugin-owned directory contains legacy/repo link: $deployed_link -> $deployed_target"
        ;;
    esac
  done < <(
    find \
      "$claude_dir/skills" \
      "$claude_dir/agents" \
      "$claude_dir/commands" \
      "$claude_dir/rules" \
      "$claude_dir/hooks" \
      -type l -print 2>/dev/null
  )

  [ "$(readlink "$claude_dir/hooks/personal_hook.py" 2>/dev/null)" = "$home_dir/personal/personal_hook.py" ] \
    && ok "$label: personal hook preserved" || bad "$label: personal hook changed"
}

assert_auxiliary_owned() {
  local home_dir="$1"
  local pin_dir="$2"
  local label="$3"
  local formula="$home_dir/.beads/formulas/mol-feature.formula.json"
  local status="$home_dir/.beads/mol-status.sh"

  [ "$(readlink "$formula" 2>/dev/null)" = "$pin_dir/beads/formulas/mol-feature.formula.json" ] \
    && ok "$label: Beads formula remains auxiliary-pin owned" \
    || bad "$label: Beads formula target is wrong"
  cmp -s "$formula" "$pin_dir/beads/formulas/mol-feature.formula.json" \
    && ok "$label: Beads formula content preserved" \
    || bad "$label: Beads formula content differs"
  [ "$(readlink "$status" 2>/dev/null)" = "$pin_dir/beads/mol-status.sh" ] \
    && ok "$label: mol-status remains auxiliary-pin owned" \
    || bad "$label: mol-status target is wrong"
  [ -x "$status" ] && ok "$label: mol-status remains executable" \
    || bad "$label: mol-status is not executable"
}

# Default install: auxiliary assets use the pin; workflow uses the plugin.
T1="$ROOT/default"
setup_claude_home "$T1"
HOME="$T1" PATH="$BIN:$PATH" ESCAPEMENT_PIN_REMOTE="$REPO" ESCAPEMENT_PIN_REF="$BASE_REF" \
  bash "$REPO/INSTALL.sh" >"$T1/out.log" 2>&1 \
  || { cat "$T1/out.log"; bad "default installer exited non-zero"; }

PIN="$T1/.claude/.escapement-pinned"
AUX="$T1/.claude/bin"
[ -d "$PIN/.git" ] && ok "pinned checkout created for auxiliary assets" \
  || bad "no pinned checkout created"
if [ -L "$AUX" ]; then
  atgt="$(readlink "$AUX")"
  case "$atgt" in
    *"/.escapement-pinned/"*) ok "claude/bin points into pinned checkout" ;;
    *) bad "claude/bin points outside pinned checkout: $atgt" ;;
  esac
  case "$atgt" in
    "$REPO"/*) bad "default claude/bin points into live working tree" ;;
    *) ok "default claude/bin is branch-safe" ;;
  esac
else
  bad "default install produced no claude/bin auxiliary link"
fi
assert_plugin_owned "$T1" "default install"
assert_auxiliary_owned "$T1" "$PIN" "default install"

SUPERVISOR_PLIST="$T1/Library/LaunchAgents/com.escapement.continuation-supervisor.plist"
if [ -f "$SUPERVISOR_PLIST" ] && python3 - "$SUPERVISOR_PLIST" "$T1/.claude/harness/bin/wakeup_waker.py" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as fh:
    job = plistlib.load(fh)
argv = job.get("ProgramArguments", [])
assert argv == [sys.argv[2], "--fire"]
assert job.get("RunAtLoad") is True
assert type(job.get("StartInterval")) is int and job["StartInterval"] == 60
PY
then
  ok "default deployment installs supervisor against the stable harness wrapper"
else
  bad "default deployment did not install a stable --fire supervisor job"
fi
rg -q '^bootstrap ' "$T1/launchctl.log" 2>/dev/null \
  && ok "default deployment loads the supervisor after wrapper convergence" \
  || bad "default deployment never loaded the supervisor"

# Sequential regression: a later legacy update may refresh the auxiliary pin,
# but it must leave plugin ownership intact.
HOME="$T1" PATH="$BIN:$PATH" ESCAPEMENT_PIN_REMOTE="$REPO" ESCAPEMENT_PIN_REF="$BASE_REF" \
  bash "$REPO/scripts/plugin-update.sh" >"$T1/plugin-update.log" 2>&1 \
  || { cat "$T1/plugin-update.log"; bad "direct plugin update exited non-zero"; }
HOME="$T1" PATH="$BIN:$PATH" ESCAPEMENT_PIN_REMOTE="$REPO" ESCAPEMENT_PIN_REF="$BASE_REF" \
  bash "$REPO/INSTALL.sh" --update >"$T1/update.log" 2>&1 \
  || { cat "$T1/update.log"; bad "INSTALL.sh --update exited non-zero"; }
assert_plugin_owned "$T1" "plugin update then legacy update"
assert_auxiliary_owned "$T1" "$PIN" "plugin update then legacy update"

# --dev affects only auxiliary assets; plugin workflow ownership is unchanged.
T2="$ROOT/dev"
setup_claude_home "$T2"
HOME="$T2" PATH="$BIN:$PATH" bash "$REPO/INSTALL.sh" --dev >"$T2/out.log" 2>&1 \
  || { cat "$T2/out.log"; bad "--dev install exited non-zero"; }
[ "$(readlink "$T2/.claude/bin" 2>/dev/null)" = "$REPO/claude/bin" ] \
  && ok "--dev points auxiliary claude/bin into working tree" \
  || bad "--dev claude/bin target is wrong"
assert_plugin_owned "$T2" "--dev install"

# CWS-era pin resolution now uses the retained claude/bin auxiliary sentinel.
T3="$ROOT/cws"
setup_claude_home "$T3"
REMOTE="$ROOT/remote"
git clone --quiet "$REPO" "$REMOTE" >/dev/null 2>&1 || bad "could not clone local remote"
git -C "$REMOTE" checkout --quiet main 2>/dev/null || git -C "$REMOTE" checkout --quiet -b main

HOME="$T3" PATH="$BIN:$PATH" CWS_PIN_DIR="$T3/.claude/.cws-pinned" \
  ESCAPEMENT_PIN_REMOTE="$REMOTE" ESCAPEMENT_PIN_REF="main" \
  bash "$REPO/INSTALL.sh" >"$T3/install.log" 2>&1 \
  || { cat "$T3/install.log"; bad "CWS-era install exited non-zero"; }

CWS_PIN="$T3/.claude/.cws-pinned"
ESC_PIN="$T3/.claude/.escapement-pinned"
SENTINEL="$T3/.claude/bin"
case "$(readlink "$SENTINEL" 2>/dev/null)" in
  *"/.cws-pinned/"*) ok "CWS-era auxiliary sentinel resolves into .cws-pinned" ;;
  *) bad "CWS-era auxiliary sentinel target is wrong" ;;
esac

cws_before="$(git -C "$CWS_PIN" rev-parse HEAD 2>/dev/null || echo MISSING)"
(
  cd "$REMOTE" || exit 1
  printf 'drift-test\n' > _drift_marker.txt
  git add _drift_marker.txt
  git -c user.email=t@t -c user.name=t commit --quiet -m "drift advance"
) || bad "could not advance local remote"

HOME="$T3" PATH="$BIN:$PATH" ESCAPEMENT_PIN_REMOTE="$REMOTE" ESCAPEMENT_PIN_REF="main" \
  bash "$REPO/INSTALL.sh" --update >"$T3/update.log" 2>&1
upd_rc=$?
cws_after="$(git -C "$CWS_PIN" rev-parse HEAD 2>/dev/null || echo MISSING)"

if [ "$cws_after" != "$cws_before" ] && [ "$cws_after" != "MISSING" ]; then
  ok "bare --update advanced the live CWS auxiliary pin"
else
  bad "bare --update did not advance the live CWS auxiliary pin"
fi
if [ "$cws_after" != "$cws_before" ]; then
  ok "no silent wrong-directory update"
elif [ "$upd_rc" -ne 0 ]; then
  ok "update failed loudly rather than updating wrong directory"
else
  bad "update silently left the live auxiliary pin stale"
fi
assert_plugin_owned "$T3" "CWS update"

# Explicit override still wins.
HOME="$T3" PATH="$BIN:$PATH" ESCAPEMENT_PIN_DIR="$ESC_PIN" \
  ESCAPEMENT_PIN_REMOTE="$REMOTE" ESCAPEMENT_PIN_REF="main" \
  bash "$REPO/INSTALL.sh" --update >"$T3/update-override.log" 2>&1 \
  || { cat "$T3/update-override.log"; bad "explicit override update exited non-zero"; }
[ -d "$ESC_PIN/.git" ] && ok "explicit pin override refreshed named directory" \
  || bad "explicit pin override did not create named directory"
assert_plugin_owned "$T3" "explicit override update"

# Fresh --update keeps the default auxiliary pin behavior.
T4="$ROOT/fresh"
setup_claude_home "$T4"
HOME="$T4" PATH="$BIN:$PATH" ESCAPEMENT_PIN_REMOTE="$REMOTE" ESCAPEMENT_PIN_REF="main" \
  bash "$REPO/INSTALL.sh" --update >"$T4/update.log" 2>&1 \
  || { cat "$T4/update.log"; bad "fresh --update exited non-zero"; }
[ -d "$T4/.claude/.escapement-pinned/.git" ] \
  && ok "fresh --update uses default auxiliary pin" \
  || bad "fresh --update did not create default auxiliary pin"
assert_plugin_owned "$T4" "fresh update"

# Fail-closed transaction ordering: without an authoritative user-scope plugin,
# INSTALL.sh must fail before refreshing even the auxiliary pin or changing any
# live target/content.
T5="$ROOT/invalid-plugin"
BAD_REMOTE="$ROOT/invalid-remote"
BAD_PIN="$T5/.claude/.escapement-pinned"
BAD_CACHE="$T5/.claude/plugins/cache/escapement/escapement/arbitrary-cache"
git clone --quiet "$REPO" "$BAD_REMOTE" >/dev/null 2>&1 || bad "could not clone invalid-plugin remote"
git -C "$BAD_REMOTE" checkout --quiet main 2>/dev/null || git -C "$BAD_REMOTE" checkout --quiet -b main
git clone --quiet "$BAD_REMOTE" "$BAD_PIN" >/dev/null 2>&1 || bad "could not create invalid-plugin pin"
git -C "$BAD_PIN" checkout --quiet main 2>/dev/null || true
mkdir -p \
  "$BAD_CACHE" \
  "$T5/.claude/plugins" \
  "$T5/.claude/harness" \
  "$T5/.claude/skills" \
  "$T5/.claude/rules" \
  "$T5/.claude/hooks" \
  "$T5/.beads/formulas"
cp -R "$REPO/plugins/escapement-claude/." "$BAD_CACHE/"
ln -s "$BAD_PIN/claude/bin" "$T5/.claude/bin"
ln -s "$BAD_PIN/harness/bin" "$T5/.claude/harness/bin"
ln -s "$BAD_PIN/claude/skills/discovery" "$T5/.claude/skills/discovery"
ln -s "$BAD_PIN/claude/rules/continuation-harness.md" "$T5/.claude/rules/continuation-harness.md"
printf 'personal invalid control\n' > "$T5/personal.py"
ln -s "$T5/personal.py" "$T5/.claude/hooks/personal.py"
ln -s "$BAD_PIN/beads/formulas/mol-feature.formula.json" "$T5/.beads/formulas/mol-feature.formula.json"
ln -s "$BAD_PIN/beads/mol-status.sh" "$T5/.beads/mol-status.sh"
cat > "$T5/.claude/settings.json" <<JSON
{
  "model": "opus",
  "enabledPlugins": { "escapement@escapement": false },
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 $T5/personal.py" } ] }
    ]
  }
}
JSON
cat > "$T5/.claude/plugins/installed_plugins.json" <<JSON
{ "version": 2, "plugins": { "escapement@escapement": [
  { "scope": "project", "installPath": "$BAD_CACHE", "version": "arbitrary-cache" }
] } }
JSON

bad_head_before="$(git -C "$BAD_PIN" rev-parse HEAD)"
bad_bin_before="$(readlink "$T5/.claude/bin")"
bad_harness_before="$(readlink "$T5/.claude/harness/bin")"
bad_skill_before="$(readlink "$T5/.claude/skills/discovery")"
bad_rule_before="$(readlink "$T5/.claude/rules/continuation-harness.md")"
bad_formula_before="$(readlink "$T5/.beads/formulas/mol-feature.formula.json")"
bad_status_before="$(readlink "$T5/.beads/mol-status.sh")"
cp "$T5/.claude/settings.json" "$ROOT/invalid-settings.before"
cp "$T5/.beads/formulas/mol-feature.formula.json" "$ROOT/invalid-formula.before"

(
  cd "$BAD_REMOTE" || exit 1
  printf 'advance must not deploy\n' > _invalid_advance.txt
  git add _invalid_advance.txt
  git -c user.email=t@t -c user.name=t commit --quiet -m "invalid plugin advance"
) || bad "could not advance invalid-plugin remote"

if HOME="$T5" PATH="$BIN:$PATH" ESCAPEMENT_PIN_DIR="$BAD_PIN" \
  ESCAPEMENT_PIN_REMOTE="$BAD_REMOTE" ESCAPEMENT_PIN_REF="main" \
  bash "$REPO/INSTALL.sh" --update >"$T5/update.log" 2>&1
then
  cat "$T5/update.log"
  bad "installer should fail without a user-scope plugin"
else
  ok "installer fails without a user-scope plugin"
fi

[ "$(git -C "$BAD_PIN" rev-parse HEAD)" = "$bad_head_before" ] \
  && ok "failed installer leaves auxiliary pin HEAD unchanged" \
  || bad "failed installer refreshed auxiliary pin before aborting"
[ "$(readlink "$T5/.claude/bin" 2>/dev/null)" = "$bad_bin_before" ] \
  && ok "failed installer preserves claude/bin target" || bad "failed installer changed claude/bin"
[ "$(readlink "$T5/.claude/harness/bin" 2>/dev/null)" = "$bad_harness_before" ] \
  && ok "failed installer preserves harness target" || bad "failed installer changed harness target"
[ "$(readlink "$T5/.claude/skills/discovery" 2>/dev/null)" = "$bad_skill_before" ] \
  && ok "failed installer preserves workflow target" || bad "failed installer changed workflow target"
[ "$(readlink "$T5/.claude/rules/continuation-harness.md" 2>/dev/null)" = "$bad_rule_before" ] \
  && ok "failed installer preserves legacy rule target" || bad "failed installer changed legacy rule target"
[ "$(readlink "$T5/.claude/hooks/personal.py" 2>/dev/null)" = "$T5/personal.py" ] \
  && ok "failed installer preserves personal target" || bad "failed installer changed personal target"
[ "$(readlink "$T5/.beads/formulas/mol-feature.formula.json" 2>/dev/null)" = "$bad_formula_before" ] \
  && ok "failed installer preserves formula target" || bad "failed installer changed formula target"
[ "$(readlink "$T5/.beads/mol-status.sh" 2>/dev/null)" = "$bad_status_before" ] \
  && ok "failed installer preserves mol-status target" || bad "failed installer changed mol-status target"
cmp -s "$T5/.beads/formulas/mol-feature.formula.json" "$ROOT/invalid-formula.before" \
  && ok "failed installer preserves formula content" || bad "failed installer changed formula content"
cmp -s "$T5/.claude/settings.json" "$ROOT/invalid-settings.before" \
  && ok "failed installer preserves settings byte-for-byte" \
  || bad "failed installer changed settings before aborting"
[ ! -e "$T5/Library/LaunchAgents/com.escapement.continuation-supervisor.plist" ] \
  && [ ! -s "$T5/launchctl.log" ] \
  && ok "plugin authority failure occurs before supervisor deployment" \
  || bad "plugin authority failure partially installed the supervisor"

# Unknown auxiliary symlinks are user-owned. A valid plugin does not authorize
# clobbering them, and the whole plan must validate before the first link moves.
T6="$ROOT/unknown-bin"
setup_claude_home "$T6"
mkdir -p "$T6/personal-bin"
ln -s "$T6/personal-bin" "$T6/.claude/bin"
if HOME="$T6" PATH="$BIN:$PATH" bash "$REPO/INSTALL.sh" --dev >"$T6/out.log" 2>&1
then
  cat "$T6/out.log"
  bad "installer should refuse an unrelated claude/bin symlink"
else
  ok "installer refuses unrelated claude/bin symlink"
fi
[ "$(readlink "$T6/.claude/bin" 2>/dev/null)" = "$T6/personal-bin" ] \
  && ok "unrelated claude/bin symlink preserved" \
  || bad "unrelated claude/bin symlink was clobbered"

T7="$ROOT/unknown-final-plan-entry"
setup_claude_home "$T7"
mkdir -p "$T7/.beads" "$T7/personal"
printf '#!/bin/sh\n' > "$T7/personal/mol-status.sh"
ln -s "$T7/personal/mol-status.sh" "$T7/.beads/mol-status.sh"
if HOME="$T7" PATH="$BIN:$PATH" bash "$REPO/INSTALL.sh" --dev >"$T7/out.log" 2>&1
then
  cat "$T7/out.log"
  bad "installer should refuse unrelated final-plan Beads status symlink"
else
  ok "installer refuses unrelated final-plan Beads status symlink"
fi
[ "$(readlink "$T7/.beads/mol-status.sh" 2>/dev/null)" = "$T7/personal/mol-status.sh" ] \
  && ok "unrelated final-plan Beads status symlink preserved" \
  || bad "unrelated final-plan Beads status symlink was clobbered"
[ ! -e "$T7/.claude/bin" ] && [ ! -e "$T7/.beads/formulas" ] \
  && ok "late auxiliary conflict prevents earlier plan mutation" \
  || bad "installer partially mutated plan before finding final conflict"

# A missing user-scope plugin must fail before compatibility directories are
# created. Registry/settings are pre-existing authority inputs; everything else
# below is observable mutation.
T8="$ROOT/clean-invalid"
mkdir -p "$T8/.claude/plugins"
cat > "$T8/.claude/settings.json" <<'JSON'
{"model":"opus","enabledPlugins":{"escapement@escapement":false}}
JSON
cat > "$T8/.claude/plugins/installed_plugins.json" <<JSON
{"version":2,"plugins":{"escapement@escapement":[
  {"scope":"project","installPath":"$BAD_CACHE","version":"wrong-scope"}
]}}
JSON
find "$T8" -print | sort > "$ROOT/t8.before"
if HOME="$T8" PATH="$BIN:$PATH" bash "$REPO/INSTALL.sh" --dev >"$T8/out.log" 2>&1
then
  cat "$T8/out.log"
  bad "clean invalid install should fail without user-scope authority"
else
  ok "clean invalid install fails closed"
fi
[ ! -e "$T8/.claude/harness" ] && [ ! -e "$T8/.beads" ] && [ ! -e "$T8/.claude/bin" ] \
  && ok "authority failure creates no compatibility state" \
  || bad "authority failure created compatibility directories or links"
find "$T8" ! -name out.log -print | sort > "$ROOT/t8.after"
cmp -s "$ROOT/t8.after" "$ROOT/t8.before" \
  && ok "authority failure leaves complete home tree unchanged" \
  || {
    diff -u "$ROOT/t8.before" "$ROOT/t8.after" || true
    bad "authority failure added pin, backup, wrapper, or deployment residue"
  }

echo
if [ "$fail" -eq 0 ]; then
  echo "PASS: legacy installer cannot roll back Claude plugin cutover"
  exit 0
fi
echo "FAIL: see above"
exit 1
