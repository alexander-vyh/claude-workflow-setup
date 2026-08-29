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


def is_actor_state_dir(thread_dir: pathlib.Path) -> bool:
    """Whether this is one canonical actor directory below a parent session."""
    state_dir = pathlib.Path(thread_dir)
    return state_dir.parent.name == "agents"


def iter_state_dirs(threads_root: pathlib.Path):
    """Yield supported state dirs: legacy parents and exactly one actor layer."""
    root = pathlib.Path(threads_root)
    if not root.is_dir():
        return
    for parent in sorted(path for path in root.iterdir() if path.is_dir()):
        yield parent
        agents = parent / "agents"
        if agents.is_dir():
            yield from sorted(path for path in agents.iterdir() if path.is_dir())


def canonical_harness_root(thread_dir: pathlib.Path) -> pathlib.Path | None:
    """Resolve a harness root only for the two canonical state-dir shapes."""
    state_dir = pathlib.Path(thread_dir)
    if is_actor_state_dir(state_dir) and state_dir.parent.parent.parent.name == "threads":
        return state_dir.parent.parent.parent.parent
    if state_dir.parent.name == "threads":
        return state_dir.parent.parent
    return None


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="resolve the active harness thread directory"
    )
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


def session_id_for_state_dir(thread_dir: pathlib.Path) -> Optional[str]:
    """Resolve the parent session bound to one legacy or actor state directory.

    Moved here from execution_supervisor when the delegated-execution ledger was
    removed: this is state-directory identity, which is this module's contract.
    """
    import json

    from trusted_source import is_trusted_file

    state_dir = pathlib.Path(thread_dir)
    is_actor = is_actor_state_dir(state_dir)
    path_session = state_dir.parent.parent.name if is_actor else state_dir.name
    mode_path = state_dir / "session_mode.json"
    if not mode_path.is_symlink() and is_trusted_file(mode_path):
        try:
            mode = json.loads(mode_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            mode = None
        if isinstance(mode, dict):
            session_id = mode.get("session_id")
            if (
                isinstance(session_id, str)
                and session_id
                and (sanitize_session_id(session_id) or "current") == path_session
            ):
                return session_id
    return None if is_actor else path_session
