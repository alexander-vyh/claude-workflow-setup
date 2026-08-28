#!/bin/bash
# Verify the live candidate while the updater's original journal remains armed.
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 DRY_RUN SOURCE_ROOT CANDIDATE_ROOT CLAUDE_BIN" >&2
  exit 2
fi

dry_run="$1"
source_root="$2"
candidate_root="$3"
claude_bin="$4"
canary="$source_root/scripts/delegation-canary.py"

if [[ "$dry_run" == true ]]; then
  echo "    [dry-run] verify installed plugin parity and isolated delegation canary"
  exit 0
fi

if ! diff -qr "$source_root/plugins/escapement-claude" "$candidate_root" >/dev/null; then
  echo "installed plugin differs from this checkout" >&2
  exit 1
fi
echo "    installed plugin parity verified"

scratch="$(mktemp -d "${TMPDIR:-/tmp}/escapement-delegation-canary.XXXXXX")"
cleanup() {
  rm -rf "$scratch"
}
trap cleanup EXIT

python3 -B "$canary" \
  --claude-bin "$claude_bin" \
  --source-root "$source_root" \
  --candidate-root "$candidate_root" \
  --scratch-root "$scratch"
echo "    isolated delegation canary passed"
