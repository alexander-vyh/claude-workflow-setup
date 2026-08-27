#!/usr/bin/env python3
"""Classify a harness wrapper target against one managed plugin cache."""

from __future__ import annotations

import sys
from pathlib import Path


OUTSIDE_CACHE = 1
INVALID_CACHE_TARGET = 2


def _validate_resolved_target(target: Path, root: Path, relative: Path) -> int:
    try:
        resolved_root = root.resolve(strict=True)
        resolved_target = target.resolve(strict=True)
    except OSError:
        return INVALID_CACHE_TARGET

    expected_target = resolved_root.joinpath(*relative.parts)
    if resolved_target != expected_target or not resolved_target.is_dir():
        return INVALID_CACHE_TARGET
    return 0


def classify_versioned_cache(target: Path, component: str, cache_root: Path) -> int:
    try:
        relative = target.relative_to(cache_root)
    except ValueError:
        return OUTSIDE_CACHE

    if (
        len(relative.parts) != 3
        or ".." in relative.parts
        or relative.parts[0] in {"", "."}
        or relative.parts[1:] != ("harness", component)
    ):
        return INVALID_CACHE_TARGET
    return _validate_resolved_target(target, cache_root, relative)


def classify_pinned(target: Path, component: str, claude_root: Path) -> int:
    try:
        relative = target.relative_to(claude_root)
    except ValueError:
        return OUTSIDE_CACHE

    if not relative.parts or not relative.parts[0].startswith(
        (".escapement-pinned", ".cws-pinned")
    ):
        return OUTSIDE_CACHE
    if (
        len(relative.parts) != 3
        or ".." in relative.parts
        or relative.parts[1:] != ("harness", component)
    ):
        return INVALID_CACHE_TARGET
    return _validate_resolved_target(target, claude_root, relative)


def classify(target_arg: str, component: str, root_arg: str, layout: str) -> int:
    if component not in {"bin", "schemas"}:
        return INVALID_CACHE_TARGET

    target = Path(target_arg)
    root = Path(root_arg)
    if not target.is_absolute() or not root.is_absolute():
        return OUTSIDE_CACHE
    if layout == "versioned-cache":
        return classify_versioned_cache(target, component, root)
    if layout == "pinned":
        return classify_pinned(target, component, root)
    return INVALID_CACHE_TARGET


def main() -> int:
    if len(sys.argv) != 5:
        return INVALID_CACHE_TARGET
    return classify(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])


if __name__ == "__main__":
    raise SystemExit(main())
