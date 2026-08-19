"""Codex-specific behavioral tests for mol_status_check.py.

Loads the hook from the repo path (no ~/.claude/hooks/ dependency) so these run
in Codex environments. The mol-status.sh boundary is pointed at a real tmp script
(hermetic), exercising the actual SessionStart emit path.

The hook fires on SessionStart: in a beads project, it runs ~/.beads/mol-status.sh
and emits its output as a systemMessage. It fails open (no output) when the cwd is
not a beads project or the script is absent.

Positive control: beads project + script with output -> systemMessage emitted.
Negative control: non-beads cwd -> no output (fail-open).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HOOK_PATH = Path(__file__).resolve().parents[1] / "mol_status_check.py"
if not _HOOK_PATH.exists():
    pytest.fail(f"mol_status_check.py not found at {_HOOK_PATH}")

_spec = importlib.util.spec_from_file_location("mol_status_check", _HOOK_PATH)
gate = importlib.util.module_from_spec(_spec)
sys.modules["mol_status_check"] = gate
_spec.loader.exec_module(gate)


def _run_main(cwd: str, script: Path | None) -> tuple[int, dict | None]:
    payload = {"hook_event_name": "SessionStart", "cwd": cwd}
    captured = io.StringIO()
    ctx = patch.object(gate, "_MOL_STATUS_SCRIPT", script) if script is not None else _noop()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        with ctx:
            with patch("sys.stdout", captured):
                try:
                    code = gate.main()
                except SystemExit as exc:
                    code = exc.code or 0
    out = captured.getvalue().strip()
    return code, json.loads(out) if out else None


class _noop:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_codex_mol_status_emits_systemmessage_in_beads_project(tmp_path):
    """Positive control: a beads project with a producing mol-status.sh emits its
    output as a systemMessage. Without this, a hook that never emits would pass
    only the negative control."""
    (tmp_path / ".beads").mkdir()
    script = tmp_path / "mol-status.sh"
    script.write_text("#!/usr/bin/env bash\necho 'MOL: dark-mode in Build phase'\n")
    script.chmod(0o755)

    code, output = _run_main(str(tmp_path), script)

    assert code == 0
    assert output is not None, "a producing mol-status.sh in a beads project must emit"
    assert "MOL: dark-mode in Build phase" in output["systemMessage"]


def test_codex_mol_status_silent_when_not_beads_project(tmp_path):
    """Negative control: not a beads project -> no output (fail-open)."""
    code, output = _run_main(str(tmp_path), tmp_path / "mol-status.sh")

    assert code == 0
    assert output is None, "non-beads cwd must produce no output"
