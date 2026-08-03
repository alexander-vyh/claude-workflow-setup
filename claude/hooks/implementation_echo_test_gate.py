#!/usr/bin/env python3
# file-complexity-waiver: pre-existing large landing gate; data-fixture classification and message framing are extracted to a sibling, while the full responsibility split remains tracked in escapement-55k
"""Hook gate for rejecting high-confidence implementation-echo tests.

This is intentionally a landing-time scanner. It compares changed tests with
changed production files and blocks only high-confidence echo patterns:
  - the same generated/opaque literal appears in production code and tests
  - a changed test asserts only mock/private interactions without an outcome

It does not try to prove every test is good. Its job is to stop the most
dangerous deterministic anti-patterns before commit, push, PR, or bd close.
"""

from __future__ import annotations

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
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_args, **_kwargs) -> None:
        return None

try:
    from data_fixture_echo import (
        build_data_fixture_warning,
        is_text_data_fixture,
    )
except ImportError:  # pragma: no cover
    def is_text_data_fixture(_filepath: str) -> bool:
        return False

    def build_data_fixture_warning(_issues) -> str:
        return "IMPLEMENTATION-ECHO DATA-FIXTURE NOTICE"

# Substance bar for the `# oracle:` override (gate-design.md Rule 3). Fail-open:
# if the validator module is unavailable, keep the prior presence-only behavior
# rather than letting an import error block a commit.
try:
    from oracle_reason_validation import (
        asserted_tokens,
        partition_overrides,
        validate_oracle_reason,
    )
except ImportError:  # pragma: no cover
    def asserted_tokens(_literals):
        return set()

    def validate_oracle_reason(_reason, _asserted):
        return None

    def partition_overrides(overrides, _asserted_by_file):
        # Fail-open: honor every non-empty override as before.
        return set(overrides), {}

# Magic-number / business-constant echo detector (bead escapement-2o7).
try:
    from magic_number_echo import find_magic_number_echoes
except ImportError:  # pragma: no cover
    def find_magic_number_echoes(_source_files, _test_files):
        return []


# Per-file oracle override: an `# oracle: <reason>` or `// oracle: <reason>`
# comment anywhere in a test file marks that file as having an explicit
# (human-attested) independent oracle. The heuristic is high-confidence but
# not infallible; this is the escape path per gate-design.md Rule 1.
_ORACLE_OVERRIDE_RE = re.compile(
    r"(?:^|\s)(?:#|//)\s*oracle:\s*(.+?)(?:\n|$)",
    re.MULTILINE,
)


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

SOURCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".rb",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".clj",
        ".cljs",
        ".cs",
        ".fs",
        ".swift",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hpp",
        ".m",
        ".mm",
        ".php",
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
        ".lua",
        ".pl",
        ".pm",
        ".r",
        ".jl",
        ".zig",
        ".nim",
        ".v",
        ".vue",
        ".svelte",
        ".astro",
        ".sql",
    }
)

EXEMPT_DIR_SEGMENTS = frozenset(
    {
        ".agent",
        ".claude",
        ".codex",
        ".git",
        "docs",
        "doc",
        "scripts",
        "bin",
        "tools",
        "scratch",
        "spike",
        "spikes",
        "prototype",
        "prototypes",
        "tmp",
        "vendor",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "__pycache__",
    }
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

SALESFORCE_ID_RE = re.compile(r"^(?:001|003|005|006|00Q|012|500|701)[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{24,}$")

PY_TEST_DEF_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+(test_\w+)\s*\(")
PY_MOCK_INTERACTION_RE = re.compile(
    r"(\.assert_(?:called|not_called|called_once|called_once_with|called_with|any_call|has_calls)\b|"
    r"\bassert_(?:called|not_called|called_once|called_once_with|called_with|any_call|has_calls)\b|"
    r"\.mock_calls\b|\.call_args\b|\.call_count\b|\.called\b)"
)
PY_OUTCOME_ASSERTION_RE = re.compile(
    r"(^\s*assert\s+.+(?:==|!=|<=|>=|<|>| in | not in | is | is not ).+|"
    r"pytest\.raises\(|assertRaises\(|assertEqual\(|assertAlmostEqual\(|assertIn\(|assertRegex\()",
    re.MULTILINE,
)

JS_TEST_START_RE = re.compile(r"\b(?:test|it)\s*\(")
JS_MOCK_INTERACTION_RE = re.compile(
    r"\.toHaveBeenCalled(?:Times|With)?\s*\(|\.toHaveBeenNthCalledWith\s*\(|"
    r"\.toHaveBeenLastCalledWith\s*\(|\.mock\.calls\b"
)
JS_OUTCOME_ASSERTION_RE = re.compile(
    r"\.(?:toBe|toEqual|toStrictEqual|toContain|toContainEqual|toMatch|toMatchObject|"
    r"toHaveProperty|toThrow|toBeGreaterThan|toBeLessThan|toBeCloseTo|toHaveLength)\s*\(|"
    r"\.(?:resolves|rejects)\b"
)


@dataclass(frozen=True)
class Issue:
    filepath: str
    kind: str
    detail: str


def allow() -> int:
    return 0


def warn(message: str) -> int:
    print(json.dumps({"systemMessage": message}))
    return 0


def deny(message: str) -> int:
    # CANONICAL DENY CONTRACT: signal the block with a single mechanism — the
    # permissionDecision="deny" JSON document on stdout, exit 0. Exit 2 is the
    # mutually-exclusive legacy stderr-feedback path; emitting both is a
    # contradictory double-block. We use the JSON path, so this returns 0.
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
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


def git_target_dir(command: str, fallback_cwd: str) -> str:
    """Resolve the directory a finishing git command actually targets.

    The hook's own cwd is the session's primary repo, but a commit may
    target a DIFFERENT repo via `git -C <path>` or a leading `cd <path> &&`
    (e.g. committing inside a git worktree). Resolving repo_root from the
    session cwd in that case scans the wrong repo's files (a cross-repo
    false positive that flags untracked scratch files in the session repo,
    never the staged change being committed). Honor the command's target.
    """
    import shlex

    try:
        tokens = shlex.split(command)
    except ValueError:
        return fallback_cwd
    target: str | None = None
    for i, tok in enumerate(tokens):  # prefer an explicit `git -C <path>`
        if tok == "-C" and i + 1 < len(tokens):
            target = tokens[i + 1]
            break
    if target is None and tokens and tokens[0] == "cd" and len(tokens) > 1:
        target = tokens[1]  # else a leading `cd <path> && ...`
    if not target:
        return fallback_cwd
    p = Path(target)
    if not p.is_absolute():
        p = Path(fallback_cwd) / p
    return str(p)


def git_files(repo_root: Path, args: list[str]) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def remote_default_ref(repo_root: Path) -> str | None:
    """Return the locally known remote landing ref only when it names a commit.

    ``refs/remotes/origin/HEAD`` is Git's local symbolic record of the remote
    default branch.  A feature's tracking ref is deliberately not a fallback:
    it identifies the feature, not where the change will land.  When the
    symbolic landing identity is absent or stale, committed scope is unknown
    and callers must retain only working-tree scope.
    """
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    ref = result.stdout.strip()
    remote_prefix = "refs/remotes/origin/"
    if not ref.startswith(remote_prefix) or ref == f"{remote_prefix}HEAD":
        return None

    try:
        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return ref if resolved.returncode == 0 and resolved.stdout.strip() else None


def committed_change_files(repo_root: Path, landing_ref: str) -> tuple[list[str] | None, str | None]:
    """Return the strict three-dot committed scope or a repairable failure.

    Unlike the working-tree probes, this result decides whether a known landing
    ref can safely omit committed history. A valid ref with an uncomputable
    merge base must therefore be surfaced to the finishing-command gate.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{landing_ref}...HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, (
            f"could not compute committed change scope from {landing_ref}: Git timed out. "
            f"Run `git merge-base {landing_ref} HEAD`, repair the branch history, and retry."
        )
    except OSError:
        return None, (
            f"could not compute committed change scope from {landing_ref}. "
            f"Run `git merge-base {landing_ref} HEAD`, repair the branch history, and retry."
        )
    if result.returncode != 0:
        return None, (
            f"could not compute committed change scope from {landing_ref}. "
            f"Run `git merge-base {landing_ref} HEAD`, repair the branch history, and retry."
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()], None


def change_scope(repo_root: Path) -> tuple[list[str], str | None]:
    files: set[str] = set()
    files.update(git_files(repo_root, ["diff", "--name-only"]))
    files.update(git_files(repo_root, ["diff", "--cached", "--name-only"]))
    files.update(git_files(repo_root, ["ls-files", "--others", "--exclude-standard"]))

    landing_ref = remote_default_ref(repo_root)
    if landing_ref:
        committed_files, scope_error = committed_change_files(repo_root, landing_ref)
        if scope_error:
            return sorted(files), scope_error
        assert committed_files is not None
        files.update(committed_files)

    return sorted(files), None


def changed_files(repo_root: Path) -> list[str]:
    """Return the public changed-file list, omitting internal scope diagnostics."""
    return change_scope(repo_root)[0]


def is_test_file(filepath: str) -> bool:
    path = Path(filepath)
    parts = set(path.parts)
    if is_text_data_fixture(filepath):
        return False
    if parts & {"tests", "test", "spec", "__tests__", "__specs__"}:
        return True
    return any(pattern.match(path.name) for pattern in TEST_FILE_PATTERNS)


def is_source_file(filepath: str) -> bool:
    path = Path(filepath)
    if set(path.parts) & EXEMPT_DIR_SEGMENTS:
        return False
    if is_test_file(filepath):
        return False
    return path.suffix.lower() in SOURCE_EXTENSIONS


def read_file(repo_root: Path, filepath: str) -> str:
    try:
        return (repo_root / filepath).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def string_literals(text: str) -> set[str]:
    values: set[str] = set()
    for match in STRING_LITERAL_RE.finditer(text):
        value = match.group("value")
        if value:
            values.add(value)
    return values


def is_opaque_generated_literal(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 12:
        return False
    if any(ch.isspace() for ch in stripped):
        return False
    if stripped.startswith(("http://", "https://", "file://")):
        return False
    if SALESFORCE_ID_RE.match(stripped) or UUID_RE.match(stripped) or HEX_TOKEN_RE.match(stripped):
        return True
    has_alpha = any(ch.isalpha() for ch in stripped)
    has_digit = any(ch.isdigit() for ch in stripped)
    return has_alpha and has_digit and bool(re.match(r"^[A-Za-z0-9_:/+=.-]+$", stripped))


def extract_opaque_literals(files: dict[str, str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for filepath, text in files.items():
        opaque = {value for value in string_literals(text) if is_opaque_generated_literal(value)}
        if opaque:
            result[filepath] = opaque
    return result


def find_shared_generated_literals(source_files: dict[str, str], test_files: dict[str, str]) -> list[Issue]:
    source_literals = extract_opaque_literals(source_files)
    if not source_literals:
        return []

    literal_to_sources: dict[str, list[str]] = {}
    for filepath, values in source_literals.items():
        for value in values:
            literal_to_sources.setdefault(value, []).append(filepath)

    issues: list[Issue] = []
    for test_path, test_values in extract_opaque_literals(test_files).items():
        for value in sorted(test_values):
            source_paths = literal_to_sources.get(value)
            if not source_paths:
                continue
            display = value if len(value) <= 40 else value[:37] + "..."
            issues.append(
                Issue(
                    test_path,
                    "shared-generated-literal",
                    f"opaque literal {display!r} also appears in production file(s): "
                    + ", ".join(source_paths[:4]),
                )
            )
    return issues


_TRIPLE_DQ_RE = re.compile(r'""".*?"""', re.DOTALL)
_TRIPLE_SQ_RE = re.compile(r"'''.*?'''", re.DOTALL)
_LINE_DQ_RE = re.compile(r'"(?:\\.|[^"\\\n])*"')
_LINE_SQ_RE = re.compile(r"'(?:\\.|[^'\\\n])*'")


def blank_string_literals(text: str) -> str:
    """Replace string-literal spans with same-shaped blanks (newlines kept,
    every other char -> space).

    Line-based scanners must not mistake code embedded in a string literal —
    e.g. a `def test_...` fixture inside a pattern-detector's own test file —
    for real code. Triple-quoted strings are blanked first so the single-line
    passes cannot re-match their now-blank interiors. Length is preserved, so
    indentation-based logic downstream is unaffected.
    """
    def _blank(m: "re.Match[str]") -> str:
        return "".join("\n" if c == "\n" else " " for c in m.group(0))

    for pat in (_TRIPLE_DQ_RE, _TRIPLE_SQ_RE, _LINE_DQ_RE, _LINE_SQ_RE):
        text = pat.sub(_blank, text)
    return text


def extract_python_test_functions(text: str) -> list[tuple[str, str]]:
    text = blank_string_literals(text)
    functions: list[tuple[str, str]] = []
    current_name: str | None = None
    current_indent = 0
    current_lines: list[str] = []

    for line in text.splitlines():
        match = PY_TEST_DEF_RE.match(line)
        if match:
            if current_name is not None:
                functions.append((current_name, "\n".join(current_lines)))
            current_name = match.group(2)
            current_indent = len(match.group(1))
            current_lines = []
            continue

        if current_name is not None:
            if line.strip() and len(line) - len(line.lstrip()) <= current_indent and not line.lstrip().startswith(("@", "#")):
                functions.append((current_name, "\n".join(current_lines)))
                current_name = None
                current_lines = []
            else:
                current_lines.append(line)

    if current_name is not None:
        functions.append((current_name, "\n".join(current_lines)))
    return functions


def find_python_mock_only_tests(filepath: str, text: str) -> list[Issue]:
    issues: list[Issue] = []
    for name, body in extract_python_test_functions(text):
        if not PY_MOCK_INTERACTION_RE.search(body):
            continue
        if PY_OUTCOME_ASSERTION_RE.search(body):
            continue
        issues.append(
            Issue(
                filepath,
                "mock-only-test",
                f"{name} asserts mock/private interactions but no observable outcome",
            )
        )
    return issues


def extract_js_test_blocks(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not JS_TEST_START_RE.search(line):
            index += 1
            continue

        name_match = re.search(r"(?:test|it)\s*\(\s*['\"]([^'\"]+)['\"]", line)
        name = name_match.group(1) if name_match else f"test block near line {index + 1}"
        block_lines = [line]
        depth = line.count("{") + line.count("(") - line.count("}") - line.count(")")
        index += 1

        while index < len(lines):
            block_lines.append(lines[index])
            depth += lines[index].count("{") + lines[index].count("(")
            depth -= lines[index].count("}") + lines[index].count(")")
            if depth <= 0 and len(block_lines) > 1:
                break
            index += 1

        blocks.append((name, "\n".join(block_lines)))
        index += 1
    return blocks


def find_js_mock_only_tests(filepath: str, text: str) -> list[Issue]:
    issues: list[Issue] = []
    for name, body in extract_js_test_blocks(text):
        if not JS_MOCK_INTERACTION_RE.search(body):
            continue
        if JS_OUTCOME_ASSERTION_RE.search(body):
            continue
        issues.append(
            Issue(
                filepath,
                "mock-only-test",
                f"{name} asserts mock/private interactions but no observable outcome",
            )
        )
    return issues


def find_mock_only_tests(test_files: dict[str, str]) -> list[Issue]:
    issues: list[Issue] = []
    for filepath, text in test_files.items():
        suffix = Path(filepath).suffix.lower()
        if suffix == ".py":
            issues.extend(find_python_mock_only_tests(filepath, text))
        elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            issues.extend(find_js_mock_only_tests(filepath, text))
    return issues


def find_oracle_overrides(test_files: dict[str, str]) -> dict[str, list[str]]:
    """Return {filepath: [override_reason, ...]} for files containing an
    `# oracle: <reason>` or `// oracle: <reason>` comment. The reason text
    becomes labeled training data captured via the signal store — if specific
    override reasons recur across many files, the heuristic that flagged them
    is wrong and should be tuned.
    """
    overrides: dict[str, list[str]] = {}
    for filepath, text in test_files.items():
        reasons = [m.group(1).strip() for m in _ORACLE_OVERRIDE_RE.finditer(text)]
        reasons = [r for r in reasons if r]
        if reasons:
            overrides[filepath] = reasons
    return overrides


def analyze(
    repo_root: Path,
    files: list[str],
) -> tuple[list[Issue], dict[str, str], list[Issue]]:
    """Return (blocking issues, test files, data-fixture advisories).

    Test files are returned so callers can find oracle overrides without
    re-reading the disk. Fixture advisories reuse the same opaque-literal
    evidence but remain separate from blocking executable-test findings.
    """
    source_paths = [path for path in files if is_source_file(path)]
    test_paths = [path for path in files if is_test_file(path)]
    fixture_paths = [path for path in files if is_text_data_fixture(path)]
    if not test_paths and not fixture_paths:
        return [], {}, []

    source_files = {path: read_file(repo_root, path) for path in source_paths}
    test_files = {path: read_file(repo_root, path) for path in test_paths}
    fixture_files = {path: read_file(repo_root, path) for path in fixture_paths}

    issues: list[Issue] = []
    issues.extend(find_shared_generated_literals(source_files, test_files))
    issues.extend(find_mock_only_tests(test_files))
    issues.extend(
        Issue(
            finding.filepath,
            "magic-number-echo",
            f"formatted number {finding.token!r} asserted in this test also "
            f"appears inside description string(s) in: {', '.join(finding.sources)} "
            f"— the test re-asserts documentation, not a computed value",
        )
        for finding in find_magic_number_echoes(source_files, test_files)
    )
    fixture_issues = [
        Issue(
            issue.filepath,
            f"data-fixture-{issue.kind}",
            issue.detail,
        )
        for issue in find_shared_generated_literals(source_files, fixture_files)
    ]
    return issues, test_files, fixture_issues


_REJECTION_HINT = {
    "circular": "names only this file's own asserted literals — that is the test "
    "validating itself, not an independent oracle",
    "placeholder": "is a placeholder (n/a, tbd, ...) — it names nothing",
    "too-short": "is too short to name an independent source of truth",
}


def build_message(
    issues: list[Issue],
    rejected_overrides: dict[str, list[tuple[str, str]]] | None = None,
) -> str:
    listed = "\n".join(
        f"  - {issue.filepath}: {issue.kind}: {issue.detail}" for issue in issues[:12]
    )
    if len(issues) > 12:
        listed += f"\n  - ... {len(issues) - 12} more"

    rejected_block = ""
    if rejected_overrides:
        rows = []
        for filepath, fails in list(rejected_overrides.items())[:12]:
            for reason, category in fails:
                hint = _REJECTION_HINT.get(category, "did not clear the substance bar")
                rows.append(f'  - {filepath}: rejected ({category}) — the reason {hint}.\n'
                            f'      reason was: "{reason.strip()[:160]}"')
        rejected_block = (
            "\nYour `# oracle:` override(s) were REJECTED (gate-design.md Rule 3 — "
            "the reason must name a source of truth INDEPENDENT of the test's own "
            "asserted literals):\n" + "\n".join(rows) + "\n"
        )

    return (
        "IMPLEMENTATION-ECHO TEST GATE: changed tests contain high-confidence "
        "implementation-echo patterns.\n\n"
        f"{listed}\n"
        f"{rejected_block}\n"
        "Two paths forward:\n\n"
        "  (1) **Rewrite the tests** so they prove observable business/user "
        "behavior using an independent oracle. Do not repeat generated IDs, "
        "opaque constants, or mock/private call structure from the "
        "implementation as the oracle.\n\n"
        "  (2) **Override the detection per file**: if the flagged constant or "
        "mock interaction IS the correct oracle for that test, add a comment "
        "anywhere in the file:\n"
        "       # oracle: <why an INDEPENDENT source of truth makes this constant/mock correct>\n"
        "       // oracle: <same, for JS/TS>\n"
        "     The reason must point OUTSIDE the test's own asserted literals (a "
        "source report, upstream model, spec, or consumer contract) — a reason "
        "that just restates the asserted column names/constants is rejected as "
        "circular. The reason is captured as labeled training data.\n\n"
        "If neither path applies (truly exempt context), say 'proceed' to "
        "override — also captured."
    )


def allow_with_fixture_warning(issues: list[Issue]) -> int:
    fixture_files = sorted({issue.filepath for issue in issues})
    _record_signal(
        gate_name="implementation_echo_test_gate",
        decision="allow-with-warning",
        reason=(
            f"{len(issues)} textual data-fixture echo signal(s); "
            "fixture provenance is unresolved"
        ),
        issue_count=len(issues),
        issue_kinds=sorted({issue.kind for issue in issues}),
        fixture_files=fixture_files,
    )
    return warn(build_data_fixture_warning(issues))


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return allow()

    hook_event = data.get("hook_event_name", "") or data.get("hookEventName", "")
    tool_name = data.get("tool_name", "")
    if hook_event != "PreToolUse" or tool_name != "Bash":
        return allow()

    command = command_from(data)
    if not is_finishing_command(command):
        return allow()

    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    # Resolve repo_root from the repo the command actually targets (e.g.
    # `git -C <worktree>`), not the session cwd, to avoid cross-repo misfires.
    repo_root = find_git_root(git_target_dir(command, cwd))
    if repo_root is None:
        return allow()

    files, scope_error = change_scope(repo_root)
    if scope_error:
        _record_signal(
            gate_name="implementation_echo_test_gate",
            decision="deny",
            reason=scope_error,
        )
        return deny(
            "IMPLEMENTATION-ECHO TEST GATE: " + scope_error
        )

    issues, test_files, fixture_issues = analyze(repo_root, files)
    if not issues:
        if fixture_issues:
            return allow_with_fixture_warning(fixture_issues)
        _record_signal(
            gate_name="implementation_echo_test_gate",
            decision="allow",
            reason="no implementation-echo patterns detected",
        )
        return allow()

    # Apply per-file # oracle: <reason> overrides, but only honor a reason that
    # clears the substance bar (gate-design.md Rule 3): a circular reason whose
    # only referents are the file's own asserted literals, a placeholder, or a
    # too-short reason does NOT exempt the file.
    overrides = find_oracle_overrides(test_files)
    asserted_by_file = {
        fp: asserted_tokens(string_literals(test_files.get(fp, ""))) for fp in overrides
    }
    files_overridden, rejected_overrides = partition_overrides(overrides, asserted_by_file)

    remaining_issues = [i for i in issues if i.filepath not in files_overridden]
    skipped_count = len(issues) - len(remaining_issues)

    if files_overridden:
        # Capture the honored override reasons as labeled training data
        _record_signal(
            gate_name="implementation_echo_test_gate",
            decision="override-applied",
            reason=f"{skipped_count} issue(s) overridden by oracle: comments",
            override_reasons={fp: overrides[fp] for fp in files_overridden},
            skipped_issue_count=skipped_count,
        )

    if rejected_overrides:
        # Capture rejected (circular/placeholder/too-short) reasons — the corpus
        # the half-life review uses to tune the bar.
        _record_signal(
            gate_name="implementation_echo_test_gate",
            decision="override-rejected",
            reason=f"{len(rejected_overrides)} oracle: override(s) failed the substance bar",
            rejected_overrides={
                fp: [{"reason": r, "category": c} for r, c in fails]
                for fp, fails in rejected_overrides.items()
            },
        )

    if not remaining_issues:
        # All issues were overridden by valid reasons — allow with the capture above.
        if fixture_issues:
            return allow_with_fixture_warning(fixture_issues)
        return allow()

    # Issues remain — deny the finishing command. The denial message itself
    # documents two first-class escape paths (gate-design.md Rule 1): the
    # per-file `# oracle:` override and saying 'proceed'. A hard deny is
    # warranted here because shared-generated-literals and mock-only tests are
    # the highest-confidence echo patterns the gate detects.
    _record_signal(
        gate_name="implementation_echo_test_gate",
        decision="deny",
        reason=f"{len(remaining_issues)} implementation-echo issue(s) remaining after overrides",
        issue_count=len(remaining_issues),
        issue_kinds=sorted({i.kind for i in remaining_issues}),
        data_fixture_issue_count=len(fixture_issues),
        data_fixture_files=sorted({i.filepath for i in fixture_issues}),
    )
    message = build_message(remaining_issues, rejected_overrides)
    if fixture_issues:
        message += "\n\n" + build_data_fixture_warning(fixture_issues)
    return deny(message)


if __name__ == "__main__":
    raise SystemExit(main())
