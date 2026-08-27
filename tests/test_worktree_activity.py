"""Live contracts for process-CWD facts that lsof cannot represent losslessly."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from escapement_worktree_activity import active_reason  # noqa: E402
from escapement_worktree_git import WorktreeError  # noqa: E402


def test_nonprintable_target_path_fails_closed_before_lsof_comparison(
    tmp_path: Path,
) -> None:
    target = tmp_path / "repo\nroot"
    target.mkdir()
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=target
    )
    try:
        with pytest.raises(WorktreeError, match="losslessly"):
            active_reason(target, tmp_path / "harness")
    finally:
        child.terminate()
        child.wait(timeout=5)
