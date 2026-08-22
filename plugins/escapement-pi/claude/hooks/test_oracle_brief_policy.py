"""Path classification and Test Oracle Brief content policy."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from oracle_brief_rapid import (  # noqa: E402
    PLACEHOLDER_VALUES,
    PROOF_STAGES,
    REQUIRED_SECTIONS,
    rapid_brief_errors,
)


BRIEF_RELATIVE_PATH = Path(".agent/runtime/test-oracle-brief.md")

# Each required section must say something responsive to that section's job.
# The anchors are stems so concise prose remains valid without prescribing exact
# wording, while arbitrary token-count padding cannot satisfy the contract.
SECTION_ANCHORS = {
    "Business invariant": frozenset(
        {"behavior", "business", "ensure", "must", "outcome", "receiv", "requir", "user"}
    ),
    "Independent source of truth": frozenset(
        {"correct", "decision", "evidence", "hook", "json", "observ", "pro", "public", "signal", "source", "truth"}
    ),
    "Solution constraints": frozenset(
        {"allow", "constraint", "deny", "landing", "may", "must", "preserv", "remain", "stay"}
    ),
    "Invalid solution classes": frozenset(
        {"bypass", "deny", "invalid", "must", "wrong", "wording"}
    ),
    "Fragile implementation to reject": frozenset(
        {"fail", "fragile", "hardcod", "only", "payload", "shortcut", "special"}
    ),
    "Negative control": frozenset(
        {"ask", "deny", "fail", "invalid", "missing", "reject", "without"}
    ),
    "Positive control": frozenset(
        {"allow", "complete", "pass", "present", "valid", "with"}
    ),
    "Missing/unresolved handling": frozenset(
        {"absent", "allow", "ask", "block", "deny", "fail", "missing", "unresolved"}
    ),
    "Final outcome verification": frozenset(
        {"command", "execute", "hook", "inspect", "json", "run", "signal", "test", "verif"}
    ),
}

CODE_EXTENSIONS = frozenset(
    {
        ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        ".rb", ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".clj",
        ".cljs", ".cs", ".fs", ".swift", ".c", ".cc", ".cpp", ".cxx",
        ".h", ".hpp", ".m", ".mm", ".php", ".ex", ".exs", ".erl",
        ".hrl", ".lua", ".pl", ".pm", ".r", ".jl", ".zig", ".nim",
        ".v", ".vue", ".svelte", ".astro", ".ipynb", ".sql", ".sh",
        ".bash", ".zsh",
    }
)

EXEMPT_EXTENSIONS = frozenset(
    {
        ".md", ".mdx", ".rst", ".txt", ".json", ".jsonl", ".yaml",
        ".yml", ".toml", ".ini", ".cfg", ".env", ".csv", ".tsv",
        ".html", ".css", ".scss", ".sass", ".svg", ".xml", ".lock",
        ".sum", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
    }
)

EXEMPT_DIR_SEGMENTS = frozenset(
    {
        ".agent", ".claude", ".codex", ".git", "docs", "doc", "scripts",
        "bin", "tools", "scratch", "spike", "spikes", "prototype",
        "prototypes", "tmp", "vendor", "node_modules", "dist", "build",
        "coverage", "__pycache__",
    }
)

TEST_FILE_PATTERNS = (
    re.compile(r"^test_.*\.py$"),
    re.compile(r"^.*_test\.py$"),
    re.compile(r"^conftest\.py$"),
    re.compile(r"^.*\.(test|spec)\.[a-z0-9]+$", re.IGNORECASE),
    re.compile(r"^.*_test\.go$"),
    re.compile(r"^.*_test\.rs$"),
)


def _normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _section_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    stripped = re.sub(r"^#{1,6}\s*", "", stripped)
    stripped = re.sub(r"^\d+[.)]\s*", "", stripped)
    normalized = _normalize_heading(stripped.strip("*_ \t:-"))
    return next(
        (section for section in REQUIRED_SECTIONS if _normalize_heading(section) == normalized),
        None,
    )


def _normalized_body(lines: list[str]) -> tuple[str, list[str]]:
    values: list[str] = []
    for line in lines:
        value = line.strip()
        if re.match(r"^#{1,6}\s+", value):
            continue
        value = re.sub(r"^(?:[-*+]|\d+[.)])\s*", "", value)
        value = value.strip("`*_ \t")
        normalized = re.sub(r"\s+", " ", value).strip(" .:;").casefold()
        if normalized and normalized not in PLACEHOLDER_VALUES:
            values.append(normalized)
    body = " ".join(values)
    return body, re.findall(r"[a-z0-9]+", body)


def _section_is_substantive(section: str, lines: list[str]) -> tuple[bool, str]:
    body, tokens = _normalized_body(lines)
    unique_tokens = set(tokens)
    if len(tokens) < 3 or len(unique_tokens) < 2:
        return False, body
    anchors = SECTION_ANCHORS[section]
    responsive = any(token.startswith(anchor) for token in unique_tokens for anchor in anchors)
    return responsive, body


def missing_brief_sections(brief_path: Path) -> list[str]:
    try:
        text = brief_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return list(REQUIRED_SECTIONS)

    section_bodies: dict[str, list[str]] = {}
    active_section: str | None = None
    for line in text.splitlines():
        heading = _section_heading(line)
        if heading is not None:
            active_section = heading
            section_bodies.setdefault(heading, [])
        elif active_section is not None:
            section_bodies[active_section].append(line)

    evaluations = {
        section: _section_is_substantive(section, section_bodies.get(section, []))
        for section in REQUIRED_SECTIONS
    }
    body_counts = Counter(body for _, body in evaluations.values() if body)
    return [
        section
        for section, (substantive, body) in evaluations.items()
        if not substantive or (body and body_counts[body] > 1)
    ]


def brief_status(
    repo_root: Path, *, stage: str = "edit"
) -> tuple[bool, str | None, str]:
    brief_path = repo_root / BRIEF_RELATIVE_PATH
    if not brief_path.exists():
        return (
            False,
            f"Missing required Test Oracle Brief: {BRIEF_RELATIVE_PATH}",
            "missing-brief",
        )
    missing = missing_brief_sections(brief_path)
    if not missing:
        return True, None, "valid-brief"

    if stage not in PROOF_STAGES:
        return False, "Unknown proof stage; use the full nine-section form.", "invalid-rapid-brief"
    try:
        text = brief_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    rapid_candidate, rapid_errors = rapid_brief_errors(text, stage)
    if rapid_candidate:
        if rapid_errors:
            return (
                False,
                "Rapid Test Oracle Brief is incomplete or ineligible: "
                + "; ".join(dict.fromkeys(rapid_errors))
                + ". Use the full nine-section form.",
                "invalid-rapid-brief",
            )
        return True, None, "valid-rapid-brief"

    return (
        False,
        "Test Oracle Brief is missing required explanatory content for: "
        + ", ".join(missing),
        "invalid-brief",
    )


def find_git_root(start: str | Path) -> Path | None:
    path = Path(start).expanduser()
    search_dir = path if path.is_dir() else path.parent
    while not search_dir.exists():
        parent = search_dir.parent
        if parent == search_dir:
            return None
        search_dir = parent
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
    return Path(result.stdout.strip()).resolve(strict=False)


def is_relevant_file(filepath: str) -> bool:
    path = Path(filepath)
    parts = set(path.parts)
    if path.name == "__init__.py" or parts & EXEMPT_DIR_SEGMENTS:
        return False
    if parts & {"tests", "test", "spec", "__tests__", "__specs__"}:
        return True
    if any(pattern.match(path.name) for pattern in TEST_FILE_PATTERNS):
        return True
    suffix = path.suffix.lower()
    if suffix in EXEMPT_EXTENSIONS:
        return False
    return suffix in CODE_EXTENSIONS


def _resolved_target(raw_path: str, cwd_path: Path) -> Path:
    path = Path(raw_path).expanduser()
    candidate = path if path.is_absolute() else cwd_path / path
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return Path(os.path.abspath(os.path.normpath(candidate)))


def classify_edit_target(
    raw_path: str, cwd: str | None
) -> tuple[Path | None, str | None, bool]:
    """Return repository, normalized repository target, and relevance."""
    cwd_path = Path(cwd or os.getcwd()).expanduser().resolve(strict=False)
    repo_root = find_git_root(cwd_path)
    target = _resolved_target(raw_path, cwd_path)
    if repo_root is None:
        repo_root = find_git_root(target)
    if repo_root is None:
        return None, None, False
    try:
        relative_target = target.relative_to(repo_root).as_posix()
    except ValueError:
        return repo_root, None, False
    return repo_root, relative_target, is_relevant_file(relative_target)
