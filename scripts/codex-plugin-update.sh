#!/bin/bash
# Refresh the Codex plugin and conservatively migrate Escapement's legacy skill.

set -euo pipefail

PLUGIN_ID="escapement@escapement"
MARKETPLACE="escapement"
CODEX_BIN="${CODEX_BIN:-codex}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GLOBAL_SKILL="${ESCAPEMENT_GLOBAL_BEADS_SKILL:-$HOME/.agents/skills/beads-execution/SKILL.md}"

command -v "$CODEX_BIN" >/dev/null || {
  echo "FATAL: Codex CLI not found: $CODEX_BIN" >&2
  exit 1
}

marketplaces="$("$CODEX_BIN" plugin marketplace list --json)"
source_type="$(
  python3 -c '
import json, sys
data = json.load(sys.stdin)
for item in data.get("marketplaces", []):
    if item.get("name") == sys.argv[1]:
        print(item.get("marketplaceSource", {}).get("sourceType", ""))
        break
' "$MARKETPLACE" <<<"$marketplaces"
)"
if [[ -z "$source_type" ]]; then
  echo "FATAL: Codex marketplace is not configured: $MARKETPLACE" >&2
  exit 1
fi

if [[ "$source_type" == "git" ]]; then
  "$CODEX_BIN" plugin marketplace upgrade "$MARKETPLACE"
fi

before="$("$CODEX_BIN" plugin list --marketplace "$MARKETPLACE" --json)"
was_installed="$(
  python3 -c '
import json, sys
data = json.load(sys.stdin)
print("true" if any(item.get("pluginId") == sys.argv[1] for item in data.get("installed", [])) else "false")
' "$PLUGIN_ID" <<<"$before"
)"
if [[ "$was_installed" == "true" ]]; then
  "$CODEX_BIN" plugin remove "$PLUGIN_ID"
fi
"$CODEX_BIN" plugin add "$PLUGIN_ID"

after="$("$CODEX_BIN" plugin list --marketplace "$MARKETPLACE" --json)"
plugin_root="$(
  python3 -c '
import json, sys
data = json.load(sys.stdin)
for item in data.get("installed", []):
    if item.get("pluginId") == sys.argv[1]:
        source = item.get("source", {})
        if source.get("source") == "local":
            print(source.get("path", ""))
        break
' "$PLUGIN_ID" <<<"$after"
)"
if [[ -z "$plugin_root" || ! -d "$plugin_root" ]]; then
  echo "FATAL: could not resolve installed Escapement plugin path" >&2
  exit 1
fi

source_skill="$plugin_root/skills/beads-execution/SKILL.md"
if [[ ! -f "$source_skill" ]]; then
  echo "FATAL: installed plugin lacks bounded Beads skill: $source_skill" >&2
  exit 1
fi

authoritative_skill="$REPO_DIR/plugins/escapement/skills/beads-execution/SKILL.md"
authoritative_hooks="$REPO_DIR/plugins/escapement/hooks/hooks.json"
authoritative_manifest="$REPO_DIR/plugins/escapement/.codex-plugin/plugin.json"
for pair in \
  "$authoritative_skill:$source_skill" \
  "$authoritative_hooks:$plugin_root/hooks/hooks.json" \
  "$authoritative_manifest:$plugin_root/.codex-plugin/plugin.json"
do
  expected="${pair%%:*}"
  installed="${pair#*:}"
  if [[ ! -f "$installed" ]] || ! cmp -s "$expected" "$installed"; then
    echo "FATAL: installed plugin surface is stale or differs from checkout: $installed" >&2
    exit 1
  fi
done

python3 "$REPO_DIR/scripts/migrate_codex_beads_skill.py" \
  "$authoritative_skill" \
  "$GLOBAL_SKILL"

if [[ -f "$GLOBAL_SKILL" ]] && ! cmp -s "$authoritative_skill" "$GLOBAL_SKILL"; then
  echo "FATAL: effective global Beads skill does not match the installed plugin" >&2
  exit 1
fi

python3 - "$REPO_DIR/.codex/hooks.json" "$authoritative_hooks" <<'PY'
import json
import sys
from pathlib import Path

repo_hooks = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["hooks"]
plugin_hooks = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["hooks"]
if repo_hooks:
    raise SystemExit("FATAL: repo-local Codex hooks are not empty")
if not plugin_hooks:
    raise SystemExit("FATAL: installed Escapement plugin has no hooks")

for event in ("SessionStart", "PreCompact"):
    context_commands = [
        hook.get("command", "")
        for group in plugin_hooks.get(event, [])
        for hook in group.get("hooks", [])
        if "escapement_session_context.py" in hook.get("command", "")
    ]
    if len(context_commands) != 1:
        raise SystemExit(
            f"FATAL: expected one {event} session-context registration, "
            f"found {len(context_commands)}"
        )
PY

echo "==> OK: Codex plugin refreshed and effective Beads routing skill verified."
