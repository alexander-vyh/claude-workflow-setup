#!/usr/bin/env python3
"""Per-session isolation: detect concurrent sessions sharing one non-isolated
checkout, and produce the `escapement-worktree create` steer (e9v.4 / Move 3).

The continuation-harness keys thread STATE per session, but `verify` runs the
contract command against the SHARED WORKING TREE. When two live sessions share
one on-disk checkout, session B's verify picks up session A's in-flight
breakage, so B's red is actually A's (root-cause UDE-7 / BLOCK-5, 2026-06-17).
The fix is ISOLATION, not result-state gating: detect the collision and steer
the blocked session to the worktree the repo already mandates (CLAUDE.md).

State-only and pure where it matters (gate-design / harness philosophy): the
collision signal is DERIVED from per-session `checkout.json` records and git,
never agent-asserted. All writes are best-effort and never crash the hook that
calls them.

Each session stamps `{thread_dir}/checkout.json`:
    {session_id, worktree_root, git_common_dir, is_linked_worktree, heartbeat}

Collision = >=2 LIVE records (heartbeat within the window) whose `worktree_root`
is the SAME on-disk working tree, with distinct session_ids. Two sessions in
different linked worktrees of one repo share `git_common_dir` but have different
`worktree_root` -> NOT a collision (that is the isolated success state).
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import shlex
import subprocess
from typing import Callable, Optional

# A session whose heartbeat is older than this is presumed idle/dead and does
# not count as a live collision (a stale record must not phantom-collide forever).
LIVENESS_WINDOW_SECONDS = 1800  # 30 min


def _parse_iso(s) -> Optional[_dt.datetime]:
    if not isinstance(s, str):
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return _dt.datetime.fromisoformat(s)
    except (ValueError, AttributeError):
        return None


def _git_checkout_identity(args, cwd: str) -> Optional[dict]:
    """Real git resolver: worktree root + common dir + linked-worktree flag.

    Returns None outside a git repo (or when git is unavailable). `args` is
    accepted for the injectable-runner signature but unused by the real impl.
    """
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel", "--git-common-dir"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
        )
    except (OSError, ValueError):
        return None
    if out.returncode != 0:
        return None
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    worktree_root = str(pathlib.Path(lines[0]).resolve())
    # --git-common-dir is relative to cwd when inside a linked worktree; resolve it.
    common = pathlib.Path(lines[1])
    if not common.is_absolute():
        common = pathlib.Path(cwd) / common
    git_common_dir = str(common.resolve())
    # In the main checkout the common dir is "<worktree_root>/.git"; in a linked
    # worktree it points back at the main repo's .git, so its parent != worktree_root.
    is_linked_worktree = str(pathlib.Path(git_common_dir).parent) != worktree_root
    return {
        "worktree_root": worktree_root,
        "git_common_dir": git_common_dir,
        "is_linked_worktree": is_linked_worktree,
    }


def checkout_identity(cwd: str, run_git: Optional[Callable] = None) -> Optional[dict]:
    """Resolve the git checkout identity for `cwd`, or None if not a git repo.

    `run_git` is injectable for hermetic tests; defaults to the real git resolver.
    """
    resolver = run_git or _git_checkout_identity
    try:
        return resolver(["rev-parse"], cwd)
    except Exception:  # noqa: BLE001 — identity is best-effort; never raise into a hook
        return None


def write_checkout(
    thread_dir: pathlib.Path,
    session_id: str,
    cwd: str,
    now: _dt.datetime,
    identity_fn: Optional[Callable] = None,
) -> Optional[dict]:
    """Stamp/refresh `{thread_dir}/checkout.json` for this session. Best-effort.

    Returns the written record, or None when `cwd` is not a git repo (no checkout
    concept -> nothing written; collision detection simply skips this session).
    """
    identity = checkout_identity(cwd, run_git=identity_fn)
    if identity is None:
        return None
    record = {
        "session_id": session_id,
        "worktree_root": identity["worktree_root"],
        "git_common_dir": identity["git_common_dir"],
        "is_linked_worktree": bool(identity["is_linked_worktree"]),
        "heartbeat": now.isoformat(),
    }
    try:
        thread_dir.mkdir(parents=True, exist_ok=True)
        (thread_dir / "checkout.json").write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        return None  # logging-grade write; never fail the calling hook
    return record


def read_checkouts(harness_root: pathlib.Path) -> list[dict]:
    """Read every `threads/*/checkout.json` under harness_root. Skips malformed."""
    records: list[dict] = []
    threads = pathlib.Path(harness_root) / "threads"
    if not threads.is_dir():
        return records
    for child in threads.iterdir():
        path = child / "checkout.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def _is_live(record: dict, now: _dt.datetime, window: int) -> bool:
    ts = _parse_iso(record.get("heartbeat"))
    if ts is None:
        return False
    # B1: a heartbeat missing an offset parses NAIVE; subtracting it from the
    # tz-aware `now` would raise TypeError and escape the call-site guards into
    # the Stop hook. Interpret a naive timestamp as UTC so detection stays
    # best-effort and NEVER raises into a hook (the in-tree writers always emit
    # aware isoformat; this hardens against hand-edited / older / foreign records).
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    age = (now - ts).total_seconds()
    # C3: allow future-skewed heartbeats (negative age). A timestamp slightly in
    # the future (clock skew across hosts / a peer stamping just after `now` was
    # captured) is MORE evidence of liveness, not less — only staleness disqualifies.
    return age <= window


def colliding_sessions(
    records: list[dict],
    me_session_id: str,
    me_worktree_root: str,
    now: _dt.datetime,
    window: int = LIVENESS_WINDOW_SECONDS,
) -> list[dict]:
    """Pure: the OTHER live sessions sharing my exact working tree.

    Collision keys on `worktree_root` (the on-disk checkout), NOT git_common_dir
    — so two isolated worktrees of one repo do not count. A record is a peer iff
    it is a dict with a distinct session_id, the same worktree_root, and a
    heartbeat within `window`.
    """
    peers: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("session_id") == me_session_id or not rec.get("session_id"):
            continue
        if rec.get("worktree_root") != me_worktree_root:
            continue
        if not _is_live(rec, now, window):
            continue
        peers.append(rec)
    return peers


def detect_collision(
    harness_root: pathlib.Path,
    me_session_id: str,
    me_thread_dir: pathlib.Path,
    now: _dt.datetime,
    window: int = LIVENESS_WINDOW_SECONDS,
) -> list[dict]:
    """Read my own checkout.json for my worktree_root, then return live peers in
    the same checkout. Empty list if I have no checkout (not git / not stamped)."""
    mine_path = pathlib.Path(me_thread_dir) / "checkout.json"
    try:
        mine = json.loads(mine_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    me_worktree_root = mine.get("worktree_root") if isinstance(mine, dict) else None
    if not me_worktree_root:
        return []
    return colliding_sessions(
        read_checkouts(harness_root), me_session_id, me_worktree_root, now, window
    )


def build_isolation_steer(
    peers: list[dict],
    worktree_root: str,
    is_linked_worktree: bool,
) -> str:
    """Point to SessionStart's concrete command and show its reference template."""
    n = len(peers)
    noun, verb = ("session", "shares") if n == 1 else ("sessions", "share")
    where = "a shared linked worktree" if is_linked_worktree else "the main checkout"
    command = (
        "python3 -B <injected-bundled-cli-path> create "
        f"--repo {shlex.quote(worktree_root)} --name <task> --branch <branch>"
    )
    return (
        f" ⚠ ISOLATION: {n} other live agent {noun} {verb} this checkout "
        f"({worktree_root} — {where}). A red verify here may reflect THEIR in-flight "
        "edits, not yours — one session's red must not gate another's finish. If your "
        "own change is complete, isolate and verify it on its own: run the concrete "
        "`escapement-worktree create` command emitted by SessionStart. Its reference "
        "template is "
        f"`{command}`. Then "
        "move your work there, re-run "
        "`~/.claude/harness/bin/verify` in the worktree, and file+attribute any "
        "shared-boundary defect you did not cause as a bead."
    )


def isolation_steer_for_thread(
    harness_root: pathlib.Path,
    me_session_id: str,
    me_thread_dir: pathlib.Path,
    now: _dt.datetime,
    window: int = LIVENESS_WINDOW_SECONDS,
) -> Optional[str]:
    """Convenience: the steer string if this session is in a collision, else None."""
    peers = detect_collision(harness_root, me_session_id, me_thread_dir, now, window)
    if not peers:
        return None
    mine_path = pathlib.Path(me_thread_dir) / "checkout.json"
    worktree_root = ""
    is_linked = False
    try:
        mine = json.loads(mine_path.read_text(encoding="utf-8"))
        if isinstance(mine, dict):
            worktree_root = mine.get("worktree_root", "")
            is_linked = bool(mine.get("is_linked_worktree", False))
    except (OSError, json.JSONDecodeError):
        pass
    return build_isolation_steer(peers, worktree_root, is_linked)
