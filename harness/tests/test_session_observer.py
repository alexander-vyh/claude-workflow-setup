import json
import pathlib
import sys

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import session_observer as so  # noqa: E402


def _write(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows))
    return path


def test_claude_uses_stop_summary_parent_and_real_human_request(tmp_path):
    path = _write(tmp_path / "claude.jsonl", [
        {"type": "user", "uuid": "u1", "origin": {"kind": "human", "promptSource": "typed"},
         "message": {"role": "user", "content": "Fix it end to end."}},
        {"type": "user", "uuid": "tool", "parentUuid": "u1", "toolUseResult": {"ok": True},
         "message": {"role": "user", "content": "tool output"}},
        {"type": "user", "uuid": "meta", "parentUuid": "tool", "isMeta": True,
         "message": {"role": "user", "content": "Stop hook feedback: keep going"}},
        {"type": "user", "uuid": "other", "message": {"role": "user", "content": "unrelated fork"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "meta", "cwd": str(tmp_path),
         "message": {"role": "assistant", "stop_reason": "end_turn",
                     "content": [{"type": "text", "text": "The next step is wiring Codex."}]}},
        {"type": "system", "subtype": "stop_hook_summary", "uuid": "s1",
         "parentUuid": "a1", "preventedContinuation": False,
         "timestamp": "2026-09-01T20:00:00Z"},
    ])
    candidate = so.parse_claude(path)
    assert candidate is not None
    assert (candidate.host, candidate.terminal_id) == ("claude", "a1")
    assert candidate.request == "Fix it end to end."
    assert candidate.response == "The next step is wiring Codex."
    assert candidate.cwd == str(tmp_path)


def test_claude_prefers_trusted_session_checkout_over_transcript_cwd(tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    worktree = tmp_path / "trusted-worktree"
    stale = tmp_path / "stale-primary"
    worktree.mkdir()
    stale.mkdir()
    checkout = harness / "threads" / "session" / "checkout.json"
    checkout.parent.mkdir(parents=True)
    checkout.write_text(json.dumps({
        "session_id": "session", "worktree_root": str(worktree),
    }))
    monkeypatch.setenv("CONTINUATION_HARNESS_HOME", str(harness))
    path = _write(tmp_path / "session.jsonl", [
        {"type": "user", "uuid": "u", "message": {"role": "user", "content": "fix"}},
        {"type": "assistant", "uuid": "a", "parentUuid": "u", "cwd": str(stale),
         "message": {"role": "assistant", "stop_reason": "end_turn", "content": "left"}},
        {"type": "system", "subtype": "stop_hook_summary", "parentUuid": "a",
         "preventedContinuation": False, "timestamp": "2026-09-01T20:00:00Z"},
    ])
    assert so.parse_claude(path).cwd == str(worktree)


def test_malformed_trusted_checkout_fails_only_its_session(tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    malformed = harness / "threads" / "bad" / "checkout.json"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("[]")
    monkeypatch.setenv("CONTINUATION_HARNESS_HOME", str(harness))

    def terminal(path, session):
        return _write(path / f"{session}.jsonl", [
            {"type": "user", "uuid": f"u-{session}", "message": {"role": "user", "content": "fix"}},
            {"type": "assistant", "uuid": f"a-{session}", "parentUuid": f"u-{session}", "cwd": str(tmp_path),
             "message": {"role": "assistant", "stop_reason": "end_turn", "content": "left"}},
            {"type": "system", "subtype": "stop_hook_summary", "parentUuid": f"a-{session}",
             "preventedContinuation": False, "timestamp": "2026-09-01T20:00:00Z"},
        ])

    claude_root = tmp_path / "claude"
    claude_root.mkdir()
    terminal(claude_root, "bad")
    terminal(claude_root, "good")
    assert so.parse_claude(claude_root / "bad.jsonl") is None
    found = so.discover(claude_root, tmp_path / "missing-codex", modified_since=0)
    assert [candidate.session_id for candidate in found] == ["good"]


def test_claude_rejects_later_human_turn_and_prevented_stop(tmp_path):
    base = [
        {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "fix"}},
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "stop_reason": "end_turn", "content": "later"}},
        {"type": "system", "subtype": "stop_hook_summary", "parentUuid": "a1",
         "preventedContinuation": False, "timestamp": "2026-09-01T20:00:00Z"},
    ]
    assert so.parse_claude(_write(tmp_path / "later.jsonl", base + [
        {"type": "user", "uuid": "u2", "message": {"role": "user", "content": "continue"}},
    ])) is None
    blocked = list(base)
    blocked[-1] = {**blocked[-1], "preventedContinuation": True}
    assert so.parse_claude(_write(tmp_path / "blocked.jsonl", blocked)) is None


def test_claude_meta_activity_after_stop_supersedes_old_terminal(tmp_path):
    rows = [
        {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "fix"}},
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "stop_reason": "end_turn", "content": "later"}},
        {"type": "system", "subtype": "stop_hook_summary", "parentUuid": "a1",
         "preventedContinuation": False, "timestamp": "2026-09-01T20:00:00Z"},
        {"type": "user", "uuid": "meta", "isMeta": True,
         "message": {"role": "user", "content": "# Autonomous loop check"}},
    ]
    assert so.parse_claude(_write(tmp_path / "meta-later.jsonl", rows)) is None


def test_codex_pairs_final_answer_with_completed_turn_and_latest_cwd(tmp_path):
    tid, turn = "thread-1", "turn-1"
    path = _write(tmp_path / "codex.jsonl", [
        {"type": "session_meta", "payload": {"id": tid, "cwd": "/old", "thread_source": "user"}},
        {"type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_id": tid}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn}},
        {"type": "turn_context", "payload": {"turn_id": turn, "cwd": str(tmp_path)}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "Finish the monitor."}]}},
        {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": turn,
         "item": {"type": "AgentMessage", "id": "msg-1", "phase": "final_answer",
                  "content": [{"type": "Text", "text": "The remaining work is deployment."}]}}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn,
         "completed_at": 1788312447}},
    ])
    candidate = so.parse_codex(path)
    assert candidate is not None
    assert (candidate.session_id, candidate.terminal_id) == (tid, turn)
    assert candidate.request == "Finish the monitor."
    assert candidate.response == "The remaining work is deployment."
    assert candidate.cwd == str(tmp_path)


def test_codex_rejects_active_or_aborted_latest_turn(tmp_path):
    base = [
        {"type": "session_meta", "payload": {"id": "t", "cwd": str(tmp_path), "thread_source": "user"}},
        {"type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_id": "t"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "one"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "one", "item": {
            "type": "AgentMessage", "phase": "final_answer", "content": [{"type": "Text", "text": "left"}]}}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "one", "completed_at": 1}},
    ]
    active = base + [{"type": "event_msg", "payload": {"type": "task_started", "turn_id": "two"}}]
    assert so.parse_codex(_write(tmp_path / "active.jsonl", active)) is None
    aborted = base[:-1] + [{"type": "event_msg", "payload": {"type": "turn_aborted", "turn_id": "one"}}]
    assert so.parse_codex(_write(tmp_path / "aborted.jsonl", aborted)) is None


def test_codex_rejects_subagent_thread_before_turn_parsing(tmp_path):
    rows = [
        {"type": "session_meta", "payload": {
            "id": "worker", "cwd": str(tmp_path), "thread_source": "subagent",
            "source": {"subagent": {"thread_spawn": {
                "parent_thread_id": "parent", "depth": 1, "agent_path": "/root/worker",
            }}},
        }},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "one"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "one", "item": {
            "type": "AgentMessage", "phase": "final_answer", "content": [{"type": "Text", "text": "done"}]}}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "one", "completed_at": 1}},
    ]
    assert so.parse_codex(_write(tmp_path / "subagent.jsonl", rows)) is None


def test_codex_hostile_payload_shape_does_not_hide_valid_session(tmp_path):
    bad = _write(tmp_path / "bad.jsonl", [{"type": "session_meta", "payload": []}])
    assert so.parse_codex(bad) is None
    valid_dir = tmp_path / "valid"
    valid_dir.mkdir()
    _write(valid_dir / "good.jsonl", [
        {"type": "session_meta", "payload": {"id": "good", "cwd": str(tmp_path), "thread_source": "user"}},
        {"type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_id": "good"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "one"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "one", "item": {
            "type": "AgentMessage", "phase": "final_answer", "content": [{"type": "Text", "text": "left"}]}}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "one", "completed_at": 1}},
    ])
    found = so.discover(tmp_path / "missing", tmp_path, modified_since=0)
    assert [item.session_id for item in found] == ["good"]
