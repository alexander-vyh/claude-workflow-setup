#!/usr/bin/env python3
"""Tests for schedule_wakeup_bridge.py — bead escapement-0wg.

Business invariant
------------------
When the agent calls the ScheduleWakeup tool, the harness Stop gate's documented
"path 2" (a registered wakeup releases Stop) must actually fire. Before this
bridge, ScheduleWakeup persisted inside the Claude Code runtime and never wrote
the {thread_dir}/scheduled.json that would_block_stop reads — so every wait-turn
got no_completion_or_resumption_proof. The bridge is a PostToolUse:ScheduleWakeup
hook that translates the tool call into a schema-conforming scheduled.json entry
in *that session's* thread dir.

Independent source of truth
---------------------------
- harness/schemas/scheduled.schema.json  (entry shape the gate consumes)
- the UNMODIFIED would_block_stop()       (must recognize the bridge's output)

Fragile implementations these tests REJECT
-------------------------------------------
- writing to threads/current/ regardless of session_id  -> the per-session gate
  for the ACTUAL session would still see nothing. The end-to-end test keys the
  gate by the same session_id and asserts allow, so a wrong-path impl fails.
- appending unboundedly  -> scheduled.json grows with stale ScheduleWakeup entries;
  dedup test rejects this.
- ignoring delaySeconds clamping  -> wake_at wouldn't match the real wakeup time.
- not pruning past entries  -> a fired/stale fallback keeps "releasing" the gate.

Run: python3 -m pytest harness/tests/test_schedule_wakeup_bridge.py -q
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import sys

import pytest
from jsonschema import Draft202012Validator, ValidationError

REPO = pathlib.Path(__file__).resolve().parents[2]
HARNESS_BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(HARNESS_BIN))

import schedule_wakeup_bridge as bridge  # noqa: E402
import wakeup_waker  # noqa: E402
from would_block_stop import would_block_stop, load_thread_state  # noqa: E402

SCHEMA = json.loads(
    (REPO / "harness" / "schemas" / "scheduled.schema.json").read_text()
)
_REQUIRED = SCHEMA["items"]["required"]

NOW = _dt.datetime(2026, 5, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _payload(
    session_id="sess-abc", delay=600, prompt="resume the loop", reason="waiting on CI"
):
    return {
        "session_id": session_id,
        "tool_name": "ScheduleWakeup",
        "tool_input": {"delaySeconds": delay, "prompt": prompt, "reason": reason},
        "tool_response": {},
    }


def _read(thread_dir: pathlib.Path):
    return json.loads((thread_dir / "scheduled.json").read_text())


# --- the core end-to-end oracle: ScheduleWakeup releases the Stop gate --------


def test_schedulewakeup_call_satisfies_stop_gate(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    # The gate compares wake_at against the real wall clock, so register relative
    # to actual now (not the fixed NOW used by the arithmetic-only tests).
    real_now = _dt.datetime.now(_dt.timezone.utc)
    written = bridge.parse_and_register(
        _payload(session_id="sess-XYZ"), now=real_now, harness_root=tmp_path
    )
    assert written is not None, (
        "a ScheduleWakeup call must write a scheduled.json entry"
    )

    # The entry must live in THIS session's thread dir (not threads/current).
    assert written == tmp_path / "threads" / "sess-XYZ" / "scheduled.json", (
        "entry must be keyed by session_id, or the per-session gate won't see it"
    )

    # The UNMODIFIED gate, reading that thread dir, must now allow Stop via path 2.
    state = load_thread_state(written.parent, recent_user_message=None)
    decision, reason = would_block_stop(state)
    assert (decision, reason) == ("allow", "wakeup_registered"), (
        f"a registered wakeup must release the gate; got {decision}/{reason}"
    )


def test_entry_conforms_to_schema(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    written = bridge.parse_and_register(_payload(), now=NOW, harness_root=tmp_path)
    entries = _read(written.parent)
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    for key in _REQUIRED:
        assert key in entry, f"schema requires '{key}'"
    assert entry["created_by"] == "ScheduleWakeup"
    assert entry["crash_count"] == 0
    assert entry["prompt"] == "resume the loop"
    # wake_at is future relative to NOW
    wa = _dt.datetime.fromisoformat(entry["wake_at"])
    assert wa > NOW


def test_generic_schedulewakeup_schema_does_not_require_execution_identity():
    """Compatibility control: ordinary external-event wakes remain schema-valid."""
    assert not {
        "parent_session_id",
        "watchdog_id",
        "execution_id",
        "attempt",
        "generation",
        "registered_at",
        "supervisor_installation_id",
        "supervisor_generation",
    } & set(SCHEMA["items"]["required"])


def test_wake_at_is_now_plus_clamped_delay(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    # below floor -> clamped up to 60
    w = bridge.parse_and_register(_payload(delay=5), now=NOW, harness_root=tmp_path)
    wa = _dt.datetime.fromisoformat(_read(w.parent)[0]["wake_at"])
    assert (wa - NOW).total_seconds() == 60
    # above ceiling -> clamped down to 3600
    w2 = bridge.parse_and_register(
        _payload(session_id="s2", delay=99999), now=NOW, harness_root=tmp_path
    )
    wa2 = _dt.datetime.fromisoformat(_read(w2.parent)[0]["wake_at"])
    assert (wa2 - NOW).total_seconds() == 3600


# --- negative controls --------------------------------------------------------


def test_non_schedulewakeup_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    p = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert bridge.parse_and_register(p, now=NOW, harness_root=tmp_path) is None
    assert not (tmp_path / "threads" / "s" / "scheduled.json").exists()


def test_missing_delay_is_noop_failopen(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    p = {
        "session_id": "s",
        "tool_name": "ScheduleWakeup",
        "tool_input": {"prompt": "x"},
    }
    # No delaySeconds -> cannot compute a real wake_at -> no-op (don't fabricate).
    assert bridge.parse_and_register(p, now=NOW, harness_root=tmp_path) is None


# --- part 2: stale / completed wakeups must not linger or replay --------------


def test_dedup_latest_schedulewakeup_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    bridge.parse_and_register(_payload(prompt="first"), now=NOW, harness_root=tmp_path)
    later = NOW + _dt.timedelta(seconds=30)
    w = bridge.parse_and_register(
        _payload(prompt="second"), now=later, harness_root=tmp_path
    )
    entries = _read(w.parent)
    sw = [e for e in entries if e["created_by"] == "ScheduleWakeup"]
    assert len(sw) == 1, (
        "repeated ScheduleWakeup calls must not accumulate stale entries"
    )
    assert sw[0]["prompt"] == "second"


def test_past_entries_pruned_on_write(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    thread_dir = tmp_path / "threads" / "sess-abc"
    thread_dir.mkdir(parents=True)
    past = (NOW - _dt.timedelta(hours=1)).isoformat()
    (thread_dir / "scheduled.json").write_text(
        json.dumps(
            [
                {
                    "wake_at": past,
                    "prompt": "stale",
                    "thread_id": "sess-abc",
                    "created_by": "adapter-fallback",
                    "crash_count": 0,
                }
            ]
        )
    )
    w = bridge.parse_and_register(_payload(), now=NOW, harness_root=tmp_path)
    entries = _read(w.parent)
    assert all(_dt.datetime.fromisoformat(e["wake_at"]) > NOW for e in entries), (
        "a past-dated (already-fired/stale) entry must be pruned, not kept as a live wakeup"
    )


def test_prune_thread_cancels_future_wakeups_on_completion(tmp_path):
    # When tracked work completes, its pending wakeup is cancelled so it cannot
    # replay a finished task list (part 2, harness side).
    thread_dir = tmp_path / "threads" / "sess-done"
    thread_dir.mkdir(parents=True)
    future = (NOW + _dt.timedelta(hours=2)).isoformat()
    (thread_dir / "scheduled.json").write_text(
        json.dumps(
            [
                {
                    "wake_at": future,
                    "prompt": "redo everything",
                    "thread_id": "sess-done",
                    "created_by": "ScheduleWakeup",
                    "crash_count": 0,
                }
            ]
        )
    )
    bridge.prune_thread(thread_dir)
    remaining = _read(thread_dir)
    state = load_thread_state(thread_dir, recent_user_message=None)
    decision, reason = would_block_stop(state)
    assert not any(e["created_by"] == "ScheduleWakeup" for e in remaining), (
        "completion must cancel the ScheduleWakeup wakeup so it can't replay"
    )
    assert reason != "wakeup_registered", (
        "a cancelled wakeup must not keep releasing the gate"
    )


def test_prune_preserves_other_creators(tmp_path):
    # Cancelling on completion must only drop the agent's own ScheduleWakeup
    # entries, not e.g. a supervisor-registered fallback for a different concern.
    thread_dir = tmp_path / "threads" / "sess-mixed"
    thread_dir.mkdir(parents=True)
    future = (NOW + _dt.timedelta(hours=2)).isoformat()
    (thread_dir / "scheduled.json").write_text(
        json.dumps(
            [
                {
                    "wake_at": future,
                    "prompt": "mine",
                    "thread_id": "sess-mixed",
                    "created_by": "ScheduleWakeup",
                    "crash_count": 0,
                },
                {
                    "wake_at": future,
                    "prompt": "supervisor",
                    "thread_id": "sess-mixed",
                    "created_by": "stop-barrier-supervisor",
                    "crash_count": 0,
                },
            ]
        )
    )
    bridge.prune_thread(thread_dir)
    remaining = _read(thread_dir)
    creators = {e["created_by"] for e in remaining}
    assert creators == {"stop-barrier-supervisor"}


# --- wiring regression: the SHIPPED template must wire the bridge -------------
# (Same lesson as test_stop_gate_wiring: a hook file on disk that the template
# never invokes is dead-on-arrival for everyone but the author.)


def test_shipped_template_wires_schedulewakeup_bridge():
    template = json.loads(
        (REPO / "plugins" / "escapement-claude" / "hooks" / "hooks.json").read_text()
    )
    post = template.get("hooks", {}).get("PostToolUse", [])
    groups = [g for g in post if g.get("matcher") == "ScheduleWakeup"]
    assert groups, (
        "plugin hooks.json has no PostToolUse matcher for ScheduleWakeup — "
        "distributees get a dead path-2 (the documented resumption escape never fires)"
    )
    cmds = [h.get("command", "") for g in groups for h in g.get("hooks", [])]
    assert any("schedule_wakeup_bridge.py" in c for c in cmds), (
        f"ScheduleWakeup matcher does not invoke the bridge; commands: {cmds}"
    )


# --- the real hook entrypoint (subprocess, stdin payload) ---------------------


def test_hook_main_via_stdin_writes_entry(tmp_path):
    import subprocess

    payload = {
        "session_id": "sess-cli",
        "tool_name": "ScheduleWakeup",
        "tool_input": {"delaySeconds": 600, "prompt": "resume via cli"},
    }
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(tmp_path)
    env.pop("HARNESS_THREAD_DIR", None)
    r = subprocess.run(
        ["python3", str(HARNESS_BIN / "schedule_wakeup_bridge.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
    )
    assert r.returncode == 0, f"hook must exit 0 (fail-open); stderr={r.stderr}"
    sched = tmp_path / "threads" / "sess-cli" / "scheduled.json"
    assert sched.exists(), "the real hook entrypoint must write scheduled.json"
    entry = json.loads(sched.read_text())[0]
    assert (
        entry["created_by"] == "ScheduleWakeup" and entry["prompt"] == "resume via cli"
    )


# --- responsiveness under contention -----------------------------------------
# Ported from the Task 5 production review when the delegated-execution ledger
# was removed. A PostToolUse hook that waits on another process's lock stalls the
# agent's turn, so the bridge must decline rather than block.


def test_bridge_returns_promptly_when_the_schedule_lock_is_held(tmp_path):
    """Fragile implementation this rejects: a blocking LOCK_EX in a hook."""
    import fcntl
    import subprocess
    import time

    thread_dir = tmp_path / "threads" / "sess-contended"
    thread_dir.mkdir(parents=True)
    lock_path = thread_dir / "scheduled.json.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.chmod(lock_path, 0o600)
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(tmp_path)
    env.pop("HARNESS_THREAD_DIR", None)

    with os.fdopen(lock_fd, "a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        started = time.monotonic()
        try:
            result = subprocess.run(
                ["python3", str(HARNESS_BIN / "schedule_wakeup_bridge.py")],
                input=json.dumps(_payload(session_id="sess-contended")),
                text=True,
                capture_output=True,
                env=env,
                timeout=5.0,
            )
        except subprocess.TimeoutExpired:
            pytest.fail("bridge stalled behind the schedule lock")
        elapsed = time.monotonic() - started

    assert result.returncode == 0, f"hook must fail open; stderr={result.stderr}"
    assert elapsed < 5.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
