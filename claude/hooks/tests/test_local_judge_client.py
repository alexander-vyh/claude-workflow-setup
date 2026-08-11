"""Tests for the shared local OpenAI-compatible judge client."""

import ast
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_hooks_dir = Path(__file__).resolve().parent.parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

import _local_judge_client as lj  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_BIN = REPO_ROOT / "harness" / "bin"
if str(HARNESS_BIN) not in sys.path:
    sys.path.insert(0, str(HARNESS_BIN))

import execution_ledger  # noqa: E402


class _ChatHandler(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(size).decode("utf-8")
        self.__class__.requests.append((self.path, json.loads(body)))
        payload = {"choices": [{"message": {"content": "stop_solicitation"}}]}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):  # noqa: A002
        return None


class _UnauthorizedHandler(BaseHTTPRequestHandler):
    authorization = None
    request_count = 0

    def do_POST(self):  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(size)
        self.__class__.authorization = self.headers.get("Authorization")
        self.__class__.request_count += 1
        self.send_response(401)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        return None


def _call_loopback(handler, callback):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        return callback(base_url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _HTTPResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        payload = {"choices": [{"message": {"content": "stop_solicitation"}}]}
        return json.dumps(payload).encode("utf-8")


def _capture_urlopen(monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append(
            (
                request.full_url,
                json.loads(request.data.decode("utf-8")),
                request.get_header("Authorization"),
                timeout,
            )
        )
        return _HTTPResponse()

    monkeypatch.setattr(lj, "urlopen", urlopen)
    return requests


def test_boolean_verdict_uses_configured_openai_compatible_contract(monkeypatch):
    monkeypatch.setenv(
        "ESCAPEMENT_LOCAL_JUDGE_BASE_URL", "http://127.0.0.1:8123/custom/v1/"
    )
    monkeypatch.setenv("ESCAPEMENT_LOCAL_JUDGE_MODEL", "judge-model")
    monkeypatch.setenv("ESCAPEMENT_LOCAL_JUDGE_TIMEOUT", "2.5")
    calls = []

    def post(url, payload, timeout):
        calls.append((url, payload, timeout))
        return "stop_solicitation"

    verdict = lj.boolean_verdict(
        "I can hand this back here; say the word and I will proceed.",
        system_prompt="label the message",
        positive_labels=("stop_solicitation",),
        negative_labels=("not_stop_solicitation",),
        post=post,
    )

    assert verdict is True
    assert calls == [
        (
            "http://127.0.0.1:8123/custom/v1/chat/completions",
            {
                "model": "judge-model",
                "messages": [
                    {"role": "system", "content": "label the message"},
                    {
                        "role": "user",
                        "content": "I can hand this back here; say the word and I will proceed.",
                    },
                ],
                "max_tokens": 32,
                "enable_thinking": False,
                "temperature": 0,
            },
            2.5,
        )
    ]


def test_boolean_verdict_checks_negative_label_before_positive_substring():
    verdict = lj.boolean_verdict(
        "want me to wrap for the night, or keep going?",
        system_prompt="label the message",
        positive_labels=("stop_solicitation",),
        negative_labels=("not_stop_solicitation",),
        post=lambda url, payload, timeout: "not_stop_solicitation",
    )

    assert verdict is False


def test_boolean_verdict_fails_open_on_transport_error():
    def boom(url, payload, timeout):
        raise TimeoutError("model server down")

    assert (
        lj.boolean_verdict(
            "I can stop here.",
            system_prompt="label the message",
            positive_labels=("stop_solicitation",),
            negative_labels=("not_stop_solicitation",),
            post=boom,
        )
        is None
    )


def test_boolean_verdict_fails_open_on_unclear_response():
    assert (
        lj.boolean_verdict(
            "I can stop here.",
            system_prompt="label the message",
            positive_labels=("stop_solicitation",),
            negative_labels=("not_stop_solicitation",),
            post=lambda url, payload, timeout: "unclear",
        )
        is None
    )


def test_default_post_parses_real_openai_compatible_http_response():
    _ChatHandler.requests = []
    verdict = _call_loopback(
        _ChatHandler,
        lambda base_url: lj.boolean_verdict(
            "I can hand this back here; say the word and I will proceed.",
            system_prompt="label the message",
            positive_labels=("stop_solicitation",),
            negative_labels=("not_stop_solicitation",),
            base_url=base_url,
            model="fake-local-model",
            timeout=2,
        ),
    )

    assert verdict is True
    assert _ChatHandler.requests == [
        (
            "/v1/chat/completions",
            {
                "model": "fake-local-model",
                "messages": [
                    {"role": "system", "content": "label the message"},
                    {
                        "role": "user",
                        "content": "I can hand this back here; say the word and I will proceed.",
                    },
                ],
                "max_tokens": 32,
                "enable_thinking": False,
                "temperature": 0,
            },
        )
    ]


def _observed_auth(monkeypatch, *, key=None, key_file=None):
    monkeypatch.delenv("ESCAPEMENT_LOCAL_JUDGE_API_KEY", raising=False)
    monkeypatch.delenv("ESCAPEMENT_LOCAL_JUDGE_API_KEY_FILE", raising=False)
    if key is not None:
        monkeypatch.setenv("ESCAPEMENT_LOCAL_JUDGE_API_KEY", key)
    if key_file is not None:
        monkeypatch.setenv("ESCAPEMENT_LOCAL_JUDGE_API_KEY_FILE", str(key_file))

    requests = _capture_urlopen(monkeypatch)
    verdict = lj.boolean_verdict(
        "authorization probe",
        system_prompt="Answer only ready or broken.",
        positive_labels=("stop_solicitation",),
        negative_labels=("not_stop_solicitation",),
        base_url="http://judge.invalid/v1",
        model="fake-local-model",
        timeout=2,
    )
    assert verdict is True
    assert len(requests) == 1
    return requests[0][2]


def test_real_request_uses_bearer_auth_from_environment(monkeypatch):
    assert _observed_auth(monkeypatch, key="environment-secret") == (
        "Bearer environment-secret"
    )


def test_real_request_uses_bearer_auth_from_mode_0600_file(monkeypatch, tmp_path):
    key_file = tmp_path / "judge.key"
    key_file.write_text("file-secret\n", encoding="utf-8")
    key_file.chmod(0o600)

    assert _observed_auth(monkeypatch, key_file=key_file) == "Bearer file-secret"


def test_environment_auth_takes_precedence_over_mode_0600_file(monkeypatch, tmp_path):
    key_file = tmp_path / "judge.key"
    key_file.write_text("file-secret\n", encoding="utf-8")
    key_file.chmod(0o600)

    assert (
        _observed_auth(
            monkeypatch,
            key="environment-secret",
            key_file=key_file,
        )
        == "Bearer environment-secret"
    )


def test_real_request_is_unauthenticated_by_default(monkeypatch):
    assert _observed_auth(monkeypatch) is None


@pytest.mark.parametrize(
    "mode",
    [
        0o000,
        0o100,
        0o200,
        0o400,
        0o500,
        0o601,
        0o602,
        0o604,
        0o610,
        0o620,
        0o640,
        0o644,
        0o660,
        0o700,
    ],
)
def test_auth_file_without_exact_mode_0600_fails_closed(monkeypatch, tmp_path, mode):
    key_file = tmp_path / "judge.key"
    key_file.write_text("insecure-secret\n", encoding="utf-8")
    key_file.chmod(mode)

    assert _observed_auth(monkeypatch, key_file=key_file) is None


def test_symlink_auth_file_fails_closed_even_when_target_is_mode_0600(
    monkeypatch, tmp_path
):
    target = tmp_path / "target.key"
    target.write_text("target-secret\n", encoding="utf-8")
    target.chmod(0o600)
    key_file = tmp_path / "judge.key"
    key_file.symlink_to(target)

    assert _observed_auth(monkeypatch, key_file=key_file) is None


def test_wrong_owner_auth_file_fails_closed_without_sending_header(
    monkeypatch, tmp_path
):
    key_file = tmp_path / "judge.key"
    key_file.write_text("wrong-owner-secret\n", encoding="utf-8")
    key_file.chmod(0o600)

    # Exercise the public request boundary while presenting this process as a
    # different effective owner.  The real file and Request stay intact; only
    # the caller identity changes.
    actual_uid = os.getuid()
    monkeypatch.setattr(lj.os, "getuid", lambda: actual_uid + 1)

    assert _observed_auth(monkeypatch, key_file=key_file) is None


def test_multiline_mode_0600_auth_file_fails_closed(monkeypatch, tmp_path):
    key_file = tmp_path / "judge.key"
    key_file.write_text("first-secret\nsecond-secret\n", encoding="utf-8")
    key_file.chmod(0o600)

    assert _observed_auth(monkeypatch, key_file=key_file) is None


def _subprocess_observed_auth(key_file):
    code = f"""
import json
import sys
sys.path.insert(0, {str(_hooks_dir)!r})
import _local_judge_client as lj

captured = []
class Response:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self):
        return b'{{"choices":[{{"message":{{"content":"stop_solicitation"}}}}]}}'

def urlopen(request, timeout):
    captured.append(request.get_header("Authorization"))
    return Response()

lj.urlopen = urlopen
verdict = lj.boolean_verdict(
    "authorization probe",
    system_prompt="label",
    positive_labels=("stop_solicitation",),
    negative_labels=("not_stop_solicitation",),
    base_url="http://judge.invalid/v1",
    timeout=1,
)
assert verdict is True
print(json.dumps(captured[0]))
"""
    environment = os.environ.copy()
    environment.pop("ESCAPEMENT_LOCAL_JUDGE_API_KEY", None)
    environment["ESCAPEMENT_LOCAL_JUDGE_API_KEY_FILE"] = str(key_file)
    environment["PYTHONPATH"] = ""
    environment["PYTHONNOUSERSITE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        timeout=2,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_fifo_auth_file_fails_closed_without_blocking(tmp_path):
    key_file = tmp_path / "judge.fifo"
    os.mkfifo(key_file, 0o600)

    assert _subprocess_observed_auth(key_file) is None


def test_unix_socket_auth_file_fails_closed_without_blocking():
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="esc-judge-") as directory:
        key_file = Path(directory) / "judge.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(key_file))
            assert _subprocess_observed_auth(key_file) is None
        finally:
            listener.close()


@pytest.mark.parametrize("kind", ["missing", "empty", "directory"])
def test_unresolved_auth_file_fails_closed(monkeypatch, tmp_path, kind):
    key_file = tmp_path / "judge.key"
    if kind == "empty":
        key_file.write_text("\n", encoding="utf-8")
        key_file.chmod(0o600)
    elif kind == "directory":
        key_file.mkdir()
        key_file.chmod(0o600)

    assert _observed_auth(monkeypatch, key_file=key_file) is None


def test_http_401_reports_optional_judge_unavailable_without_raising(monkeypatch):
    monkeypatch.setenv("ESCAPEMENT_LOCAL_JUDGE_API_KEY", "wrong-secret")
    _UnauthorizedHandler.authorization = None
    _UnauthorizedHandler.request_count = 0
    result = _call_loopback(
        _UnauthorizedHandler,
        lambda base_url: lj.health_check(
            base_url=base_url,
            model="fake-local-model",
            timeout=2,
        ),
    )

    assert _UnauthorizedHandler.authorization == "Bearer wrong-secret"
    assert result["ok"] is False
    assert result["base_url"].startswith("http://127.0.0.1:")
    assert result["model"] == "fake-local-model"
    assert result["reason"] == "unavailable"


@pytest.mark.parametrize("entrypoint", ["execution_supervisor.py", "wakeup_waker.py"])
def test_deterministic_reconcilers_do_not_import_optional_local_judge(entrypoint):
    source_path = REPO_ROOT / "harness" / "bin" / entrypoint
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    assert not any("local_judge" in name for name in imported)
    assert "_local_judge_client" not in source


def test_public_waker_recovers_due_work_during_judge_401(monkeypatch, tmp_path):
    monkeypatch.setenv("ESCAPEMENT_LOCAL_JUDGE_API_KEY", "wrong-secret")
    _UnauthorizedHandler.authorization = None
    _UnauthorizedHandler.request_count = 0
    session_id = "judge-outage-recovery-session"
    child_bead = "escapement-judge-child"
    parent_bead = "escapement-judge-parent"
    execution_id = "exec-judge-outage"
    threads_root = tmp_path / "harness" / "threads"
    thread_dir = threads_root / session_id
    repo_cwd = tmp_path / "canonical-repo"
    repo_cwd.mkdir()

    ledger = execution_ledger.new_ledger(session_id)
    execution_ledger.register_execution(
        ledger,
        {
            "kind": "dispatch_registered",
            "parent_session_id": session_id,
            "bead_id": child_bead,
            "execution_id": execution_id,
            "host": "claude",
            "agent_name": "judge-outage-worker",
            "dispatch_tool_use_id": "tool-judge-outage",
            "watchdog_id": "watch-judge-outage",
            "attempt": 1,
            "generation": 1,
        },
        dt.datetime(2000, 1, 1, 0, 0, tzinfo=dt.timezone.utc),
    )

    def write_trusted(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)

    ledger_path = thread_dir / "executions.json"
    write_trusted(ledger_path, ledger)
    write_trusted(
        thread_dir / "session_mode.json",
        {
            "mode": "task",
            "repo_cwd": str(repo_cwd),
            "task_id": child_bead,
            "parent_id": parent_bead,
            "session_id": session_id,
        },
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    bd_record = tmp_path / "bd-record.jsonl"
    spawn_record = tmp_path / "spawn-record.jsonl"
    fake_bd = fake_bin / "bd"
    fake_bd.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args = [arg for arg in sys.argv[1:] if arg != '--json']\n"
        "with pathlib.Path(os.environ['BD_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'args': args}) + '\\n')\n"
        f"if args == ['show', {child_bead!r}]:\n"
        f"    print(json.dumps([{{'id': {child_bead!r}, 'status': 'in_progress', "
        f"'parent': {parent_bead!r}}}]))\n"
        f"elif args == ['show', {parent_bead!r}]:\n"
        f"    print(json.dumps([{{'id': {parent_bead!r}, 'status': 'in_progress'}}]))\n"
        "else:\n"
        "    raise SystemExit(74)\n",
        encoding="utf-8",
    )
    fake_bd.chmod(0o755)
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "with pathlib.Path(os.environ['SPAWN_RECORD']).open('a') as handle:\n"
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}) + '\\n')\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    def probe_and_reconcile(base_url):
        advisory = lj.health_check(
            base_url=base_url,
            model="fake-local-model",
            timeout=2,
        )
        environment = os.environ.copy()
        environment["ESCAPEMENT_LOCAL_JUDGE_BASE_URL"] = base_url
        environment["PATH"] = f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
        environment["BD_RECORD"] = str(bd_record)
        environment["SPAWN_RECORD"] = str(spawn_record)
        result = subprocess.run(
            [
                sys.executable,
                str(HARNESS_BIN / "wakeup_waker.py"),
                "--threads-root",
                str(threads_root),
                "--fire",
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
        )
        return advisory, result

    advisory, result = _call_loopback(_UnauthorizedHandler, probe_and_reconcile)

    assert advisory["ok"] is False
    assert advisory["reason"] == "unavailable"
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("FIRED: 0 spawn(s) planned")
    deadline = time.monotonic() + 3
    while not spawn_record.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    spawned = [json.loads(line) for line in spawn_record.read_text().splitlines()]
    assert len(spawned) == 1
    assert spawned[0]["cwd"] == str(repo_cwd)
    assert spawned[0]["argv"][:3] == ["--resume", session_id, "-p"]
    prompt = spawned[0]["argv"][3]
    assert execution_id in prompt
    assert child_bead in prompt
    assert parent_bead in prompt
    bd_calls = [json.loads(line) for line in bd_record.read_text().splitlines()]
    assert bd_calls == [
        {"cwd": str(repo_cwd), "args": ["show", child_bead]},
        {"cwd": str(repo_cwd), "args": ["show", parent_bead]},
    ]
    durable = json.loads(ledger_path.read_text(encoding="utf-8"))
    execution = durable["executions"][0]
    assert execution["reconcile_due"] == "hard"
    assert execution["recovery_claim"]["owner"].startswith("wakeup-waker:")
    assert execution["recovery_claim"]["execution_id"] == execution_id
    assert execution["recovery_claim"]["attempt"] == 1
    assert execution["recovery_claim"]["generation"] == 1
    health = json.loads(
        (threads_root.parent / "supervisor-health.json").read_text(encoding="utf-8")
    )
    assert health["completed_generation"] == 1
    assert health["last_successful_reconcile_at"] is not None
    assert health["counts"]["threads"] == 1
    assert health["counts"]["recoveries"] == 1
    assert _UnauthorizedHandler.request_count == 1


def test_health_check_reports_unavailable_without_raising():
    def boom(url, payload, timeout):
        raise ConnectionRefusedError("no listener")

    result = lj.health_check(post=boom)

    assert result["ok"] is False
    assert result["base_url"] == lj.DEFAULT_BASE_URL
    assert result["model"] == lj.DEFAULT_MODEL
    assert result["reason"] == "unavailable"
