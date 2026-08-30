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

import hashlib
import json
import os
import pathlib
import re
import sys
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOKS_DIR))

try:
    from _advisory_dedupe import already_reported as _already_seen
    from _advisory_dedupe import clear as _forget_seen
    from _gate_signal import record as _record_signal
except Exception:  # pragma: no cover - signal is best-effort

    def _record_signal(*_args, **_kwargs) -> None:
        return None

    def _already_seen(*_args, **_kwargs) -> bool:
        return False

    def _forget_seen(*_args, **_kwargs) -> None:
        return None


def _allow() -> int:
    """Permit the Stop with no output."""
    return 0


def _build_message(findings: list[tuple[str, list[str]]]) -> str:
    """One compact line: which files, which test functions, how many.

    This advisory fires on every Stop. It previously inlined up to eight raw
    assertion source strings per changed test file, which on a phone is pages of
    unreadable text per turn -- and reliably trains the reader to skip it.

    What survives here is what a reader can act on: the file, the specific test
    functions that weakened, and the scale. The raw assertion sources go to the
    gate-signal corpus via _record_signal, which is the durable store built for
    them.
    """
    def _subjects(reasons: list[str]) -> str:
        names: list[str] = []
        for reason in reasons:
            for name in re.findall(r"'([A-Za-z_][A-Za-z0-9_]*)'", reason):
                if name not in names:
                    names.append(name)
        if not names:
            return f"{len(reasons)} finding(s)"
        shown = ", ".join(names[:6])
        if len(names) > 6:
            shown += f", +{len(names) - 6} more"
        return shown

    total = sum(len(reasons) for _, reasons in findings)
    parts = [f"{rel} ({_subjects(reasons)})" for rel, reasons in findings[:4]]
    if len(findings) > 4:
        parts.append(f"+{len(findings) - 4} more file(s)")
    return (
        f"⚠ Oracle-downgrade advisory (non-blocking): {total} weakened assertion(s) "
        f"in {len(findings)} changed test file(s) — " + "; ".join(parts) + ". "
        "Intentional refactor or red→green? No action needed. Otherwise restore "
        "equivalent coverage before this lands; full detail in the gate-signal log."
    )

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

    session_id = data.get("session_id") or data.get("sessionId") or ""

    if not findings:
        # A clean turn invalidates the memory. Leaving the old digest in place
        # would silently suppress an identical weakening reintroduced later.
        _forget_seen("oracle_downgrade_stop", str(session_id))
        _record_signal(
            gate_name="oracle_downgrade_stop",
            decision="allow",
            reason="stop: no oracle-downgrade signals in changed test files",
        )
        return _allow()

    if _already_seen("oracle_downgrade_stop", str(session_id), findings):
        _record_signal(
            gate_name="oracle_downgrade_stop",
            decision="allow",
            reason="stop: unchanged oracle-downgrade finding already reported",
            issue_count=len(findings),
            files=[rel for rel, _ in findings],
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
        # Detail moved off the user-facing message and into the durable corpus.
        weakened={rel: reasons for rel, reasons in findings},
    )
    json.dump({"systemMessage": _build_message(findings)}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
