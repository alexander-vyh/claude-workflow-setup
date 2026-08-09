#!/usr/bin/env python3
"""Parent outcome is the task-mode completion oracle.

The 2026-08-09 design-session incident closed every child bead but left the
claimed root in_progress.  A descendant-only queue drain therefore allowed a
premature final response.  These fixtures keep the canonical ``bd show`` root
state independent from the descendant queue results.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness" / "bin"))

import stop_hook  # noqa: E402


ROOT = "escapement-e3ai"


def _mode(root_id: str = ROOT, repo_cwd: str = "/repo") -> dict:
    return {"mode": "task", "repo_cwd": repo_cwd, "parent_id": root_id}


def _check_task_scope(session_mode: dict, run_bd):
    """Use the extracted owner once present; keep RED pointed at old behavior."""
    try:
        module = importlib.import_module("beads_task_state")
    except ModuleNotFoundError:
        return stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    return module.check_task_scope(session_mode, run_bd=run_bd)


def _runner(responses: dict[tuple[str, ...], object]):
    return lambda args: responses.get(tuple(args))


def _responses(root_response, *, root_id: str = ROOT, ready=None, blocked=None):
    return {
        ("show", root_id): root_response,
        ("ready", "--parent", root_id): [] if ready is None else ready,
        ("blocked", "--parent", root_id): [] if blocked is None else blocked,
    }


def test_closed_children_do_not_complete_in_progress_parent():
    """Regression: descendant drain is not proof that the root outcome ended."""
    responses = {
        ("show", "escapement-e3ai"): [{"id": "escapement-e3ai", "status": "in_progress"}],
        ("ready", "--parent", "escapement-e3ai"): [],
        ("blocked", "--parent", "escapement-e3ai"): [],
    }
    decision = _check_task_scope(_mode("escapement-e3ai"), _runner(responses))
    assert decision == ("block", "parent_outcome_unresolved")


@pytest.mark.parametrize("root_id", ["escapement-e3ai", "cake-parent-9"])
def test_open_requested_root_blocks_for_its_own_canonical_status(root_id):
    """Reject hardcoded roots: the requested Beads id controls completion."""
    calls: list[list[str]] = []
    responses = _responses(
        [{"id": root_id, "status": "in_progress"}], root_id=root_id
    )

    def run_bd(args):
        calls.append(args)
        return responses.get(tuple(args))

    assert _check_task_scope(_mode(root_id), run_bd) == ("block", "parent_outcome_unresolved")
    assert ["show", root_id] in calls


def test_closed_root_and_empty_descendants_drain():
    """Positive control: verified root closure keeps the real clean-drain path."""
    responses = _responses([{"id": ROOT, "status": "closed"}])
    assert _check_task_scope(_mode(ROOT), _runner(responses)) == ("allow", "queue_drained")


def test_closed_foreign_root_does_not_complete_requested_root():
    """A closed record for another bead is not closure evidence for this root."""
    responses = _responses([{"id": "foreign-parent", "status": "closed"}])
    assert _check_task_scope(_mode(ROOT), _runner(responses)) == (
        "block",
        "parent_outcome_unresolved",
    )


@pytest.mark.parametrize(
    "root_response",
    [
        [],
        [{"id": ROOT, "status": None}],
        [{"id": ROOT, "status": "open"}],
        [{"id": ROOT, "status": "blocked"}],
        [{"id": ROOT, "status": "deferred"}],
        None,
    ],
    ids=["missing", "malformed", "open", "blocked", "deferred", "show-failed"],
)
def test_unresolved_root_never_drains(root_response, tmp_path):
    """Negative controls reject missing, malformed, nonterminal, and failed show."""
    (tmp_path / ".beads").mkdir()
    responses = _responses(root_response)
    assert _check_task_scope(_mode(ROOT, str(tmp_path)), _runner(responses)) == (
        "block",
        "parent_outcome_unresolved",
    )


def test_compatibility_alias_queries_root_before_allowing_drain(tmp_path):
    """The public alias must not bypass the extracted root-state query."""
    calls: list[list[str]] = []
    responses = _responses([{"id": ROOT, "status": "closed"}])

    def run_bd(args):
        calls.append(args)
        return responses.get(tuple(args))

    assert stop_hook._check_task_mode_queue(_mode(ROOT, str(tmp_path)), run_bd=run_bd) == (
        "allow",
        "queue_drained",
    )
    assert ["show", ROOT] in calls


def _write_fake_bd(tmp_path: pathlib.Path, root_status: str) -> pathlib.Path:
    """Create a real executable boundary for stop_hook.main()'s subprocess path."""
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    script = fakebin / "bd"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = tuple(arg for arg in sys.argv[1:] if arg != '--json')\n"
        "if args[:1] == ('show',):\n"
        f"    print(json.dumps([{{'id': args[1], 'status': {root_status!r}}}]))\n"
        "elif args[:1] in (('ready',), ('blocked',)):\n"
        "    print('[]')\n"
        "else:\n"
        "    raise SystemExit(1)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return fakebin


def _run_stop(
    monkeypatch,
    capsys,
    tmp_path: pathlib.Path,
    *,
    scheduled: list[dict],
    root_status: str = "in_progress",
) -> str:
    session_id = "parent-outcome-session"
    root = tmp_path / "harness"
    repo_cwd = tmp_path / "repo"
    repo_cwd.mkdir()
    (repo_cwd / ".beads").mkdir()
    thread_dir = root / "threads" / session_id
    thread_dir.mkdir(parents=True)
    (thread_dir / "session_mode.json").write_text(
        json.dumps(_mode(ROOT, str(repo_cwd))), encoding="utf-8"
    )
    (thread_dir / "scheduled.json").write_text(json.dumps(scheduled), encoding="utf-8")
    fakebin = _write_fake_bd(tmp_path, root_status)
    monkeypatch.setattr(stop_hook, "HARNESS_ROOT", root)
    monkeypatch.setattr(stop_hook.session_isolation, "write_checkout", lambda *args: None)
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(stop_hook.sys, "stdin", __import__("io").StringIO(
        json.dumps({"session_id": session_id, "transcript_path": ""})
    ))

    assert stop_hook.main() == 0
    return capsys.readouterr().out


@pytest.mark.parametrize(
    "scheduled",
    [
        [],
        [{"wake_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()}],
        [{
            "wake_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
            "supervisor_health": {"last_successful_reconcile_at": "2000-01-01T00:00:00+00:00"},
        }],
    ],
    ids=["no-wake", "future-wake-missing-health", "future-wake-stale-health"],
)
def test_public_stop_blocks_open_root_even_when_wakeup_cannot_prove_recovery(
    monkeypatch, capsys, tmp_path, scheduled
):
    """Public wiring must not let a future wake bypass an unresolved root outcome."""
    output = _run_stop(monkeypatch, capsys, tmp_path, scheduled=scheduled)
    assert "parent_outcome_unresolved" in output


def test_public_stop_allows_closed_root_with_empty_descendants(monkeypatch, capsys, tmp_path):
    """Positive public control: root proof is not an always-block Stop gate."""
    output = _run_stop(
        monkeypatch,
        capsys,
        tmp_path,
        scheduled=[],
        root_status="closed",
    )
    assert output == ""
