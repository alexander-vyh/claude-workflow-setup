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
CODEX_STATE_HOME="${CODEX_HOME:-$HOME/.codex}"
plugin_root="$(
  python3 -c '
import json, sys
from pathlib import Path
data = json.load(sys.stdin)
for item in data.get("installed", []):
    if item.get("pluginId") == sys.argv[1]:
        marketplace = item.get("marketplaceName")
        name = item.get("name")
        version = item.get("version")
        parts = (marketplace, name, version)
        if all(isinstance(part, str) and part and "/" not in part and part not in {".", ".."} for part in parts):
            print(Path(sys.argv[2]) / "plugins" / "cache" / marketplace / name / version)
        break
' "$PLUGIN_ID" "$CODEX_STATE_HOME" <<<"$after"
)"
if [[ -z "$plugin_root" || ! -d "$plugin_root" ]]; then
  echo "FATAL: could not resolve versioned installed Escapement plugin path" >&2
  exit 1
fi

authoritative_root="$REPO_DIR/plugins/escapement"
python3 - "$authoritative_root" "$plugin_root" <<'PY'
import sys
from pathlib import Path

expected_root = Path(sys.argv[1])
installed_root = Path(sys.argv[2])
relative_paths = {
    Path(".codex-plugin/plugin.json"),
    Path("claude/hooks/codex_pretool_dispatch.py"),
    Path("hooks/hooks.json"),
    Path("skills/beads-execution/SKILL.md"),
}
relative_paths.update(
    path.relative_to(expected_root)
    for path in (expected_root / "bin").iterdir()
    if path.is_file()
    and (path.name == "escapement-worktree" or path.name.startswith("escapement_worktree"))
)
for directory in (Path("harness/bin"), Path("harness/schemas")):
    relative_paths.update(
        path.relative_to(expected_root)
        for path in (expected_root / directory).iterdir()
        if path.is_file() and path.suffix in {".py", ".json"}
    )

for relative in sorted(relative_paths):
    expected = expected_root / relative
    installed = installed_root / relative
    if not installed.is_file() or expected.read_bytes() != installed.read_bytes():
        raise SystemExit(
            f"FATAL: installed plugin surface is stale or differs from checkout: {installed}"
        )
PY
chmod +x "$plugin_root/bin/escapement-worktree" "$plugin_root/harness/bin/"*.py

# Codex and Claude share one host-neutral Escapement supervisor STATE root, but
# their runtime packages are not interchangeable.  In particular, the Codex
# package is intentionally smaller and must never replace Claude's `bin` link.
HARNESS_HOME="${CONTINUATION_HARNESS_HOME:-$HOME/.claude/harness}"
CODEX_RUNTIME_HOME="${ESCAPEMENT_CODEX_RUNTIME_HOME:-$CODEX_STATE_HOME/escapement-harness}"
mkdir -p "$HARNESS_HOME" "$CODEX_RUNTIME_HOME"
for stable in "$CODEX_RUNTIME_HOME/bin" "$CODEX_RUNTIME_HOME/schemas"; do
  if [[ -e "$stable" && ! -L "$stable" ]]; then
    echo "FATAL: refusing to replace non-symlink Escapement runtime path: $stable" >&2
    exit 1
  fi
done
promote_runtime_link() {
  python3 - "$1" "$2" <<'PY'
import os
import sys

source, target = sys.argv[1:]
os.replace(source, target)
directory = os.open(os.path.dirname(target), os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}
ln -sfn "$plugin_root/harness/bin" "$CODEX_RUNTIME_HOME/.bin.next"
promote_runtime_link "$CODEX_RUNTIME_HOME/.bin.next" "$CODEX_RUNTIME_HOME/bin"
ln -sfn "$plugin_root/harness/schemas" "$CODEX_RUNTIME_HOME/.schemas.next"
promote_runtime_link "$CODEX_RUNTIME_HOME/.schemas.next" "$CODEX_RUNTIME_HOME/schemas"
mkdir -p "$HARNESS_HOME/worktrees"
chmod 700 "$HARNESS_HOME" "$HARNESS_HOME/worktrees" "$CODEX_RUNTIME_HOME"
if [[ "${ESCAPEMENT_SKIP_SUPERVISOR_INSTALL:-0}" != "1" ]]; then
  shared_waker="$HARNESS_HOME/bin/wakeup_waker.py"
  if [[ -x "$shared_waker" ]] \
    && [[ "$("$shared_waker" --capabilities 2>/dev/null)" == "cross-host-continuation-v1" ]]; then
    bash "$REPO_DIR/scripts/continuation-supervisor-install.sh"
  else
    ESCAPEMENT_SUPERVISOR_WAKER="$CODEX_RUNTIME_HOME/bin/wakeup_waker.py" \
      bash "$REPO_DIR/scripts/continuation-supervisor-install.sh"
  fi
fi

source_skill="$plugin_root/skills/beads-execution/SKILL.md"
authoritative_skill="$authoritative_root/skills/beads-execution/SKILL.md"
authoritative_hooks="$REPO_DIR/plugins/escapement/hooks/hooks.json"

python3 "$REPO_DIR/scripts/migrate_codex_beads_skill.py" \
  "$authoritative_skill" \
  "$GLOBAL_SKILL"

python3 "$REPO_DIR/scripts/prune_codex_hooks.py" \
  "$plugin_root/hooks/hooks.json" \
  "$CODEX_STATE_HOME/hooks.json" \
  --codex-home "$CODEX_STATE_HOME" \
  --home "$HOME"

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
