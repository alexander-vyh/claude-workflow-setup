"""Unit tests for discovery-gate.py.

The hook fires as PreToolUse on Bash commands containing `bd create`. For
features and epics it requires a design doc at openspec/changes/{name}/design.md
with the three required sections. Bugs and chores always pass.

This suite focuses on the SINGLE-SIGNAL deny contract (the fxh.7 scope): a deny
is carried by ONE mechanism — the permissionDecision="deny" JSON document on
stdout AND exit code 0 (NOT exit 2). permissionDecision=deny *plus* exit 2 is a
contradictory double-block, which is exactly what every hard-deny hook in this
repo was converted away from.

Run from anywhere with:
  python -m pytest claude/hooks/tests/test_discovery_gate.py -v
"""

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_hooks_dir = Path(__file__).resolve().parents[1]

if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))


def _load_module():
    """Import discovery-gate.py by file path (the hyphen blocks `import`)."""
    import importlib.util

    path = _hooks_dir / "discovery-gate.py"
    spec = importlib.util.spec_from_file_location("discovery_gate_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COMPLETE_DESIGN = """# Design - my-feature

## Problem Statement
Users cannot reset their password without contacting support.

## Non-Goals
Social login is out of scope for this change.

## Riskiest Assumption
A self-service email flow covers 90% of cases.
"""


def _make_change(tmpdir, name="my-feature", design=None):
    """Create openspec/changes/{name}/ with optional design.md.

    Returns the project dir (the parent of openspec/) as a string.
    """
    change_dir = Path(tmpdir) / "openspec" / "changes" / name
    change_dir.mkdir(parents=True)
    if design is not None:
        (change_dir / "design.md").write_text(design)
    return str(tmpdir)


def _run_hook(mod, command, cwd="", hook_event="PreToolUse", tool_name="Bash",
              raw_stdin=None):
    """Run the hook's main() and return (exit_code, stdout, stderr).

    The hook may exit via sys.exit (deny/ask paths) or return normally (allow);
    both are normalized to an exit code here.
    """
    if raw_stdin is None:
        payload = {
            "hook_event_name": hook_event,
            "tool_name": tool_name,
            "tool_input": {"command": command},
            "cwd": cwd,
        }
        stdin_data = json.dumps(payload)
    else:
        stdin_data = raw_stdin

    out, err = io.StringIO(), io.StringIO()
    try:
        with patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", out), patch("sys.stderr", err):
            rc = mod.main()
        # main() returned normally — its return value is the exit code.
        return (rc if rc is not None else 0), out.getvalue(), err.getvalue()
    except SystemExit as exc:
        return exc.code, out.getvalue(), err.getvalue()


def _decision(stdout):
    """Extract permissionDecision from a hook's JSON stdout, or None.

    json.loads raises on two stacked documents, so this also rejects a doubled
    signal (two JSON docs on stdout) by construction.
    """
    if not stdout.strip():
        return None
    return json.loads(stdout)["hookSpecificOutput"]["permissionDecision"]


def assert_denied(code, stdout):
    """Assert the deny was honored EXACTLY ONCE via the canonical mechanism: a
    single permissionDecision="deny" JSON document on stdout AND exit code 0
    (NOT exit 2). permissionDecision=deny *plus* exit 2 is a contradictory
    double-block — asserting exit 0 rejects that shape.
    """
    assert code == 0, (
        "deny is carried by the stdout JSON decision, not exit 2 — "
        "permissionDecision=deny plus exit 2 is a contradictory double-block"
    )
    assert _decision(stdout) == "deny"


# ===========================================================================
# Negative / deny control — the fxh.7 single-signal contract
# ===========================================================================

class TestDenyContract:
    def test_feature_no_design_doc_denies_single_signal(self):
        """Negative control: a feature `bd create` with NO design doc must DENY
        via the single-signal contract (deny JSON + exit 0, never exit 2)."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            # openspec/changes/ exists but contains no design.md
            project = _make_change(tmp, design=None)
            code, out, err = _run_hook(
                mod, 'bd create "new thing" --type=feature', cwd=project)
        assert_denied(code, out)
        # explicit guard: the deny path must NOT use the exit-2 double-block
        assert code != 2, "residual exit 2 double-block on the deny path"

    def test_epic_no_design_doc_denies_single_signal(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_change(tmp, design=None)
            code, out, _ = _run_hook(
                mod, 'bd create "big thing" -t epic', cwd=project)
        assert_denied(code, out)
        assert code != 2


# ===========================================================================
# Positive controls — things that should pass through (not denied)
# ===========================================================================

class TestPassThrough:
    def test_feature_with_complete_design_allows(self):
        """Positive control: a feature with a complete design doc -> ALLOW
        (no deny JSON, exit 0)."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_change(tmp, design=COMPLETE_DESIGN)
            code, out, _ = _run_hook(
                mod, 'bd create "new thing" --type=feature', cwd=project)
        assert code == 0
        assert _decision(out) != "deny"
        assert out == ""

    def test_bug_always_allows(self):
        """Bugs pass through regardless of design docs."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_change(tmp, design=None)
            code, out, _ = _run_hook(
                mod, 'bd create "squash it" --type=bug', cwd=project)
        assert code == 0
        assert _decision(out) != "deny"
        assert out == ""

    def test_chore_always_allows(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_change(tmp, design=None)
            code, out, _ = _run_hook(
                mod, 'bd create "tidy up" -t chore', cwd=project)
        assert code == 0
        assert _decision(out) != "deny"
        assert out == ""

    def test_non_bd_create_command_allows(self):
        """A command that is not `bd create` is never gated."""
        mod = _load_module()
        code, out, _ = _run_hook(mod, "ls -la")
        assert code == 0
        assert out == ""

    def test_non_bash_tool_allows(self):
        mod = _load_module()
        code, out, _ = _run_hook(
            mod, 'bd create "x" --type=feature', tool_name="Write")
        assert code == 0
        assert out == ""

    def test_invalid_json_stdin_allows(self):
        """Malformed stdin fails open."""
        mod = _load_module()
        code, _, _ = _run_hook(mod, "", raw_stdin="not json at all")
        assert code == 0


# ===========================================================================
# Ask path is preserved (must not regress to deny or double-block)
# ===========================================================================

class TestAskPathPreserved:
    def test_standalone_task_asks(self):
        """A standalone task (no --parent) triggers an 'ask', exit 0."""
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_change(tmp, design=None)
            code, out, _ = _run_hook(
                mod, 'bd create "loose task" --type=task', cwd=project)
        assert code == 0
        assert _decision(out) == "ask"
