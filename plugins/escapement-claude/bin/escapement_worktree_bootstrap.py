"""Repository-declared bootstrap contract for verified worktree creation."""

from __future__ import annotations

import json
import math
import os
import ctypes
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from escapement_worktree_git import (
    RepositoryContext,
    ResolvedSource,
    WorktreeError,
    git,
)

REPO_CONFIG_PATH = ".escapement/repo.json"
BOOTSTRAP_FIELDS = frozenset({"argv", "timeout_seconds"})
BOOTSTRAP_OUTPUT_TAIL_BYTES = 8192
BOOTSTRAP_TERMINATION_GRACE_SECONDS = 0.5


@dataclass(frozen=True)
class BootstrapContract:
    argv: tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class _ProcessIdentity:
    pid: int
    birth: str


class _DarwinProcessInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        *[
            (name, ctypes.c_uint32)
            for name in ("uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")
        ],
        ("command", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        *[
            (name, ctypes.c_uint32)
            for name in ("nfiles", "pgid", "jobc", "tty_device", "tty_pgid")
        ],
        ("nice", ctypes.c_int32),
        ("start_seconds", ctypes.c_uint64),
        ("start_microseconds", ctypes.c_uint64),
    ]


def _linux_process_fields(pid: int) -> tuple[int, str] | None:
    try:
        fields = (
            (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        )
        return int(fields[1]), fields[19]
    except (IndexError, OSError, ValueError):
        return None


def _darwin_process_fields(pid: int) -> tuple[int, str] | None:
    library = ctypes.CDLL("/usr/lib/libproc.dylib")
    info = _DarwinProcessInfo()
    size = ctypes.sizeof(info)
    if library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), size) != size:
        return None
    return info.ppid, f"{info.start_seconds}:{info.start_microseconds}"


def _process_fields(pid: int) -> tuple[int, str] | None:
    if sys.platform == "darwin":
        return _darwin_process_fields(pid)
    if sys.platform.startswith("linux"):
        return _linux_process_fields(pid)
    return None


def _direct_children(parent: int) -> tuple[int, ...]:
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libproc.dylib")
        buffer = (ctypes.c_int * 4096)()
        used = library.proc_listpids(6, parent, buffer, ctypes.sizeof(buffer))
        return tuple(
            pid
            for pid in buffer[: max(0, used) // ctypes.sizeof(ctypes.c_int)]
            if pid > 0
        )
    if sys.platform.startswith("linux"):
        children = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            fields = _linux_process_fields(int(entry.name))
            if fields is not None and fields[0] == parent:
                children.append(int(entry.name))
        return tuple(children)
    return ()


class _DescendantTracker:
    """Continuously retain the identities of one bootstrap's descendants."""

    def __init__(self, root_pid: int) -> None:
        self._root_pid = root_pid
        self._identities: dict[int, _ProcessIdentity] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sample()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _sample(self) -> None:
        with self._lock:
            root_fields = _process_fields(self._root_pid)
            if root_fields is not None and self._root_pid not in self._identities:
                self._identities[self._root_pid] = _ProcessIdentity(
                    self._root_pid,
                    root_fields[1],
                )
            pending = list(self._identities)
            while pending:
                for pid in _direct_children(pending.pop()):
                    if pid in self._identities:
                        continue
                    fields = _process_fields(pid)
                    if fields is None:
                        continue
                    self._identities[pid] = _ProcessIdentity(pid, fields[1])
                    pending.append(pid)

    def _watch(self) -> None:
        while not self._stop.wait(0.01):
            self._sample()

    def identities(self) -> tuple[_ProcessIdentity, ...]:
        self._sample()
        with self._lock:
            return tuple(self._identities.values())

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=BOOTSTRAP_TERMINATION_GRACE_SECONDS)


def _config_at_source(
    ctx: RepositoryContext, source: ResolvedSource
) -> dict[str, object] | None:
    """Read repository policy from the resolved commit, never the checkout."""
    entry = git(
        ctx,
        "ls-tree",
        "-z",
        "--full-tree",
        source.sha,
        "--",
        REPO_CONFIG_PATH,
    ).stdout
    if not entry:
        return None
    records = [record for record in entry.split("\0") if record]
    if len(records) != 1:
        raise WorktreeError(f"source has ambiguous {REPO_CONFIG_PATH}")
    metadata, separator, path = records[0].partition("\t")
    mode, object_type, _object_id = metadata.split(" ", maxsplit=2)
    if not separator or path != REPO_CONFIG_PATH or object_type != "blob":
        raise WorktreeError(f"source {REPO_CONFIG_PATH} is not a regular file")
    if mode not in {"100644", "100755"}:
        raise WorktreeError(f"source {REPO_CONFIG_PATH} is not a regular file")

    raw = git(ctx, "show", f"{source.sha}:{REPO_CONFIG_PATH}").stdout
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WorktreeError(
            f"source {REPO_CONFIG_PATH} is malformed JSON: {error}"
        ) from error
    if not isinstance(config, dict):
        raise WorktreeError(f"source {REPO_CONFIG_PATH} must be a JSON object")
    return config


def resolve_bootstrap_contract(
    ctx: RepositoryContext, source: ResolvedSource
) -> BootstrapContract | None:
    config = _config_at_source(ctx, source)
    if config is None or "worktree" not in config:
        return None
    worktree = config["worktree"]
    if not isinstance(worktree, dict):
        raise WorktreeError("worktree configuration must be a JSON object")
    if "bootstrap" not in worktree:
        return None
    bootstrap = worktree["bootstrap"]
    if not isinstance(bootstrap, dict):
        raise WorktreeError("worktree.bootstrap must be a JSON object")

    fields = set(bootstrap)
    unknown = fields - BOOTSTRAP_FIELDS
    missing = BOOTSTRAP_FIELDS - fields
    if unknown:
        raise WorktreeError(
            "worktree.bootstrap has unknown fields: " + ", ".join(sorted(unknown))
        )
    if missing:
        raise WorktreeError(
            "worktree.bootstrap is missing fields: " + ", ".join(sorted(missing))
        )

    argv = bootstrap["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(
            not isinstance(argument, str) or not argument or "\0" in argument
            for argument in argv
        )
    ):
        raise WorktreeError(
            "worktree.bootstrap.argv must be a non-empty array of non-empty, "
            "NUL-free strings"
        )
    timeout = bootstrap["timeout_seconds"]
    try:
        timeout_value = float(timeout)
    except (OverflowError, TypeError, ValueError):
        timeout_value = math.nan
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout_value)
        or timeout_value <= 0
    ):
        raise WorktreeError(
            "worktree.bootstrap.timeout_seconds must be a positive finite number"
        )
    return BootstrapContract(argv=tuple(argv), timeout_seconds=timeout_value)


def _output_tail(stream) -> str:
    stream.flush()
    size = stream.seek(0, os.SEEK_END)
    stream.seek(max(0, size - BOOTSTRAP_OUTPUT_TAIL_BYTES))
    raw = stream.read(BOOTSTRAP_OUTPUT_TAIL_BYTES)
    return "".join(f"\\x{byte:02x}" for byte in raw)


def _output_diagnostic(stdout_stream, stderr_stream) -> str:
    stderr_tail = _output_tail(stderr_stream)
    stdout_tail = _output_tail(stdout_stream)
    parts = []
    if stderr_tail:
        parts.append(f"stderr tail: {stderr_tail}")
    if stdout_tail:
        parts.append(f"stdout tail: {stdout_tail}")
    return "; " + "; ".join(parts) if parts else ""


def _identity_is_live(identity: _ProcessIdentity) -> bool:
    fields = _process_fields(identity.pid)
    return fields is not None and fields[1] == identity.birth


def _signal_live(identity: _ProcessIdentity, signum: signal.Signals) -> None:
    if not _identity_is_live(identity):
        return
    try:
        os.kill(identity.pid, signum)
    except (PermissionError, ProcessLookupError):
        pass


def _terminate_descendants(
    process: subprocess.Popen[bytes], tracker: _DescendantTracker
) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (PermissionError, ProcessLookupError):
        pass
    term_sent: set[_ProcessIdentity] = set()
    term_deadline = time.monotonic() + BOOTSTRAP_TERMINATION_GRACE_SECONDS
    while time.monotonic() < term_deadline:
        live = [
            identity for identity in tracker.identities() if _identity_is_live(identity)
        ]
        for identity in live:
            if identity not in term_sent:
                _signal_live(identity, signal.SIGTERM)
                term_sent.add(identity)
        if not live:
            break
        time.sleep(0.02)

    kill_deadline = time.monotonic() + BOOTSTRAP_TERMINATION_GRACE_SECONDS
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (PermissionError, ProcessLookupError):
        pass
    while time.monotonic() < kill_deadline:
        live = [
            identity for identity in tracker.identities() if _identity_is_live(identity)
        ]
        for identity in live:
            _signal_live(identity, signal.SIGKILL)
        if not live:
            break
        time.sleep(0.02)

    tracker.stop()
    try:
        process.wait(timeout=BOOTSTRAP_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_bootstrap(contract: BootstrapContract, target: Path) -> None:
    try:
        with (
            tempfile.TemporaryFile() as stdout_stream,
            tempfile.TemporaryFile() as stderr_stream,
        ):
            process = subprocess.Popen(
                list(contract.argv),
                cwd=target,
                stdout=stdout_stream,
                stderr=stderr_stream,
                start_new_session=True,
            )
            tracker = _DescendantTracker(process.pid)
            try:
                returncode = process.wait(timeout=contract.timeout_seconds)
            except subprocess.TimeoutExpired:
                _terminate_descendants(process, tracker)
                detail = _output_diagnostic(stdout_stream, stderr_stream)
                raise WorktreeError(
                    f"bootstrap timed out after {contract.timeout_seconds:g} seconds"
                    f"{detail}"
                ) from None

            if returncode:
                _terminate_descendants(process, tracker)
            else:
                tracker.stop()
            detail = _output_diagnostic(stdout_stream, stderr_stream)
    except FileNotFoundError as error:
        raise WorktreeError(
            f"bootstrap executable was not found: {contract.argv[0]}"
        ) from error
    except ValueError as error:
        raise WorktreeError(f"bootstrap argv is invalid: {error}") from error
    except OSError as error:
        raise WorktreeError(f"bootstrap executable failed to start: {error}") from error

    if returncode < 0:
        raise WorktreeError(f"bootstrap was terminated by signal {-returncode}{detail}")
    if returncode:
        raise WorktreeError(f"bootstrap failed with exit status {returncode}{detail}")
