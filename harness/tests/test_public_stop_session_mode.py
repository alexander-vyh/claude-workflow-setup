#!/usr/bin/env python3
"""Public Stop controls for untrusted session_mode, and the stop_hook waiver.

Business outcome
----------------
A session must not finish its turn on the strength of a task-mode record another
local user could have written. These three controls came from the Task 5
production review; they were kept when the delegated-execution ledger was
removed because their subject -- the public ``stop_hook`` entrypoint -- survives.

Independent source of truth
---------------------------
``stop_hook.main()`` driven end to end over real stdin with a real fake ``bd`` on
PATH. Not the gate function, not a mock of it.

Invalid solution classes rejected here
--------------------------------------
- reading session_mode.json without a trust check -> each ``invalid_kind``
  variant (malformed / world-writable / symlink) must leave the planted
  ``repo_cwd`` unused, so no Beads call runs against an attacker's repository
- letting an untrusted record suppress the user's own release -> the release
  test asserts the turn still ends when the user says stop

Deliberately NOT asserted: that an untrusted record blocks. Untrusted state
alone must not be able to freeze a session -- the same line
harness/tests/test_task_mode_scope.py holds for task_mode_incident.json.
Before the delegated-execution ledger was removed a block did occur here, but it
came from the ledger's trusted-evidence rule, not from a session_mode policy.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
BIN = REPO / "harness" / "bin"
sys.path.insert(0, str(BIN))

import stop_hook  # noqa: E402

SESSION = "incident-parent-session"
ROOT_BEAD = "escapement-e3ai"


def _write_fake_bd(tmp_path: pathlib.Path, root_status: str) -> pathlib.Path:
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    script = fakebin / "bd"
    cwd_log = tmp_path / "bd-cwds.txt"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(cwd_log)!r}, 'a').write(os.getcwd() + chr(10))\n"
        "args = tuple(a for a in sys.argv[1:] if a != '--json')\n"
        "if args[:1] == ('show',):\n"
        f"    print(json.dumps([{{'id': {ROOT_BEAD!r}, 'status': {root_status!r}}}]))\n"
        "elif args[:1] in (('ready',), ('blocked',)):\n"
        "    print('[]')\n"
        "else:\n"
        "    raise SystemExit(1)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return fakebin


def _public_stop_with_session_mode(
    monkeypatch,
    capsys,
    tmp_path: pathlib.Path,
    *,
    invalid_kind: str,
    recent_user_message: str | None = None,
) -> str:
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / ".beads").mkdir()
    thread_dir = root / "threads" / SESSION
    thread_dir.mkdir(parents=True)
    mode = {
        "mode": "task",
        "session_id": SESSION,
        "repo_cwd": str(repo),
        "parent_id": ROOT_BEAD,
    }
    mode_path = thread_dir / "session_mode.json"
    if invalid_kind == "malformed":
        mode_path.write_text("{malformed", encoding="utf-8")
    elif invalid_kind == "world-writable":
        mode_path.write_text(json.dumps(mode), encoding="utf-8")
        mode_path.chmod(0o666)
    else:
        target = tmp_path / "redirected-session-mode.json"
        target.write_text(json.dumps(mode), encoding="utf-8")
        target.chmod(0o600)
        mode_path.symlink_to(target)
    (thread_dir / "scheduled.json").write_text("[]", encoding="utf-8")

    fakebin = _write_fake_bd(tmp_path, "closed")
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(stop_hook, "HARNESS_ROOT", root)
    monkeypatch.setattr(stop_hook, "INCIDENTS_LOG", root / "incidents.jsonl")
    monkeypatch.setattr(stop_hook.session_isolation, "write_checkout", lambda *a: None)

    transcript_path = ""
    if recent_user_message is not None:
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": recent_user_message},
                }
            ),
            encoding="utf-8",
        )
        transcript_path = str(transcript)
    monkeypatch.setattr(
        stop_hook.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": SESSION, "transcript_path": transcript_path})),
    )
    assert stop_hook.main() == 0
    return capsys.readouterr().out


@pytest.mark.parametrize("invalid_kind", ["malformed", "world-writable", "symlink"])
def test_untrusted_session_mode_is_never_adopted_at_public_stop(
    monkeypatch, capsys, tmp_path, invalid_kind
):
    """The planted repo_cwd must never become the repository Beads runs in.

    Fragile implementation this rejects: json.loads on session_mode.json with no
    ownership/mode/symlink check. That adopts the record, and every Beads call in
    the turn then runs inside a repository the attacker chose.
    """
    _public_stop_with_session_mode(
        monkeypatch, capsys, tmp_path, invalid_kind=invalid_kind
    )
    planted_repo = str((tmp_path / "repo").resolve())
    cwd_log = tmp_path / "bd-cwds.txt"
    ran_in = cwd_log.read_text().split() if cwd_log.exists() else []
    assert planted_repo not in ran_in, (
        f"an untrusted session_mode was adopted; bd ran in {planted_repo}"
    )


@pytest.mark.parametrize("invalid_kind", ["malformed", "world-writable", "symlink"])
def test_user_release_remains_unconditional_with_invalid_session_mode(
    monkeypatch, capsys, tmp_path, invalid_kind
):
    """Positive control: an untrusted record must not trap the user in a turn."""
    output = _public_stop_with_session_mode(
        monkeypatch,
        capsys,
        tmp_path,
        invalid_kind=invalid_kind,
        recent_user_message="stop",
    )
    assert output == ""


def test_stop_hook_complexity_waiver_is_current_and_adapter_wiring_stays_thin():
    """The waiver must describe the file as it is, not as it once was."""
    source = (BIN / "stop_hook.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    waiver = next((line for line in lines[:5] if "file-complexity-waiver:" in line), None)
    assert waiver is not None
    assert f"{len(lines)} lines" in waiver
    assert "execution_stop_adapter.py" in waiver
    assert source.count("decide_task_mode") == 2
