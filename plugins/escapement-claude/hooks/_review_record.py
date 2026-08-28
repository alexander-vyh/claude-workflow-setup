#!/usr/bin/env python3
"""Host-neutral review-record store for the independent-review gate.

`review_gate.py` must decide, at `bd close`, whether an independent critical
review of *this bead's current work* actually happened. That decision needs
evidence that outlives the session and is visible to every host, so the record
lives in Beads itself (`metadata.escapement_review`) and travels with the bead
through Dolt rather than sitting in per-session `/tmp` state.

Why not the Claude `Agent` tool metadata (what the old gate used): Codex
exposes no Agent event at all — its reliable surface is `Bash` PreToolUse plus
`SessionStart`. `agent-surfaces/manifest.json` therefore marked `review_gate`
`codex: unsupported`, "Depends on Claude Agent tool metadata and
review-dispatch semantics". Moving the evidence to `bd` + a git work
fingerprint makes the core enforceable on both hosts; the Agent-dispatch
ledger stays as a Claude-only *corroborating* check (see `review_gate.py`).

Three things this module owns:

  work_fingerprint()  identifies the state of the work under review, so a
                      review followed by more edits is detectably stale.
  read/write_record() persist the verdict against the bead via `bd`.
  validate_findings() the value-not-presence bar (gate-design.md Rule 3) —
                      a recorded review must carry substance, not `tbd`.

Every subprocess call fails open (returns None) rather than raising: a hook
that crashes on an unusual git state would block work for a reason that has
nothing to do with review discipline.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
from typing import Any

# Beads metadata key holding the review record. Namespaced so it cannot
# collide with a repository's own metadata conventions.
METADATA_KEY = "escapement_review"

# Record schema version. Bumped when the shape changes.
#
# v2 (escapement-1l04) adds `verdict_source`, `verdict_digest`, `blocking`, and
# `response`. v1 records were written under an oracle that never looked at the
# reviewer's output at all, so the gate refuses them — honouring them would
# grandfather in exactly the evidence v2 exists to stop accepting, and since the
# recording CLI only ever writes the current version and hand-writing the
# metadata key is denied outright, there is no door here to leave open.
RECORD_VERSION = 2

# Versions we can still *parse*. A v1 record must come back as a record rather
# than as None, so the gate can say "this review predates the current schema,
# re-record it" instead of "no review is on record". Telling an agent that no
# review exists when one plainly does sends it to re-run a reviewer without ever
# explaining why the first one stopped counting — a denial that misdescribes its
# own cause fails Internal Transparency (gate-design.md).
READABLE_RECORD_VERSIONS = {1, 2}

_SUBPROCESS_TIMEOUT = 5

# Untracked files above this size contribute their length rather than their
# bytes, so a PreToolUse hook cannot be made slow by a large scratch file.
_MAX_UNTRACKED_BYTES = 2 * 1024 * 1024

# Substance threshold for recorded findings. Deliberately higher than the
# 20-char waiver-reason bar in gate-design.md: a waiver is one sentence of
# justification, whereas a review verdict that fits in 20 characters is a
# rubber stamp by construction.
MIN_FINDINGS_CHARS = 120

# Null patterns rejected outright, per the standard waiver convention.
_NULL_PATTERNS = {
    "", "-", "?", "??", "???", "n/a", "na", "none", "tbd", "todo", "wip",
    "fixme", "ok", "okay", "lgtm", "looks good", "looks good to me",
    "no findings", "no issues", "all good", "fine", "passed", "pass",
    "clean", "approved", "done",
}

# Agent types that are structurally independent reviewers: dispatched with no
# shared conversation history, so they see the artifact rather than the
# implementer's rationalisation of it.
#
# Every entry must correspond to a real agent definition in `claude/agents/`,
# in bare and plugin-namespaced form. `code-reviewer` used to be listed here
# and is not a registered agent in this install — an allowlist entry that
# names no dispatchable agent is dead weight at best, and at worst hides the
# reverse drift, where a rename makes `independent: true` unreachable while
# every test stays green. test_review_record.py pins this set against the
# agent directory so the drift cannot go unnoticed.
INDEPENDENT_REVIEWER_TYPES = {
    "adversarial-reviewer",
    "escapement:adversarial-reviewer",
    "test-quality-reviewer",
    "escapement:test-quality-reviewer",
}

# Beads ids seen in this ecosystem: `escapement-iw8s`, `escapement-mol-4ef`,
# `escapement-858.4`, `cake-4cq.1.1`. Prefix segments are lowercase words; the
# final segment is a short alphanumeric suffix, optionally followed by
# dot-separated child indices.
BEAD_ID_RE = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\.\d+)*\b")


def _run(args: list[str], cwd: str | None = None) -> str | None:
    """Run a command and return stripped stdout, or None on any failure."""
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# Work fingerprint
# ---------------------------------------------------------------------------

def work_fingerprint(cwd: str | None = None) -> str | None:
    """Return a digest of the work tree's current state, or None if unknowable.

    Covers committed HEAD, tracked modifications (staged and unstaged), and the
    names *and contents* of untracked files.

    Untracked contents are included deliberately. Hashing only the path list
    was tried first and is wrong: in a fresh worktree — the normal shape for
    Escapement work — most new code is untracked until the first commit, so a
    review would never go stale no matter how much was rewritten after it,
    which is precisely the failure this fingerprint exists to catch. Build
    output is not a concern here because `--exclude-standard` already applies
    `.gitignore`.

    The digest is taken against the branch's merge-base, NOT against HEAD.
    Against HEAD, committing changes the digest with byte-identical content —
    HEAD moves and the diff empties — so the ordinary sequence *review, commit,
    close* was denied every single time, and an earlier version of this file's
    tests enshrined that false positive as correct behaviour. Diffing from the
    merge-base makes the fingerprint a function of the work, not of whether the
    work happens to be committed yet.

    Returning None is a real answer — "this is not a git work tree, or git is
    unavailable" — and callers must treat it as "staleness is not checkable"
    rather than as "stale".
    """
    base = _diff_base(cwd)
    if base is None:
        return None

    # Diffing from the merge-base covers committed-on-branch, staged, and
    # unstaged changes in one pass.
    tracked_diff = _run(["git", "diff", base], cwd=cwd) or ""
    untracked = _run(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=cwd
    ) or ""

    digest = hashlib.sha256()
    digest.update(tracked_diff.encode("utf-8", "replace"))
    digest.update(b"\x00")

    root = pathlib.Path(cwd) if cwd else pathlib.Path.cwd()
    # Sort so the fingerprint does not depend on git's listing order.
    for name in sorted(untracked.splitlines()):
        if not name.strip():
            continue
        digest.update(name.encode("utf-8", "replace"))
        digest.update(b"\x00")
        digest.update(_untracked_digest(root / name))
        digest.update(b"\x00")
    return digest.hexdigest()


def _diff_base(cwd: str | None) -> str | None:
    """Return the commit to diff the current work against, or None.

    Prefers the merge-base with the repository's default branch so that
    committing work does not change the fingerprint. Falls back to HEAD when
    there is no reachable default branch (a detached tree, a fresh repo, a
    clone without remotes) — there the digest degrades to the old
    HEAD-relative behaviour, which is still correct, just stricter about
    commits.
    """
    head = _run(["git", "rev-parse", "HEAD"], cwd=cwd)
    if head is None:
        return None

    default_ref = _run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=cwd
    )
    candidates = [default_ref] if default_ref else []
    candidates += ["origin/main", "origin/master", "main", "master"]

    for candidate in candidates:
        if not candidate:
            continue
        base = _run(["git", "merge-base", "HEAD", candidate], cwd=cwd)
        if base:
            return base
    return head


def store_available(cwd: str | None = None) -> bool:
    """True when `bd` itself can run here.

    Separates "the task store is down" from "that bead could not be read".
    Without this split, any command whose target does not resolve to a real
    bead fell through the unavailable-store fail-open and closed unchecked —
    which is exactly how `bd close -r "finished the work"` got through, since
    the reason prose was looked up as a bead id and naturally failed.
    """
    return _run(["bd", "--version"], cwd=cwd) is not None


def _untracked_digest(path: pathlib.Path) -> bytes:
    """Digest one untracked file's contents, bounded so the hook stays cheap.

    A file larger than the cap contributes its size rather than its bytes: an
    edit that changes a large file's length is still detected, and one that
    does not is a trade we accept to keep a PreToolUse hook fast. Unreadable
    paths (a dangling symlink, a directory, a permissions error) contribute a
    marker instead of raising — a fingerprint that crashes would block every
    close in the repository.
    """
    try:
        size = path.stat().st_size
        if size > _MAX_UNTRACKED_BYTES:
            return f"size:{size}".encode("ascii")
        return hashlib.sha256(path.read_bytes()).digest()
    except OSError:
        return b"unreadable"


def changed_paths_since(fingerprint_cwd: str | None = None) -> list[str]:
    """Return the paths in play, for reporting *what* went stale.

    Covers tracked modifications and untracked files, matching what
    `work_fingerprint` actually hashes. Reporting only tracked changes was
    tried first and misdirects the repair: an untracked-only edit would deny
    the close while naming unrelated tracked files as the cause.

    Used only to make a stale-review denial specific ("3 files changed since
    the review") rather than a bare assertion. Never used for the decision.
    """
    base = _diff_base(fingerprint_cwd) or "HEAD"
    tracked = _run(["git", "diff", "--name-only", base], cwd=fingerprint_cwd) or ""
    untracked = _run(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=fingerprint_cwd
    ) or ""
    paths: list[str] = []
    for line in (*tracked.splitlines(), *untracked.splitlines()):
        stripped = line.strip()
        if stripped and stripped not in paths:
            paths.append(stripped)
    return paths


# ---------------------------------------------------------------------------
# Record persistence (via bd)
# ---------------------------------------------------------------------------

class _Unavailable:
    """Sentinel: the task store could not be consulted at all."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<UNAVAILABLE>"


#: Returned when `bd` itself could not be reached or did not answer.
#:
#: This must stay distinct from `None` ("this bead has no review on record").
#: Collapsing the two would make a transient `bd`/Dolt failure deny every close
#: in the repository — a gate whose infrastructure failure mode is "stop all
#: work" causes more harm than the review lapse it exists to prevent. Absence
#: of a review is the agent's problem and fails closed; absence of the store is
#: ours and fails open.
UNAVAILABLE = _Unavailable()


def read_record(bead_id: str, cwd: str | None = None) -> dict[str, Any] | None | _Unavailable:
    """Return the bead's review record, None if it has none, or UNAVAILABLE.

    A malformed or unrecognised-version record returns None rather than
    UNAVAILABLE: `bd` answered, so the store is reachable, and a record we
    cannot read is not a review we can rely on.
    """
    raw = _run(["bd", "show", bead_id, "--json"], cwd=cwd)
    if raw is None:
        # `bd` failing on ONE bead is not the store being down. Only report
        # UNAVAILABLE when bd itself cannot run; otherwise this bead simply
        # could not be read, which must fail closed.
        return UNAVAILABLE if not store_available(cwd) else None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        return None

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    stored = metadata.get(METADATA_KEY)
    if stored is None:
        return None

    # bd stores metadata values as strings; tolerate an already-decoded dict in
    # case a future bd version preserves JSON types.
    if isinstance(stored, str):
        try:
            record = json.loads(stored)
        except json.JSONDecodeError:
            return None
    elif isinstance(stored, dict):
        record = stored
    else:
        return None

    if not isinstance(record, dict):
        return None
    if record.get("v") not in READABLE_RECORD_VERSIONS:
        return None
    return record


def write_record(bead_id: str, record: dict[str, Any], cwd: str | None = None) -> bool:
    """Persist a review record onto a bead. Returns True on success."""
    payload = json.dumps(record, separators=(",", ":"), sort_keys=True)
    result = _run(
        ["bd", "update", bead_id, "--set-metadata", f"{METADATA_KEY}={payload}"],
        cwd=cwd,
    )
    return result is not None


def build_record(
    bead_id: str,
    findings: str,
    reviewer: str,
    fingerprint: str | None,
    recorded_at: str,
    host: str = "cli",
    verdict_source: str | None = None,
    verdict_digest: str | None = None,
    blocking: bool = False,
    response: str | None = None,
) -> dict[str, Any]:
    """Assemble a well-formed review record.

    `findings` is the authoritative verdict. When `verdict_source` is
    `"captured"` those are the reviewer's own bytes; `response` then holds the
    implementer's account of the work, which is kept because it is useful to a
    human reader and ignored by the gate because a check that reads text
    authored by the party it constrains is not a check.
    """
    return {
        "v": RECORD_VERSION,
        "bead": bead_id,
        "reviewer": reviewer,
        "fingerprint": fingerprint,
        "recorded_at": recorded_at,
        "findings": findings.strip(),
        "verdict_source": verdict_source,
        "verdict_digest": verdict_digest,
        "blocking": blocking,
        "response": (response or "").strip() or None,
        "host": host,
    }


# ---------------------------------------------------------------------------
# Value validation (gate-design.md Rule 3)
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower().rstrip(".!")


def validate_findings(
    findings: str | None,
    bead_id: str,
    bead_title: str | None = None,
) -> tuple[bool, str]:
    """Check that recorded findings carry substance. Returns (ok, error).

    Rejects, in order of how cheaply an agent under pressure would reach for
    them: nothing at all, a null pattern like `lgtm`, text too short to be a
    review, and text that merely restates the bead it claims to have reviewed.

    This is the difference between "a review was recorded" and "a review
    happened" — a presence-only check produces mock bureaucracy by
    construction (Wiesche et al. 2013).
    """
    if findings is None:
        return False, "no review findings supplied."

    stripped = findings.strip()
    if not stripped:
        return False, "review findings are empty."

    normalized = _normalize(stripped)
    if normalized in _NULL_PATTERNS:
        return False, (
            f"review findings {stripped!r} are a rubber stamp, not a review. "
            "Record what the reviewer actually examined and what it found "
            "(including 'no defect found in X, Y, Z' if that is the verdict)."
        )

    if len(stripped) < MIN_FINDINGS_CHARS:
        return False, (
            f"review findings are too short ({len(stripped)} chars). At least "
            f"{MIN_FINDINGS_CHARS} characters are required — a verdict shorter "
            "than that cannot say what was examined or what was concluded."
        )

    if normalized == _normalize(bead_id):
        return False, "review findings merely echo the bead id."

    if bead_title and _normalize(bead_title) and normalized == _normalize(bead_title):
        return False, (
            "review findings merely echo the bead title and carry no verdict."
        )

    return True, ""


def validate_waiver_reason(reason: str | None, bead_id: str) -> tuple[bool, str]:
    """Validate a REVIEW_WAIVER reason per the standard waiver convention.

    Same substance rules as a `--<gate>-waiver` flag reason: >= 20 chars, no
    null patterns, no bare echo of the source artifact.
    """
    if reason is None:
        return False, "no waiver reason supplied."
    stripped = reason.strip()
    if not stripped:
        return False, "waiver reason is empty."
    normalized = _normalize(stripped)
    if normalized in _NULL_PATTERNS:
        return False, (
            f"waiver reason {stripped!r} is a placeholder, not a rationale."
        )
    if len(stripped) < 20:
        return False, (
            f"waiver reason is too short ({len(stripped)} chars). At least 20 "
            "characters are required."
        )
    if normalized == _normalize(bead_id):
        return False, "waiver reason merely echoes the bead id."
    return True, ""


def extract_bead_ids(*texts: str | None) -> list[str]:
    """Return bead-like ids found across the given texts, order-preserving."""
    seen: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in BEAD_ID_RE.findall(text):
            if match not in seen:
                seen.append(match)
    return seen
