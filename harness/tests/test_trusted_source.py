"""Tests for trusted_source — the flat-file command-source trust guard.

Business invariant: the harness must NOT shell-execute a command string sourced
from a file that another local user could have tampered with. "Tampered with"
means: the file (or the directory holding it) is owned by someone else, or is
writable by group/other. This is the StrictModes pattern (cf. ssh) applied to
the harness's auto-executing config (scheduled.json / contract.json).

Fragile implementation this rejects: an existence-only check. A guard that only
asks "does the file exist?" would happily run a command from a world-writable
file — the negative controls below (0o666 file, 0o777 parent) catch exactly that.
"""

import importlib.util
import json
import multiprocessing
import os
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from jsonschema import Draft202012Validator

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))


def _load_trusted_source():
    spec = importlib.util.spec_from_file_location(
        "trusted_source", BIN / "trusted_source.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load harness/bin/trusted_source.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ts = _load_trusted_source()

import execution_store  # noqa: E402


def _load_execution_ledger():
    spec = importlib.util.spec_from_file_location(
        "execution_ledger", BIN / "execution_ledger.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load harness/bin/execution_ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


execution_ledger = _load_execution_ledger()

POSIX = hasattr(os, "geteuid")
skip_non_posix = pytest.mark.skipif(not POSIX, reason="perms/ownership are POSIX-only")


# --- positive control: a normal user-owned, tightly-permissioned file -------


@skip_non_posix
def test_user_owned_private_file_is_trusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o600)
    assert ts.is_trusted_file(f) is True


@skip_non_posix
def test_group_other_readable_but_not_writable_is_trusted(tmp_path):
    # 0o644 / 0o755 dirs are the common default umask result — must stay usable.
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o644)
    assert ts.is_trusted_file(f) is True


# --- negative controls: the tamper surfaces ---------------------------------


def test_missing_file_is_untrusted(tmp_path):
    assert ts.is_trusted_file(tmp_path / "nope.json") is False


@skip_non_posix
def test_world_writable_file_is_untrusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o666)  # group + other writable: anyone can rewrite the command
    assert ts.is_trusted_file(f) is False


@skip_non_posix
def test_group_writable_file_is_untrusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o664)
    assert ts.is_trusted_file(f) is False


@skip_non_posix
def test_world_writable_parent_dir_is_untrusted(tmp_path):
    # Even a 0o600 file is forgeable if its directory is world-writable (no
    # sticky bit): an attacker replaces the file wholesale.
    d = tmp_path / "loose"
    d.mkdir()
    f = d / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o600)
    d.chmod(0o777)  # world-writable, NOT sticky
    try:
        assert ts.is_trusted_file(f) is False
    finally:
        d.chmod(0o755)  # let pytest clean up


@skip_non_posix
def test_directory_is_not_a_trusted_file(tmp_path):
    assert ts.is_trusted_file(tmp_path) is False


# --- assert_trusted_file raises with an actionable message ------------------


@skip_non_posix
def test_assert_trusted_file_raises_on_untrusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o666)
    with pytest.raises(ts.UntrustedSource):
        ts.assert_trusted_file(f)


@skip_non_posix
def test_assert_trusted_file_passes_on_trusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o600)
    assert ts.assert_trusted_file(f) is None


def _ledger(parent: str = "parent-7") -> dict:
    return {
        "version": 1,
        "parent_session_id": parent,
        "updated_at": None,
        "executions": [],
        "incidents": [],
    }


def _valid_ledger() -> dict:
    return {
        "version": 1,
        "parent_session_id": "parent-7",
        "updated_at": "2026-08-09T20:05:00Z",
        "executions": [
            {
                "bead_id": "escapement-e3ai.2",
                "execution_id": "exec-alpha",
                "host": "codex",
                "agent_name": "worker",
                "native_child_id": "child-native-1",
                "dispatch_tool_use_id": "call-44",
                "attempt": 1,
                "generation": 1,
                "state": "running",
                "queued_at": "2026-08-09T20:00:00Z",
                "started_at": "2026-08-09T20:00:20Z",
                "last_activity_at": "2026-08-09T20:04:00Z",
                "last_activity_kind": "tool_completed",
                "start_deadline": "2026-08-09T20:02:00Z",
                "idle_deadline": "2026-08-09T20:19:00Z",
                "hard_deadline": "2026-08-09T22:00:00Z",
                "reconcile_due": None,
                "terminal_at": None,
                "terminal_reason": None,
                "terminal_event_id": None,
                "result_digest": None,
                "watchdog_id": "watch-exec-alpha",
                "recovery_count": 0,
                "recovery_claim": None,
                "result_application": {
                    "state": "unapplied",
                    "claim": None,
                    "claim_generation": 0,
                    "idempotency_key": "execution:exec-alpha:attempt:1:generation:1",
                    "applied_at": None,
                },
            }
        ],
        "incidents": [],
    }


def _write_ledger(path: pathlib.Path, value) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_execution_ledger_loads_only_the_expected_parent(tmp_path):
    path = tmp_path / "executions.json"
    _write_ledger(path, _ledger("parent-foreign"))
    assert execution_ledger.load_trusted(path, "parent-7") is None


def test_complete_private_execution_ledger_loads_as_positive_control(tmp_path):
    path = tmp_path / "executions.json"
    expected = _valid_ledger()
    _write_ledger(path, expected)
    assert execution_ledger.load_trusted(path, "parent-7") == expected


@pytest.mark.parametrize("content", ["{broken", "[]", "null"])
def test_malformed_or_non_dictionary_execution_ledger_is_unresolved(tmp_path, content):
    path = tmp_path / "executions.json"
    path.write_text(content)
    path.chmod(0o600)
    assert execution_ledger.load_trusted(path, "parent-7") is None


@skip_non_posix
def test_world_writable_execution_ledger_is_unresolved(tmp_path):
    path = tmp_path / "executions.json"
    _write_ledger(path, _ledger())
    path.chmod(0o666)
    assert execution_ledger.load_trusted(path, "parent-7") is None


@skip_non_posix
def test_execution_ledger_in_nonsticky_world_writable_parent_is_unresolved(tmp_path):
    directory = tmp_path / "loose-ledger-dir"
    directory.mkdir()
    path = directory / "executions.json"
    _write_ledger(path, _valid_ledger())
    directory.chmod(0o777)
    try:
        assert execution_ledger.load_trusted(path, "parent-7") is None
    finally:
        directory.chmod(0o755)


@pytest.mark.parametrize(
    "field_path, bad_value",
    [
        (("state",), "probably-running"),
        (("reconcile_due",), "tomorrow-maybe"),
        (("result_application", "state"), "verified-by-digest"),
    ],
)
def test_each_invalid_runtime_enum_is_independently_unresolved(
    tmp_path, field_path, bad_value
):
    path = tmp_path / "executions.json"
    invalid = _valid_ledger()
    target = invalid["executions"][0]
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = bad_value
    _write_ledger(path, invalid)
    assert execution_ledger.load_trusted(path, "parent-7") is None


@pytest.mark.parametrize(
    "field_path, bad_value",
    [
        (("claim", "execution_id"), "exec-foreign"),
        (("claim", "attempt"), 2),
        (("claim", "generation"), 2),
        (("claim", "claim_generation"), 2),
        (("idempotency_key",), "execution:exec-foreign:attempt:1:generation:1"),
    ],
)
def test_mismatched_application_fence_identity_is_unresolved(
    tmp_path, field_path, bad_value
):
    path = tmp_path / "executions.json"
    invalid = _valid_ledger()
    item = invalid["executions"][0]
    item["state"] = "terminal"
    item["terminal_at"] = "2026-08-09T20:05:00Z"
    item["terminal_reason"] = "completed"
    item["terminal_event_id"] = "terminal-900"
    item["result_digest"] = "sha256:result-a"
    application = item["result_application"]
    application["state"] = "applying"
    application["claim_generation"] = 1
    application["claim"] = {
        "owner": "applier-a",
        "execution_id": "exec-alpha",
        "attempt": 1,
        "generation": 1,
        "claim_generation": 1,
        "claimed_at": "2026-08-09T20:06:00Z",
        "expires_at": "2026-08-09T20:06:30Z",
    }
    target = application
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = bad_value
    _write_ledger(path, invalid)
    assert execution_ledger.load_trusted(path, "parent-7") is None


def test_schema_declares_closed_enums_for_runtime_decisions():
    schema_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "schemas"
        / "executions.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    execution_properties = schema["properties"]["executions"]["items"]["properties"]
    assert execution_properties["state"]["enum"] == [
        "queued",
        "running",
        "terminal",
        "cancelled",
        "aborted",
        "unknown",
    ]
    assert execution_properties["reconcile_due"]["enum"] == [
        "start",
        "idle",
        "hard",
        None,
    ]
    assert execution_properties["result_application"]["properties"]["state"][
        "enum"
    ] == [
        "unapplied",
        "applying",
        "applied",
    ]


def test_schema_enforces_resolved_deadline_and_host_observation_contracts():
    schema_path = BIN.parent / "schemas" / "executions.schema.json"
    schema = json.loads(schema_path.read_text())
    validator = Draft202012Validator(schema)
    resolved = _valid_ledger()
    item = resolved["executions"][0]
    item.update(
        {
            "state": "terminal",
            "terminal_at": "2026-08-09T20:05:00Z",
            "terminal_reason": "completed",
            "terminal_event_id": "terminal-schema-control",
            "result_digest": "sha256:schema-control",
            "start_deadline": None,
            "idle_deadline": None,
            "hard_deadline": None,
            "reconcile_due": None,
            "recovery_claim": None,
        }
    )
    assert validator.is_valid(resolved)

    retained_deadline = json.loads(json.dumps(resolved))
    retained_deadline["executions"][0]["idle_deadline"] = "2026-08-09T20:19:00Z"
    assert not validator.is_valid(retained_deadline)

    invalid_observation = json.loads(json.dumps(resolved))
    invalid_observation["incidents"] = [
        {
            "type": "host_event_observation",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "host_event_id": "claude:schema:invalid",
        }
    ]
    assert not validator.is_valid(invalid_observation)


@pytest.mark.parametrize("state", ["queued", "running", "unknown"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("terminal_at", "2026-08-09T20:05:00Z"),
        ("terminal_reason", "completed"),
        ("terminal_event_id", "terminal-active-state-control"),
        ("result_digest", "sha256:active-state-control"),
    ],
)
def test_schema_rejects_terminal_evidence_on_active_execution_states(
    state, field, value
):
    schema_path = BIN.parent / "schemas" / "executions.schema.json"
    validator = Draft202012Validator(json.loads(schema_path.read_text()))
    active_with_terminal_evidence = _valid_ledger()
    item = active_with_terminal_evidence["executions"][0]
    item["state"] = state
    item[field] = value

    assert not validator.is_valid(active_with_terminal_evidence)


def test_concurrent_mutations_serialize_without_lost_updates(tmp_path):
    path = tmp_path / "executions.json"
    initial = _ledger()
    _write_ledger(path, initial)
    barrier = threading.Barrier(2)

    def mutate(_worker: str) -> None:
        barrier.wait()

        def increment(current: dict) -> dict:
            seen = list(current["incidents"])
            time.sleep(0.03)
            current["incidents"] = [*seen, {"type": "test_mutation", "worker": _worker}]
            return current

        execution_ledger.mutate_atomic(path, increment)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(mutate, ["a", "b"]))
    stored = json.loads(path.read_text())
    assert sorted(entry["worker"] for entry in stored["incidents"]) == ["a", "b"]


@skip_non_posix
def test_atomic_initializer_rejects_untrusted_parent_directory(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o777)
    path = loose / "executions.json"
    try:
        with pytest.raises(ValueError, match="trusted"):
            execution_store.initialize_or_mutate_atomic(
                path,
                _ledger,
                lambda current: current,
            )
        assert not path.exists()
    finally:
        loose.chmod(0o755)


@skip_non_posix
def test_atomic_initializer_preserves_dangling_target_symlink(tmp_path):
    path = tmp_path / "executions.json"
    target = tmp_path / "missing-target.json"
    path.symlink_to(target)

    with pytest.raises(ValueError, match="trusted"):
        execution_store.initialize_or_mutate_atomic(
            path,
            _ledger,
            lambda current: current,
        )

    assert path.is_symlink()
    assert os.readlink(path) == str(target)
    assert not target.exists()


@skip_non_posix
def test_cross_process_mutations_serialize_without_lost_updates(tmp_path):
    path = tmp_path / "executions.json"
    _write_ledger(path, _ledger())
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)

    def worker(name: str) -> None:
        barrier.wait()

        def append_incident(current: dict) -> dict:
            seen = list(current["incidents"])
            time.sleep(0.05)
            current["incidents"] = [*seen, {"type": "process_mutation", "worker": name}]
            return current

        execution_ledger.mutate_atomic(path, append_incident)

    processes = [context.Process(target=worker, args=(name,)) for name in ("a", "b")]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0

    stored = json.loads(path.read_text())
    assert sorted(entry["worker"] for entry in stored["incidents"]) == ["a", "b"]


def test_atomic_mutation_takes_an_exclusive_flock(tmp_path, monkeypatch):
    path = tmp_path / "executions.json"
    _write_ledger(path, _ledger())
    real_flock = execution_store.fcntl.flock
    operations: list[int] = []

    def recording_flock(file_descriptor, operation):
        operations.append(operation)
        return real_flock(file_descriptor, operation)

    monkeypatch.setattr(execution_store.fcntl, "flock", recording_flock)
    execution_ledger.mutate_atomic(path, lambda current: current)
    assert execution_store.fcntl.LOCK_EX in operations


def test_atomic_mutation_replaces_from_same_directory(tmp_path, monkeypatch):
    path = tmp_path / "executions.json"
    _write_ledger(path, _ledger())
    real_replace = execution_store.os.replace
    replacements: list[tuple[pathlib.Path, pathlib.Path]] = []

    def recording_replace(source, destination):
        replacements.append((pathlib.Path(source), pathlib.Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(execution_store.os, "replace", recording_replace)
    execution_ledger.mutate_atomic(path, lambda current: current)
    assert len(replacements) == 1
    source, destination = replacements[0]
    assert source.parent == path.parent
    assert source != path
    assert destination == path


def test_atomic_mutation_fsyncs_temporary_file_before_replace(tmp_path, monkeypatch):
    path = tmp_path / "executions.json"
    _write_ledger(path, _ledger())
    real_fsync = execution_store.os.fsync
    real_replace = execution_store.os.replace
    events: list[str] = []
    fsynced_identities: list[tuple[int, int]] = []
    replacement_source_identity: list[tuple[int, int]] = []
    source_was_fsynced_at_replace: list[bool] = []

    def recording_fsync(file_descriptor):
        events.append("fsync")
        stat_result = execution_store.os.fstat(file_descriptor)
        fsynced_identities.append((stat_result.st_dev, stat_result.st_ino))
        return real_fsync(file_descriptor)

    def recording_replace(source, destination):
        events.append("replace")
        stat_result = execution_store.os.stat(source)
        source_identity = (stat_result.st_dev, stat_result.st_ino)
        replacement_source_identity.append(source_identity)
        source_was_fsynced_at_replace.append(source_identity in fsynced_identities)
        return real_replace(source, destination)

    monkeypatch.setattr(execution_store.os, "fsync", recording_fsync)
    monkeypatch.setattr(execution_store.os, "replace", recording_replace)
    execution_ledger.mutate_atomic(path, lambda current: current)
    assert "replace" in events
    assert "fsync" in events[: events.index("replace")]
    assert replacement_source_identity[0] in fsynced_identities
    assert source_was_fsynced_at_replace == [True]


def test_failed_atomic_replace_preserves_previous_valid_json(tmp_path, monkeypatch):
    path = tmp_path / "executions.json"
    original = _ledger()
    _write_ledger(path, original)

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(execution_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        execution_ledger.mutate_atomic(
            path,
            lambda current: {**current, "updated_at": "2026-08-09T20:00:00Z"},
        )
    assert json.loads(path.read_text()) == original
