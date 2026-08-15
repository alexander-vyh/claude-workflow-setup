"""Fail-closed local liveness inspection for cleanup candidates."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from escapement_worktree_git import WorktreeError, run

LEASE_WINDOW_SECONDS = 1800


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _parse_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _trusted_checkout_records(harness_home: Path) -> list[dict[str, object]]:
    threads = harness_home / "threads"
    if not threads.exists():
        return []
    if threads.is_symlink() or not threads.is_dir():
        raise WorktreeError("session registry is untrusted")
    paths = list(threads.glob("*/checkout.json"))
    paths.extend(threads.glob("*/agents/*/checkout.json"))
    records: list[dict[str, object]] = []
    for path in paths:
        try:
            metadata = path.lstat()
            if path.is_symlink() or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
                raise WorktreeError("session checkout record is untrusted")
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise WorktreeError(f"session checkout record cannot be inspected: {error}") from error
        if not isinstance(value, dict):
            raise WorktreeError("session checkout record is malformed")
        records.append(value)
    return records


def active_reason(worktree: Path, harness_home: Path) -> str | None:
    """Return semantic liveness, or raise when enumeration is incomplete."""
    target = worktree.resolve(strict=True)
    if _inside(Path.cwd(), target):
        return "worktree-active-invoking-cwd"

    now = dt.datetime.now(dt.timezone.utc)
    for record in _trusted_checkout_records(harness_home):
        root = record.get("worktree_root")
        heartbeat = _parse_time(record.get("heartbeat"))
        if not isinstance(root, str) or heartbeat is None:
            raise WorktreeError("session checkout record is incomplete")
        if Path(root) == target and (now - heartbeat).total_seconds() <= LEASE_WINDOW_SECONDS:
            return "worktree-active-lease"

    result = run(
        ("lsof", "-a", "-d", "cwd", "+D", str(target), "-Fpcn"),
        check=False,
    )
    if result.returncode not in {0, 1}:
        detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
        raise WorktreeError(f"process CWD enumeration failed: {detail}")
    if result.returncode == 1 and not result.stdout and not result.stderr.strip():
        return None
    if result.returncode == 1:
        raise WorktreeError("process CWD enumeration was incomplete")

    cwd_values: list[Path] = []
    saw_cwd = False
    for line in result.stdout.splitlines():
        if line.startswith("f"):
            saw_cwd = line[1:] == "cwd"
        elif line.startswith("n") and saw_cwd:
            cwd_values.append(Path(line[1:]))
            saw_cwd = False
    if result.returncode == 0 and not cwd_values:
        raise WorktreeError("process CWD enumeration returned incomplete records")
    if any(_inside(cwd, target) for cwd in cwd_values):
        return "worktree-active-process-cwd"
    return None
