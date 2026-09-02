import fcntl
import json
import pathlib
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import continuation_watchdog as cw  # noqa: E402
import session_observer as so  # noqa: E402


def _candidate(tmp_path, host="claude", session="s", terminal="turn", completed=0):
    transcript = tmp_path / f"{host}-{session}.jsonl"
    transcript.write_text("{}\n")
    return so.Candidate(
        host, session, terminal, transcript, str(tmp_path), "Finish it end to end.",
        "The remaining step is deployment.", completed,
    )


def _discover(candidates):
    return lambda *_args, **_kwargs: candidates


def _initialized(root, records=None):
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({
        "version": 1, "initialized": True, "deliveries": records or {},
    }))


def test_first_pass_baselines_existing_sessions_without_injecting(tmp_path):
    root = tmp_path / "state"
    candidate = _candidate(tmp_path)
    launched = []
    result = cw.run_once(
        root, now=1000, discoverer=_discover([candidate]),
        launcher=lambda *args: launched.append(args) or True,
    )
    assert result["initialized"] is True
    assert launched == []
    state = json.loads((root / "state.json").read_text())
    assert state["deliveries"][candidate.delivery_key]["phase"] == "baseline"


def test_malformed_state_is_safely_rebaselined_without_launch(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    (root / "state.json").write_text('{"initialized":true,"deliveries":[]}')
    candidate = _candidate(tmp_path)
    launched = []
    result = cw.run_once(
        root, now=1000, discoverer=_discover([candidate]),
        judge=lambda *_a, **_k: True,
        launcher=lambda *args: launched.append(args) or True,
    )
    assert result["initialized"] is True
    assert launched == []
    state = json.loads((root / "state.json").read_text())
    assert state["deliveries"][candidate.delivery_key]["phase"] == "baseline"


def test_each_malformed_state_scalar_is_safely_rebaselined(tmp_path):
    invalid_states = [
        {"version": 1, "initialized": True, "last_scan_started": "garbage", "deliveries": {}},
        {"version": 1, "initialized": True, "deliveries": {"claude:s:t": {"phase": []}}},
        {"version": 1, "initialized": True, "deliveries": {
            "claude:s:t": {"phase": "launching", "lease_until": "garbage"},
        }},
    ]
    for index, invalid in enumerate(invalid_states):
        root = tmp_path / f"state-{index}"
        root.mkdir()
        (root / "state.json").write_text(json.dumps(invalid))
        candidate = _candidate(tmp_path, session=f"candidate-{index}")
        result = cw.run_once(root, now=1000, discoverer=_discover([candidate]))
        assert result["initialized"] is True
        state = json.loads((root / "state.json").read_text())
        assert state["deliveries"] == {
            candidate.delivery_key: {"phase": "baseline", "seen_at": 1000}
        }


def test_later_pass_scans_only_files_changed_since_prior_pass(tmp_path):
    root = tmp_path / "state"
    seen = []

    def discover(*_args, modified_since):
        seen.append(modified_since)
        return []

    cw.run_once(root, now=1000, discoverer=discover)
    cw.run_once(root, now=1060, discoverer=discover, active_claude_ids=lambda: set())
    assert seen[0] == 1000 - cw.DISCOVERY_LOOKBACK_SECONDS - 1
    assert seen[1] == 999


def test_unchanged_young_terminal_is_revisited_after_quiet_window(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    candidate = _candidate(tmp_path, completed=950)
    monkeypatch.setattr(cw, "_candidate_from_record", lambda _record: candidate)
    monkeypatch.setattr(cw, "_still_current", lambda value: value)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    launched = []
    cw.run_once(
        root, now=1000, discoverer=_discover([candidate]),
        judge=lambda *_a, **_k: True,
        launcher=lambda *args: launched.append(args) or True,
        active_claude_ids=lambda: set(),
    )
    assert launched == []
    cw.run_once(
        root, now=1000 + cw.QUIET_SECONDS + 1, discoverer=_discover([]),
        judge=lambda *_a, **_k: True,
        launcher=lambda *args: launched.append(args) or True,
        active_claude_ids=lambda: set(),
    )
    assert len(launched) == 1


def test_unchanged_terminal_survives_judge_backoff_and_failed_launch(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(cw, "_candidate_from_record", lambda _record: candidate)
    monkeypatch.setattr(cw, "_still_current", lambda value: value)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    launches = []
    cw.run_once(
        root, now=1000, discoverer=_discover([candidate]),
        judge=lambda *_a, **_k: None, active_claude_ids=lambda: set(),
    )
    cw.run_once(
        root, now=1000 + cw.EVALUATION_BACKOFF_SECONDS + 1, discoverer=_discover([]),
        judge=lambda *_a, **_k: True,
        launcher=lambda *args: launches.append(args) or False,
        active_claude_ids=lambda: set(),
    )
    cw.run_once(
        root, now=1000 + cw.EVALUATION_BACKOFF_SECONDS + cw.LAUNCH_GRACE_SECONDS + 2,
        discoverer=_discover([]), judge=lambda *_a, **_k: True,
        launcher=lambda *args: launches.append(args) or True,
        active_claude_ids=lambda: set(),
    )
    assert len(launches) == 2


def test_positive_controls_route_claude_resume_and_active_codex_queue(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    claude = _candidate(tmp_path, "claude", "same", "one")
    codex = _candidate(tmp_path, "codex", "same", "one")
    monkeypatch.setattr(cw, "_still_current", lambda candidate: candidate)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    launches = []
    result = cw.run_once(
        root, now=1000, discoverer=_discover([claude, codex]),
        judge=lambda *_a, **_k: True,
        launcher=lambda candidate, prompt, active: launches.append((candidate, prompt, active)) or True,
        active_claude_ids=lambda: set(), codex_writer_active=lambda _sid: True,
    )
    assert result == {
        "initialized": False, "launched": 2, "evaluated": 2,
        "deferred": 0, "exhausted": 0,
    }
    assert [(item[0].host, item[2]) for item in launches] == [("claude", False), ("codex", True)]
    assert launches[0][1] != launches[1][1]
    assert cw.marker_for(claude.delivery_key) in launches[0][1]


def test_negative_and_unclear_verdicts_never_consume_host_attempts(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    negative = _candidate(tmp_path, session="negative")
    unclear = _candidate(tmp_path, session="unclear")
    monkeypatch.setattr(cw, "_still_current", lambda candidate: candidate)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    verdicts = iter((False, None))
    cw.run_once(
        root, now=1000, discoverer=_discover([negative, unclear]),
        judge=lambda *_a, **_k: next(verdicts),
        launcher=lambda *_: (_ for _ in ()).throw(AssertionError("must not launch")),
        active_claude_ids=lambda: set(),
    )
    state = json.loads((root / "state.json").read_text())["deliveries"]
    assert state[negative.delivery_key]["phase"] == "not_needed"
    assert state[negative.delivery_key].get("attempts", 0) == 0
    assert state[unclear.delivery_key]["phase"] == "pending"
    assert state[unclear.delivery_key]["evaluation_failures"] == 1
    assert state[unclear.delivery_key].get("attempts", 0) == 0


def test_unclear_evaluation_backoff_is_separate_from_launch_budget(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    calls = []
    kwargs = dict(
        discoverer=_discover([candidate]), judge=lambda *_a, **_k: calls.append(1),
        launcher=lambda *_: True, active_claude_ids=lambda: set(),
    )
    cw.run_once(root, now=1000, **kwargs)
    cw.run_once(root, now=1100, **kwargs)
    cw.run_once(root, now=1301, **kwargs)
    record = json.loads((root / "state.json").read_text())["deliveries"][candidate.delivery_key]
    assert len(calls) == 2
    assert record["evaluation_failures"] == 2
    assert record.get("attempts", 0) == 0


def test_toctou_reparse_blocks_launch_after_a_later_turn(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(cw, "_still_current", lambda _candidate: None)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    launched = []
    cw.run_once(
        root, now=1000, discoverer=_discover([candidate]), judge=lambda *_a, **_k: True,
        launcher=lambda *args: launched.append(args) or True, active_claude_ids=lambda: set(),
    )
    assert launched == []


def test_claude_active_state_is_rechecked_after_the_judge(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(cw, "_still_current", lambda value: value)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    active = set()
    launched = []

    def judge(*_args, **_kwargs):
        active.add(candidate.session_id)
        return True

    cw.run_once(
        root, now=1000, discoverer=_discover([candidate]), judge=judge,
        launcher=lambda *args: launched.append(args) or True,
        active_claude_ids=lambda: set(active),
    )
    assert launched == []
    record = json.loads((root / "state.json").read_text())["deliveries"][candidate.delivery_key]
    assert record.get("attempts", 0) == 0


def test_claude_activity_probe_fails_closed_on_cli_error_or_schema_drift(monkeypatch):
    for returncode, stdout in ((1, ""), (0, "{}")):
        monkeypatch.setattr(
            cw.subprocess, "run",
            lambda *_a, _code=returncode, _out=stdout, **_k: type(
                "Result", (), {"returncode": _code, "stdout": _out}
            )(),
        )
        assert cw._active_claude_ids() is None


def test_marker_reconciliation_prevents_duplicate_after_ambiguous_launch(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(cw, "_still_current", lambda value: value)
    marker_present = {"value": False}
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: marker_present["value"])
    launches = []
    kwargs = dict(
        discoverer=_discover([candidate]), judge=lambda *_a, **_k: True,
        launcher=lambda *args: launches.append(args) or True,
        active_claude_ids=lambda: set(),
    )
    cw.run_once(root, now=1000, **kwargs)
    marker_present["value"] = True
    monkeypatch.setattr(cw, "PROMPT_VERSION", 999)
    cw.run_once(root, now=1000 + cw.LAUNCH_GRACE_SECONDS + 1, **kwargs)
    assert len(launches) == 1
    record = json.loads((root / "state.json").read_text())["deliveries"][candidate.delivery_key]
    assert record["phase"] == "delivered"


def test_expired_lease_recovers_but_three_attempts_exhaust(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(cw, "_still_current", lambda value: value)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    root = tmp_path / "recover"
    _initialized(root, {candidate.delivery_key: {
        "phase": "launching", "attempts": 1, "lease_until": 900, "verdict": True,
    }})
    launches = []
    cw.run_once(
        root, now=1000, discoverer=_discover([candidate]),
        launcher=lambda *args: launches.append(args) or True,
        active_claude_ids=lambda: set(),
    )
    assert len(launches) == 1
    exhausted = tmp_path / "exhausted"
    _initialized(exhausted, {candidate.delivery_key: {
        "phase": "launched", "attempts": 3, "retry_after": 900, "verdict": True,
    }})
    cw.run_once(
        exhausted, now=1000, discoverer=_discover([candidate]),
        launcher=lambda *args: launches.append(args) or True,
        active_claude_ids=lambda: set(),
    )
    record = json.loads((exhausted / "state.json").read_text())["deliveries"][candidate.delivery_key]
    assert record["phase"] == "exhausted" and len(launches) == 1


def test_global_lock_allows_only_one_supervisor(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    lock = (root / "watchdog.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = cw.run_once(root, now=1000, discoverer=_discover([]))
    finally:
        lock.close()
    assert result["locked"] is True


def test_evaluation_budget_defers_later_candidates_without_launch_attempts(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    candidates = [_candidate(tmp_path, session=f"s{index}") for index in range(3)]
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    result = cw.run_once(
        root, now=1000, discoverer=_discover(candidates),
        judge=lambda *_a, **_k: None, active_claude_ids=lambda: set(),
    )
    assert result["evaluated"] == cw.MAX_EVALUATIONS_PER_PASS
    assert result["deferred"] == 1
    state = json.loads((root / "state.json").read_text())["deliveries"]
    assert sum(record.get("attempts", 0) for record in state.values()) == 0


def test_old_deferred_candidate_runs_before_fresh_stream(tmp_path, monkeypatch):
    root = tmp_path / "state"
    old = _candidate(tmp_path, session="old")
    _initialized(root, {old.delivery_key: {
        "phase": "pending", "seen_at": 10, "host": old.host,
        "transcript": str(old.transcript), "next_evaluation_at": 900,
    }})
    fresh = [_candidate(tmp_path, session=f"fresh-{index}") for index in range(3)]
    monkeypatch.setattr(cw, "_candidate_from_record", lambda record: old)
    monkeypatch.setattr(cw, "_still_current", lambda value: value)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    judged = []

    def judge(_text, *, user_request):
        judged.append(user_request)
        return False

    cw.run_once(
        root, now=1000, discoverer=_discover(fresh), judge=judge,
        active_claude_ids=lambda: set(),
    )
    state = json.loads((root / "state.json").read_text())["deliveries"]
    assert state[old.delivery_key]["phase"] == "not_needed"
    assert len(judged) == cw.MAX_EVALUATIONS_PER_PASS


def test_launch_exception_is_bounded_to_candidate_and_later_candidate_runs(tmp_path, monkeypatch):
    root = tmp_path / "state"
    _initialized(root)
    first = _candidate(tmp_path, session="first")
    second = _candidate(tmp_path, session="second")
    monkeypatch.setattr(cw, "_still_current", lambda value: value)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    called = []

    def launch(candidate, _prompt, _active):
        called.append(candidate.session_id)
        if candidate is first:
            raise subprocess.TimeoutExpired("codex queue", 20)
        return True

    result = cw.run_once(
        root, now=1000, discoverer=_discover([first, second]),
        judge=lambda *_a, **_k: True, launcher=launch,
        active_claude_ids=lambda: set(), codex_writer_active=lambda _sid: True,
    )
    assert called == ["first", "second"]
    assert result["launched"] == 1


def test_secret_never_enters_state_or_continuation_prompt(tmp_path, monkeypatch):
    secret = "sentinel-local-judge-secret"
    monkeypatch.setenv("ESCAPEMENT_LOCAL_JUDGE_API_KEY", secret)
    root = tmp_path / "state"
    _initialized(root)
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(cw, "_still_current", lambda value: value)
    monkeypatch.setattr(cw.observer, "contains_marker", lambda *_: False)
    prompts = []
    cw.run_once(
        root, now=1000, discoverer=_discover([candidate]), judge=lambda *_a, **_k: True,
        launcher=lambda _c, prompt, _a: prompts.append(prompt) or True,
        active_claude_ids=lambda: set(),
    )
    assert secret not in (root / "state.json").read_text()
    assert all(secret not in prompt for prompt in prompts)


def test_default_codex_routing_uses_queue_for_writer_and_stdin_for_resume(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path, host="codex", session="thread")
    runs, popens = [], []

    class Input:
        def __init__(self):
            self.value = ""

        def write(self, value):
            self.value += value

        def close(self):
            pass

    class Process:
        def __init__(self):
            self.stdin = Input()

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("host", timeout)

    def run(argv, **kwargs):
        runs.append((argv, kwargs))
        return type("Result", (), {"returncode": 0})()

    def popen(argv, **kwargs):
        process = Process()
        popens.append((argv, kwargs, process))
        return process

    monkeypatch.setattr(cw.subprocess, "run", run)
    monkeypatch.setattr(cw.subprocess, "Popen", popen)
    prompt = cw.continuation_prompt(candidate)
    assert cw._launch(candidate, prompt, True) is True
    assert runs[0][0] == ["codex", "queue", "--thread", "thread", "--message", prompt]
    assert "ESCAPEMENT_LOCAL_JUDGE_API_KEY" not in runs[0][1]["env"]
    assert cw._launch(candidate, prompt, False) is True
    assert popens[0][0] == ["codex", "exec", "resume", "--json", "thread", "-"]
    assert prompt not in popens[0][0]
    assert popens[0][2].stdin.value == prompt
    assert "ESCAPEMENT_LOCAL_JUDGE_API_KEY" not in popens[0][1]["env"]


def test_default_launcher_rejects_immediate_nonzero_child(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path, host="claude")

    class Process:
        def wait(self, timeout):
            return 2

    monkeypatch.setattr(cw.subprocess, "Popen", lambda *_a, **_k: Process())
    assert cw._launch(candidate, "continue", False) is False
