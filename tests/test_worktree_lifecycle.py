"""Minimum behavioral oracle for Escapement worktree completion."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from worktree_fixtures import git, rev, run_cli


@dataclass
class LifecycleScenario:
    primary: Path
    remote: Path
    seed: Path
    worktree: Path
    receipt: Path
    cwd_facts: Path
    lsof_log: Path
    branch: str
    source_sha: str
    env: dict[str, str]


def _write_fake_tools(root: Path, facts: Path) -> Path:
    fake_bin = root / "bin"
    fake_bin.mkdir(parents=True)
    gh = fake_bin / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    raise SystemExit(0)
with open(os.environ["LIFECYCLE_GITHUB_FACTS"], encoding="utf-8") as stream:
    facts = json.load(stream)
if facts.get("failure"):
    print(facts["failure"], file=sys.stderr)
    raise SystemExit(1)
joined = " ".join(args)
if "graphql" in args:
    pull = {
        "number": 7, "state": "MERGED", "merged": True,
        "mergedAt": "2026-08-14T12:00:00Z",
        "baseRefName": "trunk", "headRefName": facts["branch"],
        "headRefOid": facts["candidate"],
        "mergeCommit": {"oid": facts["merge_result"]},
        "headRepository": {"id": 42, "databaseId": 42, "nameWithOwner": "acme/widget"},
    }
    if "associatedPullRequests" in joined:
        repo = {"object": {"oid": facts["candidate"], "associatedPullRequests": {
            "nodes": [pull], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
    else:
        repo = {"pullRequests": {"nodes": [], "pageInfo": {
            "hasNextPage": False, "endCursor": None}}}
    print(json.dumps({"data": {"repository": repo}}))
elif "/compare/" in joined:
    print(json.dumps(facts.get("compare", {
        "status": "ahead", "base_commit": {"sha": facts["merge_result"]},
        "head_commit": {"sha": facts["default_sha"]}})))
else:
    print(json.dumps({"id": 42, "databaseId": 42, "full_name": "acme/widget",
                      "default_branch": "trunk", "defaultBranchRef": {
                          "name": "trunk", "target": {"oid": facts["default_sha"]}}}))
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    lsof = fake_bin / "lsof"
    lsof.write_text(
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
log = Path(os.environ["LIFECYCLE_LSOF_LOG"])
scan_number = len(log.read_text(encoding="utf-8").splitlines()) + 1
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")

with open(os.environ["LIFECYCLE_CWD_FACTS"], encoding="utf-8") as stream:
    facts = json.load(stream)

warning = (
    "lsof: WARNING: can't stat() opaque file system "
    "/unrelated/unreadable-volume\\nOutput information may be incomplete."
)
print(warning, file=sys.stderr)

if args != ["-a", "-d", "cwd", "-Fpcfn0"]:
    raise SystemExit(64)
if facts.get("mode") == "failure" or facts.get("failure_at") == scan_number:
    raise SystemExit(72)
if facts.get("mode") == "empty":
    raise SystemExit(0)
if facts.get("mode") == "bad-pid":
    cwd = facts["cwds"][0]
    sys.stdout.write(f"pnot-a-pid\\0cfixture\\0\\nfcwd\\0n{cwd}\\0\\n")
    raise SystemExit(0)

if facts.get("mode") == "partial":
    _incomplete, complete = facts["cwds"]
    sys.stdout.write(
        f"p100\\0cfixture\\0\\nfcwd\\0"
        f"\\np101\\0cfixture\\0\\nfcwd\\0n{complete}\\0\\n"
    )
    raise SystemExit(0)

for index, cwd in enumerate(facts["cwds"], start=100):
    sys.stdout.write(f"p{index}\\0cfixture\\0\\nfcwd\\0n{cwd}\\0\\n")
""",
        encoding="utf-8",
    )
    lsof.chmod(0o755)
    return fake_bin


def _scenario(tmp_path: Path) -> LifecycleScenario:
    remote = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    primary = tmp_path / "primary"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "--initial-branch", "trunk", str(seed))
    (seed / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    policy = seed / ".escapement" / "repo.json"
    policy.parent.mkdir()
    policy.write_text(
        json.dumps({"intended_outcome": "merged", "auto_merge_on_green": True}) + "\n",
        encoding="utf-8",
    )
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "base")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "trunk")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
    git(tmp_path, "clone", str(remote), str(primary))
    git(primary, "switch", "-c", "root-main")

    harness = tmp_path / "harness"
    facts = tmp_path / "github.json"
    facts.write_text("{}\n", encoding="utf-8")
    cwd_facts = tmp_path / "cwd.json"
    target = primary / ".worktrees" / "life-1"
    cwd_facts.write_text(
        json.dumps({"target": str(target), "cwds": [str(primary)]}) + "\n",
        encoding="utf-8",
    )
    lsof_log = tmp_path / "lsof.jsonl"
    lsof_log.write_text("", encoding="utf-8")
    fake_bin = _write_fake_tools(tmp_path / "fake", facts)
    github_url = "https://github.com/acme/widget.git"
    file_url = remote.resolve().as_uri()
    env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CONTINUATION_HARNESS_HOME": str(harness),
        "LIFECYCLE_CWD_FACTS": str(cwd_facts),
        "LIFECYCLE_GITHUB_FACTS": str(facts),
        "LIFECYCLE_LSOF_LOG": str(lsof_log),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{file_url}.insteadOf",
        "GIT_CONFIG_VALUE_0": github_url,
    }
    git(primary, "remote", "set-url", "origin", github_url)
    git(primary, "config", f"url.{file_url}.insteadOf", github_url)
    result = run_cli(
        primary,
        "create",
        "--repo",
        str(primary),
        "--name",
        "life-1",
        "--branch",
        "feature/life-1",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    worktree = primary / ".worktrees" / "life-1"
    return LifecycleScenario(
        primary=primary,
        remote=remote,
        seed=seed,
        worktree=worktree,
        receipt=harness / "worktrees" / "life-1.json",
        cwd_facts=cwd_facts,
        lsof_log=lsof_log,
        branch="feature/life-1",
        source_sha=rev(worktree),
        env=env,
    )


def _land(scenario: LifecycleScenario, *, advance_default: bool = True) -> str:
    (scenario.worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(scenario.worktree, "add", "feature.txt")
    git(scenario.worktree, "commit", "-m", "feature")
    candidate = rev(scenario.worktree)
    git(scenario.worktree, "push", "origin", f"HEAD:refs/heads/{scenario.branch}")
    git(
        scenario.seed,
        "fetch",
        str(scenario.remote),
        f"refs/heads/{scenario.branch}:refs/remotes/origin/{scenario.branch}",
    )
    git(scenario.seed, "merge", "--no-ff", "--no-edit", f"origin/{scenario.branch}")
    merge_result = rev(scenario.seed)
    if advance_default:
        (scenario.seed / "later.txt").write_text("later\n", encoding="utf-8")
        git(scenario.seed, "add", "later.txt")
        git(scenario.seed, "commit", "-m", "later")
    default_sha = rev(scenario.seed)
    git(scenario.seed, "push", "origin", "trunk")
    Path(scenario.env["LIFECYCLE_GITHUB_FACTS"]).write_text(
        json.dumps(
            {
                "branch": scenario.branch,
                "candidate": candidate,
                "merge_result": merge_result,
                "default_sha": default_sha,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return candidate


def _finish(scenario: LifecycleScenario, *, cwd: Path | None = None):
    return run_cli(cwd or scenario.primary, "finish", "--lifecycle-id", "life-1", env=scenario.env)


def _set_cwd_scan(
    scenario: LifecycleScenario,
    *,
    cwds: list[Path] | None = None,
    mode: str = "ok",
    failure_at: int | None = None,
) -> None:
    scenario.cwd_facts.write_text(
        json.dumps(
            {
                "target": str(scenario.worktree),
                "cwds": [str(path) for path in (cwds or [])],
                "mode": mode,
                "failure_at": failure_at,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _assert_pending_preserved(
    scenario: LifecycleScenario, result, reason: str
) -> None:
    assert json.loads(result.stdout) == {
        "lifecycle_id": "life-1",
        "reason": reason,
        "status": "pending",
    }
    assert scenario.worktree.exists()
    assert scenario.receipt.exists()


def test_create_publishes_one_durable_receipt(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)

    receipt = json.loads(scenario.receipt.read_text(encoding="utf-8"))

    assert receipt["lifecycle_id"] == "life-1"
    assert receipt["phase"] == "created"
    assert receipt["source_sha"] == scenario.source_sha
    assert receipt["branch_ref"] == f"refs/heads/{scenario.branch}"
    assert scenario.receipt.stat().st_mode & 0o777 == 0o600


def test_finish_removes_safe_local_state_despite_unrelated_mount_warning(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    candidate = _land(scenario)

    result = _finish(scenario)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "completed"
    assert not scenario.worktree.exists()
    assert git(
        scenario.primary,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{scenario.branch}",
        check=False,
    ).returncode == 1
    assert not scenario.receipt.exists()
    assert git(scenario.remote, "show-ref", "--verify", f"refs/heads/{scenario.branch}").stdout.startswith(candidate)
    invocations = [json.loads(line) for line in scenario.lsof_log.read_text().splitlines()]
    assert len(invocations) >= 2
    assert all(args == ["-a", "-d", "cwd", "-Fpcfn0"] for args in invocations)


def test_finish_preserves_worktree_used_as_nested_process_cwd(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)
    nested = scenario.worktree / "nested" / "cwd"
    nested.mkdir(parents=True)
    _set_cwd_scan(scenario, cwds=[scenario.primary, nested])

    result = _finish(scenario)

    _assert_pending_preserved(scenario, result, "worktree-active-process-cwd")


@pytest.mark.parametrize("mode", ["failure", "empty", "bad-pid"])
def test_finish_fails_closed_when_global_cwd_enumeration_is_incomplete(
    tmp_path: Path, mode: str
) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)
    _set_cwd_scan(scenario, cwds=[scenario.primary], mode=mode)

    result = _finish(scenario)

    _assert_pending_preserved(scenario, result, "activity-inspection-failed")


def test_finish_fails_closed_when_one_cwd_record_is_incomplete(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)
    nested = scenario.worktree / "nested" / "cwd"
    nested.mkdir(parents=True)
    _set_cwd_scan(scenario, cwds=[nested, scenario.primary], mode="partial")

    result = _finish(scenario)

    _assert_pending_preserved(scenario, result, "activity-inspection-failed")


@pytest.mark.parametrize("failure_at", [2, 3])
def test_finish_reports_late_cwd_enumeration_failure_consistently(
    tmp_path: Path, failure_at: int
) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)
    _set_cwd_scan(scenario, cwds=[scenario.primary], failure_at=failure_at)

    result = _finish(scenario)

    _assert_pending_preserved(scenario, result, "activity-inspection-failed")


def test_ignored_content_is_preserved(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)
    valuable = scenario.worktree / ".worktrees" / "valuable.cache"
    valuable.parent.mkdir()
    valuable.write_text("keep\n", encoding="utf-8")

    result = _finish(scenario)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "lifecycle_id": "life-1",
        "reason": "ignored-content",
        "status": "pending",
    }
    assert valuable.read_text(encoding="utf-8") == "keep\n"
    assert scenario.receipt.exists()


def test_in_worktree_finish_hands_off_then_external_finish_completes(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)

    pending = _finish(scenario, cwd=scenario.worktree)
    completed = _finish(scenario)

    assert json.loads(pending.stdout)["status"] == "pending"
    assert json.loads(pending.stdout)["reason"] == "worktree-active-invoking-cwd"
    assert json.loads(completed.stdout)["status"] == "completed"
    assert not scenario.receipt.exists()


def test_missing_github_preserves_candidate(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)
    facts = Path(scenario.env["LIFECYCLE_GITHUB_FACTS"])
    value = json.loads(facts.read_text(encoding="utf-8"))
    value["failure"] = "offline"
    facts.write_text(json.dumps(value), encoding="utf-8")

    result = _finish(scenario)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "pending"
    assert scenario.worktree.exists()
    assert scenario.receipt.exists()


def test_clean_unmerged_head_is_not_authorized_by_receipt_source(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    _land(scenario)
    (scenario.worktree / "unmerged.txt").write_text("unique\n", encoding="utf-8")
    git(scenario.worktree, "add", "unmerged.txt")
    git(scenario.worktree, "commit", "-m", "not merged")
    unmerged_sha = rev(scenario.worktree)

    result = _finish(scenario)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "pending"
    assert rev(scenario.worktree) == unmerged_sha
    assert rev(scenario.primary, f"refs/heads/{scenario.branch}") == unmerged_sha
    assert scenario.receipt.exists()


def test_resume_after_removal_preserves_approval_when_branch_moved(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    candidate = _land(scenario)
    git(scenario.primary, "worktree", "remove", str(scenario.worktree))
    git(
        scenario.primary,
        "update-ref",
        f"refs/heads/{scenario.branch}",
        scenario.source_sha,
        candidate,
    )
    receipt = json.loads(scenario.receipt.read_text(encoding="utf-8"))
    receipt.update(phase="worktree_removed", approved_head_sha=candidate)
    scenario.receipt.write_text(json.dumps(receipt) + "\n", encoding="utf-8")

    result = _finish(scenario)
    retained = json.loads(scenario.receipt.read_text(encoding="utf-8"))

    assert json.loads(result.stdout)["reason"] == "branch-tip-moved"
    assert retained["phase"] == "worktree_removed"
    assert retained["approved_head_sha"] == candidate


def test_finish_rejects_lifecycle_id_that_escapes_registry(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    result = run_cli(
        tmp_path,
        "finish",
        "--lifecycle-id",
        "../escaped",
        env={"CONTINUATION_HARNESS_HOME": str(harness)},
    )

    assert result.returncode != 0
    assert "invalid or unsafe lifecycle identity" in result.stderr
    assert not (harness / "escaped.lock").exists()


def test_finish_does_not_follow_a_symlinked_lifecycle_lock(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    registry = harness / "worktrees"
    registry.mkdir(parents=True, mode=0o700)
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("keep\n", encoding="utf-8")
    sentinel.chmod(0o644)
    (registry / ".life-1.lock").symlink_to(sentinel)

    result = run_cli(
        tmp_path,
        "finish",
        "--lifecycle-id",
        "life-1",
        env={"CONTINUATION_HARNESS_HOME": str(harness)},
    )

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert sentinel.stat().st_mode & 0o777 == 0o644


def test_finish_preserves_symbolic_feature_ref_and_its_target(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    candidate = _land(scenario)
    victim = "refs/heads/victim"
    feature = f"refs/heads/{scenario.branch}"
    git(scenario.primary, "worktree", "remove", str(scenario.worktree))
    receipt = json.loads(scenario.receipt.read_text(encoding="utf-8"))
    receipt.update(phase="worktree_removed", approved_head_sha=candidate)
    scenario.receipt.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    git(scenario.primary, "update-ref", victim, candidate)
    git(scenario.primary, "symbolic-ref", feature, victim)

    result = _finish(scenario)

    assert json.loads(result.stdout)["reason"] == "branch-ref-symbolic"
    assert git(scenario.primary, "rev-parse", victim).stdout.strip() == candidate
    assert git(scenario.primary, "symbolic-ref", feature).stdout.strip() == victim
    assert scenario.receipt.exists()
