#!/usr/bin/env python3
"""Safely replace a recognized legacy global Codex Beads skill."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


KNOWN_LEGACY_SHA256 = {
    "2096820ff0d7a712aa4b58ca2590979f80f2d0fd168a23bda788a024f47792e0",
    "c65855a32ece63079c692332a968174748187b9fb8e1a57bf3803dcc76beb402",
}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_recognized_legacy(content: bytes, recognized_hashes: set[str]) -> bool:
    return _sha256(content) in recognized_hashes


def migrate(source: Path, target: Path, recognized_hashes: set[str]) -> int:
    source_content = source.read_bytes()
    if not target.exists():
        print(f"no legacy global skill at {target}; nothing to migrate")
        return 0

    target_content = target.read_bytes()
    if target_content == source_content:
        print(f"global skill already current: {target}")
        return 0

    target_hash = _sha256(target_content)
    if not _is_recognized_legacy(target_content, recognized_hashes):
        print(
            f"refusing to replace unrecognized global skill at {target} "
            f"(sha256={target_hash})",
            file=sys.stderr,
        )
        return 2

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = target.with_name(f"{target.name}.backup-{timestamp}")
    shutil.copy2(target, backup)

    temporary = target.with_name(f".{target.name}.escapement-{os.getpid()}")
    try:
        temporary.write_bytes(source_content)
        shutil.copymode(source, temporary)
        if not target.exists() or target.read_bytes() != target_content:
            print(
                f"refusing to replace global skill changed during migration: {target}",
                file=sys.stderr,
            )
            return 2
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    print(f"replaced recognized legacy skill; backup: {backup}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replace only recognized Escapement legacy Codex Beads skills."
    )
    parser.add_argument("source", type=Path, help="current safe SKILL.md")
    parser.add_argument("target", type=Path, help="legacy global SKILL.md")
    parser.add_argument(
        "--legacy-sha256",
        action="append",
        default=[],
        help="additional recognized legacy SHA-256 (repeatable)",
    )
    args = parser.parse_args()
    return migrate(
        args.source,
        args.target,
        KNOWN_LEGACY_SHA256 | set(args.legacy_sha256),
    )


if __name__ == "__main__":
    raise SystemExit(main())
