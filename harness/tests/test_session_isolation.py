#!/usr/bin/env python3
"""Per-session isolation: detect concurrent sessions sharing one non-isolated
checkout and steer the blocked one to `escapement-worktree create` (bead e9v.4 /
Move 3).

WHY THIS EXISTS
---------------
The continuation-harness keys thread STATE (contract.json, scheduled.json) per
session, but `verify` runs the contract command against the SHARED WORKING TREE.
When two live sessions share one non-isolated checkout, session B's verify picks
up session A's in-flight breakage -> B's red is actually A's. The 2026-06-17
root-cause (UDE-7 / BLOCK-5) lands on: the fix is ISOLATION, not result-state
gating. A session's finishing boundary must reflect only its own work; the repo
already mandates `escapement-worktree create` (CLAUDE.md), so the harness detects the
collision and steers the blocked session there.

TEST ORACLE BRIEF
-----------------
1. Business invariant: when >=2 LIVE sessions share one on-disk working tree
   (same git toplevel), the harness detects it and surfaces the worktree escape
   path; a SOLO session or sessions already isolated in separate worktrees are
   NOT nagged.
2. Source of truth: per-session checkout.json under threads/*/ (worktree_root +
   heartbeat), derived from git (`rev-parse --show-toplevel`), never agent-asserted.
3. Invalid solution classes rejected here:
   - "any two checkout files == collision" (presence-only) -> rejected by the
     different-worktree control AND the stale-peer control.
   - keying on git_common_dir (would flag two ISOLATED worktrees of one repo as
     colliding — the opposite of the goal) -> rejected by the real-git
     linked-worktree control.
   - always-append the steer -> rejected by the solo-session negative control.
4. Named fragile implementation: `collision = len(read_checkouts) >= 2`.
   Defeated by test_different_worktree_root_no_collision and
   test_stale_peer_not_live.
5. Negative controls: different worktree_root; stale peer heartbeat; solo session.
6. Positive controls: two live same-root sessions -> peer returned; a blocked
   Stop in that collision -> block message contains `escapement-worktree create`.
7. Final outcome verification: this file green + test_session_watermark.py +
   test_stop_messages.py green (no SessionStart / Stop-message regression).

Run: python3 -m pytest harness/tests/test_session_isolation.py -q
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness" / "bin"))

import session_isolation as si  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(ts: _dt.datetime) -> str:
    return ts.isoformat()


def _rec(session_id: str, worktree_root: str, heartbeat: _dt.datetime, *,
         git_common_dir: str = "/repo/.git", is_linked_worktree: bool = False) -> dict:
    return {
        "session_id": session_id,
        "worktree_root": worktree_root,
        "git_common_dir": git_common_dir,
        "is_linked_worktree": is_linked_worktree,
        "heartbeat": _iso(heartbeat),
    }


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# colliding_sessions — the core oracle (pure function)
# ---------------------------------------------------------------------------
def test_two_live_same_root_collide() -> None:
    """POSITIVE CONTROL: two live sessions in the same working tree collide."""
    now = _now()
    me = _rec("A", "/work/repo", now)
    peer = _rec("B", "/work/repo", now)
    out = si.colliding_sessions([me, peer], "A", "/work/repo", now)
    assert [r["session_id"] for r in out] == ["B"], (
        f"a live peer in the same checkout must be detected; got {out}"
    )


def test_solo_session_no_collision() -> None:
    """NEGATIVE CONTROL: only my own record -> no collision (no nag for solo work)."""
    now = _now()
    me = _rec("A", "/work/repo", now)
    assert si.colliding_sessions([me], "A", "/work/repo", now) == []


def test_different_worktree_root_no_collision() -> None:
    """NEGATIVE CONTROL + kills the named fragile impl: two records EXIST but in
    different working trees (the success state we steer toward) -> NO collision.
    `len(records) >= 2` would wrongly fire here."""
    now = _now()
    me = _rec("A", "/work/repo", now)
    peer = _rec("B", "/work/repo-wt", now)  # an isolated worktree
    assert si.colliding_sessions([me, peer], "A", "/work/repo", now) == []


def test_stale_peer_not_live() -> None:
    """NEGATIVE CONTROL + kills the fragile impl: a same-root peer whose heartbeat
    is older than the liveness window is a dead session, not a live collision."""
    now = _now()
    me = _rec("A", "/work/repo", now)
    dead = _rec("B", "/work/repo", now - _dt.timedelta(seconds=si.LIVENESS_WINDOW_SECONDS + 60))
    assert si.colliding_sessions([me, dead], "A", "/work/repo", now) == []


def test_self_never_collides_with_self() -> None:
    """A duplicate record carrying my own session_id is not a peer."""
    now = _now()
    me = _rec("A", "/work/repo", now)
    dup = _rec("A", "/work/repo", now)
    assert si.colliding_sessions([me, dup], "A", "/work/repo", now) == []


def test_malformed_records_skipped() -> None:
    """A record missing fields must be skipped, not crash detection."""
    now = _now()
    good = _rec("B", "/work/repo", now)
    out = si.colliding_sessions([{"junk": 1}, {"session_id": "C"}, good], "A", "/work/repo", now)
    assert [r["session_id"] for r in out] == ["B"]


def test_naive_heartbeat_does_not_crash_and_is_live() -> None:
    """B1 REGRESSION: a peer heartbeat missing a tz offset parses NAIVE. Subtracting
    it from the tz-aware `now` must NOT raise TypeError (which would escape the hook
    guards and crash the Stop gate). A naive heartbeat is interpreted as UTC, so a
    recent one still counts as a live collision."""
    now = _dt.datetime(2026, 6, 18, 12, 0, 0, tzinfo=_dt.timezone.utc)
    # naive isoformat (no offset, no Z) — what a hand-edit / strftime-dropped-offset emits
    peer = {
        "session_id": "B", "worktree_root": "/work/repo",
        "git_common_dir": "/work/repo/.git", "is_linked_worktree": False,
        "heartbeat": "2026-06-18T11:59:30",  # 30s before `now`, naive
    }
    me = _rec("A", "/work/repo", now)
    out = si.colliding_sessions([me, peer], "A", "/work/repo", now)  # must not raise
    assert [r["session_id"] for r in out] == ["B"], (
        "a recent naive-timestamp peer must be treated as UTC and seen as live"
    )


def test_future_skew_heartbeat_is_live() -> None:
    """C3: a heartbeat slightly in the future (clock skew / stamped just after `now`
    was captured) is MORE evidence of liveness, not less — it must count."""
    now = _now()
    peer = _rec("B", "/work/repo", now + _dt.timedelta(seconds=30))
    assert [r["session_id"] for r in si.colliding_sessions([_rec("A", "/work/repo", now), peer], "A", "/work/repo", now)] == ["B"]


# ---------------------------------------------------------------------------
# build_isolation_steer — the escape path must be named (gate-design Rule 1)
# ---------------------------------------------------------------------------
def test_steer_names_worktree_escape_path() -> None:
    peers = [_rec("B", "/work/repo", _now())]
    steer = si.build_isolation_steer(peers, "/work/repo", is_linked_worktree=False)
    low = steer.lower()
    assert "escapement-worktree create" in low, (
        "the steer must name the generic Escapement escape command"
    )
    assert "--repo /work/repo" in low
    assert "--name" in low
    assert "--branch" in low
    assert "isolat" in low, "the steer must explain it is about isolation"
    assert ("red" in low or "verif" in low), (
        "the steer must connect the collision to a possibly-not-yours red/verify"
    )


def test_steer_distinguishes_checkout_location() -> None:
    """N1: the tracked is_linked_worktree flag must actually shape the message —
    main checkout vs a shared linked worktree."""
    peers = [_rec("B", "/work/repo", _now())]
    main_msg = si.build_isolation_steer(peers, "/work/repo", is_linked_worktree=False)
    wt_msg = si.build_isolation_steer(peers, "/work/repo", is_linked_worktree=True)
    assert "main checkout" in main_msg.lower()
    assert "linked worktree" in wt_msg.lower()
    assert main_msg != wt_msg, "the flag must change the message, not be dead"


# ---------------------------------------------------------------------------
# checkout_identity — real git: a linked worktree is NOT a collision
# ---------------------------------------------------------------------------
def test_real_git_main_vs_linked_worktree(tmp_path) -> None:
    """REAL-GIT CONTROL: a main checkout and a `git worktree add` linked worktree
    share git_common_dir but have DIFFERENT worktree_root, so they do NOT collide.
    This is the exact isolation the steer points sessions toward."""
    main = tmp_path / "main"
    main.mkdir()
    _git(["init", "-q"], main)
    _git(["config", "user.email", "t@t"], main)
    _git(["config", "user.name", "t"], main)
    (main / "f.txt").write_text("x")
    _git(["add", "."], main)
    _git(["commit", "-q", "-m", "init"], main)
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-q", str(wt)], main)

    id_main = si.checkout_identity(str(main))
    id_wt = si.checkout_identity(str(wt))
    assert id_main is not None and id_wt is not None
    assert id_main["worktree_root"] != id_wt["worktree_root"], (
        "main and linked worktree must have distinct worktree_root"
    )
    assert id_wt["is_linked_worktree"] is True
    assert id_main["is_linked_worktree"] is False
    # Same repo (shared common dir) but isolated -> colliding_sessions sees none.
    now = _now()
    rec_main = _rec("A", id_main["worktree_root"], now, git_common_dir=id_main["git_common_dir"])
    rec_wt = _rec("B", id_wt["worktree_root"], now,
                  git_common_dir=id_wt["git_common_dir"], is_linked_worktree=True)
    assert si.colliding_sessions([rec_main, rec_wt], "A", id_main["worktree_root"], now) == []


def test_checkout_identity_outside_git_is_none(tmp_path) -> None:
    """Outside a git repo there is no checkout concept -> None (no collision)."""
    assert si.checkout_identity(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# write_checkout / read_checkouts / detect — filesystem integration
# ---------------------------------------------------------------------------
def _fake_git_identity(worktree_root: str):
    def run_git(args, cwd):  # noqa: ARG001
        return {
            "worktree_root": worktree_root,
            "git_common_dir": worktree_root + "/.git",
            "is_linked_worktree": False,
        }
    return run_git


def test_write_and_read_roundtrip(tmp_path) -> None:
    harness = tmp_path / "harness"
    td_a = harness / "threads" / "A"
    rec = si.write_checkout(td_a, "A", "/work/repo", _now(),
                            identity_fn=_fake_git_identity("/work/repo"))
    assert rec is not None and rec["worktree_root"] == "/work/repo"
    assert (td_a / "checkout.json").exists()
    found = si.read_checkouts(harness)
    assert any(r["session_id"] == "A" for r in found)


def test_write_checkout_outside_git_writes_nothing(tmp_path) -> None:
    harness = tmp_path / "harness"
    td = harness / "threads" / "A"
    rec = si.write_checkout(td, "A", "/not/a/repo", _now(),
                            identity_fn=lambda args, cwd: None)
    assert rec is None
    assert not (td / "checkout.json").exists()


def test_detect_collision_end_to_end(tmp_path) -> None:
    """POSITIVE CONTROL (fs): two sessions stamp the same worktree_root -> the
    blocked session's detect surfaces the peer."""
    harness = tmp_path / "harness"
    now = _now()
    si.write_checkout(harness / "threads" / "A", "A", "/work/repo", now,
                      identity_fn=_fake_git_identity("/work/repo"))
    si.write_checkout(harness / "threads" / "B", "B", "/work/repo", now,
                      identity_fn=_fake_git_identity("/work/repo"))
    peers = si.detect_collision(harness, "A", harness / "threads" / "A", now)
    assert [r["session_id"] for r in peers] == ["B"]
    steer = si.isolation_steer_for_thread(harness, "A", harness / "threads" / "A", now)
    assert steer is not None and "escapement-worktree create" in steer.lower()


def test_detect_no_peer_returns_none_steer(tmp_path) -> None:
    """NEGATIVE CONTROL (fs): solo session -> no steer."""
    harness = tmp_path / "harness"
    now = _now()
    si.write_checkout(harness / "threads" / "A", "A", "/work/repo", now,
                      identity_fn=_fake_git_identity("/work/repo"))
    assert si.detect_collision(harness, "A", harness / "threads" / "A", now) == []
    assert si.isolation_steer_for_thread(harness, "A", harness / "threads" / "A", now) is None


# ---------------------------------------------------------------------------
# stop_hook end-to-end: the steer must actually REACH the block output
# (guards the "wired but inert" failure class — e9v.8)
# ---------------------------------------------------------------------------
def _make_git_repo(tmp_path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "f.txt").write_text("x")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    return repo


def _run_stop_hook(repo: pathlib.Path, harness: pathlib.Path, session_id: str):
    payload = json.dumps({"session_id": session_id, "transcript_path": ""})
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(harness)
    proc = subprocess.run(
        ["python3", str(REPO / "harness" / "bin" / "stop_hook.py")],
        input=payload, cwd=str(repo), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return proc


def _seed_failing_contract(thread_dir: pathlib.Path) -> None:
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "contract.json").write_text(json.dumps({
        "goal": "g",
        "verification_command": "pytest",
        "expected_exit": 0,
        "source": "agent-declared",
        "thread_id": thread_dir.name,
        "created_at": _iso(_now()),
        "last_run": {"exit_code": 1, "timestamp": _iso(_now()), "output_excerpt": "FAIL"},
    }))


def test_stop_block_in_collision_includes_steer(tmp_path) -> None:
    """POSITIVE CONTROL (e2e): a blocked Stop while a live peer shares the checkout
    must carry the worktree escape path in the block message."""
    repo = _make_git_repo(tmp_path)
    harness = tmp_path / "harness"
    toplevel = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(repo),
                              stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
    # A live peer already in the same checkout.
    si.write_checkout(harness / "threads" / "PEER", "PEER", toplevel, _now(),
                      identity_fn=_fake_git_identity(toplevel))
    _seed_failing_contract(harness / "threads" / "ME")

    proc = _run_stop_hook(repo, harness, "ME")
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert "escapement-worktree create" in out["reason"].lower(), (
        f"a blocked Stop in a shared-checkout collision must steer to worktree "
        f"isolation; got: {out['reason']!r}"
    )
    assert f"--repo {toplevel}" in out["reason"]
    assert "--name" in out["reason"]
    assert "--branch" in out["reason"]


def test_stop_block_solo_has_no_steer(tmp_path) -> None:
    """NEGATIVE CONTROL (e2e): the SAME failing contract with NO peer must block
    WITHOUT the isolation steer — we never nag a session that owns its checkout."""
    repo = _make_git_repo(tmp_path)
    harness = tmp_path / "harness"
    _seed_failing_contract(harness / "threads" / "ME")

    proc = _run_stop_hook(repo, harness, "ME")
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert "escapement-worktree create" not in out["reason"].lower(), (
        f"a solo session must not be steered to a worktree; got: {out['reason']!r}"
    )
    assert "isolation:" not in out["reason"].lower()


@pytest.mark.parametrize(
    "peer_kind",
    ("stale-same-checkout", "live-isolated-worktree"),
)
def test_stop_block_noncollision_peer_has_no_steer(tmp_path, peer_kind) -> None:
    """Final-output negatives: only a live peer in this exact checkout gets a steer."""
    repo = _make_git_repo(tmp_path)
    harness = tmp_path / "harness"
    now = _now()
    toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.strip()
    common_dir = str(repo / ".git")
    if peer_kind == "stale-same-checkout":
        peer = _rec(
            "PEER",
            toplevel,
            now - _dt.timedelta(seconds=si.LIVENESS_WINDOW_SECONDS + 1),
            git_common_dir=common_dir,
        )
    else:
        peer = _rec(
            "PEER",
            str(tmp_path / "other-linked-worktree"),
            now,
            git_common_dir=common_dir,
            is_linked_worktree=True,
        )
    peer_dir = harness / "threads" / "PEER"
    peer_dir.mkdir(parents=True)
    (peer_dir / "checkout.json").write_text(json.dumps(peer), encoding="utf-8")
    _seed_failing_contract(harness / "threads" / "ME")

    proc = _run_stop_hook(repo, harness, "ME")
    out = json.loads(proc.stdout)

    assert out["decision"] == "block"
    assert "escapement-worktree" not in out["reason"].lower()
    assert "isolation:" not in out["reason"].lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
