"""Classification and advisory framing for textual test-data fixtures.

The implementation-echo gate owns literal extraction and decision policy.
This sibling owns the narrower question of which non-executable files live in
test buckets and how their advisory evidence is presented.
"""

from __future__ import annotations

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


class FixtureIssue(Protocol):
    filepath: str
    kind: str
    detail: str


def is_text_data_fixture(filepath: str) -> bool:
    """Return whether a textual data file lives in an established test bucket."""
    path = Path(filepath)
    return (
        path.suffix.lower() in TEXT_DATA_FIXTURE_EXTENSIONS
        and bool(set(path.parts) & TEST_DIR_SEGMENTS)
    )


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
