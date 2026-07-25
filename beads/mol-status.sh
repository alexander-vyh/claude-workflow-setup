#!/bin/bash
# mol-status.sh — Output active molecule state for Claude Code SessionStart
# Called by the workflow profile to give Claude task-state context.
# Lives at ~/.beads/mol-status.sh (global, works with any beads project)

# Only run if we're in a beads project
if [ ! -d ".beads" ] && [ -z "$BEADS_DIR" ]; then
  exit 0
fi

# Check for active molecules by scanning epic list for mol- prefix IDs
ACTIVE=$(bd list --type epic --status open 2>/dev/null | grep 'mol-' | sed 's/^.*\(reticle-mol-[^ ]*\).*\[epic\] \(.*\)$/\1:\2/' 2>/dev/null)

# Also check for in_progress molecules
ACTIVE_IP=$(bd list --type epic --status in_progress 2>/dev/null | grep 'mol-' | sed 's/^.*\(reticle-mol-[^ ]*\).*\[epic\] \(.*\)$/\1:\2/' 2>/dev/null)

# Combine
ACTIVE=$(printf '%s\n%s' "$ACTIVE" "$ACTIVE_IP" | grep -v '^$')

if [ -z "$ACTIVE" ]; then
  exit 0
fi

echo ""
echo "# Active Molecules"
echo ""
echo "You have active feature molecules. Check their state and guide the user."
echo "Present the PHASE (Design / Validate / Build), not internal step IDs."
echo "When a gate is ready, present it as a decision — not a command to run."
echo "When a step is ready, offer to run it — don't wait for the user to ask."
echo ""

# Show current state for each molecule
echo "$ACTIVE" | while IFS=: read -r id title; do
  echo "## $title"
  bd mol current "$id" 2>/dev/null
  echo ""
done

echo "## How to Guide the User"
echo ""
echo "- If a step is [ready]: Offer to run it. 'Discovery is next for X. Want me to start?'"
echo "- If a gate is [ready]: Present it as a decision. 'The design doc is ready for review. Here's a summary: ...'"
echo "- If the user says 'looks good' / 'approved' / 'yes' to a gate: Run bd gate resolve <gate-id> silently."
echo "- If the user says 'revise' / 'no': Keep the gate open, help them revise."
echo "- NEVER show bd commands, gate IDs, or molecule internals to the user."
echo "- Present phases: Design (discovery + review), Validate (skeleton + review), Build (full execution)."
