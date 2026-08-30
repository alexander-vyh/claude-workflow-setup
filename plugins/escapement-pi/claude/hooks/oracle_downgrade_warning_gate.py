#!/usr/bin/env python3
"""Landing-time warning for likely test-oracle downgrades.

This hook is deliberately advisory: it emits permissionDecision=ask, not deny.
It looks only at changed test diffs and warns on conservative signals:
  - skip/xfail added
  - strong outcome assertion weakened to structural assertion
  - semantic identity assertion replaced by generated/opaque ID assertion
  - negative-control test or assertion removed
  - strong outcome assertion removed without replacement
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Shared signal capture per claude/rules/gate-design.md Rule 2.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _advisory_dedupe import already_reported as _already_seen
    from _advisory_dedupe import clear as _forget_seen
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover

    def _record_signal(*_args, **_kwargs) -> None:
        return None

    def _already_seen(*_args, **_kwargs) -> bool:
        return False

    def _forget_seen(*_args, **_kwargs) -> None:
        return None


from git_change_scope import change_sources, net_tree_scope


# Test Oracle Brief location (matches test_oracle_brief_gate.py BRIEF_RELATIVE_PATH).
# Used to verify the "Test Oracle Brief has been updated" claim mechanically
# rather than as an honor-system assertion.
_BRIEF_RELATIVE_PATH = Path(".agent/runtime/test-oracle-brief.md")
_BRIEF_RECENT_WINDOW_SEC = 24 * 60 * 60  # 24 hours


FINISHING_COMMANDS = (
    ("git", "commit"),
    ("git", "push"),
    ("gh", "pr", "create"),
    ("gh", "pr", "merge"),
    ("bd", "close"),
)

TEST_FILE_PATTERNS = (
    re.compile(r"^test_.*\.py$"),
    re.compile(r"^.*_test\.py$"),
    re.compile(r"^conftest\.py$"),
    re.compile(r"^.*\.(test|spec)\.[a-z0-9]+$", re.IGNORECASE),
    re.compile(r"^.*_test\.go$"),
    re.compile(r"^.*_test\.rs$"),
)

SKIP_OR_XFAIL_RE = re.compile(
    r"(@pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail)\(|"
    r"@unittest\.skip|@unittest\.skipIf|"
    r"\b(?:test|it|describe)\.skip\s*\(|\bxdescribe\s*\(|\bxit\s*\(|"
    r"\bpending\s*\(|\bskip\s*\()",
    re.IGNORECASE,
)

STRONG_ASSERTION_RE = re.compile(
    r"("
    r"\bassert\s+.+(?:==|!=|<=|>=|<|>| in | not in ).+|"
    r"\bassert(?:Equal|NotEqual|AlmostEqual|In|NotIn|Regex|Raises)\s*\(|"
    r"\bpytest\.raises\s*\(|"
    r"\bexpect\s*\(.+\)\.(?:toBe|toEqual|toStrictEqual|toContain|toContainEqual|toMatch|"
    r"toMatchObject|toHaveProperty|toThrow|toHaveLength)\s*\(|"
    r"\bexpect\s*\(.+\)\.to\s+(?:eq|eql|include|match|raise_error)\b|"
    r"\bassert_response\s+:(?:success|created|accepted|bad_request|unauthorized|forbidden|not_found)"
    r")",
    re.IGNORECASE,
)

WEAK_ASSERTION_RE = re.compile(
    r"("
    r"\bassert\s+[\w.]+\s*$|"
    r"\bassert\s+.+\s+is\s+not\s+None\b|"
    r"\bassert\s+len\(.+\)\s*>\s*0\b|"
    r"\bassert(?:IsNotNone|True|Greater)\s*\(|"
    r"\.toBeTruthy\s*\(|\.toBeDefined\s*\(|\.toBeGreaterThan\s*\(\s*0\s*\)|"
    r"\.not\.toBeNull\s*\(|\.not\.toBeUndefined\s*\("
    r")",
    re.IGNORECASE,
)

NEGATIVE_CONTROL_RE = re.compile(
    r"(test_|it\(|describe\(|context\().{0,120}"
    r"(reject|invalid|deny|unauthori[sz]ed|forbid|forbidden|missing|absent|"
    r"fails?|raises?|error|negative|not allowed|without|malformed|bad request)",
    re.IGNORECASE,
)

NEGATIVE_ASSERTION_RE = re.compile(
    r"(pytest\.raises|assertRaises|toThrow|raise_error|"
    r"status(?:_code)?\s*==\s*40[0134]|assert_response\s+:(?:bad_request|unauthorized|forbidden|not_found))",
    re.IGNORECASE,
)

SEMANTIC_IDENTITY_RE = re.compile(
    r"(DeveloperName|RecordType\.Name|\.Name\b|name\b|slug\b|key\b|type\b|category\b|semantic)",
    re.IGNORECASE,
)

STRING_LITERAL_RE = re.compile(
    r"""
    (?P<prefix>[rubfRUBF]{0,3})
    (?P<quote>['"])
    (?P<value>(?:\\.|(?! (?P=quote) ).)*?)
    (?P=quote)
    """,
    re.VERBOSE | re.DOTALL,
)

SALESFORCE_ID_RE = re.compile(
    r"^(?:001|003|005|006|00Q|012|500|701)[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?$"
)
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{24,}$")


@dataclass(frozen=True)
class Issue:
    filepath: str
    kind: str
    detail: str


def allow() -> int:
    return 0


def ask(message: str) -> int:
    """Prompt the user for confirmation.

    CANONICAL DECISION CONTRACT: signal the decision with a single mechanism —
    one permissionDecision JSON document on stdout, exit 0. Exit 2 is the
    mutually-exclusive legacy stderr-feedback path; emitting both the JSON
    decision *and* a non-zero exit is a contradictory double-signal. This gate
    is advisory (permissionDecision="ask"), but uses the same single-mechanism
    JSON-on-stdout-plus-exit-0 contract as the hard-deny gates, so it returns 0.
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": message,
                }
            }
        )
    )
    return 0


def command_from(data: dict) -> str:
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def split_command_segments(command: str) -> list[list[str]]:
    normalized = command.replace("\\\n", " ")
    segments: list[list[str]] = []
    for raw in re.split(r"\s*(?:&&|\|\||;|\|)\s*", normalized):
        try:
            parts = shlex.split(raw)
        except ValueError:
            continue
        if parts:
            segments.append(parts)
    return segments


def is_finishing_command(command: str) -> bool:
    for parts in split_command_segments(command):
        for target in FINISHING_COMMANDS:
            if tuple(parts[: len(target)]) == target:
                return True
    return False


def find_git_root(start: str | Path) -> Path | None:
    path = Path(start)
    search_dir = path if path.is_dir() else path.parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(search_dir),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def changed_files(repo_root: Path) -> list[str]:
    return list(net_tree_scope(repo_root).files)


def is_test_file(filepath: str) -> bool:
    path = Path(filepath)
    parts = set(path.parts)
    if parts & {"tests", "test", "spec", "__tests__", "__specs__"}:
        return True
    return any(pattern.match(path.name) for pattern in TEST_FILE_PATTERNS)


def parse_diff_lines(diff_text: str) -> tuple[list[str], list[str]]:
    added: list[str] = []
    deleted: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            deleted.append(line[1:])
    return added, deleted


def _source_diff_lines(
    old_src: str,
    new_src: str,
    filepath: str,
) -> tuple[list[str], list[str]]:
    diff = difflib.unified_diff(
        old_src.splitlines(),
        new_src.splitlines(),
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
        n=0,
        lineterm="",
    )
    return parse_diff_lines("\n".join(diff))


def diff_for_file(repo_root: Path, filepath: str) -> tuple[list[str], list[str]]:
    scope = net_tree_scope(repo_root)
    change = next(
        (candidate for candidate in scope.changes if candidate.filepath == filepath),
        None,
    )
    if change is None:
        return [], []
    old_src, new_src = change_sources(repo_root, scope, change)
    return _source_diff_lines(old_src, new_src, filepath)


def is_opaque_generated_literal(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 12:
        return False
    if any(ch.isspace() for ch in stripped):
        return False
    if (
        SALESFORCE_ID_RE.match(stripped)
        or UUID_RE.match(stripped)
        or HEX_TOKEN_RE.match(stripped)
    ):
        return True
    has_alpha = any(ch.isalpha() for ch in stripped)
    has_digit = any(ch.isdigit() for ch in stripped)
    return (
        has_alpha and has_digit and bool(re.match(r"^[A-Za-z0-9_:/+=.-]+$", stripped))
    )


def line_has_generated_literal(line: str) -> bool:
    for match in STRING_LITERAL_RE.finditer(line):
        if is_opaque_generated_literal(match.group("value")):
            return True
    return False


def line_label(line: str) -> str:
    stripped = line.strip()
    return stripped if len(stripped) <= 140 else stripped[:137] + "..."


def analyze_file(filepath: str, added: list[str], deleted: list[str]) -> list[Issue]:
    issues: list[Issue] = []

    for line in added:
        if SKIP_OR_XFAIL_RE.search(line):
            issues.append(
                Issue(filepath, "skip-or-xfail-added", f"added: {line_label(line)}")
            )

    deleted_strong = [line for line in deleted if STRONG_ASSERTION_RE.search(line)]
    added_strong = [line for line in added if STRONG_ASSERTION_RE.search(line)]
    added_weak = [line for line in added if WEAK_ASSERTION_RE.search(line)]

    if deleted_strong and added_weak:
        issues.append(
            Issue(
                filepath,
                "strong-assertion-weakened",
                f"removed outcome assertion and added structural assertion: {line_label(added_weak[0])}",
            )
        )
    elif deleted_strong and not added_strong:
        issues.append(
            Issue(
                filepath,
                "strong-assertion-removed",
                f"removed outcome assertion without an obvious replacement: {line_label(deleted_strong[0])}",
            )
        )

    semantic_deleted = [line for line in deleted if SEMANTIC_IDENTITY_RE.search(line)]
    generated_added = [line for line in added if line_has_generated_literal(line)]
    if semantic_deleted and generated_added:
        issues.append(
            Issue(
                filepath,
                "semantic-identity-to-generated-id",
                f"added generated/opaque ID assertion after removing semantic identity: {line_label(generated_added[0])}",
            )
        )

    negative_deleted = [
        line
        for line in deleted
        if NEGATIVE_CONTROL_RE.search(line) or NEGATIVE_ASSERTION_RE.search(line)
    ]
    negative_added = [
        line
        for line in added
        if NEGATIVE_CONTROL_RE.search(line) or NEGATIVE_ASSERTION_RE.search(line)
    ]
    if negative_deleted and not negative_added:
        issues.append(
            Issue(
                filepath,
                "negative-control-removed",
                f"removed negative-control signal: {line_label(negative_deleted[0])}",
            )
        )

    return issues


def analyze(repo_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    scope = net_tree_scope(repo_root)
    for change in scope.changes:
        filepath = change.filepath
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
        if baseline_is_test and not candidate_is_test:
            try:
                import oracle_strength_diff as osd

                if osd.evaluate(old_src, "", filepath).level == osd.Level.WARN:
                    issues.append(
                        Issue(
                            filepath,
                            "test-discovery-removed",
                            "moved a meaningful test oracle out of test discovery",
                        )
                    )
            except Exception:
                pass
            continue
        added, deleted = _source_diff_lines(old_src, new_src, filepath)
        if not added and not deleted:
            continue
        file_issues = analyze_file(filepath, added, deleted)

        # Line-level heuristics cannot distinguish a real strength loss from a
        # duplicate assertion being deduplicated.  Let the corpus-backed,
        # per-function differ adjudicate only the strength-loss class; the
        # independent skip/generated-identity/negative-control signals retain
        # their existing behavior.  Import/parse uncertainty fails open for the
        # strength class rather than manufacturing a warning.
        if any(issue.kind.startswith("strong-assertion-") for issue in file_issues):
            strength_warn = False
            try:
                import oracle_strength_diff as osd

                strength_warn = (
                    osd.evaluate(old_src, new_src, filepath).level == osd.Level.WARN
                )
            except Exception:
                pass
            if not strength_warn:
                file_issues = [
                    issue
                    for issue in file_issues
                    if not issue.kind.startswith("strong-assertion-")
                ]
        issues.extend(file_issues)
    return issues


def _brief_recently_modified(repo_root: Path) -> bool:
    """Check whether the Test Oracle Brief was modified in the last 24h.

    Per gate-design.md Rule 3 (validate the value, not just the presence):
    the prior text "Proceed only if the Test Oracle Brief has been updated"
    was an unverifiable honor system. This check turns the claim into a
    mechanically-testable assertion.
    """
    brief_path = repo_root / _BRIEF_RELATIVE_PATH
    if not brief_path.is_file():
        return False
    try:
        import time

        return (time.time() - brief_path.stat().st_mtime) <= _BRIEF_RECENT_WINDOW_SEC
    except OSError:
        return False


def build_message(issues: list[Issue], brief_was_updated: bool) -> str:
    grouped: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue not in grouped.setdefault(issue.filepath, []):
            grouped[issue.filepath].append(issue)
    lines = []
    selected = list(grouped.items())[:12]
    for filepath, path_issues in selected:
        lines.append(f"  - {filepath}")
        for issue in path_issues[:8]:
            lines.append(f"      - {issue.kind}: {issue.detail}")
        if len(path_issues) > 8:
            lines.append(f"      - ... {len(path_issues) - 8} more reasons")
    if len(grouped) > 12:
        lines.append(f"  - ... {len(grouped) - 12} more paths")
    listed = "\n".join(lines)

    if brief_was_updated:
        # The mechanical check passed; the warning is now informational —
        # the user has acknowledged this class of change in the brief.
        return (
            "ORACLE DOWNGRADE NOTICE: changed tests may weaken what the oracle "
            "proves. The Test Oracle Brief was updated within the last 24 hours, "
            "so this is informational only — confirm the brief reflects the new "
            "oracle, then proceed.\n\n"
            f"{listed}"
        )

    # No recent brief update — the prior honor-system text becomes an explicit
    # ask, with the brief-update path named as a verifiable next step.
    return (
        "ORACLE DOWNGRADE WARNING: changed tests may weaken what the oracle proves.\n\n"
        f"{listed}\n\n"
        "This is a warning, not a hard block. To proceed cleanly:\n"
        f"  - Update {_BRIEF_RELATIVE_PATH} to reflect the new oracle, OR\n"
        "  - Confirm the replacement test is an equal-or-stronger oracle by\n"
        "    saying 'proceed' (your decision is captured in the signal log).\n\n"
        "Either way, name *why* this change is safe — the captured rationale "
        "becomes labeled training data for future revisions of this gate."
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return allow()

    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    tool_name = data.get("tool_name", "")
    if hook_event != "PreToolUse" or tool_name != "Bash":
        return allow()

    if not is_finishing_command(command_from(data)):
        return allow()

    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    repo_root = find_git_root(cwd)
    if repo_root is None:
        return allow()

    issues = analyze(repo_root)
    session_id = data.get("session_id") or data.get("sessionId") or ""
    if not issues:
        # A clean run invalidates the memory. Leaving the old fingerprint in
        # place would silently suppress an identical weakening reintroduced later.
        _forget_seen("oracle_downgrade_warning_gate", str(session_id))
        _record_signal(
            gate_name="oracle_downgrade_warning_gate",
            decision="allow",
            reason="no oracle-downgrade signals in changed tests",
        )
        return allow()

    brief_updated = _brief_recently_modified(repo_root)
    fingerprint = sorted((i.kind, getattr(i, "filepath", ""), str(getattr(i, "detail", ""))) for i in issues)
    if _already_seen("oracle_downgrade_warning_gate", str(session_id), fingerprint):
        _record_signal(
            gate_name="oracle_downgrade_warning_gate",
            decision="allow",
            reason="unchanged oracle-downgrade finding already reported",
            issue_count=len(issues),
        )
        return allow()

    _record_signal(
        gate_name="oracle_downgrade_warning_gate",
        decision="ask",
        reason=(
            f"{len(issues)} oracle-downgrade signal(s); "
            f"brief_updated_recently={brief_updated}"
        ),
        issue_count=len(issues),
        brief_recently_updated=brief_updated,
        issue_kinds=sorted({i.kind for i in issues}),
    )
    return ask(build_message(issues, brief_updated))


if __name__ == "__main__":
    raise SystemExit(main())
