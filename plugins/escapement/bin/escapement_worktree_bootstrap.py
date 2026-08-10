"""Repository-declared bootstrap contract for verified worktree creation."""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import tempfile
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
            not isinstance(argument, str)
            or not argument
            or "\0" in argument
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
    decoded = raw.decode("utf-8", errors="replace")
    return decoded.encode("unicode_escape").decode("ascii")


def _output_diagnostic(stdout_stream, stderr_stream) -> str:
    stderr_tail = _output_tail(stderr_stream)
    stdout_tail = _output_tail(stdout_stream)
    parts = []
    if stderr_tail:
        parts.append(f"stderr tail: {stderr_tail}")
    if stdout_tail:
        parts.append(f"stdout tail: {stdout_tail}")
    return "; " + "; ".join(parts) if parts else ""


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    process_group = process.pid
    termination_delivered = False
    try:
        os.killpg(process_group, signal.SIGTERM)
        termination_delivered = True
    except ProcessLookupError:
        pass

    # Do not reap the session leader until after the final group signal. Its
    # unreaped PID pins the process-group identity and prevents a reuse race.
    time.sleep(BOOTSTRAP_TERMINATION_GRACE_SECONDS)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS can report EPERM for a group containing only unsignalable
        # zombies. This is safe only after TERM was successfully delivered to
        # the transaction-owned group while its leader was still pinned.
        if not termination_delivered:
            raise

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
            try:
                returncode = process.wait(timeout=contract.timeout_seconds)
            except subprocess.TimeoutExpired:
                _terminate_process_group(process)
                detail = _output_diagnostic(stdout_stream, stderr_stream)
                raise WorktreeError(
                    f"bootstrap timed out after {contract.timeout_seconds:g} seconds"
                    f"{detail}"
                ) from None

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
        raise WorktreeError(
            f"bootstrap was terminated by signal {-returncode}{detail}"
        )
    if returncode:
        raise WorktreeError(
            f"bootstrap failed with exit status {returncode}{detail}"
        )
