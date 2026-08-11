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
        for reason in reasons[:8]:
            lines.append(f"      – {reason}")
        if len(reasons) > 8:
            lines.append(f"      – ... {len(reasons) - 8} more reasons")
    lines.append(
        "If a removed or weakened assertion protected real behavior, restore or "
        "re-add equivalent coverage before this change lands."
    )
    return "\n".join(lines)


def _collect_findings(repo_root: Path) -> list[tuple[str, list[str]]]:
    from git_change_scope import change_sources, net_tree_scope
    from oracle_downgrade_warning_gate import is_test_file
    import oracle_strength_diff as osd

    grouped: dict[str, list[str]] = {}
    scope = net_tree_scope(repo_root)
    for change in scope.changes:
        rel = change.filepath
        baseline_path = (
            os.fsdecode(change.baseline_path)
            if change.baseline_path is not None
            else ""
        )
        candidate_path = (
            os.fsdecode(change.candidate_path)
            if change.candidate_path is not None
            else ""
        )
        baseline_is_test = bool(baseline_path) and is_test_file(baseline_path)
        candidate_is_test = bool(candidate_path) and is_test_file(candidate_path)
        if not baseline_is_test and not candidate_is_test:
            continue
        old_src, new_src = change_sources(repo_root, scope, change)
        if not old_src and not new_src:
            continue
        try:
            finding = osd.evaluate(
                old_src,
                "" if baseline_is_test and not candidate_is_test else new_src,
                rel,
            )
        except Exception:
            continue  # fail-open per file
        if finding.level == osd.Level.WARN:
            reasons = grouped.setdefault(rel, [])
            for reason in finding.reasons:
                if reason not in reasons:
                    reasons.append(reason)
    return list(grouped.items())


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
