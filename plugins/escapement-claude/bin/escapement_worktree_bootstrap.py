"""Repository-declared bootstrap contract for verified worktree creation."""

from __future__ import annotations

import json
import math
import subprocess
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


def run_bootstrap(contract: BootstrapContract, target: Path) -> None:
    try:
        result = subprocess.run(
            list(contract.argv),
            cwd=target,
            text=True,
            capture_output=True,
            timeout=contract.timeout_seconds,
            check=False,
        )
    except FileNotFoundError as error:
        raise WorktreeError(
            f"bootstrap executable was not found: {contract.argv[0]}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise WorktreeError(
            f"bootstrap timed out after {contract.timeout_seconds:g} seconds"
        ) from error
    except ValueError as error:
        raise WorktreeError(f"bootstrap argv is invalid: {error}") from error
    except OSError as error:
        raise WorktreeError(f"bootstrap executable failed to start: {error}") from error

    if result.returncode < 0:
        raise WorktreeError(
            f"bootstrap was terminated by signal {-result.returncode}"
        )
    if result.returncode:
        raise WorktreeError(
            f"bootstrap failed with exit status {result.returncode}"
        )
