"""Mutation-strength controls for Git-native oracle change semantics."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oracle_downgrade_git_fixtures import (
    PUBLIC_HOOKS,
    STRONG,
    WEAK,
    advisory_message,
    commit,
    edited_rename_repo,
    feature_repo,
    git,
    git_bytes,
    landing_repo,
    raw_object_repo,
    raw_rename_repo,
    run_hook,
    weakening_rename_repo,
    write,
)


def rename_count(records: bytes) -> int:
    return sum(
        field.startswith(b"R") and field[1:].isdigit() for field in records.split(b"\0")
    )


def raw_rename_records(records: bytes) -> tuple[tuple[bytes, bytes, bytes], ...]:
    fields = records.removesuffix(b"\0").split(b"\0")
    assert len(fields) % 3 == 0
    parsed = tuple(
        (fields[index], fields[index + 1], fields[index + 2])
        for index in range(0, len(fields), 3)
    )
    assert all(status.startswith(b"R") for status, _old, _new in parsed)
    return parsed


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_raw_inexact_renames_warn_independently_at_destination_paths(
    tmp_path: Path, hook: Path, event: str
) -> None:
    renames = (
        (b"test_old_\xfe.py", b"test_new_\xfe.py", WEAK),
        (b"test_old_\xff.py", b"test_new_\xff.py", WEAK),
    )
    repo, raw_pairs, baseline = raw_rename_repo(tmp_path, renames)
    records = raw_rename_records(
        git_bytes(
            repo,
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            "-l0",
            baseline,
            "HEAD",
        )
    )
    assert {(old_path, new_path) for _status, old_path, new_path in records} == set(
        raw_pairs
    )
    old_display = tuple(os.fsdecode(old_path) for old_path, _new_path in raw_pairs)
    new_display = tuple(os.fsdecode(new_path) for _old_path, new_path in raw_pairs)
    assert new_display[0] != new_display[1]

    message = advisory_message(run_hook(hook, repo, event), event)

    for path in new_display:
        assert message.count(path) == 1
    for path in old_display:
        assert path not in message


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_raw_inexact_rename_that_remains_strong_is_silent(
    tmp_path: Path, hook: Path, event: str
) -> None:
    stronger = STRONG + "    assert raw_rename_extra() == 8\n"
    repo, raw_pairs, baseline = raw_rename_repo(
        tmp_path,
        ((b"test_old_\xff.py", b"test_new_\xff.py", stronger),),
    )
    records = raw_rename_records(
        git_bytes(
            repo,
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            "-l0",
            baseline,
            "HEAD",
        )
    )
    assert tuple((old_path, new_path) for _status, old_path, new_path in records) == (
        raw_pairs[0],
    )

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("target_kind", ("absolute", "relative"))
@pytest.mark.parametrize("candidate_state", ("committed", "staged", "unstaged"))
@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_external_symlink_cannot_supply_candidate_oracle_strength(
    tmp_path: Path,
    hook: Path,
    event: str,
    candidate_state: str,
    target_kind: str,
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    external = tmp_path / "external-strong-looking.py"
    external.write_text(STRONG, encoding="utf-8")
    test_path = repo / "tests" / "test_total.py"
    link_target = (
        external
        if target_kind == "absolute"
        else Path("../../external-strong-looking.py")
    )
    assert git(repo, "show", "HEAD:tests/test_total.py") == STRONG.strip()
    test_path.unlink()
    test_path.symlink_to(link_target)
    assert external.read_text(encoding="utf-8") == STRONG
    assert test_path.is_symlink()
    assert test_path.readlink() == link_target
    assert test_path.resolve(strict=True) == external
    if candidate_state == "committed":
        commit(repo, "replace repository test with external symlink")
        assert git(repo, "ls-tree", "HEAD", "tests/test_total.py").startswith(
            "120000 blob "
        )
        candidate = git(repo, "show", "HEAD:tests/test_total.py")
    elif candidate_state == "staged":
        git(repo, "add", "tests/test_total.py")
        assert git(repo, "ls-files", "--stage", "tests/test_total.py").startswith(
            "120000 "
        )
        candidate = git(repo, "show", ":tests/test_total.py")
    else:
        assert git(repo, "diff", "--name-only") == "tests/test_total.py"
        candidate = os.fspath(test_path.readlink())
    assert candidate == os.fspath(link_target)
    assert "assert" not in candidate

    message = advisory_message(run_hook(hook, repo, event), event)

    assert message.count("tests/test_total.py") == 1


@pytest.mark.parametrize("target_kind", ("absolute", "relative"))
@pytest.mark.parametrize("candidate_state", ("committed", "staged", "unstaged"))
@pytest.mark.parametrize("scenario", ("new-symlink", "weak-baseline"))
@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_symlink_without_removed_strong_oracle_is_silent(
    tmp_path: Path,
    hook: Path,
    event: str,
    scenario: str,
    candidate_state: str,
    target_kind: str,
) -> None:
    baseline = STRONG if scenario == "new-symlink" else WEAK
    repo = feature_repo(tmp_path, baseline)
    external = tmp_path / "external-strong-looking.py"
    external.write_text(STRONG, encoding="utf-8")
    link_target = (
        external
        if target_kind == "absolute"
        else Path("../../external-strong-looking.py")
    )
    relative_path = (
        Path("tests/test_new_link.py")
        if scenario == "new-symlink"
        else Path("tests/test_total.py")
    )
    test_path = repo / relative_path
    if test_path.exists():
        test_path.unlink()
    test_path.symlink_to(link_target)
    assert test_path.resolve(strict=True) == external
    if candidate_state == "committed":
        commit(repo, "add symlink without removing a strong oracle")
        assert git(repo, "ls-tree", "HEAD", os.fspath(relative_path)).startswith(
            "120000 blob "
        )
    elif candidate_state == "staged":
        git(repo, "add", os.fspath(relative_path))
        assert git(repo, "ls-files", "--stage", os.fspath(relative_path)).startswith(
            "120000 "
        )
    elif scenario == "new-symlink":
        records = git_bytes(repo, "ls-files", "--others", "--exclude-standard", "-z")
        assert os.fsencode(relative_path) in records.split(b"\0")
    else:
        assert git(repo, "diff", "--name-only") == os.fspath(relative_path)

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_staged_symlink_then_unstaged_exact_strong_restore_is_silent(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    external = tmp_path / "outside.py"
    external.write_text(STRONG, encoding="utf-8")
    test_path = repo / "tests" / "test_total.py"
    test_path.unlink()
    test_path.symlink_to(Path("../../outside.py"))
    git(repo, "add", "tests/test_total.py")
    assert git(repo, "ls-files", "--stage", "tests/test_total.py").startswith("120000 ")
    test_path.unlink()
    write(repo, "tests/test_total.py", STRONG)
    assert not test_path.is_symlink()
    assert test_path.read_text(encoding="utf-8") == STRONG

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_staged_symlink_then_unstaged_stronger_regular_candidate_is_silent(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    external = tmp_path / "outside.py"
    external.write_text(STRONG, encoding="utf-8")
    test_path = repo / "tests" / "test_total.py"
    test_path.unlink()
    test_path.symlink_to(Path("../../outside.py"))
    git(repo, "add", "tests/test_total.py")
    assert git(repo, "ls-files", "--stage", "tests/test_total.py").startswith("120000 ")
    test_path.unlink()
    stronger = STRONG + "    assert extra_check() == 7\n"
    write(repo, "tests/test_total.py", stronger)
    assert not test_path.is_symlink()
    assert test_path.read_text(encoding="utf-8") == stronger
    assert stronger != STRONG

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("target_kind", ("absolute", "relative"))
@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_external_symlinked_ancestor_cannot_supply_candidate_oracle_strength(
    tmp_path: Path, hook: Path, event: str, target_kind: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    external_tests = tmp_path / "external-tests"
    external_tests.mkdir()
    external_leaf = external_tests / "test_total.py"
    external_leaf.write_text(STRONG, encoding="utf-8")
    tests_dir = repo / "tests"
    test_path = tests_dir / "test_total.py"
    test_path.unlink()
    tests_dir.rmdir()
    link_target = (
        external_tests if target_kind == "absolute" else Path("../external-tests")
    )
    tests_dir.symlink_to(link_target, target_is_directory=True)
    assert tests_dir.is_symlink()
    assert tests_dir.readlink() == link_target
    assert tests_dir.resolve(strict=True) == external_tests
    assert not test_path.is_symlink()
    assert test_path.resolve(strict=True) == external_leaf
    assert test_path.read_text(encoding="utf-8") == STRONG
    deleted = git_bytes(repo, "diff", "--name-only", "-z")
    untracked = git_bytes(repo, "ls-files", "--others", "--exclude-standard", "-z")
    assert b"tests/test_total.py" in deleted.split(b"\0")
    assert b"tests" in untracked.split(b"\0")

    message = advisory_message(run_hook(hook, repo, event), event)

    assert message.count("tests/test_total.py") == 1


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_normal_in_repo_ancestor_reads_stronger_candidate_silently(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo = feature_repo(tmp_path, STRONG)
    tests_dir = repo / "tests"
    assert tests_dir.is_dir()
    assert not tests_dir.is_symlink()
    assert tests_dir.resolve(strict=True) == tests_dir
    stronger = STRONG + "    assert normal_ancestor_check() == 9\n"
    write(repo, "tests/test_total.py", stronger)
    assert (tests_dir / "test_total.py").read_text(encoding="utf-8") == stronger

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("local_state", ("staged", "unstaged", "untracked-replacement"))
@pytest.mark.parametrize("landing_state", ("missing", "dangling"))
@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_unresolved_landing_ref_keeps_unusual_local_path_nul_safe(
    tmp_path: Path,
    hook: Path,
    event: str,
    landing_state: str,
    local_state: str,
) -> None:
    filename = "tests/test_local_newline\ncase.py"
    repo = landing_repo(tmp_path, STRONG)
    git(repo, "mv", "tests/test_total.py", filename)
    commit(repo, "move baseline to unusual local path")
    git(repo, "push", "origin", "trunk")
    git(repo, "checkout", "-b", "feature/oracle-change")
    if landing_state == "missing":
        git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    else:
        git(
            repo,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/dangling",
        )
    if local_state == "staged":
        write(repo, filename, WEAK)
        git(repo, "add", filename)
        records = git_bytes(repo, "diff", "--cached", "--name-only", "-z")
        assert filename.encode() in records.split(b"\0")
    elif local_state == "unstaged":
        write(repo, filename, WEAK)
        records = git_bytes(repo, "diff", "--name-only", "-z")
        assert filename.encode() in records.split(b"\0")
    else:
        git(repo, "rm", filename)
        write(repo, filename, WEAK)
        cached = git_bytes(repo, "diff", "--cached", "--name-only", "-z")
        untracked = git_bytes(repo, "ls-files", "--others", "--exclude-standard", "-z")
        assert filename.encode() in cached.split(b"\0")
        assert filename.encode() in untracked.split(b"\0")

    message = advisory_message(run_hook(hook, repo, event), event)

    assert message.count(filename) == 1


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_multiple_edited_renames_ignore_ambient_rename_limit(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo, baseline, _new_paths = edited_rename_repo(tmp_path)
    limited = git_bytes(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        "-l1",
        baseline,
        "HEAD",
    )
    unlimited = git_bytes(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        "-l0",
        baseline,
        "HEAD",
    )
    assert rename_count(limited) == 0
    assert rename_count(unlimited) == 3

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_inexact_rename_that_weakens_oracle_warns_once_at_new_path(
    tmp_path: Path, hook: Path, event: str
) -> None:
    repo, baseline, new_path = weakening_rename_repo(tmp_path)
    records = git_bytes(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        "-l0",
        baseline,
        "HEAD",
    ).split(b"\0")
    assert records[:3] == [
        b"R095",
        b"tests/test_total.py",
        new_path.encode(),
    ]

    message = advisory_message(run_hook(hook, repo, event), event)

    assert message.count(new_path) == 1


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_distinct_raw_paths_that_replacement_decode_collides_warn_independently(
    tmp_path: Path, hook: Path, event: str
) -> None:
    candidates = {
        b"test_collision_\xfe.py": WEAK,
        b"test_collision_\xff.py": WEAK,
    }
    repo, raw_paths, baseline = raw_object_repo(tmp_path, candidates)
    records = git_bytes(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        baseline,
        "HEAD",
    )
    for raw_path in raw_paths:
        assert raw_path in records.split(b"\0")
    assert raw_paths[0].decode(errors="replace") == raw_paths[1].decode(
        errors="replace"
    )
    decoded_paths = tuple(os.fsdecode(path) for path in raw_paths)
    assert decoded_paths[0] != decoded_paths[1]

    message = advisory_message(run_hook(hook, repo, event), event)

    for decoded_path in decoded_paths:
        assert message.count(decoded_path) == 1


@pytest.mark.parametrize(("hook", "event"), PUBLIC_HOOKS)
def test_raw_object_candidate_that_remains_strong_is_silent_without_checkout(
    tmp_path: Path, hook: Path, event: str
) -> None:
    raw_name = b"test_still_strong_\xff.py"
    stronger = STRONG + "    assert extra_check() == 7\n"
    repo, raw_paths, baseline = raw_object_repo(tmp_path, {raw_name: stronger})
    records = git_bytes(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        baseline,
        "HEAD",
    )
    assert raw_paths[0] in records.split(b"\0")

    result = run_hook(hook, repo, event)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
