#!/usr/bin/env bash
# Test: INSTALL.sh --update warns + aborts on deploy-dir drift (uncommitted edits
# made directly in the pinned checkout), with an --allow-pinned-drift escape.
#
# Business invariant: a hand-edit made directly in the pinned deploy checkout
# bypasses review and is invisible until it conflicts with a later update (two such
# drifts were recovered on 2026-06-14). `--update` must surface it loudly with an
# escape path, not silently strand it. The fragile impl this rejects: refreshing the
# pinned checkout while ignoring its dirty working tree.
#
# Offline + isolated: throwaway HOME, installed-plugin fixture, stub Claude CLI,
# and local repo as the pin remote. No real ~/.claude.
# Run: bash tests/test_install_pinned_drift.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_REF="$(git -C "$REPO" rev-parse HEAD)"
fail=0
ok()  { printf '  ok: %s\n' "$*"; }
bad() { printf '  FAIL: %s\n' "$*"; fail=1; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
BIN="$T/stub-bin"
CACHE="$T/.claude/plugins/cache/escapement/escapement/sha-current"
mkdir -p "$BIN" "$CACHE" "$T/.claude/plugins"
cp -R "$REPO/plugins/escapement-claude/." "$CACHE/"
cat > "$T/.claude/settings.json" <<JSON
{
  "model": "opus[1m]",
  "enabledPlugins": { "escapement@escapement": true },
  "hooks": {}
}
JSON
cat > "$T/.claude/plugins/installed_plugins.json" <<JSON
{ "version": 2, "plugins": { "escapement@escapement": [
  { "scope": "user", "installPath": "$CACHE", "version": "sha-current" }
] } }
JSON
ln -s "$REPO/tests/helpers/fake_delegation_canary_claude.py" "$BIN/claude"
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
chmod +x "$BIN/claude" "$BIN/uname" "$BIN/launchctl"
launchctl() {
  local state="$HOME/launchctl.loaded"
  local label="com.escapement.continuation-supervisor"
  touch "$state"
  printf '%s\n' "$*" >> "$HOME/launchctl.log"
  if [[ "${1:-}" == print ]]; then
    grep -Fxq "$label" "$state" && return 0
    return 113
  fi
  if [[ "${1:-}" == bootout ]]; then
    grep -Fxq "$label" "$state" || return 3
    grep -Fvx "$label" "$state" > "$state.next" || true
    mv -f "$state.next" "$state"
    return 0
  fi
  if [[ "${1:-}" == bootstrap ]]; then
    grep -Fxq "$label" "$state" && return 72
    printf '%s\n' "$label" >> "$state"
  fi
  return 0
}
export -f launchctl
ENV=(HOME="$T" PATH="$BIN:$PATH" ESCAPEMENT_PIN_REMOTE="$REPO" ESCAPEMENT_PIN_REF="$BASE_REF")

# Initial install creates the pinned checkout.
env "${ENV[@]}" bash "$REPO/INSTALL.sh" >"$T/install.log" 2>&1 \
  || { cat "$T/install.log"; bad "initial install failed"; }
python3 "$REPO/tests/helpers/fake_delegation_canary_claude.py" \
  --assert-audit "$T/canary-audit.jsonl" \
  && ok "initial install exercised armed native-hook canary" \
  || bad "initial install did not exercise armed native-hook canary"
PIN="$T/.claude/.escapement-pinned"
[ -d "$PIN/.git" ] && ok "pinned checkout created" || bad "no pinned checkout at $PIN"

# Sanity: pinned checkout is clean right after install (else the test is moot).
[ -z "$(git -C "$PIN" status --porcelain 2>/dev/null)" ] \
  && ok "pinned checkout clean post-install" || bad "pinned checkout dirty post-install (unexpected)"

# Introduce deploy-dir drift: edit a tracked file directly in the pinned checkout.
echo "# injected drift" >> "$PIN/INSTALL.sh"

# --update WITHOUT the flag must ABORT and name the drift.
if env "${ENV[@]}" bash "$REPO/INSTALL.sh" --update >"$T/u1.log" 2>&1; then
  cat "$T/u1.log"; bad "update with drift should have aborted (exited 0)"
else
  ok "update aborts on drift (non-zero exit)"
fi
grep -qi "deploy-dir drift" "$T/u1.log" && ok "drift warning shown" || { cat "$T/u1.log"; bad "no drift warning in output"; }
grep -q "INSTALL.sh" "$T/u1.log" && ok "drift listing names the drifted file" || bad "drift listing did not name the file"

# --update WITH --allow-pinned-drift must PROCEED.
rm -f "$T/canary-audit.jsonl"
if env "${ENV[@]}" bash "$REPO/INSTALL.sh" --update --allow-pinned-drift >"$T/u2.log" 2>&1; then
  ok "--allow-pinned-drift proceeds"
else
  cat "$T/u2.log"; bad "--allow-pinned-drift should proceed (exited non-zero)"
fi
python3 "$REPO/tests/helpers/fake_delegation_canary_claude.py" \
  --assert-audit "$T/canary-audit.jsonl" \
  && ok "allowed drift update exercised armed native-hook canary" \
  || bad "allowed drift update did not exercise armed native-hook canary"

[ "$fail" -eq 0 ] && { echo "PASS"; exit 0; } || { echo "FAILED"; exit 1; }
