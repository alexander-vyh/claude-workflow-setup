"""Classification and advisory framing for textual test-data fixtures.

The implementation-echo gate owns literal extraction and decision policy.
This sibling owns the narrower question of which non-executable files live in
test buckets and how their advisory evidence is presented.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, Sequence


TEXT_DATA_FIXTURE_EXTENSIONS = frozenset(
    {
        ".json",
        ".jsonl",
        ".ndjson",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".csv",
        ".tsv",
    }
)

TEST_DIR_SEGMENTS = frozenset(
    {"tests", "test", "spec", "__tests__", "__specs__"}
)

DATA_FIXTURE_SCAN_BYTES = 8 * 1024 * 1024
DATA_FIXTURE_MATCH_LIMIT = 96

_DATA_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_:/+=.-])"
    r"(?P<value>[A-Za-z0-9_:/+=.-]{12,})"
    r"(?![A-Za-z0-9_:/+=.-])"
)


class FixtureIssue(Protocol):
    filepath: str
    kind: str
    detail: str


def is_text_data_fixture(filepath: str) -> bool:
    """Return whether a textual data file lives in an established test bucket."""
    path = Path(filepath)
    return (
        has_text_data_extension(filepath)
        and bool(set(path.parts) & TEST_DIR_SEGMENTS)
    )


def has_text_data_extension(filepath: str) -> bool:
    """Return whether a path uses one of the supported textual-data formats."""
    return Path(filepath).suffix.lower() in TEXT_DATA_FIXTURE_EXTENSIONS


def data_scalar_tokens(text: str) -> set[str]:
    """Return raw scalar-like tokens from quoted or unquoted textual data."""
    return {match.group("value") for match in _DATA_TOKEN_RE.finditer(text)}


def scan_data_fixture_matches(
    repo_root: Path,
    fixture_paths: Sequence[str],
    candidate_values: set[str],
) -> tuple[dict[str, set[str]], dict[str, object]]:
    """Find candidate values within one bounded, decision-wide scan budget."""
    remaining_bytes = DATA_FIXTURE_SCAN_BYTES
    retained_matches = 0
    matches_by_file: dict[str, set[str]] = {}
    truncated_files: list[str] = []
    issue_limit_reached = False

    for index, filepath in enumerate(fixture_paths):
        if remaining_bytes <= 0:
            truncated_files.extend(fixture_paths[index:])
            break

        try:
            with (repo_root / filepath).open("rb") as fixture:
                payload = fixture.read(remaining_bytes + 1)
        except OSError:
            continue

        if len(payload) > remaining_bytes:
            truncated_files.append(filepath)
            payload = payload[:remaining_bytes]
        remaining_bytes -= len(payload)

        matched = data_scalar_tokens(
            payload.decode("utf-8", errors="replace")
        ) & candidate_values
        for value in sorted(matched):
            if retained_matches >= DATA_FIXTURE_MATCH_LIMIT:
                issue_limit_reached = True
                break
            matches_by_file.setdefault(filepath, set()).add(value)
            retained_matches += 1
        if issue_limit_reached:
            break

    metadata: dict[str, object] = {}
    if truncated_files:
        metadata["fixture_scan_truncated_files"] = sorted(set(truncated_files))
    if issue_limit_reached:
        metadata["fixture_issue_limit_reached"] = True
    return matches_by_file, metadata


def build_data_fixture_warning(issues: Sequence[FixtureIssue]) -> str:
    """Build one nonblocking decision-level warning for fixture echo evidence."""
    listed = "\n".join(
        f"  - {issue.filepath}: {issue.kind}: {issue.detail}"
        for issue in issues[:12]
    )
    if len(issues) > 12:
        listed += f"\n  - ... {len(issues) - 12} more"

    return (
        "IMPLEMENTATION-ECHO DATA-FIXTURE NOTICE: changed textual fixtures "
        "share opaque values with changed production code.\n\n"
        f"{listed}\n\n"
        "This notice does not block the finishing command because the gate "
        "cannot determine fixture provenance from inert data alone. Confirm "
        "that the expected values come from an independent specification, "
        "source export, or consumer contract rather than copied implementation "
        "output. The finding is retained in the gate-signal corpus for review."
    )
