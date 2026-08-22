#!/usr/bin/env python3
"""PreToolUse gate on `gh pr merge` — the deterministic merge-authorization check.

Motivated by a real incident (2026-07-04, simplifi/cro-reporting PR #262): an agent
told the user "the only remaining step is merging ... a platform-level gate, not
something resolvable from my side" when no such gate existed (GitHub branch
protection: 404 Branch not protected) — the repo simply had no
`.escapement/repo.json` declaration. Two existing enforcement layers
(continuation-harness.md rule text, and the Stop-gate backstop in stop_hook.py)
both operate around the merge decision, not AT it — neither intercepts a `gh pr
merge` invocation itself, so an agent that decides (correctly or not) whether to
attempt the merge is reasoning in free text with no mechanical backstop at the
point of action. This hook is that backstop: it resolves the same
`.escapement/repo.json` declaration `repo_outcome.py` already reads for the
Stop-gate, and lets the file's declaration — not the agent's in-context guess —
decide whether the merge proceeds.

Fail-safe by construction, mirroring repo_outcome.py's own philosophy: any error
resolving the declaration (missing reader, unreadable file, resolver exception)
denies rather than allows. An unconfigured or unresolvable repo behaves exactly
like today — stop and ask — it is never upgraded to authorization by a broken
check.

Escape path: `# merge-authorization-waiver: <reason>` appended to the `gh pr merge`
command, once the user has given explicit go-ahead in the conversation this turn.
The denial reason always names the TRUE cause (no repo.json declaration, or a
malformed one) — it must never be paraphrased into a fabricated external
constraint; that fabrication is the exact defect this gate exists to make
impossible once a merge is actually attempted.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn, Optional

# Ensure sibling helpers (_gate_signal, _gh_command) import whether this runs as a script
# (script-dir on path) or is loaded by importlib from the test harness (dir NOT on path) —
# without this, the _gh_command import falls to its loose fallback under test.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover - standalone execution fallback

    def _record_signal(*_args, **_kwargs) -> None:
        return None


try:
    from _gh_command import is_gh_pr_command
except ImportError:  # pragma: no cover - fail TOWARD checking, never silently disable
    # A blocking security gate must not go dark if its sibling helper is missing. This
    # fallback is looser (no command-position guard) but errs toward resolving
    # authorization on anything that looks like a merge — the safe direction.
    _FALLBACK_MERGE_RE = re.compile(r"gh\s+pr\s+merge\b")

    def is_gh_pr_command(command: str, *_verbs: str) -> bool:
        return bool(command) and _FALLBACK_MERGE_RE.search(command) is not None


def _add_repo_outcome_to_path() -> None:
    """`repo_outcome.py` lives in `harness/bin/`, a different directory than this
    hook in both deploy layouts this repo maintains — and at a different relative
    depth in each: `harness/bin` is a direct sibling of `claude/` at the repo root
    (symlink-install layout), but a direct sibling of `hooks/` under
    `plugins/escapement-claude/` (plugin-marketplace layout). Try both so this file
    stays byte-identical between the two trees."""
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "harness" / "bin", here.parent.parent / "harness" / "bin"):
        if (candidate / "repo_outcome.py").exists():
            sys.path.insert(0, str(candidate))
            return


_add_repo_outcome_to_path()

WAIVER_RE = re.compile(r"#\s*merge-authorization-waiver:\s*(\S.*?)\s*$", re.MULTILINE)
WAIVER_PLACEHOLDERS = frozenset(
    {"<reason>", "tbd", "n/a", "na", "none", "todo", "fixme", "wip", "?", "??", "???"}
)
MIN_WAIVER_REASON_LEN = 20

# Detection is delegated to the shared _gh_command helper (command-position aware) so a
# compound/prefixed `gh pr merge` — e.g. `cd /wt\ngh pr merge` — is caught, not bypassed.

_UNAUTHORIZED_REASON = (
    "merge_authorization_gate: this repo has no .escapement/repo.json declaring "
    "auto_merge_on_green (or the declaration is absent/malformed) — there is no "
    "platform-level restriction here; this is escapement's own conservative default "
    "when a repo hasn't opted in to auto-merge. Escape paths: (1) get the user's "
    "explicit go-ahead THIS turn and retry with "
    "`# merge-authorization-waiver: <reason>` appended to the command; "
    "(2) offer to write .escapement/repo.json (see harness/bin/set_repo_outcome.py) "
    "so future merges in this repo don't ask again. Report the true cause — do NOT "
    "describe this as an external or platform-level gate; it is this repo's own "
    "unconfigured state."
)


def _emit_deny(reason: str) -> NoReturn:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def _extract_waiver_reason(text: str) -> Optional[str]:
    match = WAIVER_RE.search(text)
    return match.group(1).strip() if match else None


def _is_substantive_waiver(reason: Optional[str]) -> bool:
    if not reason:
        return False
    normalized = reason.strip().lower()
    return len(reason.strip()) >= MIN_WAIVER_REASON_LEN and normalized not in WAIVER_PLACEHOLDERS


def _is_merge_command(command: str) -> bool:
    return is_gh_pr_command(command, "merge")


def _authorizes_auto_merge(cwd: str) -> Optional[bool]:
    """True/False on a resolved declaration; None if the resolver itself could not
    be loaded or raised — an unresolvable check is treated as unauthorized by the
    caller, it never fabricates an allow."""
    if not cwd:
        return False
    try:
        import repo_outcome
    except ImportError:
        return None
    try:
        return bool(repo_outcome.authorizes_auto_merge(repo_outcome.resolve(cwd)))
    except Exception:
        return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if data.get("hook_event_name") != "PreToolUse":
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command or not _is_merge_command(command):
        return 0

    cwd = str(data.get("cwd") or data.get("workingDirectory") or os.getcwd())

    waiver_reason = _extract_waiver_reason(command)
    if _is_substantive_waiver(waiver_reason):
        _record_signal(
            gate_name="merge_authorization_gate",
            decision="waiver-accepted",
            reason=waiver_reason or "",
            event_type="waiver",
            command=command,
            cwd=cwd,
        )
        return 0

    authorized = _authorizes_auto_merge(cwd)
    if authorized:
        _record_signal(
            gate_name="merge_authorization_gate",
            decision="allow",
            reason="repo declares auto_merge_on_green with intended_outcome >= merged",
            command=command,
            cwd=cwd,
        )
        return 0

    _record_signal(
        gate_name="merge_authorization_gate",
        decision="deny",
        reason=_UNAUTHORIZED_REASON,
        command=command,
        cwd=cwd,
    )
    _emit_deny(_UNAUTHORIZED_REASON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
