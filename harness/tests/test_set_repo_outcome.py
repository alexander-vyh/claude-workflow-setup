"""Behavioral tests for harness/bin/set_repo_outcome.py.

Oracle: a written `.escapement/repo.json` is valid iff `repo_outcome.resolve()` (the
real reader, not reimplemented here) parses it back to the same declaration with
`source == "declared"` — round-tripping through the actual consumer is the
independent check, not re-asserting the writer's own logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))

import repo_outcome
import set_repo_outcome


def test_default_writes_conservative_declaration(tmp_path: Path) -> None:
    path = set_repo_outcome.write_declaration(tmp_path, set_repo_outcome.build_declaration())
    resolved = repo_outcome.resolve(tmp_path)
    assert resolved.source == "declared"
    assert resolved.intended_outcome == "pr-opened"
    assert resolved.auto_merge_on_green is False
    assert path == repo_outcome.declaration_path(tmp_path)


def test_authorized_declaration_round_trips(tmp_path: Path) -> None:
    declaration = set_repo_outcome.build_declaration(
        intended_outcome="merged-and-deployed",
        auto_merge_on_green=True,
        deploy_on="push-to-main",
        deploy_surface="Cloud Run exec dashboard",
    )
    set_repo_outcome.write_declaration(tmp_path, declaration)
    resolved = repo_outcome.resolve(tmp_path)
    assert repo_outcome.authorizes_auto_merge(resolved) is True
    assert resolved.deploy == {"on": "push-to-main", "surface": "Cloud Run exec dashboard"}


def test_invalid_intended_outcome_is_rejected() -> None:
    try:
        set_repo_outcome.build_declaration(intended_outcome="not-a-real-outcome")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "invalid intended_outcome" in str(exc)


def test_auto_merge_without_sufficient_outcome_is_rejected() -> None:
    try:
        set_repo_outcome.build_declaration(intended_outcome="pr-opened", auto_merge_on_green=True)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "auto_merge_on_green" in str(exc)


def test_confirm_class_round_trips(tmp_path: Path) -> None:
    declaration = set_repo_outcome.build_declaration(
        intended_outcome="merged", auto_merge_on_green=True, confirm_class=["db-migration"]
    )
    set_repo_outcome.write_declaration(tmp_path, declaration)
    resolved = repo_outcome.resolve(tmp_path)
    assert resolved.confirm_class == ["db-migration"]
    assert not hasattr(resolved, "confirm_class_absolute")


def test_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    set_repo_outcome.write_declaration(tmp_path, set_repo_outcome.build_declaration())
    try:
        set_repo_outcome.write_declaration(tmp_path, set_repo_outcome.build_declaration())
        assert False, "expected FileExistsError"
    except FileExistsError:
        pass


def test_force_overwrites_existing_declaration(tmp_path: Path) -> None:
    set_repo_outcome.write_declaration(tmp_path, set_repo_outcome.build_declaration())
    new_declaration = set_repo_outcome.build_declaration(
        intended_outcome="merged", auto_merge_on_green=True
    )
    set_repo_outcome.write_declaration(tmp_path, new_declaration, force=True)
    resolved = repo_outcome.resolve(tmp_path)
    assert repo_outcome.authorizes_auto_merge(resolved) is True


def test_cli_main_writes_file_and_reports_path(tmp_path: Path, capsys) -> None:
    code = set_repo_outcome.main(["--repo-root", str(tmp_path), "--intended-outcome", "merged", "--auto-merge"])
    assert code == 0
    out = capsys.readouterr().out
    assert str(repo_outcome.declaration_path(tmp_path)) in out
    resolved = repo_outcome.resolve(tmp_path)
    assert repo_outcome.authorizes_auto_merge(resolved) is True


def test_cli_main_rejects_invalid_combo_with_nonzero_exit(tmp_path: Path, capsys) -> None:
    code = set_repo_outcome.main(["--repo-root", str(tmp_path), "--auto-merge"])
    assert code != 0
    assert "auto_merge_on_green" in capsys.readouterr().err
    assert not repo_outcome.declaration_path(tmp_path).exists()
