"""Public-process Codex recovery routing control for the supervisor."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger as ledger_api  # noqa: E402
import execution_store  # noqa: E402


def test_fresh_process_codex_ledger_uses_codex_resume_not_claude(tmp_path) -> None:
    session_id = "codex-supervisor-session-22"
    child_bead = "escapement-codex-child-22"
    parent_bead = "escapement-codex-parent-22"
    execution_id = "exec-codex-public-22"
    threads_root = tmp_path / "harness" / "threads"
    ledger_path = threads_root / session_id / "executions.json"
    ledger_path.parent.mkdir(parents=True)
    repo_cwd = tmp_path / "codex-originating-worktree"
    ambient_cwd = tmp_path / "codex-launchd-ambient"
    repo_cwd.mkdir()
    ambient_cwd.mkdir()
    queued_at = dt.datetime(2026, 8, 9, 20, 0, tzinfo=dt.timezone.utc)
    ledger = ledger_api.new_ledger(session_id)
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": session_id,
            "bead_id": child_bead,
            "execution_id": execution_id,
            "host": "codex",
            "agent_name": "codex-recovery-worker",
            "dispatch_tool_use_id": "codex-dispatch-22",
            "watchdog_id": "codex-watchdog-22",
            "attempt": 1,
            "generation": 1,
        },
        queued_at,
    )
    ledger["executions"][0]["reconcile_due"] = "start"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    ledger_path.chmod(0o600)
    (ledger_path.parent / "session_mode.json").write_text(
        json.dumps(
            {
                "mode": "task",
                "repo_cwd": str(repo_cwd),
                "task_id": child_bead,
                "parent_id": parent_bead,
                "session_id": session_id,
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    codex_record = tmp_path / "codex-record.jsonl"
    claude_record = tmp_path / "claude-must-not-run.jsonl"
    bd_record = tmp_path / "bd-record.jsonl"
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "with pathlib.Path(os.environ['CODEX_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}) + '\\n')\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "with pathlib.Path(os.environ['CLAUDE_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(91)\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        "with pathlib.Path(os.environ['BD_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps(args) + '\\n')\n"
        f"if args == ['show', {child_bead!r}]:\n"
        f"    print(json.dumps([{{'id': {child_bead!r}, 'status': "
        f"'in_progress', 'parent': {parent_bead!r}}}]))\n"
        f"elif args == ['show', {parent_bead!r}]:\n"
        f"    print(json.dumps([{{'id': {parent_bead!r}, 'status': "
        "'in_progress'}]))\n"
        "else:\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["CODEX_RECORD"] = str(codex_record)
    env["CLAUDE_RECORD"] = str(claude_record)
    env["BD_RECORD"] = str(bd_record)

    result = subprocess.run(
        [
            sys.executable,
            str(BIN / "execution_supervisor.py"),
            "--threads-root",
            str(threads_root),
            "--now",
            "2026-08-09T20:03:00Z",
            "--owner",
            "codex-public-supervisor",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=ambient_cwd,
    )
    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 3
    while not codex_record.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert codex_record.exists()
    assert not claude_record.exists()
    records = [json.loads(line) for line in codex_record.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["cwd"] == str(repo_cwd.resolve())
    argv = records[0]["argv"]
    assert argv == ["exec", "resume", session_id, argv[3]]
    prompt = argv[3]
    item = ledger["executions"][0]
    for literal in (
        parent_bead,
        child_bead,
        execution_id,
        "attempt 1",
        "generation 1",
        "start deadline",
        item["start_deadline"],
        "native status unknown",
        str(ledger_path),
    ):
        assert literal in prompt

    durable = execution_store.load_trusted(ledger_path, session_id)
    assert durable is not None
    assert durable["executions"][0]["host"] == "codex"
    assert durable["executions"][0]["recovery_claim"]["generation"] == 1
    bd_calls = [json.loads(line) for line in bd_record.read_text().splitlines()]
    assert bd_calls
    assert all(call[:1] == ["show"] for call in bd_calls)
    assert ["show", child_bead] in bd_calls
    assert ["show", parent_bead] in bd_calls
