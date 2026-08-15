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


def test_invalid_registry_entry_fails_the_tick(tmp_path):
    registry = tmp_path / "worktrees"
    registry.mkdir()
    (registry / "unexpected").mkdir()
    cli = tmp_path / "unused"
    cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid lifecycle registry entry"):
        lifecycle.reconcile(tmp_path, cli=cli)
