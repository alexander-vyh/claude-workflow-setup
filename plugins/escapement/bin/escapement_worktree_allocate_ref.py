"""Prepare and journal an exact branch ref before Git publishes it."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from escapement_worktree_git import RepositoryContext, WorktreeError
from escapement_worktree_registry import write_lifecycle
from escapement_worktree_rollback_lock import (
    AnchoredPath,
    location_lstat,
    pin_leaf,
    regular_file_identity_and_content,
)


def _response(process: subprocess.Popen[str], expected: str) -> None:
    assert process.stdout is not None
    line = process.stdout.readline().strip()
    if line != expected:
        detail = ""
        if process.poll() is not None and process.stderr is not None:
            detail = process.stderr.read().strip()
        raise WorktreeError(
            f"Git ref transaction expected {expected!r}, received {line!r}"
            + (f": {detail}" if detail else "")
        )


def _abort(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if process.stdin is not None:
            process.stdin.write("abort\n")
            process.stdin.flush()
            process.stdin.close()
        process.wait(timeout=5)
    except (BrokenPipeError, subprocess.TimeoutExpired):
        process.kill()
        process.wait(timeout=5)


def allocate_branch_ref(
    ctx: RepositoryContext,
    lifecycle_id: str,
    branch: str,
    source_sha: str,
    receipt: dict[str, object],
) -> None:
    """Commit exactly the ref inode whose prepared lock identity was journaled."""
    branch_ref = f"refs/heads/{branch}"
    process = subprocess.Popen(
        [
            "git",
            "-C",
            str(ctx.primary),
            "-c",
            "core.logAllRefUpdates=false",
            "update-ref",
            "--stdin",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    prepared = False
    try:
        assert process.stdin is not None
        process.stdin.write(
            f"start\ncreate {branch_ref} {source_sha}\nprepare\n"
        )
        process.stdin.flush()
        _response(process, "start: ok")
        _response(process, "prepare: ok")
        prepared = True

        relative_ref = Path("refs") / "heads" / branch
        prepared_lock = pin_leaf(
            ctx.common_dir,
            Path(f"{relative_ref}.lock"),
        )
        try:
            lock_identity, content = regular_file_identity_and_content(
                prepared_lock
            )
            metadata = location_lstat(prepared_lock)
            if not stat.S_ISREG(metadata.st_mode) or content != (
                f"{source_sha}\n".encode()
            ):
                raise WorktreeError(
                    f"prepared branch ref has unexpected content: {branch_ref}"
                )
            receipt.update(
                branch_allocation_state="prepared",
                branch_ref_device=lock_identity[0],
                branch_ref_inode=lock_identity[1],
                branch_reflog=str(ctx.common_dir / "logs" / relative_ref),
                branch_reflog_present=False,
            )
            write_lifecycle(lifecycle_id, receipt)

            process.stdin.write("commit\n")
            process.stdin.flush()
            _response(process, "commit: ok")
            process.stdin.close()
            returncode = process.wait(timeout=5)
            if returncode:
                detail = process.stderr.read().strip() if process.stderr else ""
                raise WorktreeError(
                    f"Git ref transaction commit failed ({returncode})"
                    + (f": {detail}" if detail else "")
                )

            committed_ref = AnchoredPath(
                ctx.common_dir / relative_ref,
                prepared_lock.parent_descriptor,
                relative_ref.name,
            )
            committed_identity, committed_content = (
                regular_file_identity_and_content(committed_ref)
            )
            if (
                committed_identity != lock_identity
                or committed_content != f"{source_sha}\n".encode()
            ):
                raise WorktreeError(
                    f"committed branch ref identity changed: {branch_ref}"
                )
            receipt.update(
                branch_allocated=True,
                branch_allocation_state="committed",
            )
            write_lifecycle(lifecycle_id, receipt)
        finally:
            os.close(prepared_lock.parent_descriptor)
    except (OSError, subprocess.SubprocessError):
        raise
    finally:
        if not prepared or process.poll() is None:
            _abort(process)
