#!/usr/bin/env python3
"""Task-mode gate must not block on whole-repo backlog for an UNSCOPED session
(bead escapement-e9v.11).

WHY THIS EXISTS
---------------
A session enters task mode when task_mode_entry.py sees a bd claim. But a claim
like `bd ready --claim` has no `bd update <id>` to parse, so `_extract_task_id`
returns None and the writer stamped session_mode.json with
`task_id: null, parent_id: null`. `_check_task_mode_queue` then runs `bd ready`
with NO `--parent` scope = the ENTIRE repo backlog, so a session whose own work
is complete can never Stop — it blocks on unrelated beads forever (observed: a
finished session stuck behind tasks_remain_in_queue for whole-repo backlog).
This contradicts continuation-harness.md's own rule ("if bd ready shows tasks
outside the current session's scope, ignore them — they belong to a different
session").

TEST ORACLE BRIEF (rapid form — narrow scoping fix)
---------------------------------------------------
1. Business invariant: task-mode queue-drain gating applies ONLY to a session
   with a real scope (a claimed task_id or its molecule parent_id). A scopeless
   task-mode record must NOT gate Stop on whole-repo backlog — it falls through
   to the normal contract gate (which still blocks a red contract: teeth kept).
2. Negative control (the bug): mode==task but task_id AND parent_id both null
   -> task-mode gating must NOT be in effect.
3. Positive controls: a scoped session (parent_id OR task_id set) -> task-mode
   gating IS in effect (the queue-drain feature is preserved); a non-claim
   `bd ready --claim` writes NO scopeless record at the source.
4. Fragile impl rejected: "unscoped -> allow" inside _check_task_mode_queue
   (would bypass the contract gate and let a red session stop). The fix instead
   makes main() treat scopeless as non-task-mode -> contract gate still runs.

Run: python3 -m pytest harness/tests/test_task_mode_scope.py -q
"""

from __future__ import annotations

import json
import datetime as dt
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness" / "bin"))

import stop_hook  # noqa: E402
import execution_stop_adapter  # noqa: E402
import task_session_mode  # noqa: E402

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# _task_mode_in_effect — the scoping rule (pure)
# ---------------------------------------------------------------------------
def test_unscoped_task_mode_not_in_effect() -> None:
    """NEGATIVE CONTROL / the bug: both ids null -> task-mode gating off."""
    sm = {"mode": "task", "repo_cwd": "/r", "task_id": None, "parent_id": None}
    assert stop_hook._task_mode_in_effect(sm) is False


def test_parent_scoped_task_mode_in_effect() -> None:
    """POSITIVE CONTROL: a molecule-scoped session keeps queue-drain gating."""
    sm = {"mode": "task", "repo_cwd": "/r", "task_id": "x.1", "parent_id": "x"}
    assert stop_hook._task_mode_in_effect(sm) is True


def test_leaf_task_scoped_task_mode_in_effect() -> None:
    """POSITIVE CONTROL: a standalone leaf task (task_id only) is still scoped."""
    sm = {"mode": "task", "repo_cwd": "/r", "task_id": "x.1", "parent_id": None}
    assert stop_hook._task_mode_in_effect(sm) is True


def test_non_task_mode_not_in_effect() -> None:
    assert stop_hook._task_mode_in_effect({"mode": "task"}) is False  # no scope
    assert stop_hook._task_mode_in_effect({"mode": "conversational"}) is False
    assert stop_hook._task_mode_in_effect(None) is False
    assert stop_hook._task_mode_in_effect("garbage") is False


# ---------------------------------------------------------------------------
# task_mode_entry.py — root cause: do not stamp a scopeless record
# ---------------------------------------------------------------------------
def _run_entry(
    command: str,
    thread_dir: pathlib.Path,
    *,
    hook_event_name: str = "PostToolUse",
):
    payload = json.dumps({
        "hook_event_name": hook_event_name,
        "tool_name": "Bash", "session_id": "S",
        "tool_input": {"command": command},
        "tool_response": {
            "interrupted": False,
            "stderr": "",
            "stdout": "",
        },
    })
    env = dict(os.environ)
    env["HARNESS_THREAD_DIR"] = str(thread_dir)
    return subprocess.run(
        ["python3", str(REPO / "harness" / "bin" / "task_mode_entry.py")],
        input=payload, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_entry_skips_unscopeable_ready_claim(tmp_path) -> None:
    """ROOT CAUSE: `bd ready --claim` has no parseable task id, so no scope can be
    determined -> the writer must NOT create a scopeless task-mode record."""
    td = tmp_path / "thread"
    _run_entry("bd ready --claim", td)
    assert not (td / "session_mode.json").exists(), (
        "an unscopeable claim must not create a task-mode record (root cause of e9v.11)"
    )


def test_entry_skips_when_no_extractable_id(tmp_path) -> None:
    """A claim whose id cannot be extracted must also not create a scopeless record."""
    td = tmp_path / "thread"
    _run_entry("bd update --claim", td)  # no id token
    assert not (td / "session_mode.json").exists()


@pytest.mark.parametrize(
    "command",
    (
        "echo 'bd update fake --claim'",
        "printf '%s' 'bd update fake --claim'",
        "rg 'bd update fake --claim' README.md",
    ),
)
def test_quoted_claim_prose_does_not_create_task_mode(tmp_path, command) -> None:
    td = tmp_path / "thread"
    _run_entry(command, td)
    assert not (td / "session_mode.json").exists()


def test_failed_claim_result_does_not_create_task_mode(tmp_path) -> None:
    td = tmp_path / "thread"
    _run_entry("bd update fake --claim", td, hook_event_name="PostToolUseFailure")
    assert not (td / "session_mode.json").exists()


def test_chained_fail_open_claim_command_does_not_create_task_mode(tmp_path) -> None:
    td = tmp_path / "thread"
    _run_entry("bd update fake --claim || true", td)
    assert not (td / "session_mode.json").exists()


@pytest.mark.parametrize("line_control", ("\n", "\r", "\r\n"), ids=("lf", "cr", "crlf"))
@pytest.mark.parametrize("position", ("before", "between", "after"))
def test_line_separated_claim_command_does_not_create_task_mode(
    tmp_path, line_control, position
) -> None:
    td = tmp_path / "thread"
    command = {
        "before": f"true{line_control}bd update fake --claim",
        "between": f"bd{line_control}update fake --claim",
        "after": f"bd update fake --claim{line_control}true",
    }[position]
    _run_entry(command, td)
    assert not (td / "session_mode.json").exists()


def test_mode_persistence_failure_records_incident_and_blocks_completion(
    tmp_path,
) -> None:
    td = tmp_path / "thread"
    td.mkdir()
    (td / "session_mode.json").mkdir()

    result = _run_entry("bd update fake --claim", td)

    assert result.returncode == 0
    incident_path = td / "task_mode_incident.json"
    assert incident_path.is_file()
    incident = json.loads(incident_path.read_text(encoding="utf-8"))
    assert incident["parent_session_id"] == "S"
    assert incident["reason"] == "task_mode_persistence_failed"
    assert not incident_path.is_symlink()
    assert incident_path.stat().st_mode & 0o077 == 0
    assert task_session_mode.load_task_mode_incident(incident_path, "S") == incident
    mode, decision = execution_stop_adapter.decide_task_mode(
        "S",
        td,
        dt.datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        harness_root=tmp_path,
    )
    assert mode is None
    # Renamed from "delegated_execution_unresolved" when the delegated-execution
    # ledger was removed: the block is the same, the old name named a subsystem
    # that no longer exists.
    assert decision == ("block", "task_mode_incident_unresolved")


def test_parallel_first_claim_wins_atomically_across_processes(tmp_path) -> None:
    path = tmp_path / "thread" / "session_mode.json"
    process_count = 8
    code = """
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.environ["ESCAPEMENT_HARNESS_BIN"])
from task_session_mode import record_task_context_first_claim

path = pathlib.Path(os.environ["MODE_PATH"])
ready = path.parent / f"ready-{os.environ['WORKER_ID']}"
ready.parent.mkdir(parents=True, exist_ok=True)
ready.touch()
deadline = time.monotonic() + 10
while len(list(ready.parent.glob("ready-*"))) < int(os.environ["WORKER_COUNT"]):
    if time.monotonic() >= deadline:
        raise SystemExit("barrier timeout")
    time.sleep(0.005)
worker = os.environ["WORKER_ID"]
stored = record_task_context_first_claim(
    path,
    {
        "mode": "task",
        "repo_cwd": f"/repo/{worker}",
        "task_id": f"task-{worker}",
        "parent_id": f"root-{worker}",
        "entered_at": "2026-08-25T20:00:00Z",
        "session_id": os.environ["PARENT_SESSION"],
    },
)
print(json.dumps(stored, sort_keys=True))
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            env={
                **os.environ,
                "ESCAPEMENT_HARNESS_BIN": str(REPO / "harness" / "bin"),
                "MODE_PATH": str(path),
                "PARENT_SESSION": "S",
                "WORKER_ID": str(index),
                "WORKER_COUNT": str(process_count),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(process_count)
    ]
    results = []
    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        if process.returncode != 0:
            failures.append((process.returncode, stdout, stderr))
        else:
            results.append(json.loads(stdout))
    assert failures == []
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert all(item == persisted for item in results)


@pytest.mark.parametrize(
    "variant", ("malformed", "foreign-session", "symlink", "world-writable")
)
def test_untrusted_task_mode_incident_alone_is_unmanaged(tmp_path, variant) -> None:
    td = tmp_path / "thread"
    td.mkdir()
    incident_path = td / "task_mode_incident.json"
    value = {
        "version": 1,
        "parent_session_id": "S",
        "reason": "task_mode_persistence_failed",
        "recorded_at": "2026-08-25T20:00:00Z",
    }
    if variant == "malformed":
        incident_path.write_text("{malformed", encoding="utf-8")
        incident_path.chmod(0o600)
    elif variant == "foreign-session":
        value["parent_session_id"] = "foreign"
        incident_path.write_text(json.dumps(value), encoding="utf-8")
        incident_path.chmod(0o600)
    elif variant == "symlink":
        target = tmp_path / "foreign-incident.json"
        target.write_text(json.dumps(value), encoding="utf-8")
        target.chmod(0o600)
        incident_path.symlink_to(target)
    else:
        incident_path.write_text(json.dumps(value), encoding="utf-8")
        incident_path.chmod(0o666)

    assert task_session_mode.load_task_mode_incident(incident_path, "S") is None
    mode, decision = execution_stop_adapter.decide_task_mode(
        "S",
        td,
        dt.datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        harness_root=tmp_path,
    )
    assert mode is None
    assert decision is None


@pytest.mark.parametrize(
    "variant", ("malformed", "foreign-session", "symlink", "world-writable")
)
def test_untrusted_task_mode_incident_blocks_with_trusted_mode(
    tmp_path, variant
) -> None:
    td = tmp_path / "thread"
    td.mkdir()
    task_session_mode.record_task_context_first_claim(
        td / "session_mode.json",
        {
            "mode": "task",
            "session_id": "S",
            "repo_cwd": str(tmp_path),
            "task_id": "escapement-xncx",
            "parent_id": "escapement-xncx",
            "entered_at": "2026-08-25T20:00:00Z",
        },
    )
    incident_path = td / "task_mode_incident.json"
    value = {
        "version": 1,
        "parent_session_id": "S",
        "reason": "task_mode_persistence_failed",
        "recorded_at": "2026-08-25T20:00:00Z",
    }
    if variant == "malformed":
        incident_path.write_text("{malformed", encoding="utf-8")
        incident_path.chmod(0o600)
    elif variant == "foreign-session":
        value["parent_session_id"] = "foreign"
        incident_path.write_text(json.dumps(value), encoding="utf-8")
        incident_path.chmod(0o600)
    elif variant == "symlink":
        target = tmp_path / "foreign-incident.json"
        target.write_text(json.dumps(value), encoding="utf-8")
        target.chmod(0o600)
        incident_path.symlink_to(target)
    else:
        incident_path.write_text(json.dumps(value), encoding="utf-8")
        incident_path.chmod(0o666)

    mode, decision = execution_stop_adapter.decide_task_mode(
        "S",
        td,
        dt.datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        harness_root=tmp_path,
    )

    assert mode is not None
    # Renamed from "delegated_execution_unresolved" when the delegated-execution
    # ledger was removed: the block is the same, the old name named a subsystem
    # that no longer exists.
    assert decision == ("block", "task_mode_incident_unresolved")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
