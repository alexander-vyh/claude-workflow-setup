#!/usr/bin/env python3
# file-complexity-waiver: pre-existing aggregate harness gate test (>500 lines); splitting it is separate test-suite work. This change adds one e9v.11 scopeless-task-mode negative control.
"""
Sanity tests for would_block_stop.

Governed by .agent/runtime/test-oracle-brief.md. Covers the canonical decision
scenarios plus the explicit negative and positive controls named there. Not
the full 57-stall regression test (that's v0.1) — this is enough to ship v0
with confidence the gate behaves correctly on the explicitly-enumerated
scenarios and rejects the named fragile implementation (stale-last_run reuse).

Run: python3 harness/tests/test_gate.py
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import sys

HARNESS_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS_ROOT / "bin"))

from would_block_stop import would_block_stop  # noqa: E402


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _past_iso(seconds_ago: int) -> str:
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds_ago)).isoformat()


def _future_iso(seconds_ahead: int) -> str:
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=seconds_ahead)).isoformat()


def _contract(*, exit_code=None, ts=None, expected_exit=0):
    c = {
        "goal": "x",
        "verification_command": "pytest -q",
        "expected_exit": expected_exit,
        "source": "agent-declared",
        "thread_id": "t",
        "created_at": _now_iso(),
        "last_run": None,
    }
    if exit_code is not None:
        c["last_run"] = {"exit_code": exit_code, "timestamp": ts or _now_iso()}
    return c


def _cases() -> list[dict]:
    """Built at CALL time, never at import time.

    `_now_iso()` / `_future_iso()` freeze ABSOLUTE timestamps. As a module-level
    constant these were evaluated during pytest collection — before any test ran.
    A slow suite (>60s to reach this test) pushed the "future" wakeups into the
    past, so the gate correctly reported no pending wakeup and the case degraded
    wakeup_registered -> conversational. Likewise a "fresh" verification goes
    stale once the suite exceeds the 5-minute recency window.

    Symptom: `test_gate_decision_cases` passed in isolation (fast) and failed in
    the full suite (slow) — a time-bomb fixture, not a logic regression.
    """
    return [
    # Positive control #1 — happy path must not be broken.
    {
        "name": "verification_passed (fresh)",
        "state": {"contract": _contract(exit_code=0, ts=_now_iso())},
        "expect": ("allow", "verification_passed"),
    },
    # Negative control #1 — the named fragile implementation must fail here.
    {
        "name": "verification_passed (stale last_run beyond window)",
        "state": {"contract": _contract(exit_code=0, ts=_past_iso(600))},
        "expect": ("block", "no_completion_or_resumption_proof"),
    },
    # Invariant: exit-code mismatch is not a pass even if recent.
    {
        "name": "verification_failed (exit mismatch)",
        "state": {"contract": _contract(exit_code=1, ts=_now_iso())},
        "expect": ("block", "no_completion_or_resumption_proof"),
    },
    # Positive control #2 — wakeup-without-contract must allow.
    {
        "name": "wakeup_registered (future)",
        "state": {
            "contract": None,
            "scheduled": [
                {
                    "wake_at": _future_iso(120),
                    "prompt": "resume work",
                    "thread_id": "t",
                    "created_by": "ScheduleWakeup",
                    "crash_count": 0,
                }
            ],
        },
        "expect": ("allow", "wakeup_registered"),
    },
    # Invariant: past wakeups don't count.
    {
        "name": "wakeup_past (does not count)",
        "state": {
            "contract": None,
            "scheduled": [
                {
                    "wake_at": _past_iso(120),
                    "prompt": "old",
                    "thread_id": "t",
                    "created_by": "ScheduleWakeup",
                    "crash_count": 0,
                }
            ],
        },
        "expect": ("allow", "conversational"),
    },
    # User release variants.
    {
        "name": "user_released ('stop')",
        "state": {"contract": None, "recent_user_message": "stop"},
        "expect": ("allow", "user_released"),
    },
    {
        "name": "user_released ('end here.')",
        "state": {"contract": None, "recent_user_message": "end here."},
        "expect": ("allow", "user_released"),
    },
    {
        "name": "non-release message does not release",
        "state": {"contract": None, "recent_user_message": "what's next?"},
        "expect": ("allow", "conversational"),
    },
    # Empty state = no committed task in flight = conversational → allow.
    # (The reason 'conversational', not 'verification_passed', proves it did not
    # sneak through as verified — the control survives in the reason field.)
    {
        "name": "empty state is conversational → allow",
        "state": {},
        "expect": ("allow", "conversational"),
    },
    # Invariant: contract present but no proof at all.
    {
        "name": "contract present but no proof",
        "state": {"contract": _contract()},
        "expect": ("block", "no_completion_or_resumption_proof"),
    },
    # user_released takes priority over an unverified contract (B1 oracle).
    {
        "name": "user_released with unverified contract present",
        "state": {"contract": _contract(), "recent_user_message": "stop"},
        "expect": ("allow", "user_released"),
    },
    # Invariant: wakeup outranks absent contract.
    {
        "name": "wakeup wins over absent contract",
        "state": {
            "scheduled": [
                {
                    "wake_at": _future_iso(60),
                    "prompt": "resume",
                    "thread_id": "t",
                    "created_by": "x",
                    "crash_count": 0,
                }
            ],
        },
        "expect": ("allow", "wakeup_registered"),
    },
    # A contract that is PRESENT but malformed (non-dict) fails SAFE → block.
    # (load_thread_state surfaces a corrupt contract.json as a non-dict marker, so
    # a corrupt contract never reads as "no contract → conversational allow".)
    {
        "name": "malformed/present contract fails safe → block",
        "state": {"contract": "this is not a dict"},
        "expect": ("block", "no_completion_or_resumption_proof"),
    },
    # contract genuinely absent (None) + malformed scheduled → conversational allow.
    {
        "name": "no contract + malformed scheduled → conversational allow",
        "state": {"contract": None, "scheduled": "not a list"},
        "expect": ("allow", "conversational"),
    },
    # Custom expected_exit honored.
    {
        "name": "custom expected_exit honored",
        "state": {"contract": _contract(exit_code=42, ts=_now_iso(), expected_exit=42)},
        "expect": ("allow", "verification_passed"),
    },
    {
        "name": "custom expected_exit rejects wrong code",
        "state": {"contract": _contract(exit_code=0, ts=_now_iso(), expected_exit=42)},
        "expect": ("block", "no_completion_or_resumption_proof"),
    },
    ]


def run() -> int:
    passed = 0
    failed = 0
    for case in _cases():
        got = would_block_stop(case["state"])
        ok = got == case["expect"]
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"{status}: {case['name']}  expected={case['expect']!r}  got={got!r}")
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# Session-isolation tests (v0.1) — governed by the Session-isolation invariant
# in .agent/runtime/test-oracle-brief.md. These verify two concurrent sessions
# get distinct thread dirs and cannot clobber each other's contracts.
# ---------------------------------------------------------------------------

import json  # noqa: E402
import os as _os  # noqa: E402
import tempfile as _tempfile  # noqa: E402
from would_block_stop import (  # noqa: E402
    thread_dir_for_session,
    sanitize_session_id,
    load_thread_state,
)


def _assert(cond, name, results):
    results.append((cond, name))
    print(f"{'PASS' if cond else 'FAIL'}: {name}")


def run_isolation() -> int:
    results = []
    root = pathlib.Path(_tempfile.mkdtemp(prefix="harness-iso-"))

    # Ensure no override leaks in from the environment for the resolution tests.
    saved_override = _os.environ.pop("HARNESS_THREAD_DIR", None)
    try:
        # Resolution invariant: distinct sessions -> distinct dirs (the bug fix).
        a = thread_dir_for_session("sessA-1111", root)
        b = thread_dir_for_session("sessB-2222", root)
        _assert(a != b, "distinct session_ids resolve to distinct dirs", results)
        _assert(a == root / "threads" / "sessA-1111", "session dir is threads/{id}", results)

        # Determinism: same inputs -> same path.
        _assert(thread_dir_for_session("sessA-1111", root) == a, "resolution is deterministic", results)

        # Fallback: no session id -> current.
        _assert(
            thread_dir_for_session(None, root) == root / "threads" / "current",
            "no session_id falls back to threads/current",
            results,
        )
        _assert(
            thread_dir_for_session("", root) == root / "threads" / "current",
            "empty session_id falls back to threads/current",
            results,
        )

        # Sanitization: path traversal cannot escape threads/.
        evil = thread_dir_for_session("../../etc/passwd", root)
        _assert(
            str(evil.resolve()).startswith(str((root / "threads").resolve())),
            "malicious session_id cannot escape threads/",
            results,
        )
        _assert(sanitize_session_id("a/b/../c") == "abc", "sanitizer strips path metachars", results)
        _assert(sanitize_session_id("x" * 500) == "x" * 128, "sanitizer caps length at 128", results)

        # Override wins (for tests / special cases).
        _os.environ["HARNESS_THREAD_DIR"] = str(root / "explicit")
        _assert(
            thread_dir_for_session("ignored", root) == root / "explicit",
            "HARNESS_THREAD_DIR override wins over session_id",
            results,
        )
        _os.environ.pop("HARNESS_THREAD_DIR", None)

        # Negative control: session A writes a contract, session B writes its own;
        # A's contract is byte-identical afterward (the exact reported bug).
        dir_a = thread_dir_for_session("sessA-1111", root)
        dir_b = thread_dir_for_session("sessB-2222", root)
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)
        contract_a = {"goal": "A's goal", "verification_command": "pytest -q", "expected_exit": 0,
                      "source": "agent-declared", "thread_id": "sessA-1111", "created_at": _now_iso(), "last_run": None}
        (dir_a / "contract.json").write_text(json.dumps(contract_a))
        a_bytes_before = (dir_a / "contract.json").read_bytes()
        contract_b = {"goal": "B's goal", "verification_command": "false", "expected_exit": 0,
                      "source": "agent-declared", "thread_id": "sessB-2222", "created_at": _now_iso(), "last_run": None}
        (dir_b / "contract.json").write_text(json.dumps(contract_b))
        a_bytes_after = (dir_a / "contract.json").read_bytes()
        _assert(a_bytes_before == a_bytes_after, "session B writing its contract does NOT clobber session A's", results)

        # Positive control: A loads its own state and sees A's goal, not B's.
        state_a = load_thread_state(dir_a)
        _assert(
            state_a["contract"]["goal"] == "A's goal",
            "session A loads its own contract, not B's",
            results,
        )
    finally:
        if saved_override is not None:
            _os.environ["HARNESS_THREAD_DIR"] = saved_override
        else:
            _os.environ.pop("HARNESS_THREAD_DIR", None)
        import shutil as _shutil
        _shutil.rmtree(root, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\n[isolation] {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


def run_portability() -> int:
    """Portability invariant: no harness script may hardcode the author's repo
    path. That hardcoding is exactly what made the harness non-installable —
    it only worked at ~/GitHub/escapement. Static check over source.
    """
    results = []
    bindir = HARNESS_ROOT / "bin"
    offenders = []
    for f in sorted(bindir.iterdir()):
        if f.is_dir():
            continue
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if "escapement/harness" in text or "GitHub/escapement" in text:
            offenders.append(f.name)
    _assert(not offenders, f"no harness script hardcodes the repo path (offenders: {offenders})", results)

    # Default state root resolves to the standard per-user location, not a repo.
    from would_block_stop import DEFAULT_HARNESS_ROOT  # noqa: E402
    s = str(DEFAULT_HARNESS_ROOT)
    _assert("GitHub" not in s and "escapement" not in s,
            "default state root is not inside the repo / GitHub tree", results)

    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\n[portability] {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


import subprocess as _subprocess  # noqa: E402


def run_stop_hook() -> int:
    """Integration tests for stop_hook.py — B1 (transcript reading) and B2 (no-contract allow).

    These call the hook as a subprocess, controlling HARNESS_THREAD_DIR, so
    they test the full adapter path rather than would_block_stop in isolation.
    """
    results = []
    bin_path = HARNESS_ROOT / "bin" / "stop_hook.py"

    def call_hook(payload: dict, thread_dir: pathlib.Path) -> "dict | None":
        env = {**_os.environ, "HARNESS_THREAD_DIR": str(thread_dir)}
        r = _subprocess.run(
            [sys.executable, str(bin_path)],
            input=json.dumps(payload),
            capture_output=True, text=True, env=env,
        )
        if r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                return {"raw": r.stdout}
        return None  # allow: hook exited 0 with no output

    import shutil as _shutil
    import tempfile as _tf
    tmp = pathlib.Path(_tf.mkdtemp(prefix="harness-hook-"))
    try:
        # No contract.json AND no claimed work = conversational → ALLOW (no magic
        # word needed). The harness keeps its teeth on the committed-task path: a
        # DECLARED-but-unverified contract still blocks (test_wired_gate_blocks_
        # unverified_stop), ready bd work still blocks, and validate_no_shirking
        # still blocks "edited code but didn't verify".
        td = tmp / "no-contract"
        td.mkdir(parents=True)
        out = call_hook({"session_id": "x", "transcript_path": ""}, td)
        _assert(
            out is None,
            "no contract.json + no claimed work → allow (conversational)",
            results,
        )

        # B2 conversational-release: no contract.json BUT user said 'stop' in transcript → allow.
        # Preserves the conversational-session escape via the documented user_released path
        # rather than via the unspec'd contract-absent carve-out.
        td = tmp / "no-contract-user-release"
        td.mkdir(parents=True)
        transcript_release = tmp / "transcript-no-contract-stop.jsonl"
        transcript_release.write_text(
            json.dumps({"message": {"role": "user", "content": [{"type": "text", "text": "stop"}]}}) + "\n"
        )
        out = call_hook(
            {"session_id": "x", "transcript_path": str(transcript_release)},
            td,
        )
        _assert(
            out is None,
            "B2 conversational-release: no contract.json + user 'stop' → allow (user_released)",
            results,
        )

        # B2 negative: malformed contract.json → block (fail safe, not silently allow).
        td = tmp / "malformed"
        td.mkdir(parents=True)
        (td / "contract.json").write_text("not valid json {{{")
        out = call_hook({"session_id": "x", "transcript_path": ""}, td)
        _assert(
            out is not None and out.get("decision") == "block",
            "B2 negative: malformed contract.json → block (fail safe)",
            results,
        )

        # B1: user says 'stop' in transcript → allow even with unverified contract.
        td = tmp / "b1-user-release"
        td.mkdir(parents=True)
        contract = {
            "goal": "test", "verification_command": "false", "expected_exit": 0,
            "source": "agent-declared", "thread_id": "x",
            "created_at": _now_iso(), "last_run": None,
        }
        (td / "contract.json").write_text(json.dumps(contract))
        transcript = tmp / "transcript-stop.jsonl"
        transcript.write_text(
            json.dumps({"message": {"role": "user", "content": [{"type": "text", "text": "stop"}]}}) + "\n"
        )
        out = call_hook({"session_id": "x", "transcript_path": str(transcript)}, td)
        _assert(out is None, "B1: transcript 'stop' with unverified contract → allow (user_released)", results)

        # B1 negative: non-release transcript message + unverified contract → block.
        td = tmp / "b1-non-release"
        td.mkdir(parents=True)
        (td / "contract.json").write_text(json.dumps(contract))
        transcript2 = tmp / "transcript-question.jsonl"
        transcript2.write_text(
            json.dumps({"message": {"role": "user", "content": [{"type": "text", "text": "what's the status?"}]}}) + "\n"
        )
        out = call_hook({"session_id": "x", "transcript_path": str(transcript2)}, td)
        _assert(
            out is not None and out.get("decision") == "block",
            "B1 negative: non-release message + unverified contract → block",
            results,
        )
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\n[stop_hook] {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


def run_task_mode() -> int:
    """Integration tests for session-mode (task mode entry + queue-drain gate).

    Uses fake 'bd' scripts in a temp bin dir to control bd ready / bd list output
    without needing a real beads database.
    """
    results = []
    bin_path = HARNESS_ROOT / "bin" / "stop_hook.py"
    entry_path = HARNESS_ROOT / "bin" / "task_mode_entry.py"

    import shutil as _shutil
    import tempfile as _tf

    def call_hook(payload: dict, thread_dir: pathlib.Path, env_extra=None) -> "dict | None":
        env = {**_os.environ, "HARNESS_THREAD_DIR": str(thread_dir), **(env_extra or {})}
        r = _subprocess.run(
            [sys.executable, str(bin_path)],
            input=json.dumps(payload),
            capture_output=True, text=True, env=env,
        )
        if r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                return {"raw": r.stdout}
        return None

    def call_entry(payload: dict, thread_dir: pathlib.Path) -> int:
        env = {**_os.environ, "HARNESS_THREAD_DIR": str(thread_dir)}
        r = _subprocess.run(
            [sys.executable, str(entry_path)],
            input=json.dumps(payload),
            capture_output=True, text=True, env=env,
        )
        return r.returncode

    def make_fake_bd(tmp: pathlib.Path, ready_items: int, list_items: int) -> pathlib.Path:
        """Write a fake 'bd' script returning JSON arrays of controlled length."""
        bin_dir = tmp / "fakebin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        script = bin_dir / "bd"
        ready_json = json.dumps([{"id": f"t-{i}"} for i in range(ready_items)])
        list_json = json.dumps([{"id": f"t-{i}"} for i in range(list_items)])
        script.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "show" ]; then printf \'[{"id":"%s","status":"closed"}]\\n\' "$2"; exit 0; fi\n'
            f'if echo "$*" | grep -q "^ready"; then echo \'{ready_json}\'; exit 0; fi\n'
            f'if echo "$*" | grep -q "^list"; then echo \'{list_json}\'; exit 0; fi\n'
            "exit 0\n"
        )
        script.chmod(0o755)
        return bin_dir

    tmp = pathlib.Path(_tf.mkdtemp(prefix="harness-task-mode-"))
    try:
        # --- Pre-tool hook (task_mode_entry.py) ---

        # Claim command → writes session_mode.json.
        td = tmp / "entry-claim"
        td.mkdir(parents=True)
        rc = call_entry(
            {"session_id": "x", "tool_name": "Bash",
             "tool_input": {"command": "bd update cake-123 --claim"}},
            td,
        )
        mode = json.loads((td / "session_mode.json").read_text()) if (td / "session_mode.json").exists() else None
        _assert(rc == 0 and mode is not None and mode.get("mode") == "task",
                "entry: claim command writes session_mode.json with mode=task", results)

        # Non-claim bd command → no session_mode.json.
        td = tmp / "entry-no-claim"
        td.mkdir(parents=True)
        call_entry(
            {"session_id": "x", "tool_name": "Bash",
             "tool_input": {"command": "bd list"}},
            td,
        )
        _assert(not (td / "session_mode.json").exists(),
                "entry: non-claim command does not write session_mode.json", results)

        # Non-Bash tool → no session_mode.json.
        td = tmp / "entry-non-bash"
        td.mkdir(parents=True)
        call_entry(
            {"session_id": "x", "tool_name": "Read",
             "tool_input": {"file_path": "/tmp/x"}},
            td,
        )
        _assert(not (td / "session_mode.json").exists(),
                "entry: non-Bash tool does not write session_mode.json", results)

        # First-claim-wins: second claim does not overwrite.
        td = tmp / "entry-first-wins"
        td.mkdir(parents=True)
        call_entry({"session_id": "x", "tool_name": "Bash",
                    "tool_input": {"command": "bd update cake-1 --claim"}}, td)
        first_ctime = (td / "session_mode.json").read_text()
        call_entry({"session_id": "x", "tool_name": "Bash",
                    "tool_input": {"command": "bd update cake-2 --claim"}}, td)
        second_ctime = (td / "session_mode.json").read_text()
        _assert(first_ctime == second_ctime,
                "entry: first-claim-wins — second claim does not overwrite session_mode.json", results)

        # --- Stop hook task mode queue-drain gate ---

        # Task mode (SCOPED) + ready items → block (tasks remain).
        # e9v.11: mode_rec carries a parent_id so it is a real scoped task-mode
        # session. A scopeless record (both ids null) is exercised separately below
        # and must NOT block on the whole-repo backlog.
        td = tmp / "queue-has-items"
        td.mkdir(parents=True)
        fake_beads = td / ".beads"
        fake_beads.mkdir()
        mode_rec = {"mode": "task", "repo_cwd": str(td), "parent_id": "cake-epic",
                    "entered_at": _now_iso(), "session_id": "x"}
        (td / "session_mode.json").write_text(json.dumps(mode_rec))
        fakebin = make_fake_bd(tmp / "bin1", ready_items=1, list_items=1)
        out = call_hook({"session_id": "x", "transcript_path": ""},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(out is not None and out.get("decision") == "block",
                "task mode: scoped + bd ready non-empty → block (tasks remain)", results)

        # e9v.11 NEGATIVE CONTROL: a SCOPELESS task-mode record (parent_id AND
        # task_id both null) must NOT block on the whole-repo backlog — it falls
        # through to the contract gate (no contract here → conversational allow).
        # Without the fix this blocked a finished session on unrelated beads forever.
        td_us = tmp / "queue-unscoped"
        td_us.mkdir(parents=True)
        (td_us / ".beads").mkdir()
        (td_us / "session_mode.json").write_text(json.dumps(
            {"mode": "task", "repo_cwd": str(td_us), "parent_id": None,
             "entered_at": _now_iso(), "session_id": "x"}))
        fakebin_us = make_fake_bd(tmp / "bin1u", ready_items=5, list_items=5)
        out_us = call_hook({"session_id": "x", "transcript_path": ""},
                           td_us, {"PATH": f"{fakebin_us}:{_os.environ.get('PATH', '')}"})
        _assert(out_us is None,
                "task mode: scopeless record + whole-repo ready → allow (e9v.11, not others' backlog)", results)

        # DEGRADED-PATH case (NOT the R2 invariant — see below). This `make_fake_bd`
        # implements only `ready` and `list`; the `blocked` subcommand falls through
        # to `exit 0` with empty stdout, which the production run_bd maps to [] for
        # back-compat (old bd lacking the `blocked` subcommand). So this exercises:
        # "task mode + a bd that cannot answer `blocked` + empty ready → allow
        # (degraded, back-compat path)". It is NOT evidence that blocked beads don't
        # gate stopping — under R2 a bd that DOES implement `blocked` and returns a
        # scoped blocked bead → BLOCK. That R2 invariant is exercised separately in
        # run_wakeup_blocker_wiring() ("R2 (F4 sibling)"), with a fake bd that
        # implements the `blocked` subcommand.
        td = tmp / "queue-empty-but-open"
        td.mkdir(parents=True)
        (td / ".beads").mkdir()
        (td / "session_mode.json").write_text(json.dumps({**mode_rec, "repo_cwd": str(td)}))
        fakebin = make_fake_bd(tmp / "bin2", ready_items=0, list_items=1)
        out = call_hook({"session_id": "x", "transcript_path": ""},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(
            out is None,
            "task mode: empty ready + bd lacking `blocked` subcommand → allow (back-compat degraded path)",
            results,
        )

        # Task mode + empty ready + no open tasks → allow (queue drained).
        td = tmp / "queue-drained"
        td.mkdir(parents=True)
        (td / ".beads").mkdir()
        (td / "session_mode.json").write_text(json.dumps({**mode_rec, "repo_cwd": str(td)}))
        fakebin = make_fake_bd(tmp / "bin3", ready_items=0, list_items=0)
        out = call_hook({"session_id": "x", "transcript_path": ""},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(out is None,
                "task mode: bd ready empty + no open tasks → allow (queue drained)", results)

        # Task mode + user says 'stop' → allow (universal override).
        td = tmp / "task-mode-user-release"
        td.mkdir(parents=True)
        (td / ".beads").mkdir()
        (td / "session_mode.json").write_text(json.dumps({**mode_rec, "repo_cwd": str(td)}))
        transcript = tmp / "transcript-task-stop.jsonl"
        transcript.write_text(
            json.dumps({"message": {"role": "user", "content": [{"type": "text", "text": "stop"}]}}) + "\n"
        )
        fakebin = make_fake_bd(tmp / "bin4", ready_items=1, list_items=1)
        out = call_hook({"session_id": "x", "transcript_path": str(transcript)},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(out is None,
                "task mode: user 'stop' overrides queue-drain gate → allow", results)

        # --- Scoping fix: task_id as fallback when parent_id is null ---
        # Regression for session 1d6db846: standalone task claimed, parent_id=null,
        # bd ready (unscoped) found unrelated backlog → agent derailed into those tasks.
        # Fix: use task_id as --parent scope. bd ready --parent <leaf-id> returns []
        # for a leaf task → gate allows Stop once the session task is closed.

        def make_fake_bd_scoped(tmp_base: pathlib.Path, task_id: str) -> pathlib.Path:
            """Fake bd: returns [] when called with --parent task_id, else returns items."""
            bin_dir = tmp_base / "fakescoped"
            bin_dir.mkdir(parents=True, exist_ok=True)
            script = bin_dir / "bd"
            unrelated = json.dumps([{"id": "unrelated-1"}, {"id": "unrelated-2"}])
            script.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "show" ]; then printf \'[{"id":"%s","status":"closed"}]\\n\' "$2"; exit 0; fi\n'
                # When scoped to the leaf task, no children exist → empty
                f'if echo "$*" | grep -q -- "--parent {task_id}"; then echo "[]"; exit 0; fi\n'
                # Without scoping (regression case), returns unrelated items
                f'echo \'{unrelated}\'\n'
                "exit 0\n"
            )
            script.chmod(0o755)
            return bin_dir

        claimed_task = "cake-standalone-42"

        # task_id set, parent_id null, scoped bd returns [] → allow (leaf task closed).
        td = tmp / "taskid-scope-allow"
        td.mkdir(parents=True)
        (td / ".beads").mkdir()
        scoped_mode = {"mode": "task", "repo_cwd": str(td), "task_id": claimed_task,
                       "parent_id": None, "entered_at": _now_iso(), "session_id": "x"}
        (td / "session_mode.json").write_text(json.dumps(scoped_mode))
        fakebin = make_fake_bd_scoped(tmp / "bin-scoped", claimed_task)
        out = call_hook({"session_id": "x", "transcript_path": ""},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(out is None,
                "task_id scoping: standalone task with no children → allow (leaf task closed)",
                results)

        # e9v.11: a fully scopeless record (task_id=null AND parent_id=null) is NOT
        # task mode — it must NOT block on whole-repo backlog (the old "regression
        # baseline" here asserted the opposite, which trapped finished sessions on
        # other sessions' work). Teeth are kept by the CONTRACT gate, not by
        # whole-repo blocking: this asserts the stronger invariant that a scopeless
        # session with an UNVERIFIED contract still BLOCKS (no stop-with-work-undone
        # hole), while the no-contract / whole-repo-ready case allows (above).
        td = tmp / "taskid-scope-redcontract"
        td.mkdir(parents=True)
        (td / ".beads").mkdir()
        unscoped_mode = {"mode": "task", "repo_cwd": str(td), "task_id": None,
                         "parent_id": None, "entered_at": _now_iso(), "session_id": "x"}
        (td / "session_mode.json").write_text(json.dumps(unscoped_mode))
        # Red contract: present but last_run failed -> contract gate must block.
        (td / "contract.json").write_text(json.dumps({
            "goal": "g", "verification_command": "pytest", "expected_exit": 0,
            "source": "agent-declared", "thread_id": "x", "created_at": _now_iso(),
            "last_run": {"exit_code": 1, "timestamp": _now_iso(), "output_excerpt": "FAIL"},
        }))
        out = call_hook({"session_id": "x", "transcript_path": ""},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(out is not None and out.get("decision") == "block",
                "e9v.11: scopeless record + unverified contract → still block (teeth kept via contract gate)",
                results)

    finally:
        _shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\n[task_mode] {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


def run_implicit_queue() -> int:
    """Integration tests for B3 fix: implicit bd queue check after verification_passed.

    Covers the subagent-claim gap: when task-mode was not entered via the PreToolUse
    hook but bd work is still in-flight, the stop hook should block.
    """
    results = []
    bin_path = HARNESS_ROOT / "bin" / "stop_hook.py"

    import shutil as _shutil
    import tempfile as _tf

    def _passing_contract(thread_id: str) -> dict:
        """A contract whose last_run is fresh and exit_code 0 — would_block_stop returns allow."""
        return {
            "goal": "test outcome",
            "verification_command": "pytest -q",
            "expected_exit": 0,
            "source": "agent-declared",
            "thread_id": thread_id,
            "created_at": _now_iso(),
            "last_run": {
                "exit_code": 0,
                "timestamp": _now_iso(),
                "output_excerpt": "",
            },
        }

    def make_fake_bd_implicit(
        bin_dir: pathlib.Path,
        in_progress_items: int,
        ready_items: int,
    ) -> pathlib.Path:
        """Fake bd distinguishing in_progress (list --status=in_progress) from ready."""
        bin_dir.mkdir(parents=True, exist_ok=True)
        script = bin_dir / "bd"
        ip_json = json.dumps([{"id": f"t-{i}", "title": f"task {i}"} for i in range(in_progress_items)])
        ready_json = json.dumps([{"id": f"t-{i}", "title": f"task {i}"} for i in range(ready_items)])
        script.write_text(
            "#!/bin/sh\n"
            f"if echo \"$*\" | grep -q 'in_progress'; then echo '{ip_json}'; exit 0; fi\n"
            f"if echo \"$*\" | grep -q '^ready'; then echo '{ready_json}'; exit 0; fi\n"
            "echo '[]'; exit 0\n"
        )
        script.chmod(0o755)
        return bin_dir

    def call_hook(
        payload: dict,
        thread_dir: pathlib.Path,
        project_dir: pathlib.Path,
        env_extra: "dict | None" = None,
    ) -> "dict | None":
        """Run stop_hook.py from project_dir so os.getcwd() returns the project."""
        env = {**_os.environ, "HARNESS_THREAD_DIR": str(thread_dir), **(env_extra or {})}
        r = _subprocess.run(
            [sys.executable, str(bin_path)],
            input=json.dumps(payload),
            capture_output=True, text=True, env=env,
            cwd=str(project_dir),
        )
        if r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                return {"raw": r.stdout}
        return None

    tmp = pathlib.Path(_tf.mkdtemp(prefix="harness-implicit-"))
    try:
        # Test 1: verify passes, no .beads/ in cwd → allow (no bd project, skip check).
        td = tmp / "no-beads"
        td.mkdir()
        project_dir = tmp / "project-no-beads"
        project_dir.mkdir()
        (td / "contract.json").write_text(json.dumps(_passing_contract("no-beads")))
        out = call_hook({"session_id": "no-beads", "transcript_path": ""}, td, project_dir)
        _assert(
            out is None,
            "implicit: verify passes, no .beads/ → allow (no implicit check)",
            results,
        )

        # Test 2: verify passes, in-progress items in .beads/ repo → block.
        td = tmp / "in-progress-block"
        td.mkdir()
        project_dir = tmp / "project-in-progress"
        project_dir.mkdir()
        (project_dir / ".beads").mkdir()
        (td / "contract.json").write_text(json.dumps(_passing_contract("in-progress")))
        fakebin = make_fake_bd_implicit(tmp / "bin-ip", in_progress_items=1, ready_items=0)
        out = call_hook(
            {"session_id": "in-progress", "transcript_path": ""},
            td, project_dir,
            {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"},
        )
        _assert(
            out is not None and out.get("decision") == "block"
            and "unfinished work" in out.get("reason", ""),
            "implicit: verify passes, in-progress items → block (implicit_queue_in_progress)",
            results,
        )

        # Test 3: verify passes, ready items (no in-progress) → block.
        td = tmp / "ready-block"
        td.mkdir()
        project_dir = tmp / "project-ready"
        project_dir.mkdir()
        (project_dir / ".beads").mkdir()
        (td / "contract.json").write_text(json.dumps(_passing_contract("ready")))
        fakebin = make_fake_bd_implicit(tmp / "bin-ready", in_progress_items=0, ready_items=2)
        out = call_hook(
            {"session_id": "ready", "transcript_path": ""},
            td, project_dir,
            {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"},
        )
        _assert(
            out is not None and out.get("decision") == "block",
            "implicit: verify passes, ready items (no in-progress) → block (implicit_queue_ready_items)",
            results,
        )

        # Test 4: verify passes, queue empty → allow.
        td = tmp / "queue-empty"
        td.mkdir()
        project_dir = tmp / "project-empty"
        project_dir.mkdir()
        (project_dir / ".beads").mkdir()
        (td / "contract.json").write_text(json.dumps(_passing_contract("empty")))
        fakebin = make_fake_bd_implicit(tmp / "bin-empty", in_progress_items=0, ready_items=0)
        out = call_hook(
            {"session_id": "empty", "transcript_path": ""},
            td, project_dir,
            {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"},
        )
        _assert(
            out is None,
            "implicit: verify passes, queue empty → allow (implicit_queue_drained)",
            results,
        )

        # Test 5: user_released bypasses implicit check even with in-progress items.
        td = tmp / "user-release-bypass"
        td.mkdir()
        project_dir = tmp / "project-user-release"
        project_dir.mkdir()
        (project_dir / ".beads").mkdir()
        # Contract is NOT passing (last_run=None) — but user says stop.
        unverified_contract = {
            "goal": "test", "verification_command": "false", "expected_exit": 0,
            "source": "agent-declared", "thread_id": "user-release",
            "created_at": _now_iso(), "last_run": None,
        }
        (td / "contract.json").write_text(json.dumps(unverified_contract))
        transcript = tmp / "transcript-user-release.jsonl"
        transcript.write_text(
            json.dumps({"message": {"role": "user", "content": [{"type": "text", "text": "stop"}]}}) + "\n"
        )
        fakebin = make_fake_bd_implicit(tmp / "bin-urel", in_progress_items=3, ready_items=5)
        out = call_hook(
            {"session_id": "user-release", "transcript_path": str(transcript)},
            td, project_dir,
            {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"},
        )
        _assert(
            out is None,
            "implicit: user_released bypasses implicit queue check even with bd work",
            results,
        )

    finally:
        _shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\n[implicit_queue] {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


def run_wakeup_blocker_wiring() -> int:
    """R3 main()-level WIRING tests (verifier Finding 1, the BLOCKER) + R2
    integration with a bd that IMPLEMENTS the `blocked` subcommand (Finding 4).

    WHY THIS SUITE EXISTS: every existing R3 test calls
    `stop_hook._check_wakeup_blockers(...)` DIRECTLY. That verifies the function
    but NOT that main() ever calls it. The verifier proved R3 is dead in
    production: a task-mode session that registers a future wakeup and files a
    bare-prose blocker bead receives an unconditional allow at the
    `override_reason == "wakeup_registered"` branch — `_check_wakeup_blockers`
    never runs. These tests drive the REAL stop_hook.py subprocess end to end so
    the wiring cannot be satisfied by unit-testing the function in isolation; they
    FAIL until main() actually calls the check.
    """
    results = []
    bin_path = HARNESS_ROOT / "bin" / "stop_hook.py"

    import shutil as _shutil
    import tempfile as _tf

    def call_hook(payload: dict, thread_dir: pathlib.Path, env_extra=None) -> "dict | None":
        env = {**_os.environ, "HARNESS_THREAD_DIR": str(thread_dir), **(env_extra or {})}
        r = _subprocess.run(
            [sys.executable, str(bin_path)],
            input=json.dumps(payload),
            capture_output=True, text=True, env=env,
        )
        if r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                return {"raw": r.stdout}
        return None

    def make_fake_bd_blocked(bin_dir: pathlib.Path, blocked_beads: list) -> pathlib.Path:
        """Fake bd that IMPLEMENTS the `blocked` subcommand (unlike make_fake_bd,
        whose `blocked` falls through to exit 0 + empty stdout → back-compat []).

        `ready` → []; `blocked` → the provided list (with id + description, so the
        blocker-verify/waiver parsing has real text to read); `list` → [].

        The `blocked` JSON is written to a SIDECAR FILE that the fake bd `cat`s,
        NOT interpolated into a shell-quoted echo. Bead descriptions contain
        apostrophes (e.g. "another team's ...") which would terminate a
        single-quoted shell string early and corrupt the script (exit 2 → the
        production run_bd sees a failure → None → no block, a false green/red).
        A sidecar file sidesteps shell quoting entirely for arbitrary content."""
        bin_dir.mkdir(parents=True, exist_ok=True)
        script = bin_dir / "bd"
        blocked_file = bin_dir / "blocked.json"
        blocked_file.write_text(json.dumps(blocked_beads), encoding="utf-8")
        script.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "show" ]; then printf \'[{"id":"%s","status":"closed"}]\\n\' "$2"; exit 0; fi\n'
            'if echo "$*" | grep -q "^ready"; then echo "[]"; exit 0; fi\n'
            f'if echo "$*" | grep -q "blocked"; then cat "{blocked_file}"; exit 0; fi\n'
            'if echo "$*" | grep -q "^list"; then echo "[]"; exit 0; fi\n'
            "exit 0\n"
        )
        script.chmod(0o755)
        return bin_dir

    def _future_wakeup_json() -> str:
        return json.dumps([{"wake_at": _future_iso(120), "prompt": "resume work",
                            "reason": "waiting on external blocker"}])

    INCIDENT = "Blocked on another team's Salesforce test."

    tmp = pathlib.Path(_tf.mkdtemp(prefix="harness-wakeup-blocker-"))
    try:
        # --- F1 BLOCKER: wakeup registered + bare-prose blocked bead → BLOCK ----
        # The exact incident reproduction: future wakeup in scheduled.json, a
        # scoped blocked bead whose description is bare prose (no blocker-verify,
        # no blocker-waiver). main() MUST route through _check_wakeup_blockers and
        # emit block/wakeup_blocker_unverified. Current code returns 0 (allow) at
        # the wakeup branch → this FAILS until the wiring lands.
        td = tmp / "wakeup-unverified-blocker"
        td.mkdir(parents=True)
        (td / ".beads").mkdir()
        mode_rec = {"mode": "task", "repo_cwd": str(td), "parent_id": "cake-m95.4",
                    "entered_at": _now_iso(), "session_id": "x"}
        (td / "session_mode.json").write_text(json.dumps(mode_rec))
        (td / "scheduled.json").write_text(_future_wakeup_json())
        fakebin = make_fake_bd_blocked(
            tmp / "bin-unverified",
            [{"id": "cake-m95.4.9", "description": INCIDENT}],
        )
        out = call_hook({"session_id": "x", "transcript_path": ""},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(
            out is not None and out.get("decision") == "block"
            and "wakeup_blocker_unverified" in out.get("reason", "").lower().replace(" ", "_")
            or (out is not None and out.get("decision") == "block"),
            "WIRING (F1): wakeup + bare-prose blocker bead → BLOCK via main() "
            "(the incident must not reproduce; _check_wakeup_blockers must run)",
            results,
        )
        # Stronger, separate assertion on the reason so a generic block elsewhere
        # cannot satisfy this — the denial must be the blocker-unverified one and
        # must name the fix paths.
        reason_txt = (out or {}).get("reason", "")
        _assert(
            out is not None and out.get("decision") == "block"
            and ("blocker-verify" in reason_txt.lower() or "schedulewakeup" in reason_txt.lower()),
            "WIRING (F1): the wakeup-blocker denial names the escape (blocker-verify "
            "/ ScheduleWakeup / waiver), not just any block",
            results,
        )

        # --- F1 POSITIVE CONTROL: wakeup + bead with a VALID WAIVER → ALLOW ------
        td = tmp / "wakeup-waivered-blocker"
        td.mkdir(parents=True)
        (td / ".beads").mkdir()
        (td / "session_mode.json").write_text(json.dumps({**mode_rec, "repo_cwd": str(td)}))
        (td / "scheduled.json").write_text(_future_wakeup_json())
        waiver = ("blocker-waiver: SFDC sandbox refresh ETA 2026-06-12; no local "
                  "repro available, manual reverify scheduled next session")
        fakebin = make_fake_bd_blocked(
            tmp / "bin-waivered",
            [{"id": "cake-m95.4.9", "description": waiver}],
        )
        out = call_hook({"session_id": "x", "transcript_path": ""},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(
            out is None,
            "WIRING (F1 positive control): wakeup + bead with a valid waiver → "
            "ALLOW via main() (a real, substantive waiver releases the wakeup path)",
            results,
        )

        # --- F4: bd that IMPLEMENTS `blocked` returning ≥1, NO wakeup → BLOCK ----
        # The sibling to test_gate.py:569. That test passes only via the missing-
        # `blocked`-subcommand back-compat path. With a bd that actually implements
        # `blocked` and returns a scoped blocked bead, R2 says BLOCK. No wakeup is
        # registered, so this exercises _check_task_mode_queue's R2 path through
        # main(), not the wakeup branch.
        td = tmp / "r2-real-blocked-subcommand"
        td.mkdir(parents=True)
        (td / ".beads").mkdir()
        (td / "session_mode.json").write_text(json.dumps({**mode_rec, "repo_cwd": str(td)}))
        # NO scheduled.json → no wakeup override.
        fakebin = make_fake_bd_blocked(
            tmp / "bin-r2-blocked",
            [{"id": "cake-m95.4.9", "description": INCIDENT}],
        )
        out = call_hook({"session_id": "x", "transcript_path": ""},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(
            out is not None and out.get("decision") == "block",
            "R2 (F4 sibling): bd implementing `blocked` returns a scoped blocked "
            "bead + empty ready + no wakeup → BLOCK (not the back-compat allow)",
            results,
        )

        # --- F4 negative control: same bd, ZERO blocked → ALLOW (queue drained) --
        td = tmp / "r2-real-blocked-empty"
        td.mkdir(parents=True)
        (td / ".beads").mkdir()
        (td / "session_mode.json").write_text(json.dumps({**mode_rec, "repo_cwd": str(td)}))
        fakebin = make_fake_bd_blocked(tmp / "bin-r2-empty", [])
        out = call_hook({"session_id": "x", "transcript_path": ""},
                        td, {"PATH": f"{fakebin}:{_os.environ.get('PATH', '')}"})
        _assert(
            out is None,
            "R2 (F4 negative control): bd implementing `blocked` returns [] + empty "
            "ready → ALLOW (genuine queue drain still permits Stop)",
            results,
        )

    finally:
        _shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\n[wakeup_blocker_wiring] {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


# --- pytest discovery -------------------------------------------------------
# Thin wrappers exposing the run_*() suites to pytest. Previously this file
# defined only run_*() functions + a __main__ runner, so `pytest` collected
# ZERO tests here — a CI `pytest` run silently skipped the harness's core gate
# tests. Each run_*() returns 0 on success (non-zero prints its _assert
# failures), so asserting == 0 surfaces any failure to pytest without changing
# the existing test logic.

def test_gate_decision_cases():
    assert run() == 0

def test_session_isolation():
    assert run_isolation() == 0

def test_portability():
    assert run_portability() == 0

def test_stop_hook():
    assert run_stop_hook() == 0

def test_task_mode():
    assert run_task_mode() == 0

def test_implicit_queue():
    assert run_implicit_queue() == 0

def test_wakeup_blocker_wiring():
    assert run_wakeup_blocker_wiring() == 0


if __name__ == "__main__":
    rc_gate = run()
    rc_iso = run_isolation()
    rc_port = run_portability()
    rc_hook = run_stop_hook()
    rc_task = run_task_mode()
    rc_implicit = run_implicit_queue()
    rc_wakeup = run_wakeup_blocker_wiring()
    sys.exit(rc_gate or rc_iso or rc_port or rc_hook or rc_task or rc_implicit or rc_wakeup)


def test_cases_are_built_lazily_not_frozen_at_import():
    """Regression guard: `CASES` was a module constant (escapement-ptzz session).

    `_future_iso()` / `_now_iso()` freeze ABSOLUTE timestamps. Evaluated at import
    (pytest collection), a "60 seconds ahead" wakeup expires before a slow suite
    reaches this test, and the gate degrades wakeup_registered -> conversational.
    The failure was a function of SUITE DURATION, not of gate logic.

    Two calls must produce different timestamps. Identical ones mean the fixture
    is frozen again.
    """

    def _wake_ats(cases: list[dict]) -> list[str]:
        return [
            entry["wake_at"]
            for case in cases
            for entry in case.get("state", {}).get("scheduled", []) or []
            if isinstance(entry, dict) and "wake_at" in entry
        ]

    first, second = _cases(), _cases()
    a, b = _wake_ats(first), _wake_ats(second)

    # Positive control: without this, an empty list would make the guard vacuous.
    assert a, "no scheduled wake_at present in the cases — this guard proves nothing"
    assert a != b, (
        "wake_at timestamps are identical across two _cases() calls — the fixture is "
        "frozen at import again; the suite will fail once it runs slower than the horizon"
    )
