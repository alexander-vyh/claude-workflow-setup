#!/usr/bin/env python3
"""Retry pending Escapement worktree receipts from the existing supervisor."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess


def reconcile(
    harness_root: pathlib.Path, *, cli: pathlib.Path | None = None
) -> dict[str, object]:
    registry = harness_root / "worktrees"
    if not registry.exists():
        return {"status": "ok", "checked": 0}
    if registry.is_symlink() or not registry.is_dir():
        raise RuntimeError("worktree lifecycle registry is untrusted")
    cli = cli or pathlib.Path(__file__).resolve().parents[2] / "bin" / "escapement-worktree"
    if not cli.is_file():
        raise RuntimeError("installed escapement-worktree command is missing")
    checked = 0
    for receipt in sorted(registry.iterdir()):
        if receipt.name.startswith("."):
            continue
        if receipt.is_symlink() or not receipt.is_file() or receipt.suffix != ".json":
            raise RuntimeError(f"invalid lifecycle registry entry: {receipt.name}")
        lifecycle_id = receipt.stem
        result = subprocess.run(
            [str(cli), "finish", "--lifecycle-id", lifecycle_id],
            env={**os.environ, "CONTINUATION_HARNESS_HOME": str(harness_root)},
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"worktree finish failed for {lifecycle_id}: {detail}")
        try:
            outcome = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"worktree finish output is malformed: {error}") from error
        if not isinstance(outcome, dict) or outcome.get("status") not in {
            "completed",
            "pending",
        }:
            raise RuntimeError("worktree finish returned an invalid outcome")
        checked += 1
    return {"status": "ok", "checked": checked}
