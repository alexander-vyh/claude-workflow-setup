#!/usr/bin/env bash
# Test: scripts/plugin-update.sh refreshes the Claude plugin and converges a
# regressed legacy-pin deployment back to plugin-only ownership.
#
# Business invariant: after the attended plugin cutover, an update must treat
# pin-owned Claude workflow links as stale deployment residue. The installed
# user-scope plugin owns skills, agents, commands, hooks, and bootstrap; stable
# harness wrappers point at that plugin. Personal files and writable harness
# state survive.
#
# Fragile implementations this rejects:
#   - one-time/manual harness relink with no migration
#   - harness-only migration that leaves duplicate global workflow surfaces
#   - cache-directory guessing instead of the registry's user-scope installPath
#   - indiscriminate removal of personal hooks or harness runtime state
#   - migration before a valid installed plugin is resolved
#
# Offline + isolated: stubs the `claude` CLI and runs against throwaway homes.
# Run: bash tests/test_plugin_update.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0
ok()  { printf '  ok: %s\n' "$*"; }
bad() { printf '  FAIL: %s\n' "$*"; fail=1; }

TD="$(mktemp -d)"; trap 'rm -rf "$TD"' EXIT
UPDATER_REPO="$TD/updater-repo"
REPO_ONLY_NAME="repo-owned-$RANDOM.py"
REPO_DIRECT_NAME="repo-direct-$RANDOM.py"
PIN_ONLY_NAME="pin-only-$RANDOM.py"
REPO_PERSONAL_NAME="repo-name-personal-$RANDOM.py"
REPO_ONLY_DIR="repo-owned-dir-$RANDOM"
PIN_ONLY_DIR="pin-only-dir-$RANDOM"
REPO_PERSONAL_DIR="repo-name-personal-dir-$RANDOM"
HOME_DIR="$TD/home"
BIN="$TD/bin"
CLAUDE_DIR="$HOME_DIR/.claude"
CACHE="$CLAUDE_DIR/plugins/cache/escapement/escapement/sha-current"
CACHE_OLD="$CLAUDE_DIR/plugins/cache/escapement/escapement/sha-old"
PIN="$CLAUDE_DIR/.escapement-pinned-legacy"
PERSONAL="$TD/personal"

mkdir -p \
  "$UPDATER_REPO/scripts" \
  "$UPDATER_REPO/claude/hooks/tests" \
  "$UPDATER_REPO/plugins/escapement-claude/harness/bin" \
  "$BIN" \
  "$CACHE/harness/bin" \
  "$CACHE/harness/schemas" \
  "$CACHE/skills/discovery" \
  "$CACHE/skills/build" \
  "$CACHE/agents" \
  "$CACHE/commands" \
  "$CACHE/rules" \
  "$CACHE/hooks" \
  "$PIN/harness/bin" \
  "$PIN/harness/schemas" \
  "$PIN/claude/skills/discovery" \
  "$PIN/claude/agents" \
  "$PIN/claude/commands" \
  "$PIN/claude/rules" \
  "$PIN/claude/hooks" \
  "$PIN/scripts" \
  "$CLAUDE_DIR/harness/threads" \
  "$CLAUDE_DIR/skills" \
  "$CLAUDE_DIR/agents" \
  "$CLAUDE_DIR/commands" \
  "$CLAUDE_DIR/rules" \
  "$CLAUDE_DIR/hooks" \
  "$PERSONAL"

# Execute a copied updater from a miniature repository so a random, repo-owned
# source can prove cleanup is inventory-driven rather than name-specific.
cp "$REPO/scripts/plugin-update.sh" "$UPDATER_REPO/scripts/plugin-update.sh"
cp "$REPO/scripts/plugin-update-transaction.py" \
  "$UPDATER_REPO/scripts/plugin-update-transaction.py"
cp "$REPO/scripts/plugin-wrapper-target.py" \
  "$UPDATER_REPO/scripts/plugin-wrapper-target.py"
cp "$REPO/scripts/continuation-supervisor-install.sh" \
  "$UPDATER_REPO/scripts/continuation-supervisor-install.sh"
cp "$REPO/scripts/continuation-supervisor-state.py" \
  "$UPDATER_REPO/scripts/continuation-supervisor-state.py"
chmod +x \
  "$UPDATER_REPO/scripts/plugin-update-transaction.py" \
  "$UPDATER_REPO/scripts/plugin-wrapper-target.py" \
  "$UPDATER_REPO/scripts/continuation-supervisor-state.py"
cp "$REPO/scripts/prune_settings_hooks.py" "$UPDATER_REPO/scripts/prune_settings_hooks.py"
cp "$REPO/plugins/escapement-claude/harness/bin/stop_hook.py" \
  "$UPDATER_REPO/plugins/escapement-claude/harness/bin/stop_hook.py"
cp "$REPO/claude/hooks/codex_final_response_gap.py" \
  "$UPDATER_REPO/claude/hooks/codex_final_response_gap.py"
printf '#!/usr/bin/env python3\n' > "$UPDATER_REPO/claude/hooks/$REPO_ONLY_NAME"
printf '#!/usr/bin/env python3\n' > "$UPDATER_REPO/claude/hooks/$REPO_DIRECT_NAME"
printf '#!/usr/bin/env python3\n' > "$UPDATER_REPO/claude/hooks/$REPO_PERSONAL_NAME"
mkdir -p \
  "$UPDATER_REPO/claude/hooks/$REPO_ONLY_DIR" \
  "$UPDATER_REPO/claude/hooks/$REPO_PERSONAL_DIR"

# Representative plugin payload.
cp "$REPO/plugins/escapement-claude/harness/bin/stop_hook.py" "$CACHE/harness/bin/stop_hook.py"
cp "$REPO/plugins/escapement-claude/harness/bin/wakeup_waker.py" \
  "$CACHE/harness/bin/wakeup_waker.py"
cp "$REPO/plugins/escapement-claude/harness/bin/wakeup_waker.py" \
  "$PIN/harness/bin/wakeup_waker.py"
chmod +x "$PIN/harness/bin/wakeup_waker.py"
printf '#!/bin/bash\nexit 0\n' > "$CACHE/harness/bin/verify"
chmod 644 "$CACHE/harness/bin/verify"
printf '{}\n' > "$CACHE/harness/schemas/thread.schema.json"
printf 'plugin skill\n' > "$CACHE/skills/discovery/SKILL.md"
printf 'plugin skill\n' > "$CACHE/skills/build/SKILL.md"
printf 'plugin agent\n' > "$CACHE/agents/adversarial-reviewer.md"
printf 'plugin agent\n' > "$CACHE/agents/test-quality-reviewer.md"
printf 'plugin command\n' > "$CACHE/commands/discovery.md"
printf 'plugin command\n' > "$CACHE/commands/review.md"
printf 'plugin rule\n' > "$CACHE/rules/continuation-harness.md"
printf '#!/usr/bin/env python3\n' > "$CACHE/hooks/spec_id_enforcement.py"
printf '#!/usr/bin/env python3\n' > "$CACHE/hooks/validate_no_shirking.py"
printf '#!/usr/bin/env bash\n' > "$CACHE/hooks/project-bootstrap.sh"
printf '#!/usr/bin/env python3\n' > "$PIN/claude/hooks/codex_final_response_gap.py"
printf '#!/usr/bin/env python3\n' > "$PIN/claude/hooks/$REPO_ONLY_NAME"
printf '#!/usr/bin/env python3\n' > "$PIN/claude/hooks/$PIN_ONLY_NAME"
mkdir -p \
  "$PIN/claude/hooks/tests" \
  "$PIN/claude/hooks/$REPO_ONLY_DIR" \
  "$PIN/claude/hooks/$PIN_ONLY_DIR"
cp "$REPO/claude/hooks/magic_number_echo.py" "$CACHE/hooks/magic_number_echo.py"
cp "$REPO/claude/hooks/oracle_reason_validation.py" "$CACHE/hooks/oracle_reason_validation.py"
DYNAMIC_NAME="cutover-owned-$RANDOM"
mkdir -p "$CACHE/skills/$DYNAMIC_NAME" "$PIN/claude/skills/$DYNAMIC_NAME"
printf 'dynamic plugin skill\n' > "$CACHE/skills/$DYNAMIC_NAME/SKILL.md"
printf 'dynamic pin skill\n' > "$PIN/claude/skills/$DYNAMIC_NAME/SKILL.md"
for surface in agents commands rules hooks; do
  printf 'dynamic plugin surface\n' > "$CACHE/$surface/$DYNAMIC_NAME"
  printf 'dynamic pin surface\n' > "$PIN/claude/$surface/$DYNAMIC_NAME"
done
printf '%s\n' \
  '{"hooks":{"Stop":[{"hooks":[{"command":"python3 -B ${CLAUDE_PLUGIN_ROOT}/hooks/validate_no_shirking.py"}]}]}}' \
  > "$CACHE/hooks/hooks.json"
cp -R "$CACHE" "$CACHE_OLD"
# The pre-update package can legitimately predate newly required support files.
# It must remain eligible for refresh; the post-update package must be complete.
rm -f \
  "$CACHE_OLD/hooks/magic_number_echo.py" \
  "$CACHE_OLD/hooks/oracle_reason_validation.py"

# Fully regressed post-cutover state: workflow surfaces point into the pin.
ln -s "$PIN/harness/bin" "$CLAUDE_DIR/harness/bin"
ln -s "$PIN/harness/schemas" "$CLAUDE_DIR/harness/schemas"
ln -s "$PIN/claude/skills/discovery" "$CLAUDE_DIR/skills/discovery"
ln -s "$PIN/claude/agents/adversarial-reviewer.md" "$CLAUDE_DIR/agents/adversarial-reviewer.md"
ln -s "$PIN/claude/commands/discovery.md" "$CLAUDE_DIR/commands/discovery.md"
ln -s "$PIN/claude/rules/continuation-harness.md" "$CLAUDE_DIR/rules/continuation-harness.md"
ln -s "$PIN/claude/hooks/spec_id_enforcement.py" "$CLAUDE_DIR/hooks/spec_id_enforcement.py"
ln -s "$PIN/claude/hooks/codex_final_response_gap.py" "$CLAUDE_DIR/hooks/codex_final_response_gap.py"
ln -s "$PIN/claude/hooks/tests" "$CLAUDE_DIR/hooks/tests"
ln -s "$PIN/claude/hooks/$REPO_ONLY_NAME" "$CLAUDE_DIR/hooks/$REPO_ONLY_NAME"
ln -s "$UPDATER_REPO/claude/hooks/$REPO_DIRECT_NAME" "$CLAUDE_DIR/hooks/$REPO_DIRECT_NAME"
ln -s "$PIN/claude/hooks/$PIN_ONLY_NAME" "$CLAUDE_DIR/hooks/$PIN_ONLY_NAME"
ln -s "$PIN/claude/hooks/$REPO_ONLY_DIR" "$CLAUDE_DIR/hooks/$REPO_ONLY_DIR"
ln -s "$PIN/claude/hooks/$PIN_ONLY_DIR" "$CLAUDE_DIR/hooks/$PIN_ONLY_DIR"
ln -s "$PIN/claude/hooks/obsolete_escapement_hook.py" "$CLAUDE_DIR/hooks/obsolete_escapement_hook.py"
ln -s "$PIN/scripts/project-bootstrap.sh" "$CLAUDE_DIR/project-bootstrap.sh"
ln -s "$PIN/claude/skills/$DYNAMIC_NAME" "$CLAUDE_DIR/skills/$DYNAMIC_NAME"
for surface in agents commands rules hooks; do
  ln -s "$PIN/claude/$surface/$DYNAMIC_NAME" "$CLAUDE_DIR/$surface/$DYNAMIC_NAME"
done

# Positive controls: personal hook/link, custom real file, and runtime state.
printf '#!/usr/bin/env python3\n' > "$PERSONAL/jixia_send_bounce.py"
printf '#!/usr/bin/env python3\n' > "$PERSONAL/$REPO_PERSONAL_NAME"
mkdir -p "$PERSONAL/$REPO_PERSONAL_DIR"
mkdir -p "$PERSONAL/build"
printf 'personal build skill\n' > "$PERSONAL/build/SKILL.md"
printf 'personal review command\n' > "$PERSONAL/review.md"
ln -s "$PERSONAL/jixia_send_bounce.py" "$CLAUDE_DIR/hooks/jixia_send_bounce.py"
ln -s "$PERSONAL/$REPO_PERSONAL_NAME" "$CLAUDE_DIR/hooks/$REPO_PERSONAL_NAME"
ln -s "$PERSONAL/$REPO_PERSONAL_DIR" "$CLAUDE_DIR/hooks/$REPO_PERSONAL_DIR"
ln -s "$REPO/README.md" "$CLAUDE_DIR/hooks/validate_no_shirking.py"
ln -s "$PERSONAL/build" "$CLAUDE_DIR/skills/build"
printf 'personal real agent\n' > "$CLAUDE_DIR/agents/test-quality-reviewer.md"
ln -s "$PERSONAL/review.md" "$CLAUDE_DIR/commands/review.md"
printf 'personal real hook\n' > "$CLAUDE_DIR/hooks/personal_real.py"
printf 'incident state\n' > "$CLAUDE_DIR/harness/incidents.jsonl"
printf 'thread state\n' > "$CLAUDE_DIR/harness/threads/thread.json"

cat > "$CLAUDE_DIR/settings.json" <<JSON
{
  "model": "opus[1m]",
  "enabledPlugins": { "escapement@escapement": false },
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "python3 -B ~/.claude/hooks/validate_no_shirking.py" },
          { "type": "command", "command": "python3 $PERSONAL/jixia_send_bounce.py" }
          ,{
            "type": "command",
            "command": "python3 -B /opt/personal/hooks/validate_no_shirking.py",
            "timeout": 17,
            "statusMessage": "personal same-name control"
          }
        ]
      }
    ]
  }
}
JSON
mkdir -p "$CLAUDE_DIR/plugins"
cat > "$CLAUDE_DIR/plugins/installed_plugins.json" <<JSON
{ "version": 2, "plugins": { "escapement@escapement": [
  { "scope": "project", "installPath": "$TD/wrong-project-cache", "version": "wrong" },
  { "scope": "user", "installPath": "$CACHE_OLD", "version": "sha-old" }
] } }
JSON

# Stub the real reinstall side effects: install force-enables and drops model.
cat > "$BIN/claude" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$HOME/claude.log"
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
rotate_registry() { python3 - "$HOME/.claude/plugins/installed_plugins.json" "$HOME/.claude/plugins/cache/escapement/escapement/sha-current" <<'PY'
import json, os, sys
p, new_path = sys.argv[1], sys.argv[2]
if not os.path.isdir(new_path):
    sys.exit(0)
d = json.load(open(p))
d.setdefault("plugins", {})["escapement@escapement"] = [
    {"scope": "user", "installPath": new_path, "version": "sha-current"}
]
json.dump(d, open(p, "w"), indent=2)
PY
}
case "$1 $2" in
  "plugin update")    set_json enable; rotate_registry ;;
  "plugin install")   set_json enable; rotate_registry ;;
  "plugin uninstall") : ;;
  "plugin disable")
    [ "${FAIL_DISABLE:-0}" = "1" ] && exit 19
    set_json disable
    ;;
esac
exit 0
STUB
chmod +x "$BIN/claude"
cat > "$BIN/uname" <<'STUB'
#!/usr/bin/env bash
printf 'Darwin\n'
STUB
cat > "$BIN/launchctl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$HOME/launchctl.log"
state="$HOME/launchctl.loaded"
label="com.escapement.continuation-supervisor"
touch "$state"
if [[ "${1:-}" == print ]]; then
  grep -Fxq "$label" "$state" && exit 0
  exit 113
fi
if [[ "${1:-}" == bootout ]]; then
  grep -Fxq "$label" "$state" || exit 3
  grep -Fvx "$label" "$state" > "$state.next" || true
  mv -f "$state.next" "$state"
  exit 0
fi
if [[ "${1:-}" == bootstrap ]]; then
  grep -Fxq "$label" "$state" && exit 72
  printf '%s\n' "$label" >> "$state"
fi
exit 0
STUB
chmod +x "$BIN/uname" "$BIN/launchctl"

HOME="$HOME_DIR" PATH="$BIN:$PATH" bash "$UPDATER_REPO/scripts/plugin-update.sh" >"$TD/out.log" 2>&1 \
  || { cat "$TD/out.log"; bad "plugin-update.sh exited non-zero"; }

enabled="$(python3 -c "import json;print(json.load(open('$CLAUDE_DIR/settings.json')).get('enabledPlugins',{}).get('escapement@escapement'))")"
model="$(python3 -c "import json;print(json.load(open('$CLAUDE_DIR/settings.json')).get('model'))")"

[ "$enabled" = "False" ] && ok "plugin enabled state preserved" \
  || bad "plugin enabled state changed to '$enabled'"
[ "$model" = "opus[1m]" ] && ok "model key preserved" \
  || bad "model key changed to '$model'"
[ "$(readlink "$CLAUDE_DIR/harness/bin" 2>/dev/null)" = "$CACHE/harness/bin" ] \
  && ok "harness/bin converged to registry-selected plugin" \
  || bad "harness/bin did not converge to $CACHE/harness/bin"
[ "$(readlink "$CLAUDE_DIR/harness/schemas" 2>/dev/null)" = "$CACHE/harness/schemas" ] \
  && ok "harness/schemas converged to registry-selected plugin" \
  || bad "harness/schemas did not converge to $CACHE/harness/schemas"
grep -q '^bootstrap ' "$HOME_DIR/launchctl.log" 2>/dev/null \
  && ok "complete updater fixture reaches the supervisor load boundary" \
  || bad "complete updater fixture never loaded the supervisor"

for stale in \
  "$CLAUDE_DIR/skills/discovery" \
  "$CLAUDE_DIR/agents/adversarial-reviewer.md" \
  "$CLAUDE_DIR/commands/discovery.md" \
  "$CLAUDE_DIR/rules/continuation-harness.md" \
  "$CLAUDE_DIR/hooks/spec_id_enforcement.py" \
  "$CLAUDE_DIR/hooks/codex_final_response_gap.py" \
  "$CLAUDE_DIR/hooks/tests" \
  "$CLAUDE_DIR/hooks/$REPO_ONLY_NAME" \
  "$CLAUDE_DIR/hooks/$REPO_DIRECT_NAME" \
  "$CLAUDE_DIR/hooks/$REPO_ONLY_DIR" \
  "$CLAUDE_DIR/project-bootstrap.sh"
do
  [ ! -e "$stale" ] && [ ! -L "$stale" ] \
    && ok "removed recognized legacy link: ${stale#"$CLAUDE_DIR/"}" \
    || bad "legacy workflow link remains: $stale -> $(readlink "$stale")"
done

[ ! -e "$CLAUDE_DIR/skills/$DYNAMIC_NAME" ] \
  && [ ! -L "$CLAUDE_DIR/skills/$DYNAMIC_NAME" ] \
  && ok "removed runtime-generated plugin-owned skill" \
  || bad "runtime-generated plugin-owned skill remains"
for surface in agents commands rules hooks; do
  [ ! -e "$CLAUDE_DIR/$surface/$DYNAMIC_NAME" ] \
    && [ ! -L "$CLAUDE_DIR/$surface/$DYNAMIC_NAME" ] \
    && ok "removed runtime-generated plugin-owned $surface entry" \
    || bad "runtime-generated plugin-owned $surface entry remains"
done

[ "$(readlink "$CLAUDE_DIR/hooks/obsolete_escapement_hook.py" 2>/dev/null)" = \
  "$PIN/claude/hooks/obsolete_escapement_hook.py" ] \
  && ok "unrecognized legacy hook preserved for explicit disposition" \
  || bad "unrecognized legacy hook was removed"
[ "$(readlink "$CLAUDE_DIR/hooks/$PIN_ONLY_NAME" 2>/dev/null)" = \
  "$PIN/claude/hooks/$PIN_ONLY_NAME" ] \
  && ok "random pin-only hook preserved for explicit disposition" \
  || bad "random pin-only hook was removed"
[ "$(readlink "$CLAUDE_DIR/hooks/$PIN_ONLY_DIR" 2>/dev/null)" = \
  "$PIN/claude/hooks/$PIN_ONLY_DIR" ] \
  && ok "random pin-only directory preserved for explicit disposition" \
  || bad "random pin-only directory was removed"

[ "$(readlink "$CLAUDE_DIR/hooks/jixia_send_bounce.py" 2>/dev/null)" = "$PERSONAL/jixia_send_bounce.py" ] \
  && ok "personal hook symlink preserved" || bad "personal hook symlink changed"
[ "$(readlink "$CLAUDE_DIR/hooks/$REPO_PERSONAL_NAME" 2>/dev/null)" = \
  "$PERSONAL/$REPO_PERSONAL_NAME" ] \
  && ok "repo-known name with personal provenance preserved" \
  || bad "repo-known name with personal provenance changed"
[ "$(readlink "$CLAUDE_DIR/hooks/$REPO_PERSONAL_DIR" 2>/dev/null)" = \
  "$PERSONAL/$REPO_PERSONAL_DIR" ] \
  && ok "repo-known directory with personal provenance preserved" \
  || bad "repo-known directory with personal provenance changed"
[ "$(readlink "$CLAUDE_DIR/hooks/validate_no_shirking.py" 2>/dev/null)" = "$REPO/README.md" ] \
  && ok "unrelated same-name hook symlink preserved" \
  || bad "unrelated same-name hook symlink changed"
[ "$(readlink "$CLAUDE_DIR/skills/build" 2>/dev/null)" = "$PERSONAL/build" ] \
  && ok "unrelated same-name skill symlink preserved" \
  || bad "unrelated same-name skill symlink changed"
[ "$(cat "$CLAUDE_DIR/agents/test-quality-reviewer.md" 2>/dev/null)" = "personal real agent" ] \
  && ok "unrelated same-name real agent preserved" \
  || bad "unrelated same-name real agent changed"
[ "$(readlink "$CLAUDE_DIR/commands/review.md" 2>/dev/null)" = "$PERSONAL/review.md" ] \
  && ok "unrelated same-name command symlink preserved" \
  || bad "unrelated same-name command symlink changed"
[ "$(cat "$CLAUDE_DIR/hooks/personal_real.py" 2>/dev/null)" = "personal real hook" ] \
  && ok "personal real hook preserved" || bad "personal real hook changed"
[ "$(cat "$CLAUDE_DIR/harness/incidents.jsonl" 2>/dev/null)" = "incident state" ] \
  && ok "harness incident state preserved" || bad "harness incident state changed"
[ "$(cat "$CLAUDE_DIR/harness/threads/thread.json" 2>/dev/null)" = "thread state" ] \
  && ok "harness thread state preserved" || bad "harness thread state changed"
[ -x "$CACHE/harness/bin/verify" ] && ok "vendored harness executables repaired" \
  || bad "verify remains non-executable"
grep -q "$PERSONAL/jixia_send_bounce.py" "$CLAUDE_DIR/settings.json" \
  && ok "personal settings hook preserved" || bad "personal settings hook removed"
grep -q "/opt/personal/hooks/validate_no_shirking.py" "$CLAUDE_DIR/settings.json" \
  && ok "same-basename personal settings hook preserved" \
  || bad "same-basename personal settings hook removed"
grep -q "~/.claude/hooks/validate_no_shirking.py" "$CLAUDE_DIR/settings.json" \
  && bad "exact legacy settings hook remains" \
  || ok "exact legacy settings hook pruned"
python3 - "$CLAUDE_DIR/settings.json" <<'PY'
import json, sys
hooks = [
    hook
    for groups in json.load(open(sys.argv[1]))["hooks"].values()
    for group in groups
    for hook in group["hooks"]
]
personal = next(
    hook for hook in hooks
    if hook.get("command") == "python3 -B /opt/personal/hooks/validate_no_shirking.py"
)
assert personal == {
    "type": "command",
    "command": "python3 -B /opt/personal/hooks/validate_no_shirking.py",
    "timeout": 17,
    "statusMessage": "personal same-name control",
}
PY
[ "$?" -eq 0 ] \
  && ok "same-basename personal hook metadata preserved" \
  || bad "same-basename personal hook metadata changed"

# Idempotence: a second update keeps the same ownership and state.
HOME="$HOME_DIR" PATH="$BIN:$PATH" bash "$UPDATER_REPO/scripts/plugin-update.sh" >"$TD/out2.log" 2>&1 \
  || { cat "$TD/out2.log"; bad "second plugin update exited non-zero"; }
[ "$(readlink "$CLAUDE_DIR/harness/bin" 2>/dev/null)" = "$CACHE/harness/bin" ] \
  && ok "second update remains converged" || bad "second update changed harness target"

# Cross-host positive control: the Codex updater intentionally promotes the
# shared harness wrappers into its versioned Escapement plugin cache. Exercise
# a non-default CODEX_HOME so ownership follows the Codex updater's contract.
CODEX_STATE_HOME="$TD/codex-state"
CODEX_CACHE="$CODEX_STATE_HOME/plugins/cache/escapement/escapement/1.0.0"
mkdir -p "$CODEX_CACHE/harness/bin" "$CODEX_CACHE/harness/schemas"
rm -f "$CLAUDE_DIR/harness/bin" "$CLAUDE_DIR/harness/schemas"
ln -s "$CODEX_CACHE/harness/bin" "$CLAUDE_DIR/harness/bin"
ln -s "$CODEX_CACHE/harness/schemas" "$CLAUDE_DIR/harness/schemas"
if HOME="$HOME_DIR" CODEX_HOME="$CODEX_STATE_HOME/" PATH="$BIN:$PATH" \
  bash "$UPDATER_REPO/scripts/plugin-update.sh" >"$TD/codex-wrapper.log" 2>&1
then
  ok "Codex-managed shared harness wrappers are accepted"
else
  cat "$TD/codex-wrapper.log"
  bad "Codex-managed shared harness wrappers were rejected"
fi
[ "$(readlink "$CLAUDE_DIR/harness/bin" 2>/dev/null)" = "$CACHE/harness/bin" ] \
  && [ "$(readlink "$CLAUDE_DIR/harness/schemas" 2>/dev/null)" = "$CACHE/harness/schemas" ] \
  && ok "Claude refresh converges both Codex-managed wrappers" \
  || bad "Claude refresh did not converge Codex-managed wrappers"

# Cross-host negative controls: reject broad Codex paths, nested version
# segments, and component mismatches before any authority state is mutated.
snapshot_deployment_state() {
  python3 - "$CLAUDE_DIR" "$CODEX_STATE_HOME" "$1" <<'PY'
import hashlib, json, os, stat, sys
from pathlib import Path

records = []
roots = [Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])]
try:
    roots.append(Path(sys.argv[3]).resolve(strict=True))
except OSError:
    pass
seen = set()
for root in roots:
    key = str(root.absolute())
    if key in seen:
        continue
    seen.add(key)
    if not root.exists() and not root.is_symlink():
        records.append([key, "missing"])
        continue
    paths = [root]
    if root.is_dir() and not root.is_symlink():
        for parent, directories, files in os.walk(root, followlinks=False):
            paths.extend(Path(parent, name) for name in sorted(directories + files))
    for path in paths:
        metadata = path.lstat()
        record = [key, str(path.absolute()), stat.S_IFMT(metadata.st_mode), stat.S_IMODE(metadata.st_mode)]
        if path.is_symlink():
            record.append(os.readlink(path))
        elif path.is_file():
            record.append(hashlib.sha256(path.read_bytes()).hexdigest())
        records.append(record)
print(json.dumps(records, separators=(",", ":")))
PY
}
reject_codex_wrapper_target() {
  local name="$1" component="$2" target="$3"
  mkdir -p "$target"
  rm -f "$CLAUDE_DIR/harness/bin" "$CLAUDE_DIR/harness/schemas"
  ln -s "$CODEX_CACHE/harness/bin" "$CLAUDE_DIR/harness/bin"
  ln -s "$CODEX_CACHE/harness/schemas" "$CLAUDE_DIR/harness/schemas"
  rm -f "$CLAUDE_DIR/harness/$component"
  ln -s "$target" "$CLAUDE_DIR/harness/$component"
  cp "$CLAUDE_DIR/settings.json" "$TD/$name-settings.before"
  cp "$CLAUDE_DIR/plugins/installed_plugins.json" "$TD/$name-registry.before"
  touch "$HOME_DIR/claude.log" "$HOME_DIR/launchctl.log"
  cp "$HOME_DIR/claude.log" "$TD/$name-claude.before"
  cp "$HOME_DIR/launchctl.log" "$TD/$name-launchctl.before"
  snapshot_deployment_state "$target" > "$TD/$name-tree.before"
  if HOME="$HOME_DIR" CODEX_HOME="${REJECT_CODEX_HOME:-$CODEX_STATE_HOME}" PATH="$BIN:$PATH" \
    bash "$UPDATER_REPO/scripts/plugin-update.sh" >"$TD/$name.log" 2>&1
  then
    bad "$name Codex wrapper should fail closed"
  else
    ok "$name Codex wrapper fails closed"
  fi
  snapshot_deployment_state "$target" > "$TD/$name-tree.after"
  [ "$(readlink "$CLAUDE_DIR/harness/$component" 2>/dev/null)" = "$target" ] \
    && cmp -s "$TD/$name-tree.before" "$TD/$name-tree.after" \
    && cmp -s "$HOME_DIR/claude.log" "$TD/$name-claude.before" \
    && cmp -s "$HOME_DIR/launchctl.log" "$TD/$name-launchctl.before" \
    && ok "$name failure is atomic" || bad "$name failure mutated deployment state"
  cp "$TD/$name-settings.before" "$CLAUDE_DIR/settings.json"
  cp "$TD/$name-registry.before" "$CLAUDE_DIR/plugins/installed_plugins.json"
}
reject_codex_wrapper_target \
  "unrelated-root" "bin" "$CODEX_STATE_HOME/unrelated/harness/bin"
reject_codex_wrapper_target \
  "nested-version" "bin" "$CODEX_CACHE/extra/harness/bin"
reject_codex_wrapper_target \
  "wrong-component" "schemas" "$CODEX_CACHE/harness/bin"
OVERLAP_NESTED="$CLAUDE_DIR/plugins/cache/escapement/escapement/1.0.0/extra/harness/bin"
REJECT_CODEX_HOME="$CLAUDE_DIR" reject_codex_wrapper_target \
  "overlapping-root" "bin" "$OVERLAP_NESTED"
DOTDOT_TARGET="$CODEX_CACHE/../dotdot/harness/bin"
reject_codex_wrapper_target "dotdot" "bin" "$DOTDOT_TARGET"
PINNED_NESTED="$CLAUDE_DIR/.escapement-pinned-legacy/arbitrary/harness/bin"
reject_codex_wrapper_target "pinned-nested" "bin" "$PINNED_NESTED"
ESCAPED_CACHE="$TD/escaped-codex-cache"
mkdir -p "$ESCAPED_CACHE/harness/bin"
ln -s "$ESCAPED_CACHE" \
  "$CODEX_STATE_HOME/plugins/cache/escapement/escapement/escaped"
reject_codex_wrapper_target \
  "symlink-escape" "bin" \
  "$CODEX_STATE_HOME/plugins/cache/escapement/escapement/escaped/harness/bin"
INTERNAL_DEST="$CODEX_STATE_HOME/plugins/cache/escapement/escapement/internal-dest"
INTERNAL_LINK="$CODEX_STATE_HOME/plugins/cache/escapement/escapement/internal/harness/bin"
mkdir -p "$INTERNAL_DEST" "${INTERNAL_LINK%/bin}"
ln -s "$INTERNAL_DEST" "$INTERNAL_LINK"
reject_codex_wrapper_target "internal-redirect" "bin" "$INTERNAL_LINK"
SWAPPED_COMPONENT="$CODEX_STATE_HOME/plugins/cache/escapement/escapement/swapped/harness"
mkdir -p "$SWAPPED_COMPONENT/bin"
ln -s "$SWAPPED_COMPONENT/bin" "$SWAPPED_COMPONENT/schemas"
reject_codex_wrapper_target \
  "symlinked-component" "schemas" "$SWAPPED_COMPONENT/schemas"
cp "$UPDATER_REPO/scripts/plugin-wrapper-target.py" "$TD/wrapper-helper.before"
printf '#!/bin/sh\nexit 3\n' > "$UPDATER_REPO/scripts/plugin-wrapper-target.py"
chmod +x "$UPDATER_REPO/scripts/plugin-wrapper-target.py"
reject_codex_wrapper_target \
  "classifier-failure" "bin" "$CODEX_CACHE/harness/bin"
cp "$TD/wrapper-helper.before" "$UPDATER_REPO/scripts/plugin-wrapper-target.py"
chmod +x "$UPDATER_REPO/scripts/plugin-wrapper-target.py"
rm -f "$CLAUDE_DIR/harness/bin" "$CLAUDE_DIR/harness/schemas"
ln -s "$CACHE/harness/bin" "$CLAUDE_DIR/harness/bin"
ln -s "$CACHE/harness/schemas" "$CLAUDE_DIR/harness/schemas"

# Phase-split negative control: a legacy package may refresh without the new
# dependencies, but the refreshed package must satisfy the full current
# contract before any filesystem migration begins.
cp "$CACHE/hooks/magic_number_echo.py" "$TD/magic_number_echo.py"
rm -f "$CACHE/hooks/magic_number_echo.py"
rm -f "$CLAUDE_DIR/harness/bin"
ln -s "$PIN/harness/bin" "$CLAUDE_DIR/harness/bin"
ln -s "$PIN/claude/skills/discovery" "$CLAUDE_DIR/skills/discovery"
chmod 644 "$CACHE/harness/bin/verify"
python3 - "$CLAUDE_DIR/plugins/installed_plugins.json" "$CACHE_OLD" <<'PY'
import json, sys
p, old = sys.argv[1:]
d = json.load(open(p))
d["plugins"]["escapement@escapement"] = [
    {"scope": "user", "installPath": old, "version": "sha-old"}
]
json.dump(d, open(p, "w"), indent=2)
PY
cp "$CLAUDE_DIR/settings.json" "$TD/incomplete-settings.before"
cp "$CLAUDE_DIR/plugins/installed_plugins.json" "$TD/incomplete-registry.before"
incomplete_wrapper_before="$(readlink "$CLAUDE_DIR/harness/bin")"
incomplete_skill_before="$(readlink "$CLAUDE_DIR/skills/discovery")"
if HOME="$HOME_DIR" PATH="$BIN:$PATH" bash "$UPDATER_REPO/scripts/plugin-update.sh" \
  >"$TD/incomplete.log" 2>&1
then
  cat "$TD/incomplete.log"
  bad "incomplete refreshed package should fail closed"
else
  ok "incomplete refreshed package fails closed"
fi
cmp -s "$CLAUDE_DIR/settings.json" "$TD/incomplete-settings.before" \
  && cmp -s "$CLAUDE_DIR/plugins/installed_plugins.json" "$TD/incomplete-registry.before" \
  && ok "incomplete refresh restores settings and registry byte-for-byte" \
  || bad "incomplete refresh left authority state mutated"
[ "$(readlink "$CLAUDE_DIR/harness/bin")" = "$incomplete_wrapper_before" ] \
  && [ "$(readlink "$CLAUDE_DIR/skills/discovery")" = "$incomplete_skill_before" ] \
  && [ ! -x "$CACHE/harness/bin/verify" ] \
  && ok "incomplete refresh preserves wrappers, links, and modes" \
  || bad "incomplete refresh partially migrated filesystem state"
cp "$TD/magic_number_echo.py" "$CACHE/hooks/magic_number_echo.py"
HOME="$HOME_DIR" PATH="$BIN:$PATH" bash "$UPDATER_REPO/scripts/plugin-update.sh" \
  >"$TD/post-incomplete-recover.log" 2>&1 \
  || bad "could not recover after incomplete-package control"

# Failure controls: restoration or pruning failures must roll settings back,
# return non-zero, and never print the success terminator.
rm -f "$CLAUDE_DIR/harness/bin"
ln -s "$PIN/harness/bin" "$CLAUDE_DIR/harness/bin"
ln -s "$PIN/claude/skills/discovery" "$CLAUDE_DIR/skills/discovery"
python3 - "$CLAUDE_DIR/plugins/installed_plugins.json" "$CACHE_OLD" <<'PY'
import json, sys
p, old = sys.argv[1:]
d = json.load(open(p))
d["plugins"]["escapement@escapement"] = [
    {"scope": "user", "installPath": old, "version": "sha-old"}
]
json.dump(d, open(p, "w"), indent=2)
PY
cp "$CLAUDE_DIR/settings.json" "$TD/disable-settings.before"
cp "$CLAUDE_DIR/plugins/installed_plugins.json" "$TD/disable-registry.before"
disable_wrapper_before="$(readlink "$CLAUDE_DIR/harness/bin")"
disable_skill_before="$(readlink "$CLAUDE_DIR/skills/discovery")"
if HOME="$HOME_DIR" PATH="$BIN:$PATH" FAIL_DISABLE=1 \
  bash "$UPDATER_REPO/scripts/plugin-update.sh" >"$TD/disable-fail.log" 2>&1
then
  cat "$TD/disable-fail.log"
  bad "disabled-state restoration failure was swallowed"
else
  ok "disabled-state restoration failure propagates"
fi
cmp -s "$CLAUDE_DIR/settings.json" "$TD/disable-settings.before" \
  && ok "disabled-state failure restores settings byte-for-byte" \
  || bad "disabled-state failure left settings mutated"
cmp -s "$CLAUDE_DIR/plugins/installed_plugins.json" "$TD/disable-registry.before" \
  && ok "disabled-state failure restores plugin registry byte-for-byte" \
  || bad "disabled-state failure left plugin registry mutated"
[ "$(readlink "$CLAUDE_DIR/harness/bin")" = "$disable_wrapper_before" ] \
  && [ "$(readlink "$CLAUDE_DIR/skills/discovery")" = "$disable_skill_before" ] \
  && ok "disabled-state failure preserves cutover filesystem state" \
  || bad "disabled-state failure changed cutover filesystem state"
grep -q "==> done" "$TD/disable-fail.log" \
  && bad "disabled-state failure reported completion" \
  || ok "disabled-state failure does not report completion"

# Re-converge before constructing the later pruner failure.
HOME="$HOME_DIR" PATH="$BIN:$PATH" bash "$UPDATER_REPO/scripts/plugin-update.sh" \
  >"$TD/recover.log" 2>&1 || bad "could not recover after disable failure control"
rm -f "$CLAUDE_DIR/harness/bin"
ln -s "$PIN/harness/bin" "$CLAUDE_DIR/harness/bin"
ln -s "$PIN/claude/skills/discovery" "$CLAUDE_DIR/skills/discovery"
chmod 644 "$CACHE/harness/bin/verify"
python3 - "$CLAUDE_DIR/plugins/installed_plugins.json" "$CACHE_OLD" <<'PY'
import json, sys
p, old = sys.argv[1:]
d = json.load(open(p))
d["plugins"]["escapement@escapement"] = [
    {"scope": "user", "installPath": old, "version": "sha-old"}
]
json.dump(d, open(p, "w"), indent=2)
PY
cp "$CACHE/hooks/hooks.json" "$TD/hooks.before"
cp "$CLAUDE_DIR/settings.json" "$TD/prune-settings.before"
cp "$CLAUDE_DIR/plugins/installed_plugins.json" "$TD/prune-registry.before"
prune_wrapper_before="$(readlink "$CLAUDE_DIR/harness/bin")"
prune_skill_before="$(readlink "$CLAUDE_DIR/skills/discovery")"
printf '{"hooks":{}}\n' > "$CACHE/hooks/hooks.json"
if HOME="$HOME_DIR" PATH="$BIN:$PATH" \
  bash "$UPDATER_REPO/scripts/plugin-update.sh" >"$TD/prune-fail.log" 2>&1
then
  cat "$TD/prune-fail.log"
  bad "settings-pruner failure was swallowed"
else
  ok "settings-pruner failure propagates"
fi
cmp -s "$CLAUDE_DIR/settings.json" "$TD/prune-settings.before" \
  && ok "pruner failure restores settings byte-for-byte" \
  || bad "pruner failure left settings mutated"
cmp -s "$CLAUDE_DIR/plugins/installed_plugins.json" "$TD/prune-registry.before" \
  && ok "pruner failure restores plugin registry byte-for-byte" \
  || bad "pruner failure left plugin registry mutated"
[ "$(readlink "$CLAUDE_DIR/harness/bin")" = "$prune_wrapper_before" ] \
  && [ "$(readlink "$CLAUDE_DIR/skills/discovery")" = "$prune_skill_before" ] \
  && [ ! -x "$CACHE/harness/bin/verify" ] \
  && ok "pruner failure preserves wrappers, workflow links, and modes" \
  || bad "pruner failure partially changed cutover filesystem state"
grep -q "==> done" "$TD/prune-fail.log" \
  && bad "pruner failure reported completion" \
  || ok "pruner failure does not report completion"
cp "$TD/hooks.before" "$CACHE/hooks/hooks.json"

# Negative control: an unrelated cache sibling must not substitute for a valid
# user-scope registry entry. Migration must fail before touching legacy links.
BAD_HOME="$TD/bad-home"
BAD_CLAUDE="$BAD_HOME/.claude"
BAD_PIN="$BAD_CLAUDE/.escapement-pinned"
mkdir -p \
  "$BAD_CLAUDE/harness" \
  "$BAD_PIN/harness/bin" \
  "$BAD_PIN/claude/skills/discovery" \
  "$BAD_PIN/claude/agents" \
  "$BAD_PIN/claude/commands" \
  "$BAD_PIN/claude/rules" \
  "$BAD_PIN/claude/hooks" \
  "$BAD_CLAUDE/skills" \
  "$BAD_CLAUDE/agents" \
  "$BAD_CLAUDE/commands" \
  "$BAD_CLAUDE/rules" \
  "$BAD_CLAUDE/hooks" \
  "$BAD_CLAUDE/plugins/cache/escapement/escapement/arbitrary-cache/harness/bin"
ln -s "$BAD_PIN/harness/bin" "$BAD_CLAUDE/harness/bin"
ln -s "$BAD_PIN/claude/skills/discovery" "$BAD_CLAUDE/skills/discovery"
ln -s "$BAD_PIN/claude/agents/adversarial-reviewer.md" "$BAD_CLAUDE/agents/adversarial-reviewer.md"
ln -s "$BAD_PIN/claude/commands/discovery.md" "$BAD_CLAUDE/commands/discovery.md"
ln -s "$BAD_PIN/claude/rules/continuation-harness.md" "$BAD_CLAUDE/rules/continuation-harness.md"
ln -s "$BAD_PIN/claude/hooks/spec_id_enforcement.py" "$BAD_CLAUDE/hooks/spec_id_enforcement.py"
printf 'personal failure control\n' > "$BAD_CLAUDE/hooks/personal.py"
printf 'runtime failure control\n' > "$BAD_CLAUDE/harness/incidents.jsonl"
cat > "$BAD_CLAUDE/settings.json" <<JSON
{
  "model": "sonnet",
  "enabledPlugins": { "escapement@escapement": false },
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 $BAD_CLAUDE/hooks/personal.py" } ] }
    ]
  }
}
JSON
cp "$BAD_CLAUDE/settings.json" "$TD/bad-settings.before"
cat > "$BAD_CLAUDE/plugins/installed_plugins.json" <<JSON
{ "version": 2, "plugins": { "escapement@escapement": [
  { "scope": "project", "installPath": "$BAD_CLAUDE/plugins/cache/escapement/escapement/arbitrary-cache" }
] } }
JSON
if HOME="$BAD_HOME" PATH="$BIN:$PATH" bash "$UPDATER_REPO/scripts/plugin-update.sh" >"$TD/bad.log" 2>&1; then
  cat "$TD/bad.log"
  bad "missing user-scope plugin should fail closed"
else
  ok "missing user-scope plugin fails closed"
fi
[ "$(readlink "$BAD_CLAUDE/harness/bin" 2>/dev/null)" = "$BAD_PIN/harness/bin" ] \
  && ok "failed migration leaves legacy deployment untouched" \
  || bad "failed migration modified the legacy harness link"
for preserved in \
  "skills/discovery|$BAD_PIN/claude/skills/discovery" \
  "agents/adversarial-reviewer.md|$BAD_PIN/claude/agents/adversarial-reviewer.md" \
  "commands/discovery.md|$BAD_PIN/claude/commands/discovery.md" \
  "rules/continuation-harness.md|$BAD_PIN/claude/rules/continuation-harness.md" \
  "hooks/spec_id_enforcement.py|$BAD_PIN/claude/hooks/spec_id_enforcement.py"
do
  path="${preserved%|*}"
  target="${preserved#*|}"
  [ "$(readlink "$BAD_CLAUDE/$path" 2>/dev/null)" = "$target" ] \
    && ok "authority failure preserves $path" \
    || bad "authority failure changed $path"
done
[ "$(cat "$BAD_CLAUDE/hooks/personal.py" 2>/dev/null)" = "personal failure control" ] \
  && ok "authority failure preserves personal file" || bad "authority failure changed personal file"
[ "$(cat "$BAD_CLAUDE/harness/incidents.jsonl" 2>/dev/null)" = "runtime failure control" ] \
  && ok "authority failure preserves runtime state" || bad "authority failure changed runtime state"
cmp -s "$BAD_CLAUDE/settings.json" "$TD/bad-settings.before" \
  && ok "authority failure preserves settings byte-for-byte" \
  || bad "authority failure changed settings before aborting"

# Negative control: a valid plugin does not authorize replacing unknown real
# content at a stable-wrapper path.
REAL_HOME="$TD/real-home"
REAL_CLAUDE="$REAL_HOME/.claude"
REAL_PIN="$REAL_CLAUDE/.escapement-pinned"
mkdir -p \
  "$REAL_CLAUDE/harness/bin" \
  "$REAL_CLAUDE/plugins" \
  "$REAL_PIN/claude/skills/discovery" \
  "$REAL_PIN/claude/agents" \
  "$REAL_PIN/claude/commands" \
  "$REAL_PIN/claude/rules" \
  "$REAL_PIN/claude/hooks" \
  "$REAL_CLAUDE/skills" \
  "$REAL_CLAUDE/agents" \
  "$REAL_CLAUDE/commands" \
  "$REAL_CLAUDE/rules" \
  "$REAL_CLAUDE/hooks"
printf 'owned state\n' > "$REAL_CLAUDE/harness/bin/user-marker"
ln -s "$REAL_PIN/claude/skills/discovery" "$REAL_CLAUDE/skills/discovery"
ln -s "$REAL_PIN/claude/agents/adversarial-reviewer.md" "$REAL_CLAUDE/agents/adversarial-reviewer.md"
ln -s "$REAL_PIN/claude/commands/discovery.md" "$REAL_CLAUDE/commands/discovery.md"
ln -s "$REAL_PIN/claude/rules/continuation-harness.md" "$REAL_CLAUDE/rules/continuation-harness.md"
ln -s "$REAL_PIN/claude/hooks/spec_id_enforcement.py" "$REAL_CLAUDE/hooks/spec_id_enforcement.py"
printf 'personal wrapper control\n' > "$REAL_CLAUDE/hooks/personal.py"
cat > "$REAL_CLAUDE/settings.json" <<JSON
{
  "model": "haiku",
  "enabledPlugins": { "escapement@escapement": false },
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 $REAL_CLAUDE/hooks/personal.py" } ] }
    ]
  }
}
JSON
cp "$REAL_CLAUDE/settings.json" "$TD/real-settings.before"
cat > "$REAL_CLAUDE/plugins/installed_plugins.json" <<JSON
{ "version": 2, "plugins": { "escapement@escapement": [
  { "scope": "user", "installPath": "$CACHE", "version": "sha-current" }
] } }
JSON
if HOME="$REAL_HOME" PATH="$BIN:$PATH" bash "$UPDATER_REPO/scripts/plugin-update.sh" >"$TD/real.log" 2>&1; then
  cat "$TD/real.log"
  bad "unknown real harness/bin should fail closed"
else
  ok "unknown real harness/bin fails closed"
fi
[ "$(cat "$REAL_CLAUDE/harness/bin/user-marker" 2>/dev/null)" = "owned state" ] \
  && ok "failed wrapper migration preserves unknown real content" \
  || bad "failed wrapper migration changed unknown real content"
for preserved in \
  "skills/discovery|$REAL_PIN/claude/skills/discovery" \
  "agents/adversarial-reviewer.md|$REAL_PIN/claude/agents/adversarial-reviewer.md" \
  "commands/discovery.md|$REAL_PIN/claude/commands/discovery.md" \
  "rules/continuation-harness.md|$REAL_PIN/claude/rules/continuation-harness.md" \
  "hooks/spec_id_enforcement.py|$REAL_PIN/claude/hooks/spec_id_enforcement.py"
do
  path="${preserved%|*}"
  target="${preserved#*|}"
  [ "$(readlink "$REAL_CLAUDE/$path" 2>/dev/null)" = "$target" ] \
    && ok "wrapper failure preserves $path" \
    || bad "wrapper failure changed $path"
done
[ "$(cat "$REAL_CLAUDE/hooks/personal.py" 2>/dev/null)" = "personal wrapper control" ] \
  && ok "wrapper failure preserves personal file" || bad "wrapper failure changed personal file"
cmp -s "$REAL_CLAUDE/settings.json" "$TD/real-settings.before" \
  && ok "wrapper failure preserves settings byte-for-byte" \
  || bad "wrapper failure changed settings before aborting"

# Negative control: a symlink into this repository is not automatically an
# Escapement harness wrapper. The semantic target must be exact.
LINK_HOME="$TD/unrelated-wrapper-home"
LINK_CLAUDE="$LINK_HOME/.claude"
mkdir -p "$LINK_CLAUDE/harness" "$LINK_CLAUDE/plugins"
ln -s "$REPO/README.md" "$LINK_CLAUDE/harness/bin"
cat > "$LINK_CLAUDE/settings.json" <<'JSON'
{
  "model": "opus",
  "enabledPlugins": { "escapement@escapement": true },
  "hooks": {}
}
JSON
cat > "$LINK_CLAUDE/plugins/installed_plugins.json" <<JSON
{ "version": 2, "plugins": { "escapement@escapement": [
  { "scope": "user", "installPath": "$CACHE", "version": "sha-current" }
] } }
JSON
cp "$LINK_CLAUDE/settings.json" "$TD/link-settings.before"
cp "$LINK_CLAUDE/plugins/installed_plugins.json" "$TD/link-registry.before"
if HOME="$LINK_HOME" PATH="$BIN:$PATH" bash "$UPDATER_REPO/scripts/plugin-update.sh" \
  >"$TD/link.log" 2>&1
then
  cat "$TD/link.log"
  bad "repository-local unrelated wrapper should fail closed"
else
  ok "repository-local unrelated wrapper fails closed"
fi
[ "$(readlink "$LINK_CLAUDE/harness/bin" 2>/dev/null)" = "$REPO/README.md" ] \
  && ok "repository-local unrelated wrapper preserved" \
  || bad "repository-local unrelated wrapper was replaced"
cmp -s "$LINK_CLAUDE/settings.json" "$TD/link-settings.before" \
  && cmp -s "$LINK_CLAUDE/plugins/installed_plugins.json" "$TD/link-registry.before" \
  && ok "unrelated-wrapper failure preserves authority state byte-for-byte" \
  || bad "unrelated-wrapper failure changed settings or registry"
[ -z "$(find "$LINK_CLAUDE" -maxdepth 1 -name '.cutover-backup-*' -print)" ] \
  && ok "unrelated-wrapper failure creates no backup residue" \
  || bad "unrelated-wrapper failure mutated state before validation"

[ "$fail" -eq 0 ] && echo "PASS: plugin update converges durable cutover ownership" \
  || echo "FAILURES above"
exit "$fail"
