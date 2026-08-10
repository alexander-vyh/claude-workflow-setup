#!/usr/bin/env python3
"""Stop-time oracle-downgrade advisory (non-blocking, write-path-agnostic).

Runs at Stop over the FULL git diff — so it catches a test weakening regardless of
how it was written (native Edit, Serena replace_symbol_body, Bash/sed, an IDE).
For each changed test file it runs the per-function oracle-strength differ
(`oracle_strength_diff`) and, on a likely downgrade (a test function lost strong
assertions, or was removed without the coverage reappearing), surfaces a
NON-BLOCKING advisory via `systemMessage` so the author can confirm the weakening
was intentional.

Advisory by design (gate-design.md + the 2026-06-20 EV replay): it NEVER blocks the
Stop and NEVER denies. The corpus proved a hard block would false-fire on
legitimate red->green TDD (a placeholder negative control correctly dropped once
its feature shipped), mechanically indistinguishable from a genuine coverage drop.
So this surfaces + records signal; the human/agent adjudicates. It fails OPEN: any
error (missing git, import failure, parse trouble) returns silently and lets the
Stop proceed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOKS_DIR))

try:
    from _gate_signal import record as _record_signal
except Exception:  # pragma: no cover - signal is best-effort

    def _record_signal(*_args, **_kwargs) -> None:
        return None


def _allow() -> int:
    """Permit the Stop with no output."""
    return 0


def _build_message(findings: list[tuple[str, list[str]]]) -> str:
    lines = [
        "⚠ Oracle-downgrade advisory (non-blocking) — changed test file(s) look like "
        "they may weaken the test oracle. If this is intentional (refactor, dead-code "
        "removal, or red→green now that the feature shipped), no action needed:",
    ]
    for rel, reasons in findings:
        lines.append(f"  • {rel}")
        for reason in reasons:
            lines.append(f"      – {reason}")
    lines.append(
        "If a removed or weakened assertion protected real behavior, restore or "
        "re-add equivalent coverage before this change lands."
    )
    return "\n".join(lines)


def _collect_findings(repo_root: Path) -> list[tuple[str, list[str]]]:
    from git_change_scope import net_tree_scope, revision_file, worktree_file
    from oracle_downgrade_warning_gate import is_test_file
    import oracle_strength_diff as osd

    findings: list[tuple[str, list[str]]] = []
    scope = net_tree_scope(repo_root)
    for rel in scope.files:
        if not is_test_file(rel):
            continue
        old_src = revision_file(repo_root, scope.baseline, rel)
        new_src = worktree_file(repo_root, rel)
        if not old_src and not new_src:
            continue
        try:
            finding = osd.evaluate(old_src, new_src, rel)
        except Exception:
            continue  # fail-open per file
        if finding.level == osd.Level.WARN:
            findings.append((rel, list(finding.reasons)[:3]))
    return findings


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return _allow()

    event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    if event != "Stop":
        return _allow()

    raw_cwd = data.get("cwd")
    cwd = raw_cwd if isinstance(raw_cwd, str) and raw_cwd else os.getcwd()

    try:
        from oracle_downgrade_warning_gate import find_git_root
    except Exception:
        return _allow()  # fail-open: never disrupt Stop on import failure

    repo_root = find_git_root(cwd)
    if repo_root is None:
        return _allow()

    try:
        findings = _collect_findings(repo_root)
    except Exception:
        return _allow()

    if not findings:
        _record_signal(
            gate_name="oracle_downgrade_stop",
            decision="allow",
            reason="stop: no oracle-downgrade signals in changed test files",
        )
        return _allow()

    _record_signal(
        gate_name="oracle_downgrade_stop",
        decision="warn",
        reason=(
            f"stop: {len(findings)} changed test file(s) show an oracle-downgrade signal"
        ),
        issue_count=len(findings),
        files=[rel for rel, _ in findings],
    )
    json.dump({"systemMessage": _build_message(findings)}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
