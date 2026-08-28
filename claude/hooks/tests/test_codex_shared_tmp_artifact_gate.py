"""Codex-specific behavioral tests for shared_tmp_artifact_gate.py.

These drive the gate through the REAL Codex entrypoint —
`codex_pretool_dispatch.py`, the batched PreToolUse dispatcher that the rendered
Codex plugin manifest actually invokes — rather than calling the gate directly.
That is the difference between "the gate works" and "the gate is wired", and the
wiring is what the nme.1 incident needed.

Positive control: the real command that stranded a policy script in a shared
temp directory -> deny, through the dispatcher.
Negative control: the documented fleet-audit `/tmp` output convention and
per-session scratch -> allow, through the same dispatcher.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DISPATCH = REPO_ROOT / "claude" / "hooks" / "codex_pretool_dispatch.py"
GATE = "claude/hooks/shared_tmp_artifact_gate.py"

REAL_INCIDENT = "/private/tmp/nme1-policy-operation/nme1_singleton_policy.py"


def _dispatch(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "-B", str(DISPATCH), "--gate", GATE, "--gate-timeout", "5"],
        input=json.dumps(payload), text=True, capture_output=True,
        cwd=str(REPO_ROOT), check=False,
    )
    assert proc.returncode == 0, f"dispatcher crashed: {proc.stderr}"
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def _decision(out: dict) -> str | None:
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")


def _bash(command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "cwd": "/Users/x/GitHub/repo"}


def test_codex_dispatcher_denies_source_into_shared_temp():
    out = _dispatch(_bash(f"cat > {REAL_INCIDENT} <<'EOF'\nprint(1)\nEOF"))
    assert _decision(out) == "deny"
    assert "nme1_singleton_policy.py" in json.dumps(out)


def test_codex_dispatcher_denies_move_into_shared_temp():
    out = _dispatch(_bash(
        "mkdir -p /private/tmp/nme1-policy-operation && "
        f"mv /private/tmp/nme1_singleton_policy.py {REAL_INCIDENT}"))
    assert _decision(out) == "deny"


def test_codex_dispatcher_allows_fleet_audit_output():
    """The convention the repo documents must survive the gate."""
    out = _dispatch(_bash("python audit.py > /tmp/ai_assistants_inventory.csv"))
    assert _decision(out) != "deny"


def test_codex_dispatcher_allows_session_scoped_scratch():
    out = _dispatch(_bash("cp a.py /tmp/tmp.aB9xQ2z/setup.py"))
    assert _decision(out) != "deny"


def test_codex_dispatcher_allows_reading_the_shared_artifact():
    out = _dispatch(_bash(f"sed -n '1,240p' {REAL_INCIDENT}"))
    assert _decision(out) != "deny"


def test_codex_dispatcher_allows_dynamic_path():
    out = _dispatch(_bash("cp a.py $TMPDIR/policy.py"))
    assert _decision(out) != "deny"


def test_codex_dispatcher_honours_waiver_with_reason():
    out = _dispatch(_bash(
        f"cp a.py {REAL_INCIDENT}  "
        "# tmp-artifact-waiver: operator-shared fleet transaction, reviewed live"))
    assert _decision(out) != "deny"


def test_codex_apply_patch_add_file_is_denied():
    """Codex writes files as tool_name=apply_patch (proven from its runtime log)."""
    out = _dispatch({
        "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
        "tool_input": {"input": f"*** Begin Patch\n*** Add File: {REAL_INCIDENT}\n+x\n*** End Patch"},
        "cwd": "/Users/x/GitHub/repo"})
    assert _decision(out) == "deny"


def test_codex_apply_patch_into_repo_is_allowed():
    out = _dispatch({
        "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
        "tool_input": {"input": "*** Begin Patch\n*** Add File: src/policy.py\n+x\n*** End Patch"},
        "cwd": "/Users/x/GitHub/repo"})
    assert _decision(out) != "deny"
