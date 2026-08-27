"""Public lifecycle oracle for GitHub's identical-commit compare shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_worktree_lifecycle import _finish, _land, _scenario
from worktree_fixtures import git, rev, snapshot_primary


def _facts_path(scenario) -> Path:
    return Path(scenario.env["LIFECYCLE_GITHUB_FACTS"])


def _identical_response(sha: str) -> dict[str, object]:
    # Sanitized from the live 2026-08-27 response to:
    # gh api repos/alexander-vyh/escapement/compare/$sha...$sha
    # The raw object omits head_commit; it does not contain an explicit null.
    return {
        "status": "identical",
        "base_commit": {"sha": sha},
        "merge_base_commit": {"sha": sha},
        "ahead_by": 0,
        "behind_by": 0,
    }


def _set_compare(scenario, response: dict[str, object]) -> dict[str, object]:
    path = _facts_path(scenario)
    facts = json.loads(path.read_text(encoding="utf-8"))
    facts["compare"] = response
    path.write_text(json.dumps(facts) + "\n", encoding="utf-8")
    return facts


def _finish_expect_preserved(scenario, reason: str) -> None:
    primary_before = snapshot_primary(scenario.primary)
    worktree_before = snapshot_primary(scenario.worktree)
    receipt_before = json.loads(scenario.receipt.read_text(encoding="utf-8"))
    receipt_mode = scenario.receipt.stat().st_mode
    remote_before = rev(scenario.remote, f"refs/heads/{scenario.branch}")

    result = _finish(scenario)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "lifecycle_id": "life-1",
        "reason": reason,
        "status": "pending",
    }
    assert scenario.worktree.exists()
    assert scenario.receipt.exists()
    assert snapshot_primary(scenario.primary) == primary_before
    assert snapshot_primary(scenario.worktree) == worktree_before
    assert json.loads(scenario.receipt.read_text(encoding="utf-8")) == {
        **receipt_before,
        "finish_requested": True,
        "last_reason": reason,
        "phase": "requested",
    }
    assert scenario.receipt.stat().st_mode == receipt_mode
    assert rev(scenario.remote, f"refs/heads/{scenario.branch}") == remote_before


def test_finish_accepts_fully_bound_identical_compare_response(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    candidate = _land(scenario, advance_default=False)
    facts = json.loads(_facts_path(scenario).read_text(encoding="utf-8"))
    assert facts["merge_result"] == facts["default_sha"]
    _set_compare(scenario, _identical_response(facts["default_sha"]))

    result = _finish(scenario)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "completed"
    assert not scenario.worktree.exists()
    assert not scenario.receipt.exists()
    assert git(
        scenario.primary,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{scenario.branch}",
        check=False,
    ).returncode == 1
    assert git(
        scenario.remote, "show-ref", "--verify", f"refs/heads/{scenario.branch}"
    ).stdout.startswith(candidate)


def test_finish_rejects_identical_response_when_default_differs(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)
    facts = json.loads(_facts_path(scenario).read_text(encoding="utf-8"))
    assert facts["merge_result"] != facts["default_sha"]
    _set_compare(scenario, _identical_response(facts["merge_result"]))

    _finish_expect_preserved(scenario, "merge-result-not-reachable-from-live-default")


@pytest.mark.parametrize("head_field", [None, "missing"])
def test_finish_rejects_nonidentical_response_without_bound_head(
    tmp_path: Path, head_field: object
) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)
    facts = json.loads(_facts_path(scenario).read_text(encoding="utf-8"))
    response = {
        "status": "ahead",
        "base_commit": {"sha": facts["merge_result"]},
        "head_commit": head_field,
    }
    if head_field == "missing":
        response.pop("head_commit")
    _set_compare(scenario, response)

    _finish_expect_preserved(scenario, "github-inspection-failed")


@pytest.mark.parametrize(
    "mutation",
    [
        ("base_commit", None),
        ("base_commit", {"sha": "0" * 40}),
        ("merge_base_commit", None),
        ("merge_base_commit", {"sha": "0" * 40}),
        ("head_commit", None),
        ("head_commit", {"sha": "0" * 40}),
        ("ahead_by", "missing"),
        ("ahead_by", "0"),
        ("ahead_by", False),
        ("ahead_by", 1),
        ("behind_by", "missing"),
        ("behind_by", "0"),
        ("behind_by", False),
        ("behind_by", 1),
    ],
)
def test_finish_rejects_partially_bound_identical_response(
    tmp_path: Path, mutation: tuple[str, object]
) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario, advance_default=False)
    facts = json.loads(_facts_path(scenario).read_text(encoding="utf-8"))
    response = _identical_response(facts["default_sha"])
    field, value = mutation
    if value == "missing":
        response.pop(field)
    else:
        response[field] = value
    _set_compare(scenario, response)

    _finish_expect_preserved(scenario, "github-inspection-failed")
