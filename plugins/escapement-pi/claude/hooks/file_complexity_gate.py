#!/usr/bin/env python3
"""PreToolUse gate: two-tier file-length control (soft guidance + hard stop).

Line count is a weak, pragmatic proxy — not a defect/edit-reliability measurement.
The research behind this gate (.research/file-complexity-gate-2026-06-23/) found no
evidence for a defect-reducing file-LOC ceiling; the honest rationale is that >500
lines *correlates* with two real concerns this repo cares about:
  • Humans: the file likely holds more than one responsibility and gets hard to
    review and navigate atomically.
  • Agents: large files inflate the working set; LLM edit reliability degrades as the
    edit target grows (line-number mis-targeting, weaker localization — see arXiv
    2602.16069, 2506.13186). Successful agent edits keep the working set small.

Two tiers:
  SOFT_LIMIT (500)  — exceed → non-blocking `systemMessage` nudge (allowed). A poor
                      proxy, but >500 LOC is where the concerns above start to bite.
  HARD_LIMIT (1000) — exceed → deny (blocked), human-overridable by waiver.

Exemptions (fail-open on detection failure):
  Path fragments: /vendor/, /node_modules/, /migrations/, /generated/,
                  /testdata/, /fixtures/, /.beads/, /dist/, /build/
  File suffixes:  generated code (.pb.go, _pb2.py, _pb2_grpc.py, .gen.go, .g.dart,
                  .freezed.dart, .generated.ts/.js, .pb.cc, .pb.h, go.sum, .min.js,
                  .min.css, .snap, .lock, -lock.json) and passive data/docs
                  (.json, .yaml, .yml, .csv, .tsv, .svg, .ipynb, .geojson, .md,
                  .txt, .rst) — "extract a sibling module" is meaningless for these.
  Waiver comment: one of the first 5 lines contains
                  `# file-complexity-waiver: <reason>` (or //, --, /* variants).
                  Suppresses BOTH tiers (an acknowledged file is not nagged).
  Env var:        FILE_COMPLEXITY_WAIVER=<reason>

Escape path IN the denial (gate-design rule 1):
  Extract a cohesive responsibility into a sibling module, OR add a waiver.

Signal (gate-design rule 2):
  Emits to _gate_signal with gate_name='file-complexity', decision one of
  soft-nudge | deny | waiver-accepted.

Value-not-presence (gate-design rule 3):
  Waiver must be non-empty after the colon; bare marker is rejected.

Exit codes:
  0  — always. A denial rides the stdout envelope, not the exit status: Codex
       discards a gate that exits non-zero, and Claude honors the envelope
       either way.
"""

from __future__ import annotations

import json
import os
import sys

SOFT_LIMIT = 500
HARD_LIMIT = 1000

_EXEMPT_PATH_FRAGMENTS = (
    "/vendor/",
    "/node_modules/",
    "/migrations/",
    "/generated/",
    "/testdata/",
    "/fixtures/",
    "/.beads/",
    "/dist/",
    "/build/",
)

_EXEMPT_SUFFIXES = (
    # generated code
    ".pb.go",
    ".pb.cc",
    ".pb.h",
    "_pb2.py",
    "_pb2_grpc.py",
    ".gen.go",
    ".g.dart",
    ".freezed.dart",
    ".generated.ts",
    ".generated.js",
    "go.sum",
    ".min.js",
    ".min.css",
    ".snap",
    ".lock",
    "-lock.json",
    # passive data / docs (no logic to extract into a sibling module)
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".svg",
    ".ipynb",
    ".geojson",
    ".md",
    ".txt",
    ".rst",
)

_WAIVER_PREFIXES = (
    "# file-complexity-waiver:",
    "// file-complexity-waiver:",
    "/* file-complexity-waiver:",
    "-- file-complexity-waiver:",
    "<!-- file-complexity-waiver:",
)


def _is_exempt(file_path: str) -> bool:
    norm = file_path.replace(os.sep, "/")
    if any(frag in norm for frag in _EXEMPT_PATH_FRAGMENTS):
        return True
    name = os.path.basename(norm)
    return any(name.endswith(suf) for suf in _EXEMPT_SUFFIXES)


def _has_waiver(first_lines: list[str]) -> bool:
    env_reason = os.environ.get("FILE_COMPLEXITY_WAIVER", "").strip()
    if env_reason:
        return True
    for line in first_lines:
        stripped = line.strip()
        for prefix in _WAIVER_PREFIXES:
            if stripped.startswith(prefix):
                reason = stripped[len(prefix):].strip()
                return bool(reason)  # value-not-presence: reason must be non-empty
    return False


def decide(projected: int, file_path: str, first_lines: list[str]) -> str:
    """Pure decision core. Returns one of:
    exempt | pass | waiver | soft | hard.

    Ordering matters: exemption first, then the silent-pass band, then waiver
    (which suppresses both the soft nudge and the hard block), then the tiers.
    """
    if _is_exempt(file_path):
        return "exempt"
    if projected <= SOFT_LIMIT:
        return "pass"
    if _has_waiver(first_lines):
        return "waiver"
    if projected <= HARD_LIMIT:
        return "soft"
    return "hard"


def _emit_signal(decision: str, file_path: str, projected: int) -> None:
    try:
        hooks_dir = os.path.dirname(os.path.abspath(__file__))
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)
        from _gate_signal import record  # type: ignore
        record(
            gate_name="file-complexity",
            decision=decision,
            reason=f"{file_path}: projected {projected} lines "
                   f"(soft {SOFT_LIMIT} / hard {HARD_LIMIT})",
            file=file_path,
            projected_lines=projected,
        )
    except Exception:
        pass  # signal capture must never block the gate


def build_soft_message(file_path: str, projected: int) -> str:
    """Non-blocking guidance shown at the soft tier — framed for humans AND agents.

    Line count is only a proxy; the message names the real (non-LOC) complexity
    signals it stands in for and why each matters, so the reader can judge whether
    to split or waive rather than react to the number.
    """
    name = os.path.basename(file_path)
    return (
        f"{name}: {projected} lines (nudge at {SOFT_LIMIT}, block at {HARD_LIMIT}).\n"
        f"Length is a proxy for mixed responsibilities, long functions, and "
        f"near-duplicate blocks that make edits land on the wrong copy.\n"
        f"Doing several things? Extract one into a sibling module. Long but flat and "
        f"cohesive? Waive it: `# file-complexity-waiver: <reason>` in the first 5 lines."
    )


def deny_response(file_path: str, projected: int) -> dict:
    """Blocking response at the hard tier — carries the human override path."""
    name = os.path.basename(file_path)
    return {
        "permissionDecision": "deny",
        "denyReason": (
            f"{name} would be {projected} lines — past the {HARD_LIMIT}-line limit.\n"
            f"Extract a cohesive responsibility into a sibling module before writing here.\n"
            f"If it is genuinely flat, cohesive, or generated, waive it: add "
            f"`# file-complexity-waiver: <reason>` in the first 5 lines, or set "
            f"FILE_COMPLEXITY_WAIVER=<reason>.\n"
            f"Already exempt: vendor/, node_modules/, migrations/, generated/, fixtures/, "
            f"dist/, build/"
        ),
    }


def _host_output():
    """The shared builders for host-visible output, or None if unavailable."""
    hooks_dir = os.path.dirname(os.path.abspath(__file__))
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    try:
        import _host_output  # type: ignore
    except ImportError:
        return None
    return _host_output


def deny_envelope(file_path: str, projected: int) -> dict:
    """The denial, in the one shape every host honors. See _host_output."""
    reason = deny_response(file_path, projected)["denyReason"]
    shared = _host_output()
    if shared is None:  # fail open on the transport, not on the verdict
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    return shared.deny(reason)


def soft_envelope(file_path: str, projected: int) -> dict:
    """The nudge, on both names the hosts use for an advisory message.

    Sent only as `systemMessage` this tier was invisible on Codex: a live
    session in the soft band was asked whether it had been told anything about
    a line count and said no. Codex passes only `additionalContext` through.
    """
    message = build_soft_message(file_path, projected)
    shared = _host_output()
    if shared is None:
        return {"systemMessage": message}
    return shared.advisory(message)


def _respond(decision: str, file_path: str, projected: int) -> int:
    """Emit the decision. One shape for every host, so a verdict cannot drift."""
    if decision in ("exempt", "pass"):
        return 0
    if decision == "waiver":
        _emit_signal("waiver-accepted", file_path, projected)
        return 0
    if decision == "soft":
        _emit_signal("soft-nudge", file_path, projected)
        json.dump(soft_envelope(file_path, projected), sys.stdout)
        return 0
    _emit_signal("deny", file_path, projected)
    json.dump(deny_envelope(file_path, projected), sys.stdout)
    return 0


def _parse_patch(command: str, cwd: str):
    """Delegate to the shared reader of Codex's apply_patch payload."""
    hooks_dir = os.path.dirname(os.path.abspath(__file__))
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    try:
        from _codex_patch import first_target  # type: ignore
    except ImportError:
        return None  # fail open rather than block a session on a missing helper
    return first_target(command, cwd)


def patch_projection(command: str, cwd: str) -> tuple[str, int, list[str]] | None:
    """Project the post-patch line count for a Codex `apply_patch` payload.

    Parsing the patch belongs to `_codex_patch`, which owns Codex's format for
    every gate that needs it. What is decided here is what this gate measures:
    an added file is its own length, and an updated file is what is already on
    disk plus the delta. An unreadable target returns None and fails open --
    guessing a baseline would deny on a number matching no file.
    """
    parsed = _parse_patch(command, cwd)
    if parsed is None:
        return None
    kind, path, added, removed = parsed
    if kind == "Add":
        return path, added, []
    if kind == "Delete":
        return None  # a deletion cannot push a file over a length limit
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            existing = handle.read().splitlines()
    except OSError:
        return None
    return path, len(existing) + added - removed, existing[:5]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "apply_patch"):
        return 0

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0

    if tool_name == "apply_patch":
        projection = patch_projection(
            tool_input.get("command", "") or "", str(data.get("cwd") or "")
        )
        if projection is None:
            return 0
        file_path, projected, first_lines = projection
        if _is_exempt(file_path):
            return 0
        return _respond(
            decide(projected, file_path, first_lines), file_path, projected
        )

    file_path = tool_input.get("file_path", "")
    if not file_path or _is_exempt(file_path):
        return 0  # exempt short-circuit: skip the file read entirely

    try:
        if tool_name == "Write":
            content = tool_input.get("content", "")
            lines = content.splitlines()
            projected = len(lines)
            first_lines = lines[:5]
        else:  # Edit
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    existing = f.read()
            except OSError:
                return 0  # new file or unreadable — fail-open
            existing_lines = existing.splitlines()
            old_string = tool_input.get("old_string", "")
            new_string = tool_input.get("new_string", "")
            delta = len(new_string.splitlines()) - len(old_string.splitlines())
            projected = len(existing_lines) + delta
            first_lines = existing_lines[:5]
    except Exception:
        return 0  # fail-open on any unexpected error

    return _respond(decide(projected, file_path, first_lines), file_path, projected)


if __name__ == "__main__":
    sys.exit(main())
