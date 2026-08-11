#!/usr/bin/env python3
"""Exact parent-walk boundary tests for bead escapement-858.3.

These checks complement the public hook tests in test_task_mode_root_scope.py.
They constrain lookup budget and safe fallback directly so an unbounded walk,
an off-by-one cap, or a cache shared across claims cannot satisfy the public
outcome accidentally.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness" / "bin"))

import task_mode_entry  # noqa: E402


def _linear_chain(depth: int):
    """Return a literal leaf-to-root graph with ``depth`` parent transitions."""
    mapping = {
        f"node-{index}": (f"node-{index + 1}" if index < depth else None)
        for index in range(depth + 1)
    }
    calls: list[str] = []

    def run_show(issue_id: str):
        calls.append(issue_id)
        return {"id": issue_id, "parent": mapping[issue_id]}

    return run_show, calls


@pytest.mark.parametrize(
    ("depth", "expected_scope", "expected_lookups", "diagnostic"),
    [
        (0, None, 1, False),
        (1, "node-1", 2, False),
        (2, "node-2", 3, False),
        (3, "node-3", 4, False),
        (19, "node-19", 20, False),
        # The twentieth transition would enter an issue that has not itself
        # been decoded. The safe capped scope is therefore decoded node-19.
        (20, "node-19", 20, True),
        (21, "node-19", 20, True),
    ],
)
def test_parent_walk_budget_and_scope(
    depth: int,
    expected_scope: str | None,
    expected_lookups: int,
    diagnostic: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject fixed-depth, unbounded, and off-by-one parent walks."""
    run_show, calls = _linear_chain(depth)

    scope = task_mode_entry._lookup_parent_id("node-0", run_show=run_show)

    assert scope == expected_scope
    assert calls == [f"node-{index}" for index in range(expected_lookups)]
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == int(diagnostic)
    if diagnostic:
        assert "parent walk capped" in lines[0].lower()


@pytest.mark.parametrize(
    ("mapping", "expected_scope", "expected_calls"),
    [
        ({"leaf": "leaf"}, "leaf", ["leaf"]),
        (
            {"leaf": "branch", "branch": "other", "other": "branch"},
            "other",
            ["leaf", "branch", "other"],
        ),
    ],
)
def test_cycle_returns_last_decoded_issue_and_diagnoses_once(
    mapping: dict[str, str],
    expected_scope: str,
    expected_calls: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def run_show(issue_id: str):
        calls.append(issue_id)
        return {"id": issue_id, "parent": mapping[issue_id]}

    assert task_mode_entry._lookup_parent_id("leaf", run_show=run_show) == expected_scope
    assert calls == expected_calls
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert "cycle" in lines[0].lower()


def test_lookup_failure_returns_last_decoded_issue_and_diagnoses_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def run_show(issue_id: str):
        calls.append(issue_id)
        if issue_id == "parent":
            return None
        return {"id": "leaf", "parent": "parent"}

    assert task_mode_entry._lookup_parent_id("leaf", run_show=run_show) == "leaf"
    assert calls == ["leaf", "parent"]
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert "lookup failed" in lines[0].lower()


def test_two_walks_in_one_process_do_not_share_scope() -> None:
    """Reject a module-global root cache shared by unrelated claims."""
    first = {
        "leaf-a": {"id": "leaf-a", "parent": "root-a"},
        "root-a": {"id": "root-a", "parent": None},
    }
    second = {
        "leaf-b": {"id": "leaf-b", "parent": "branch-b"},
        "branch-b": {"id": "branch-b", "parent": "root-b"},
        "root-b": {"id": "root-b", "parent": None},
    }

    assert task_mode_entry._lookup_parent_id("leaf-a", run_show=first.get) == "root-a"
    assert task_mode_entry._lookup_parent_id("leaf-b", run_show=second.get) == "root-b"
