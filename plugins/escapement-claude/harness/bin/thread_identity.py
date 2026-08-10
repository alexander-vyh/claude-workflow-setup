#!/usr/bin/env python3
"""Canonical continuation-harness state identity and directory resolution."""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import sys
from collections.abc import Mapping
from typing import Optional

_ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidActorIdentity(ValueError):
    """A present CLAUDE_AGENT_ID cannot safely identify actor-owned state."""


def sanitize_session_id(session_id: Optional[str]) -> str:
    """Reduce a session id to the legacy safe single path component."""
    if not session_id or not isinstance(session_id, str):
        return ""
    return re.sub(r"[^A-Za-z0-9_-]", "", session_id)[:128]


def _actor_id(environ: Mapping[str, str]) -> Optional[str]:
    if "CLAUDE_AGENT_ID" not in environ:
        return None
    actor_id = environ.get("CLAUDE_AGENT_ID", "")
    if not isinstance(actor_id, str) or _ACTOR_RE.fullmatch(actor_id) is None:
        raise InvalidActorIdentity(
            "CLAUDE_AGENT_ID is present but invalid; expected 1-128 letters, "
            "digits, dot, underscore, or dash, starting with a letter or digit"
        )
    return actor_id


def actor_key(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Stable collision-resistant filesystem component for the current actor."""
    actor_id = _actor_id(os.environ if environ is None else environ)
    if actor_id is None:
        return None
    digest = hashlib.sha256(actor_id.encode("utf-8")).hexdigest()
    return f"agent-{digest}"


def state_identity(
    session_id: Optional[str],
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Identity stored in checkout records; distinct for parent and subagents."""
    env = os.environ if environ is None else environ
    sid = sanitize_session_id(session_id) or "current"
    override = env.get("HARNESS_THREAD_DIR")
    if override:
        digest = hashlib.sha256(override.encode("utf-8")).hexdigest()
        return f"{sid}:override-{digest}"
    key = actor_key(env)
    return sid if key is None else f"{sid}:{key}"


def resolve_thread_dir(
    session_id: Optional[str],
    harness_root: pathlib.Path,
    environ: Optional[Mapping[str, str]] = None,
) -> pathlib.Path:
    """Resolve the exact override, legacy parent dir, or actor-owned subdir."""
    env = os.environ if environ is None else environ
    override = env.get("HARNESS_THREAD_DIR")
    if override:
        return pathlib.Path(override)
    sid = sanitize_session_id(session_id) or "current"
    parent = pathlib.Path(harness_root) / "threads" / sid
    key = actor_key(env)
    return parent if key is None else parent / "agents" / key


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="resolve the active harness thread directory")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--harness-root", required=True)
    args = parser.parse_args(argv)
    try:
        print(resolve_thread_dir(args.session_id, pathlib.Path(args.harness_root)))
    except InvalidActorIdentity as exc:
        print(f"invalid actor identity: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
