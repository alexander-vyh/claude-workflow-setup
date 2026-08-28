"""Tests for shared_tmp_artifact_gate.py — keep durable source out of shared temp.

Load-bearing control: the SAME temp root must stay ALLOWED for the cases the
convention exists to serve — fleet audit output (.csv/.json/.txt), per-session
scratch, and reads. The gate fires only at the intersection (executable source
AND a shared, non-session-scoped temp path), never on `/tmp` presence alone.

The positive control is the real command that stranded the nme.1 operation.
"""
import json
import pathlib
import subprocess
import sys

HOOKS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import shared_tmp_artifact_gate as g

REAL_INCIDENT = "/private/tmp/nme1-policy-operation/nme1_singleton_policy.py"


def _run(payload: dict) -> tuple[int, dict | None]:
    proc = subprocess.run(
        [sys.executable, "-B", str(HOOKS / "shared_tmp_artifact_gate.py")],
        input=json.dumps(payload), text=True, capture_output=True, check=False,
    )
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.returncode, None


def _bash(command: str, cwd: str = "/Users/x/GitHub/repo") -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "cwd": cwd}


# --- positive controls: the real failure ---------------------------------

def test_the_actual_incident_is_denied():
    """The write that started it: source into a shared, named temp directory."""
    _, out = _run(_bash(f"cat > {REAL_INCIDENT} <<'EOF'\nprint(1)\nEOF"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_moving_source_into_shared_temp_is_denied():
    """How the directory was actually created: mkdir && mv the loose script in."""
    cmd = ("mkdir -p /private/tmp/nme1-policy-operation && "
           "mv /private/tmp/nme1_singleton_policy.py "
           "/private/tmp/nme1-policy-operation/nme1_singleton_policy.py")
    assert g.command_offenders(cmd) == [REAL_INCIDENT]


def test_shared_temp_source_variants_are_caught():
    for cmd in (
        "echo x > /tmp/op/run.sh",
        "cp ./deploy.py /var/tmp/shared/deploy.py",
        "tee /private/tmp/ops/policy.rb",
        "python gen.py >> /tmp/ops/build.js",
    ):
        assert g.command_offenders(cmd), cmd


def test_denial_names_a_repair_inside_the_repo():
    code, out = _run(_bash(f"cp a.py {REAL_INCIDENT}", cwd="/Users/x/GitHub/repo"))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "/Users/x/GitHub/repo/nme1_singleton_policy.py" in reason
    assert "tmp-artifact-waiver" in reason


# --- negative controls: the convention this must NOT break ---------------

def test_fleet_audit_output_to_tmp_is_allowed():
    """crowdstrike-py AGENTS.md documents /tmp for audit results. Still legal."""
    for cmd in (
        "python audit.py > /tmp/ai_assistants_inventory.csv",
        "cp results.json /tmp/ai_assistants_summary.txt",
        "tee /tmp/fleet/report.json",
        "sha256sum x > /tmp/ai_assistants_inventory.csv.sha256",
    ):
        assert g.command_offenders(cmd) == [], cmd


def test_session_scoped_scratch_is_allowed():
    for path in (
        "/private/tmp/claude-502/-Users-x-repo/abc/scratchpad/probe.py",
        "/tmp/codex-run-1/helper.sh",
        "/tmp/tmp.aB9xQ2z/setup.py",
        "/private/tmp/escapement-codex-probe-9f2/gate.py",
        "/tmp/de322841-ff9c-4b09-a253-11d36f1fa83c/x.py",
    ):
        assert g.offending_path(path) is None, path


def test_reads_and_execution_are_allowed():
    for cmd in (
        f"cat {REAL_INCIDENT}",
        f"sed -n '1,240p' {REAL_INCIDENT}",
        f"python {REAL_INCIDENT} preflight",
        f"ruff check {REAL_INCIDENT}",
    ):
        assert g.command_offenders(cmd) == [], cmd


def test_repo_and_worktree_paths_are_allowed():
    for cmd in (
        "cat > /Users/x/GitHub/repo/.worktrees/nme-1/policy.py <<EOF\nx\nEOF",
        "cp a.py ./src/policy.py",
    ):
        assert g.command_offenders(cmd) == [], cmd


def test_dynamic_paths_fail_open():
    """A path the gate cannot resolve is not evidence of a violation."""
    for cmd in (
        "cp a.py $TMPDIR/policy.py",
        "cp a.py /tmp/$RUN/policy.py",
        "cp a.py /tmp/*/policy.py",
        "cp a.py ~/tmp/policy.py",
    ):
        assert g.command_offenders(cmd) == [], cmd


def test_unparseable_command_allows():
    assert g.command_offenders("cp 'unbalanced /tmp/x.py") == []


# --- waiver: value, not presence -----------------------------------------

def test_waiver_with_real_reason_allows():
    cmd = (f"cp a.py {REAL_INCIDENT}  "
           "# tmp-artifact-waiver: shared with the running fleet operator by design")
    assert _run(_bash(cmd))[1] is None


def test_empty_waiver_does_not_allow():
    assert g.has_waiver("cp a.py /tmp/x.py # tmp-artifact-waiver: x") is False
    assert g.has_waiver("cp a.py /tmp/x.py") is False


# --- apply_patch (Codex file-write channel) ------------------------------

def test_apply_patch_add_file_into_shared_temp_is_denied():
    payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
               "tool_input": {"input": f"*** Begin Patch\n*** Add File: {REAL_INCIDENT}\n+x\n*** End Patch"},
               "cwd": "/Users/x/GitHub/repo"}
    _, out = _run(payload)
    assert REAL_INCIDENT in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_apply_patch_into_repo_is_allowed():
    payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
               "tool_input": {"input": "*** Begin Patch\n*** Add File: src/policy.py\n+x\n*** End Patch"},
               "cwd": "/Users/x/GitHub/repo"}
    assert _run(payload)[1] is None


def test_write_tool_file_path_is_covered():
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
               "tool_input": {"file_path": REAL_INCIDENT, "content": "x"},
               "cwd": "/Users/x/GitHub/repo"}
    assert _run(payload)[1]["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_unknown_payload_shape_fails_open():
    assert _run({"hook_event_name": "PreToolUse", "tool_name": "Mystery",
                 "tool_input": {"blob": 42}})[1] is None
