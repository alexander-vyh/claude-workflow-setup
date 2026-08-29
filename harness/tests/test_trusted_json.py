"""Controls for trusted_json.mutate_trusted_atomic — the harness's atomic writer.

Business invariant: a harness hook that updates a small JSON record must never
leave a reader a torn file, must never follow a symlink another user planted,
and must never lose a concurrent hook's update. These controls were written
against the delegated-execution ledger; when that subsystem was removed the
guarantees moved here with the code, because the Stop gate's task-context write
still depends on every one of them.

Fragile implementations these reject:
  * plain ``path.write_text`` -> torn read, and clobbers a concurrent write
    (``test_concurrent_*``, ``test_cross_process_*``)
  * write-then-rename without an fsync -> a crash can leave a zero-length file
    (``test_*_fsyncs_temporary_file_before_replace``)
  * a temp file in /tmp -> ``os.replace`` across devices fails, or is non-atomic
    (``test_*_replaces_from_same_directory``)
  * following the target path blindly -> another user's symlink redirects the
    write (``test_*_preserves_dangling_target_symlink``)
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

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

spec = importlib.util.spec_from_file_location("trusted_json", BIN / "trusted_json.py")
trusted_json = importlib.util.module_from_spec(spec)
sys.modules["trusted_json"] = trusted_json
spec.loader.exec_module(trusted_json)

skip_non_posix = pytest.mark.skipif(
    not hasattr(os, "geteuid"), reason="POSIX ownership semantics required"
)


def _record() -> dict:
    return {"version": 1, "owner": "parent-7", "entries": []}


def _valid(value: object) -> bool:
    return isinstance(value, dict) and value.get("version") == 1


def _write(path: pathlib.Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def _mutate(path, mutation):
    return trusted_json.mutate_trusted_atomic(path, _record, mutation, _valid)


# --- concurrency ----------------------------------------------------------

def test_concurrent_mutations_serialize_without_lost_updates(tmp_path):
    path = tmp_path / "record.json"
    _write(path, _record())
    barrier = threading.Barrier(2)

    def mutate(worker: str) -> None:
        barrier.wait()

        def append(current: dict) -> dict:
            seen = list(current["entries"])
            time.sleep(0.03)
            current["entries"] = [*seen, {"worker": worker}]
            return current

        _mutate(path, append)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(mutate, ["a", "b"]))
    stored = json.loads(path.read_text())
    assert sorted(entry["worker"] for entry in stored["entries"]) == ["a", "b"]


@skip_non_posix
def test_cross_process_mutations_serialize_without_lost_updates(tmp_path):
    path = tmp_path / "record.json"
    _write(path, _record())
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)

    def worker(name: str) -> None:
        barrier.wait()

        def append(current: dict) -> dict:
            seen = list(current["entries"])
            time.sleep(0.05)
            current["entries"] = [*seen, {"worker": name}]
            return current

        _mutate(path, append)

    processes = [context.Process(target=worker, args=(name,)) for name in ("a", "b")]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0

    stored = json.loads(path.read_text())
    assert sorted(entry["worker"] for entry in stored["entries"]) == ["a", "b"]


def test_mutation_takes_an_exclusive_flock(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    _write(path, _record())
    real_flock = trusted_json.fcntl.flock
    operations: list[int] = []

    def recording_flock(file_descriptor, operation):
        operations.append(operation)
        return real_flock(file_descriptor, operation)

    monkeypatch.setattr(trusted_json.fcntl, "flock", recording_flock)
    _mutate(path, lambda current: current)
    assert trusted_json.fcntl.LOCK_EX in operations


# --- trust ----------------------------------------------------------------

@skip_non_posix
def test_rejects_untrusted_parent_directory(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o777)
    path = loose / "record.json"
    try:
        with pytest.raises(ValueError, match="untrusted"):
            _mutate(path, lambda current: current)
        assert not path.exists()
    finally:
        loose.chmod(0o755)


@skip_non_posix
def test_preserves_dangling_target_symlink(tmp_path):
    """A planted symlink must not become the write target."""
    path = tmp_path / "record.json"
    target = tmp_path / "missing-target.json"
    path.symlink_to(target)

    with pytest.raises(ValueError, match="untrusted"):
        _mutate(path, lambda current: current)

    assert path.is_symlink()
    assert os.readlink(path) == str(target)
    assert not target.exists()


# --- durability -----------------------------------------------------------

def test_replaces_from_same_directory(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    _write(path, _record())
    real_replace = trusted_json.os.replace
    replacements: list[tuple[pathlib.Path, pathlib.Path]] = []

    def recording_replace(source, destination):
        replacements.append((pathlib.Path(source), pathlib.Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(trusted_json.os, "replace", recording_replace)
    _mutate(path, lambda current: current)
    assert len(replacements) == 1
    source, destination = replacements[0]
    assert source.parent == path.parent
    assert source != path
    assert destination == path


def test_fsyncs_temporary_file_before_replace(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    _write(path, _record())
    real_fsync = trusted_json.os.fsync
    real_replace = trusted_json.os.replace
    events: list[str] = []
    fsynced_identities: list[tuple[int, int]] = []
    replacement_source_identity: list[tuple[int, int]] = []
    source_was_fsynced_at_replace: list[bool] = []

    def recording_fsync(file_descriptor):
        events.append("fsync")
        stat_result = trusted_json.os.fstat(file_descriptor)
        fsynced_identities.append((stat_result.st_dev, stat_result.st_ino))
        return real_fsync(file_descriptor)

    def recording_replace(source, destination):
        events.append("replace")
        stat_result = trusted_json.os.stat(source)
        source_identity = (stat_result.st_dev, stat_result.st_ino)
        replacement_source_identity.append(source_identity)
        source_was_fsynced_at_replace.append(source_identity in fsynced_identities)
        return real_replace(source, destination)

    monkeypatch.setattr(trusted_json.os, "fsync", recording_fsync)
    monkeypatch.setattr(trusted_json.os, "replace", recording_replace)
    _mutate(path, lambda current: current)
    assert "replace" in events
    assert "fsync" in events[: events.index("replace")]
    assert replacement_source_identity[0] in fsynced_identities
    assert source_was_fsynced_at_replace == [True]


def test_failed_replace_preserves_previous_valid_json(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    original = _record()
    _write(path, original)

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(trusted_json.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        _mutate(path, lambda current: {**current, "updated_at": "2026-08-09T20:00:00Z"})
    assert json.loads(path.read_text()) == original


# --- validation -----------------------------------------------------------

def test_invalid_mutation_result_is_not_persisted(tmp_path):
    """A mutation that lands invalid must raise, leaving the old value intact."""
    path = tmp_path / "record.json"
    original = _record()
    _write(path, original)
    with pytest.raises(ValueError, match="mutation is invalid"):
        _mutate(path, lambda current: {**current, "version": 99})
    assert json.loads(path.read_text()) == original
