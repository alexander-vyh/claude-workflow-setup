import json
import pathlib
import sys

import pytest

BIN = pathlib.Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import worktree_lifecycle_supervisor as lifecycle  # noqa: E402


def test_missing_registry_is_a_healthy_noop(tmp_path):
    assert lifecycle.reconcile(tmp_path) == {"status": "ok", "checked": 0}


def test_receipt_is_retried_through_public_finish(tmp_path):
    registry = tmp_path / "worktrees"
    registry.mkdir()
    (registry / "life-1.json").write_text("{}\n", encoding="utf-8")
    cli = tmp_path / "escapement-worktree"
    cli.write_text(
        "#!/bin/sh\nprintf '{\"status\":\"pending\"}\\n'\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)

    assert lifecycle.reconcile(tmp_path, cli=cli) == {"status": "ok", "checked": 1}


@pytest.mark.parametrize(
    "phase",
    [
        "allocating",
        "bootstrap_pending",
        "bootstrap_failed",
        "rollback_claimed",
        "rollback_worktree_removed",
        "rollback_ref_claimed",
        "rollback_ref_removed",
    ],
)
def test_incomplete_creation_is_routed_to_public_recovery(
    tmp_path, monkeypatch, phase
):
    registry = tmp_path / "worktrees"
    registry.mkdir()
    (registry / "life-1.json").write_text(
        json.dumps({"phase": phase}) + "\n",
        encoding="utf-8",
    )
    invocations = tmp_path / "invocations"
    cli = tmp_path / "escapement-worktree"
    cli.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$INVOCATIONS\"\n"
        "printf '{\"status\":\"pending\"}\\n'\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    monkeypatch.setenv("INVOCATIONS", str(invocations))

    assert lifecycle.reconcile(tmp_path, cli=cli) == {"status": "ok", "checked": 1}
    assert invocations.read_text(encoding="utf-8") == (
        "recover --lifecycle-id life-1\n"
    )


def test_created_lifecycle_remains_routed_to_public_finish(tmp_path, monkeypatch):
    registry = tmp_path / "worktrees"
    registry.mkdir()
    (registry / "life-1.json").write_text(
        json.dumps({"phase": "created"}) + "\n",
        encoding="utf-8",
    )
    invocations = tmp_path / "invocations"
    cli = tmp_path / "escapement-worktree"
    cli.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$INVOCATIONS\"\n"
        "printf '{\"status\":\"pending\"}\\n'\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    monkeypatch.setenv("INVOCATIONS", str(invocations))

    assert lifecycle.reconcile(tmp_path, cli=cli) == {"status": "ok", "checked": 1}
    assert invocations.read_text(encoding="utf-8") == (
        "finish --lifecycle-id life-1\n"
    )


def test_invalid_registry_entry_fails_the_tick(tmp_path):
    registry = tmp_path / "worktrees"
    registry.mkdir()
    (registry / "unexpected").mkdir()
    cli = tmp_path / "unused"
    cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid lifecycle registry entry"):
        lifecycle.reconcile(tmp_path, cli=cli)
