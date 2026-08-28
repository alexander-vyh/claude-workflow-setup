#!/usr/bin/env python3
"""Keep durable operational artifacts out of shared temp directories.

PreToolUse. Eight Codex threads shared `/private/tmp/nme1-policy-operation` for
six days: the policy source that mutated live security config, a 346KB rollback
`state.json`, and a single mutable `scoped-review.md` holding a REJECT verdict
that any re-review overwrote in place. Nothing there was versioned, reviewable,
or isolated per session.

The trigger is over-application of a real convention. A repo may legitimately
document "results go to /tmp" for a script that runs on a REMOTE endpoint --
short-lived, disposable, reboot-cleaned. That sentence stays true-sounding when
carried to the workstation, where the same path is instead durable shared state
under a world-writable, predictably-named parent.

So this gate does NOT police temp files. It fires at one intersection:
EXECUTABLE SOURCE being written into a SHARED temp root. Audit output (.csv,
.json, .txt), session-scoped scratch (`/private/tmp/claude-<pid>/...`), reads,
and mktemp-style directories all stay allowed -- they are the good cases the
convention exists to serve.

Fail-open by construction: unparseable commands, dynamic paths, and unknown
payload shapes ALLOW. A guard that blocks when it cannot see is worse than the
waste it prevents. gate-design compliant: the denial names the repair, the
escape is inline and self-documenting, and signal is persistent.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Iterator, NoReturn

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _gate_signal import record as _record_signal
except ImportError:  # pragma: no cover
    def _record_signal(*_a, **_k) -> None:
        return None

GATE_NAME = "shared_tmp_artifact_gate"

# Roots that every session on the machine shares. `/private/tmp` and `/tmp` are
# the same directory on macOS; both spellings appear in real commands.
SHARED_TMP_ROOTS = ("/private/tmp", "/tmp", "/private/var/tmp", "/var/tmp")

# Executable source only. Deliberately NOT .json/.csv/.txt/.md -- those are the
# documented audit-output case, and denying them would make the gate coercive.
DURABLE_SUFFIXES = frozenset({
    ".py", ".sh", ".bash", ".zsh", ".rb", ".js", ".mjs", ".cjs",
    ".ts", ".tsx", ".go", ".rs", ".pl", ".php", ".lua", ".sql",
})

# Shell metacharacters that make a path non-literal. Any of these -> fail open.
_DYNAMIC_CHARS = frozenset("$`*?[]{}~")

_WAIVER = re.compile(r"#\s*tmp-artifact-waiver:\s*(.+)", re.IGNORECASE)
_WAIVER_MIN_REASON = 20

# mktemp/mkdtemp-style and host scratch dirs are per-session, not shared.
_SESSION_SCOPED = re.compile(
    r"""^(?:
        (?:claude|codex|pytest|tmp|escapement)[-.] |
        \.
    )""",
    re.VERBOSE | re.IGNORECASE,
)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", re.IGNORECASE)

# `*** Add File: path` / `*** Update File: path` inside a Codex apply_patch body.
_PATCH_TARGET = re.compile(
    r"^\*\*\*\s+(?:Add|Update|Move to)\s+File:\s*(.+?)\s*$", re.MULTILINE
)

_COPY_VERBS = frozenset({"cp", "mv", "install", "rsync"})
_STREAM_VERBS = frozenset({"tee"})


def has_waiver(text: str) -> bool:
    """An inline waiver with a real reason. Presence alone is not enough."""
    match = _WAIVER.search(text or "")
    return bool(match) and len(match.group(1).strip()) >= _WAIVER_MIN_REASON


def _shared_tmp_root(path: str) -> str | None:
    for root in SHARED_TMP_ROOTS:
        if path == root or path.startswith(root + "/"):
            return root
    return None


def is_session_scoped(path: str, root: str) -> bool:
    """True when the path sits under a per-session subdirectory of a temp root.

    `/private/tmp/claude-502/<uuid>/scratchpad/x.py` is this session's own
    scratch. `/private/tmp/nme1-policy-operation/x.py` is shared ground.
    """
    remainder = path[len(root):].lstrip("/")
    if not remainder:
        return False
    first = remainder.split("/", 1)[0]
    return bool(_SESSION_SCOPED.match(first) or _UUID.search(first))


def is_durable_artifact(path: str) -> bool:
    return Path(path).suffix.lower() in DURABLE_SUFFIXES


def offending_path(path: str) -> str | None:
    """Return `path` when writing it would strand a durable artifact.

    Fail-open on anything non-literal -- a dynamic path is not evidence.
    """
    if not path or any(char in path for char in _DYNAMIC_CHARS):
        return None
    root = _shared_tmp_root(path)
    if root is None:
        return None
    if is_session_scoped(path, root):
        return None
    if not is_durable_artifact(path):
        return None
    return path


def _write_targets(tokens: list[str]) -> Iterator[str]:
    """Yield literal write destinations from one shell segment."""
    if not tokens:
        return
    verb = Path(tokens[0]).name
    for index, token in enumerate(tokens):
        # `> path`, `>> path`, and the unspaced `>path` / `>>path` forms.
        if token in (">", ">>", "1>", "2>", "&>") and index + 1 < len(tokens):
            yield tokens[index + 1]
        elif token.startswith(">"):
            candidate = token.lstrip(">&12").strip()
            if candidate:
                yield candidate
    if verb in _STREAM_VERBS:
        for token in tokens[1:]:
            if not token.startswith("-"):
                yield token
    elif verb in _COPY_VERBS:
        operands = [t for t in tokens[1:] if not t.startswith("-")]
        if len(operands) >= 2:
            yield operands[-1]


def _segments(command: str) -> Iterator[list[str]]:
    normalized = command.replace("\\\n", " ")
    for raw in re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", normalized):
        try:
            parts = shlex.split(raw, comments=False)
        except ValueError:
            continue  # unbalanced quotes -> fail open
        if parts:
            yield parts


def command_offenders(command: str) -> list[str]:
    found: list[str] = []
    for tokens in _segments(command):
        for target in _write_targets(tokens):
            hit = offending_path(target)
            if hit and hit not in found:
                found.append(hit)
    return found


def patch_offenders(tool_input: dict[str, Any]) -> list[str]:
    """Literal targets from a Codex apply_patch / Claude Write payload.

    The apply_patch payload shape is not contractually documented, so every
    string value is scanned and anything unrecognised simply yields nothing.
    """
    found: list[str] = []
    candidates: list[str] = []
    for key in ("file_path", "path", "filename"):
        value = tool_input.get(key)
        if isinstance(value, str):
            candidates.append(value)
    for value in tool_input.values():
        if isinstance(value, str) and "*** " in value:
            candidates.extend(_PATCH_TARGET.findall(value))
    for candidate in candidates:
        hit = offending_path(candidate.strip())
        if hit and hit not in found:
            found.append(hit)
    return found


def repair_for(path: str, cwd: str) -> str:
    name = Path(path).name
    return str(Path(cwd) / name) if cwd else name


def _emit_deny(reason: str) -> NoReturn:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    # Exit 0 with the decision payload: escapement's Codex dispatcher
    # treats a non-zero gate exit as a CRASH and discards the verdict.
    raise SystemExit(0)


def _deny(paths: list[str], cwd: str, command: str) -> NoReturn:
    path = paths[0]
    _record_signal(
        gate_name=GATE_NAME, decision="deny",
        reason=f"durable artifact into shared temp: {path}", command=command[:400],
    )
    _emit_deny(
        f"`{path}` puts executable source in a shared temp root. Every session "
        f"on this machine reads and writes that path, so the file is "
        f"unversioned, unreviewable, and racy between concurrent sessions -- and "
        f"a second session overwrites it with no history.\n\n"
        f"Write it inside the repository or its worktree instead, e.g. "
        f"`{repair_for(path, cwd)}`, where it is diffable and isolated.\n\n"
        f"If this really is throwaway scratch, put it under a per-session "
        f"directory (`$TMPDIR`, or a `mktemp -d` path) rather than a shared "
        f"name. To override deliberately, append a reason: "
        f"`# tmp-artifact-waiver: <why this must be shared and unversioned>`."
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    cwd = str(data.get("cwd") or data.get("workingDirectory") or os.getcwd())

    tool_name = data.get("tool_name")
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str) or not command:
            return 0
        if has_waiver(command):
            _record_signal(gate_name=GATE_NAME, decision="waived",
                           reason="inline tmp-artifact-waiver", command=command[:400])
            return 0
        offenders = command_offenders(command)
        if offenders:
            _deny(offenders, cwd, command)
        return 0

    blob = json.dumps(tool_input)
    if has_waiver(blob):
        _record_signal(gate_name=GATE_NAME, decision="waived",
                       reason="inline tmp-artifact-waiver", command=blob[:400])
        return 0
    offenders = patch_offenders(tool_input)
    if offenders:
        _deny(offenders, cwd, blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
