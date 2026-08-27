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


def _lsof_path_is_lossless(path: Path) -> bool:
    return all(0x20 <= byte <= 0x7E for byte in os.fsencode(path))


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


def _cwd_paths(output: str) -> list[Path]:
    cwd_values: list[Path] = []
    saw_process = False
    process_has_cwd = False
    pending_cwd = False
    for raw_field in output.split("\0"):
        field = raw_field.lstrip("\n")
        if not field:
            continue
        tag, value = field[0], field[1:]
        if tag == "p":
            if (
                not value.isascii()
                or not value.isdigit()
                or pending_cwd
                or (saw_process and not process_has_cwd)
            ):
                raise WorktreeError("process CWD enumeration returned incomplete records")
            saw_process = True
            process_has_cwd = False
        elif tag == "c":
            if not saw_process:
                raise WorktreeError("process CWD enumeration returned incomplete records")
        elif tag == "f":
            if not saw_process or pending_cwd or process_has_cwd or value != "cwd":
                raise WorktreeError("process CWD enumeration returned incomplete records")
            pending_cwd = True
        elif tag == "n":
            if not pending_cwd or not value:
                raise WorktreeError("process CWD enumeration returned incomplete records")
            cwd_values.append(Path(value))
            pending_cwd = False
            process_has_cwd = True
        else:
            raise WorktreeError("process CWD enumeration returned unexpected fields")
    if pending_cwd or not saw_process or not process_has_cwd:
        raise WorktreeError("process CWD enumeration returned incomplete records")
    return cwd_values


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

    if not _lsof_path_is_lossless(target):
        raise WorktreeError("worktree path cannot be represented losslessly by lsof")

    # Enumerate only process cwd descriptors, then apply target containment
    # locally.  Combining +D target-tree traversal with lsof's mount inspection
    # makes an unrelated unreadable mount turn the targeted query nonzero.
    result = run(
        ("lsof", "-a", "-d", "cwd", "-Fpcfn0"),
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
        raise WorktreeError(f"process CWD enumeration failed: {detail}")

    cwd_values = _cwd_paths(result.stdout)
    if any(_inside(cwd, target) for cwd in cwd_values):
        return "worktree-active-process-cwd"
    return None
