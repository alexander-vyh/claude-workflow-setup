#!/usr/bin/env python3
"""Public claim-entry -> persisted scope -> Stop oracle for escapement-858.3.

The fake ``bd`` executable is the independent source of truth. Tests invoke the
real task_mode_entry.py and stop_hook.py scripts, then assert the persisted root
and final Stop decision. This rejects a helper-only fix and the original
one-parent implementation. Actual ``bd show --json`` root records may omit both
parent fields; omission and explicit null are valid terminal root shapes.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
ENTRY = REPO / "harness" / "bin" / "task_mode_entry.py"
STOP = REPO / "harness" / "bin" / "stop_hook.py"
sys.path.insert(0, str(REPO / "harness" / "bin"))

import task_mode_entry  # noqa: E402


_FAKE_BD = r"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

fixture = json.loads(pathlib.Path(os.environ["FAKE_BD_FIXTURE"]).read_text())
with pathlib.Path(os.environ["FAKE_BD_LOG"]).open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")

args = sys.argv[1:]
command = args[0] if args else ""

if command == "show":
    issue_id = args[1]
    spec = fixture.get("shows", {}).get(issue_id, {"kind": "exit_error"})
    kind = spec.get("kind", "issue")
    if kind == "exit_error":
        raise SystemExit(spec.get("returncode", 2))
    if kind == "invalid_json":
        sys.stdout.write("{")
        raise SystemExit(0)
    if kind == "undecodable":
        os.write(1, bytes([255, 254]))
        raise SystemExit(0)
    if kind == "raw_json":
        sys.stdout.write(json.dumps(spec.get("value")))
        raise SystemExit(0)

    issue = {}
    if not spec.get("omit_id"):
        issue["id"] = spec.get("id", issue_id)
    if not spec.get("omit_parent"):
        issue[spec.get("parent_key", "parent")] = spec.get("parent")
    if "status" in spec:
        issue["status"] = spec["status"]
    sys.stdout.write(json.dumps([issue]))
    raise SystemExit(0)

scope = None
if "--parent" in args:
    scope = args[args.index("--parent") + 1]
if command == "ready":
    sys.stdout.write(json.dumps(fixture.get("ready_by_parent", {}).get(scope, [])))
    raise SystemExit(0)
if command == "blocked":
    sys.stdout.write(json.dumps(fixture.get("blocked_by_parent", {}).get(scope, [])))
    raise SystemExit(0)
sys.stdout.write("[]")
"""


def _write_fake_bd(tmp_path: pathlib.Path, fixture: dict[str, Any]):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(parents=True, exist_ok=True)
    executable = fakebin / "bd"
    executable.write_text(_FAKE_BD)
    executable.chmod(0o755)
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture))
    log_path = tmp_path / "bd-calls.jsonl"
    return fakebin, fixture_path, log_path


def _hook_env(
    fakebin: pathlib.Path,
    fixture_path: pathlib.Path,
    log_path: pathlib.Path,
    thread_dir: pathlib.Path,
) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": f"{fakebin}:{os.environ.get('PATH', '')}",
        "FAKE_BD_FIXTURE": str(fixture_path),
        "FAKE_BD_LOG": str(log_path),
        "HARNESS_THREAD_DIR": str(thread_dir),
    }


def _run_entry(
    tmp_path: pathlib.Path,
    fixture: dict[str, Any],
    task_id: str = "leaf",
    session_id: str = "session",
):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".beads").mkdir(exist_ok=True)
    thread_dir = tmp_path / "thread"
    fakebin, fixture_path, log_path = _write_fake_bd(tmp_path, fixture)
    env = _hook_env(fakebin, fixture_path, log_path, thread_dir)
    payload = {
        "tool_name": "Bash",
        "session_id": session_id,
        "tool_input": {"command": f"bd update {task_id} --claim"},
    }
    proc = subprocess.run(
        [sys.executable, str(ENTRY)],
        input=json.dumps(payload),
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=3,
    )
    state_path = thread_dir / "session_mode.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    return proc, state, repo, thread_dir, env, log_path


def _run_stop(
    repo: pathlib.Path,
    thread_dir: pathlib.Path,
    env: dict[str, str],
    session_id="session",
):
    proc = subprocess.run(
        [sys.executable, str(STOP)],
        input=json.dumps({"session_id": session_id, "transcript_path": ""}),
        cwd=repo,
        env={**env, "HARNESS_THREAD_DIR": str(thread_dir)},
        capture_output=True,
        text=True,
        timeout=3,
    )
    output = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc, output


def _deep_graph() -> dict[str, Any]:
    return {
        "shows": {
            "leaf": {"parent": "branch-a"},
            "branch-a": {"parent": "root"},
            "root": {"omit_parent": True},
        }
    }


def test_deep_ready_sibling_under_other_branch_blocks_stop(
    tmp_path: pathlib.Path,
) -> None:
    # Real molecule steps use dotted hierarchical IDs (for example 858.3).
    fixture = {
        "shows": {
            "mol.1.1": {"parent": "mol.1"},
            "mol.1": {"parent": "mol"},
            # Mirror live bd output: root records omit the parent key entirely.
            "mol": {"omit_parent": True},
        }
    }
    fixture["ready_by_parent"] = {
        "mol.1": [],
        "mol": [{"id": "sibling-under-other-branch"}],
    }

    entry, state, repo, thread_dir, env, log_path = _run_entry(
        tmp_path, fixture, task_id="mol.1.1"
    )
    stop, output = _run_stop(repo, thread_dir, env)

    assert entry.returncode == 0
    assert entry.stderr == ""
    assert state["parent_id"] == "mol"
    assert stop.returncode == 0
    assert output is not None and output["decision"] == "block"
    calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert ["ready", "--parent", "mol", "--json"] in calls


def test_deep_molecule_with_all_work_complete_allows_stop(
    tmp_path: pathlib.Path,
) -> None:
    fixture = _deep_graph()
    fixture["shows"]["root"]["status"] = "closed"
    fixture["ready_by_parent"] = {"root": []}
    fixture["blocked_by_parent"] = {"root": []}

    entry, state, repo, thread_dir, env, _ = _run_entry(tmp_path, fixture)
    stop, output = _run_stop(repo, thread_dir, env)

    assert entry.returncode == 0
    assert state["parent_id"] == "root"
    assert stop.returncode == 0
    assert output is None


@pytest.mark.parametrize(
    "root_spec",
    [
        {"omit_parent": True, "status": "open"},
        {"omit_parent": True},
    ],
    ids=["open-root", "missing-root-status"],
)
def test_empty_descendant_queues_do_not_complete_an_unclosed_root(
    tmp_path: pathlib.Path, root_spec: dict[str, Any]
) -> None:
    """An empty queue is not an independent proof that the root outcome closed."""
    fixture = {
        "shows": {
            "leaf": {"parent": "root"},
            "root": root_spec,
        },
        "ready_by_parent": {"root": []},
        "blocked_by_parent": {"root": []},
    }

    entry, state, repo, thread_dir, env, log_path = _run_entry(tmp_path, fixture)
    stop, output = _run_stop(repo, thread_dir, env)

    assert entry.returncode == 0
    assert state["parent_id"] == "root"
    assert stop.returncode == 0
    assert output is not None and output["decision"] == "block"
    assert "parent_outcome_unresolved" in output["reason"]
    calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert ["ready", "--parent", "root", "--json"] in calls
    assert ["show", "root", "--json"] in calls


def test_standalone_claim_retains_task_scope(tmp_path: pathlib.Path) -> None:
    fixture = {
        "shows": {"leaf": {"parent": None}},
        "ready_by_parent": {"leaf": [{"id": "leaf-work"}]},
    }

    entry, state, repo, thread_dir, env, _ = _run_entry(tmp_path, fixture)
    stop, output = _run_stop(repo, thread_dir, env)

    assert entry.returncode == 0
    assert entry.stderr == ""
    assert state["task_id"] == "leaf"
    assert state["parent_id"] is None
    assert stop.returncode == 0
    assert output is not None and output["decision"] == "block"


@pytest.mark.parametrize("root_spec", [{"omit_parent": True}, {"parent": None}])
def test_absent_or_null_parent_is_a_valid_root_terminal(
    tmp_path: pathlib.Path,
    root_spec: dict[str, Any],
) -> None:
    fixture = {
        "shows": {"leaf": {"parent": "root"}, "root": root_spec},
        "ready_by_parent": {"root": [{"id": "root-scope-ready"}]},
    }

    entry, state, repo, thread_dir, env, _ = _run_entry(tmp_path, fixture)
    stop, output = _run_stop(repo, thread_dir, env)

    assert entry.returncode == 0
    assert entry.stderr == ""
    assert state["parent_id"] == "root"
    assert stop.returncode == 0
    assert output is not None and output["decision"] == "block"


@pytest.mark.parametrize(
    ("shows", "expected_scope", "diagnostic_fragment"),
    [
        ({"leaf": {"kind": "invalid_json"}}, "leaf", "invalid json"),
        ({"leaf": {"omit_id": True, "parent": None}}, "leaf", "issue id"),
        ({"leaf": {"parent": {"id": "root"}}}, "leaf", "parent"),
        ({"leaf": {"parent": ["root"]}}, "leaf", "parent"),
        ({"leaf": {"parent": 17}}, "leaf", "parent"),
        ({"leaf": {"parent": ""}}, "leaf", "parent"),
        ({"leaf": {"parent": False}}, "leaf", "parent"),
        ({"leaf": {"parent": []}}, "leaf", "parent"),
        ({"leaf": {"parent": {}}}, "leaf", "parent"),
        ({"leaf": {"kind": "undecodable"}}, "leaf", "utf-8"),
        ({"leaf": {"kind": "exit_error"}}, "leaf", "lookup failed"),
        ({"leaf": {"kind": "raw_json", "value": []}}, "leaf", "issue record"),
        ({"leaf": {"id": "different", "parent": None}}, "leaf", "issue id"),
        (
            {
                "leaf": {
                    "kind": "raw_json",
                    "value": [
                        {"id": "leaf", "parent": "root-a", "parent_id": "root-b"}
                    ],
                }
            },
            "leaf",
            "parent",
        ),
        (
            {"leaf": {"parent": "parent"}, "parent": {"kind": "invalid_json"}},
            "leaf",
            "invalid json",
        ),
        (
            {
                "leaf": {"parent": "branch"},
                "branch": {"parent": "unreadable"},
                "unreadable": {"kind": "invalid_json"},
            },
            "branch",
            "invalid json",
        ),
        (
            {
                "leaf": {"parent": "branch"},
                "branch": {"parent": "failed"},
                "failed": {"kind": "exit_error"},
            },
            "branch",
            "lookup failed",
        ),
    ],
)
def test_malformed_or_failed_lookup_persists_safe_scope_and_still_blocks(
    tmp_path: pathlib.Path,
    shows: dict[str, Any],
    expected_scope: str,
    diagnostic_fragment: str,
) -> None:
    fixture = {
        "shows": shows,
        "ready_by_parent": {expected_scope: [{"id": "safe-scope-ready"}]},
    }

    entry, state, repo, thread_dir, env, _ = _run_entry(tmp_path, fixture)
    stop, output = _run_stop(repo, thread_dir, env)

    assert entry.returncode == 0
    assert state["parent_id"] == expected_scope
    lines = entry.stderr.splitlines()
    assert len(lines) == 1
    assert diagnostic_fragment in lines[0].lower()
    assert stop.returncode == 0
    assert output is not None and output["decision"] == "block"


@pytest.mark.parametrize(
    ("shows", "expected_scope"),
    [
        ({"leaf": {"parent": "leaf"}}, "leaf"),
        (
            {
                "leaf": {"parent": "branch"},
                "branch": {"parent": "other"},
                "other": {"parent": "branch"},
            },
            "other",
        ),
    ],
)
def test_cycle_claim_terminates_persists_last_safe_scope_and_blocks(
    tmp_path: pathlib.Path,
    shows: dict[str, Any],
    expected_scope: str,
) -> None:
    fixture = {
        "shows": shows,
        "ready_by_parent": {expected_scope: [{"id": "cycle-scope-ready"}]},
    }

    entry, state, repo, thread_dir, env, _ = _run_entry(tmp_path, fixture)
    stop, output = _run_stop(repo, thread_dir, env)

    assert entry.returncode == 0
    assert state["parent_id"] == expected_scope
    lines = entry.stderr.splitlines()
    assert len(lines) == 1
    assert "cycle" in lines[0].lower()
    assert stop.returncode == 0
    assert output is not None and output["decision"] == "block"


def test_two_public_claims_in_one_process_keep_independent_roots_and_stop_scopes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = {
        "shows": {
            "leaf-a": {"parent": "root-a"},
            "root-a": {"parent": None},
            "leaf-b": {"parent": "branch-b"},
            "branch-b": {"parent": "root-b"},
            "root-b": {"parent": None},
        },
        "ready_by_parent": {
            "root-a": [{"id": "ready-a"}],
            "root-b": [{"id": "ready-b"}],
        },
    }
    fakebin, fixture_path, log_path = _write_fake_bd(tmp_path, fixture)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".beads").mkdir()
    harness_root = tmp_path / "harness"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", f"{fakebin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_BD_FIXTURE", str(fixture_path))
    monkeypatch.setenv("FAKE_BD_LOG", str(log_path))
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    monkeypatch.setattr(task_mode_entry, "HARNESS_ROOT", harness_root)

    for session_id, task_id in (("session-a", "leaf-a"), ("session-b", "leaf-b")):
        payload = {
            "tool_name": "Bash",
            "session_id": session_id,
            "tool_input": {"command": f"bd update {task_id} --claim"},
        }
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert task_mode_entry.main() == 0

    states = {
        session_id: json.loads(
            (harness_root / "threads" / session_id / "session_mode.json").read_text()
        )
        for session_id in ("session-a", "session-b")
    }
    assert states["session-a"]["parent_id"] == "root-a"
    assert states["session-b"]["parent_id"] == "root-b"

    env = {
        **os.environ,
        "PATH": f"{fakebin}:{os.environ.get('PATH', '')}",
        "FAKE_BD_FIXTURE": str(fixture_path),
        "FAKE_BD_LOG": str(log_path),
        "HARNESS_ROOT": str(harness_root),
    }
    for session_id in ("session-a", "session-b"):
        stop, output = _run_stop(
            repo,
            harness_root / "threads" / session_id,
            env,
            session_id=session_id,
        )
        assert stop.returncode == 0
        assert output is not None and output["decision"] == "block"

    ready_scopes = [
        call[call.index("--parent") + 1]
        for call in (json.loads(line) for line in log_path.read_text().splitlines())
        if call and call[0] == "ready"
    ]
    assert ready_scopes == ["root-a", "root-b"]


def test_same_task_id_is_rewalked_for_each_public_claim_in_one_process(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a production-only cache keyed by the claimed task id."""
    fixture = {
        "shows": {
            "leaf": {"parent": "root-a"},
            "root-a": {"parent": None},
        },
        "ready_by_parent": {
            "root-a": [{"id": "ready-a"}],
            "root-b": [],
        },
        "blocked_by_parent": {"root-b": []},
    }
    fakebin, fixture_path, log_path = _write_fake_bd(tmp_path, fixture)
    repo = tmp_path / "repo-same-id"
    repo.mkdir()
    (repo / ".beads").mkdir()
    harness_root = tmp_path / "harness-same-id"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", f"{fakebin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_BD_FIXTURE", str(fixture_path))
    monkeypatch.setenv("FAKE_BD_LOG", str(log_path))
    monkeypatch.delenv("HARNESS_THREAD_DIR", raising=False)
    monkeypatch.setattr(task_mode_entry, "HARNESS_ROOT", harness_root)

    def claim(session_id: str) -> None:
        payload = {
            "tool_name": "Bash",
            "session_id": session_id,
            "tool_input": {"command": "bd update leaf --claim"},
        }
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert task_mode_entry.main() == 0

    claim("session-a")
    fixture["shows"] = {
        "leaf": {"parent": "branch-b"},
        "branch-b": {"parent": "root-b"},
        "root-b": {"parent": None, "status": "closed"},
    }
    fixture_path.write_text(json.dumps(fixture))
    claim("session-b")

    states = {
        session_id: json.loads(
            (harness_root / "threads" / session_id / "session_mode.json").read_text()
        )
        for session_id in ("session-a", "session-b")
    }
    assert states["session-a"]["parent_id"] == "root-a"
    assert states["session-b"]["parent_id"] == "root-b"

    env = {
        **os.environ,
        "PATH": f"{fakebin}:{os.environ.get('PATH', '')}",
        "FAKE_BD_FIXTURE": str(fixture_path),
        "FAKE_BD_LOG": str(log_path),
        "HARNESS_ROOT": str(harness_root),
    }
    stop_a, output_a = _run_stop(
        repo, harness_root / "threads" / "session-a", env, session_id="session-a"
    )
    stop_b, output_b = _run_stop(
        repo, harness_root / "threads" / "session-b", env, session_id="session-b"
    )
    assert stop_a.returncode == stop_b.returncode == 0
    assert output_a is not None and output_a["decision"] == "block"
    assert output_b is None

    ready_scopes = [
        call[call.index("--parent") + 1]
        for call in (json.loads(line) for line in log_path.read_text().splitlines())
        if call and call[0] == "ready"
    ]
    assert ready_scopes == ["root-a", "root-b"]
