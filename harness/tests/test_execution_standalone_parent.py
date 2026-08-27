#!/usr/bin/env python3
"""Canonical-parent controls for standalone Beads in supervisor planning."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger as ledger_api  # noqa: E402
import execution_supervisor as supervisor  # noqa: E402

SESSION = "standalone-parent-session"
BEAD = "escapement-standalone-root"
EXECUTION = "exec-standalone-root"
NOW = dt.datetime(2026, 8, 27, 23, 0, tzinfo=dt.timezone.utc)


def at(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def registered() -> dict:
    ledger = ledger_api.new_ledger(SESSION)
    ledger_api.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": SESSION,
            "bead_id": BEAD,
            "execution_id": EXECUTION,
            "host": "claude",
            "agent_name": "standalone-worker",
            "dispatch_tool_use_id": "toolu-standalone",
            "watchdog_id": "watch-standalone",
            "attempt": 1,
            "generation": 1,
        },
        at("2026-08-27T22:59:00Z"),
    )
    return ledger


def write_thread(root: pathlib.Path) -> pathlib.Path:
    thread = root / SESSION
    thread.mkdir(parents=True)
    ledger_path = thread / "executions.json"
    ledger_path.write_text(json.dumps(registered()), encoding="utf-8")
    ledger_path.chmod(0o600)
    mode = thread / "session_mode.json"
    mode.write_text(
        json.dumps(
            {
                "mode": "task",
                "repo_cwd": str(thread),
                "task_id": BEAD,
                "session_id": SESSION,
            }
        ),
        encoding="utf-8",
    )
    mode.chmod(0o600)
    return thread


def runner(*, present: bool, value=None):
    calls: list[list[str]] = []

    def run_bd(args: list[str]):
        calls.append(args)
        if args == ["show", BEAD]:
            child = {"id": BEAD, "status": "in_progress"}
            if present:
                child["parent"] = value
            return [child]
        if isinstance(value, str) and value and args == ["show", value]:
            return [{"id": value, "status": "in_progress"}]
        return []

    return run_bd, calls


@pytest.mark.parametrize(
    ("present", "value"),
    [(False, None), (True, None)],
    ids=["absent-parent", "explicit-null-parent"],
)
def test_supervisor_accepts_standalone_bead_without_parent_lookup(
    tmp_path, present: bool, value
) -> None:
    thread = write_thread(tmp_path / "harness" / "threads")
    run_bd, calls = runner(present=present, value=value)

    plan = supervisor.plan_thread(thread, NOW, lambda _item: None, run_bd)

    assert plan["status"] == "ok"
    assert calls == [["show", BEAD]]
    canonical = plan["canonical"][EXECUTION]
    assert canonical["child"]["id"] == BEAD
    assert canonical["parent"] is None
    assert canonical["parent_id"] is None


@pytest.mark.parametrize(
    "malformed_parent",
    ["", False, True, [], {}, 17],
    ids=["empty", "false", "true", "list", "mapping", "integer"],
)
def test_supervisor_rejects_malformed_parent_without_inventing_identity(
    tmp_path, malformed_parent
) -> None:
    thread = write_thread(tmp_path / "harness" / "threads")
    run_bd, calls = runner(present=True, value=malformed_parent)

    plan = supervisor.plan_thread(thread, NOW, lambda _item: None, run_bd)

    assert plan["status"] == "unresolved"
    assert calls == [["show", BEAD]]
