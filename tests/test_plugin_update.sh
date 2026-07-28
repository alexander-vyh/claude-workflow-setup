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
HOME_DIR="$TD/home"
BIN="$TD/bin"
CLAUDE_DIR="$HOME_DIR/.claude"
CACHE="$CLAUDE_DIR/plugins/cache/escapement/escapement/sha-current"
CACHE_OLD="$CLAUDE_DIR/plugins/cache/escapement/escapement/sha-old"
PIN="$CLAUDE_DIR/.escapement-pinned-legacy"
PERSONAL="$TD/personal"

mkdir -p \
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

# Representative plugin payload.
cp "$REPO/plugins/escapement-claude/harness/bin/stop_hook.py" "$CACHE/harness/bin/stop_hook.py"
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

# Fully regressed post-cutover state: workflow surfaces point into the pin.
ln -s "$PIN/harness/bin" "$CLAUDE_DIR/harness/bin"
ln -s "$PIN/harness/schemas" "$CLAUDE_DIR/harness/schemas"
ln -s "$PIN/claude/skills/discovery" "$CLAUDE_DIR/skills/discovery"
ln -s "$PIN/claude/agents/adversarial-reviewer.md" "$CLAUDE_DIR/agents/adversarial-reviewer.md"
ln -s "$PIN/claude/commands/discovery.md" "$CLAUDE_DIR/commands/discovery.md"
ln -s "$PIN/claude/rules/continuation-harness.md" "$CLAUDE_DIR/rules/continuation-harness.md"
ln -s "$PIN/claude/hooks/spec_id_enforcement.py" "$CLAUDE_DIR/hooks/spec_id_enforcement.py"
ln -s "$PIN/claude/hooks/obsolete_escapement_hook.py" "$CLAUDE_DIR/hooks/obsolete_escapement_hook.py"
ln -s "$PIN/scripts/project-bootstrap.sh" "$CLAUDE_DIR/project-bootstrap.sh"
ln -s "$PIN/claude/skills/$DYNAMIC_NAME" "$CLAUDE_DIR/skills/$DYNAMIC_NAME"
for surface in agents commands rules hooks; do
  ln -s "$PIN/claude/$surface/$DYNAMIC_NAME" "$CLAUDE_DIR/$surface/$DYNAMIC_NAME"
done

# Positive controls: personal hook/link, custom real file, and runtime state.
printf '#!/usr/bin/env python3\n' > "$PERSONAL/jixia_send_bounce.py"
mkdir -p "$PERSONAL/build"
printf 'personal build skill\n' > "$PERSONAL/build/SKILL.md"
printf 'personal review command\n' > "$PERSONAL/review.md"
ln -s "$PERSONAL/jixia_send_bounce.py" "$CLAUDE_DIR/hooks/jixia_send_bounce.py"
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

HOME="$HOME_DIR" PATH="$BIN:$PATH" bash "$REPO/scripts/plugin-update.sh" >"$TD/out.log" 2>&1 \
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

for stale in \
  "$CLAUDE_DIR/skills/discovery" \
  "$CLAUDE_DIR/agents/adversarial-reviewer.md" \
  "$CLAUDE_DIR/commands/discovery.md" \
  "$CLAUDE_DIR/rules/continuation-harness.md" \
  "$CLAUDE_DIR/hooks/spec_id_enforcement.py" \
  "$CLAUDE_DIR/project-bootstrap.sh"
do
  [ ! -L "$stale" ] && ok "removed recognized legacy link: ${stale#"$CLAUDE_DIR/"}" \
    || bad "legacy workflow link remains: $stale -> $(readlink "$stale")"
done

[ ! -L "$CLAUDE_DIR/skills/$DYNAMIC_NAME" ] \
  && ok "removed runtime-generated plugin-owned skill" \
  || bad "runtime-generated plugin-owned skill remains"
for surface in agents commands rules hooks; do
  [ ! -L "$CLAUDE_DIR/$surface/$DYNAMIC_NAME" ] \
    && ok "removed runtime-generated plugin-owned $surface entry" \
    || bad "runtime-generated plugin-owned $surface entry remains"
done

[ "$(readlink "$CLAUDE_DIR/hooks/obsolete_escapement_hook.py" 2>/dev/null)" = \
  "$PIN/claude/hooks/obsolete_escapement_hook.py" ] \
  && ok "unrecognized legacy hook preserved for explicit disposition" \
  || bad "unrecognized legacy hook was removed"

[ "$(readlink "$CLAUDE_DIR/hooks/jixia_send_bounce.py" 2>/dev/null)" = "$PERSONAL/jixia_send_bounce.py" ] \
  && ok "personal hook symlink preserved" || bad "personal hook symlink changed"
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
HOME="$HOME_DIR" PATH="$BIN:$PATH" bash "$REPO/scripts/plugin-update.sh" >"$TD/out2.log" 2>&1 \
  || { cat "$TD/out2.log"; bad "second plugin update exited non-zero"; }
[ "$(readlink "$CLAUDE_DIR/harness/bin" 2>/dev/null)" = "$CACHE/harness/bin" ] \
  && ok "second update remains converged" || bad "second update changed harness target"

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
  bash "$REPO/scripts/plugin-update.sh" >"$TD/disable-fail.log" 2>&1
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
HOME="$HOME_DIR" PATH="$BIN:$PATH" bash "$REPO/scripts/plugin-update.sh" \
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
  bash "$REPO/scripts/plugin-update.sh" >"$TD/prune-fail.log" 2>&1
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
if HOME="$BAD_HOME" PATH="$BIN:$PATH" bash "$REPO/scripts/plugin-update.sh" >"$TD/bad.log" 2>&1; then
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
if HOME="$REAL_HOME" PATH="$BIN:$PATH" bash "$REPO/scripts/plugin-update.sh" >"$TD/real.log" 2>&1; then
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
if HOME="$LINK_HOME" PATH="$BIN:$PATH" bash "$REPO/scripts/plugin-update.sh" \
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
