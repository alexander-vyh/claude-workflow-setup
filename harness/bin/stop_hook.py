#!/usr/bin/env python3
# file-complexity-waiver: 947 lines, pre-existing; split owned by bead e9v.7. This change only adds a one-line per-session isolation steer (e9v.4), not new bulk.
"""
Claude Code Stop-hook adapter for continuation-harness.

Reads the Anthropic hook protocol JSON from stdin, calls would_block_stop
against the active thread directory, logs the decision to incidents.jsonl,
and emits a block decision (with constructive resumption prompt) when warranted.

State is keyed by the hook payload's session_id and, for a subagent, the
CLAUDE_AGENT_ID actor identity. The session_id is also included in the incidents
log for correlation.

Coexists with ~/.claude/hooks/validate_no_shirking.py — both run on Stop;
both can block. Additive coverage.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Optional, Tuple

_TRANSCRIPT_WINDOW = 25_000  # bytes — same tail size as validate_no_shirking.py

# Self-locate for the sibling import — works whether this script lives in the
# repo source tree or is installed to ~/.claude/harness/bin. No hardcoded path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from would_block_stop import (  # noqa: E402
    InvalidActorIdentity,
    would_block_stop,
    load_thread_state,
    thread_dir_for_session,
    harness_home,
    resolve_watermark,
    _load_json,
    _parse_iso,
)
from thread_identity import state_identity  # noqa: E402
import session_isolation  # noqa: E402  (per-session isolation steer, bead e9v.4)
import winddown_outage_sentinel as _wos  # noqa: E402
import datetime as _dt2  # noqa: E402

# State root is the standard per-user location (env-overridable), NOT relative
# to where this code is installed — so dev-copy and installed-copy share state
# and nothing is written into a repo working tree.
HARNESS_ROOT = harness_home()
INCIDENTS_LOG = HARNESS_ROOT / "incidents.jsonl"

RESUMPTION_PROMPT = (
    "continuation-harness: {reason}. You are NOT done and you are NOT stopping. "
    "Do NOT end your turn to summarize what's left or to ask the user what to do next — "
    "that wind-down is the exact failure this gate exists to prevent. Continue now with the "
    "next concrete in-scope action. The only ways to actually finish this turn: "
    "(1) run `~/.claude/harness/bin/verify` and have it exit 0 "
    "(declare a contract via init_contract.py first if you haven't); "
    "(2) call the ScheduleWakeup tool because you are blocked on an external event. "
    "The user can release you by saying 'stop' — but do not solicit that by halting."
)

# Task-mode-specific display text, keyed by reason code.
# Reason codes are short log-friendly strings; display text carries the guidance.
_TASK_MODE_DISPLAY: dict[str, str] = {
    "verification_passed_git_work_remains": (
        "continuation-harness: verification_passed_git_work_remains. Your contract's "
        "verify passed, but uncommitted/unpushed git work remains in this repo — a green "
        "oracle on one goal is not a finished session (the 'Harness cleared, then stopped "
        "with work left' miss). Do NOT stop on the strength of the verify alone. Escape "
        "paths: (1) commit AND push the remaining tracked changes, then stop; (2) if the "
        "work is genuinely paused, call ScheduleWakeup so it resumes; (3) the user can "
        "release you by saying 'stop'."
    ),
    "verification_passed_unmerged_automerge_pr": (
        "continuation-harness: verification_passed_unmerged_automerge_pr. Your contract's "
        "verify passed (green) and this repo's .escapement/repo.json declared "
        "auto_merge_on_green with intended_outcome at merged-and-deployed — so you are "
        "durably authorized to ship, and an open PR is not a finished session. Do NOT stop "
        "at PR-opened and do NOT ask 'merge or review first?' — the repo already authorized "
        "that outcome. Escape paths: (1) merge it and ship live — `gh pr merge <PR> --squash` "
        "(find it with `gh pr list --head $(git branch --show-current) --state open`), then "
        "stop; (2) if the merge genuinely requires a human you cannot be (a protected-branch "
        "credential, an external approval), name that exact blocker and call ScheduleWakeup; "
        "(3) the user can release you by saying 'stop'."
    ),
    "tasks_remain_in_queue": (
        "continuation-harness [task-mode]: tasks_remain_in_queue. In-scope work is ready "
        "under this session's goal — keep working it. Do NOT stop to summarize or to ask the "
        "user what to do next; run the next ready task to completion. Stop is allowed only "
        "when the scoped queue is drained, the user has already said 'stop', or you take the "
        "sanctioned PAUSE: if you must pause for quality/pacing rather than finish now, file "
        "the remaining in-scope work as beads (durable) AND call ScheduleWakeup — together "
        "they record WHAT to resume and bring you back. ScheduleWakeup WITHOUT filing the "
        "remaining work is pause-and-evaporate; that is the stall this gate exists to prevent."
    ),
    "blocked_tasks_no_wakeup": (
        "continuation-harness [task-mode]: blocked_tasks_no_wakeup. The ready queue is "
        "empty but scoped blocked beads exist — Stop is NOT granted. A blocked bead is not "
        "a clean queue drain; it is a laundering hole unless the blocker is real and "
        "verifiable. Escape paths: (1) Call ScheduleWakeup to register a future check-in — "
        "together with the blocked bead it records when and why you'll resume; (2) if the "
        "blocker claim is refuted, unblock or close the bead and keep working; (3) add a "
        "`blocker-verify: <cmd>` line to the bead's description so the claim can be "
        "mechanically confirmed. The user can always release you by saying 'stop'."
    ),
    "wakeup_blocker_unverified": (
        "continuation-harness [task-mode]: wakeup_blocker_unverified. A wakeup is "
        "registered, but a scoped blocked bead carries an unverified blocker claim — the "
        "wakeup does not release the gate. A blocker must be substantiated before it can "
        "unlock a wakeup-path stop. Escape paths: (1) Add a `blocker-verify: <cmd>` line "
        "to the bead description whose command exits 0 to confirm the blocker is real; "
        "(2) add a `blocker-waiver: <reason>` line (≥20 chars, non-placeholder) if the "
        "blocker genuinely cannot be scripted; (3) if the blocker claim is invalid, "
        "unblock or close the bead. The user can always release you by saying 'stop'."
    ),
}



_VERIFICATION_SUPPRESSED_DISPLAY = (
    "continuation-harness: verification_suppressed. Your contract's verify command reached "
    "exit 0, but the command is SELF-NEUTERING (e.g. `|| true`, a bare `true`/`:`, "
    "`--no-verify`, or `SKIP=`) — a gamed green is not a pass, so re-running it will NOT "
    "release this gate. Do NOT keep re-running it. Fix the verification_command so it actually "
    "runs the check and propagates a real non-zero on failure, then re-run "
    "`~/.claude/harness/bin/verify`. If a hook is genuinely broken, FIX the hook — do not "
    "disable it in the verify command."
)


def _read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


def _read_last_user_message(transcript_path: str) -> Optional[str]:
    """Return the most recent user message text from the transcript tail.

    Mirrors the read_recent_messages pattern in validate_no_shirking.py so that
    _user_released() in would_block_stop actually fires when the user says 'stop'.
    Returns None if transcript_path is empty, unreadable, or has no user turns.
    """
    if not transcript_path:
        return None
    path = pathlib.Path(transcript_path)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    tail = raw[-_TRANSCRIPT_WINDOW:] if len(raw) > _TRANSCRIPT_WINDOW else raw
    last_user_text: Optional[str] = None
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = entry.get("message", entry)
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content", [])
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    parts.append(blk.get("text", ""))
                elif isinstance(blk, str):
                    parts.append(blk)
        if parts:
            last_user_text = "\n".join(parts)
    return last_user_text


def _read_last_assistant_message(transcript_path: str) -> Optional[str]:
    """Most recent ASSISTANT text from the transcript tail (mirror of the user reader).

    This is the wind-down rung's input: the assistant's turn-final message, where a
    wrap/decision-punt offer lives. Returns None if unavailable.
    """
    if not transcript_path:
        return None
    path = pathlib.Path(transcript_path)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    tail = raw[-_TRANSCRIPT_WINDOW:] if len(raw) > _TRANSCRIPT_WINDOW else raw
    last_text: Optional[str] = None
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("isSidechain"):  # subagent turn, not the main assistant
            continue
        msg = entry.get("message", entry)
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    parts.append(blk.get("text", ""))
                elif isinstance(blk, str):
                    parts.append(blk)
        if parts:
            last_text = "\n".join(parts)
    return last_text


# Wind-down rung (winddown_judge + winddown_gate). Imported fail-open: if the modules
# or httpx are unavailable the rung simply never fires — it must NEVER break the gate.
try:
    import winddown_judge as _wj  # noqa: E402
    import winddown_gate as _wg  # noqa: E402
except Exception:  # pragma: no cover - defensive
    _wj = None
    _wg = None

_WINDDOWN_VERDICT_FRESH_SECONDS = 300
# Bounded — this runs in the Stop critical path (not a daemon), so it must be snappy.
# Fail-open on timeout means a slow/cold model yields None → allow (judge-only).


def _text_sha(text: str) -> str:
    """Short stable hash used to scope a cached verdict to the message it judged."""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]


def _read_cached_winddown_verdict(thread_dir, text: Optional[str] = None) -> Optional[bool]:
    """Read a cached model verdict for this session (monitor- or inline-written).

    {thread_dir}/winddown_verdict.json = {"verdict": bool, "ts": ISO, "text_sha"?: str}.
    Returns the bool only if fresh (within the current-turn window); else None so the
    Stop hook treats it as allow (no classifier fired; judge-only architecture).

    MESSAGE-SCOPED: a verdict tagged with `text_sha` applies ONLY to that message, so a
    still-fresh verdict for an EARLIER turn cannot mis-fire as a false-positive block on
    a later, different message. A verdict written without `text_sha` (e.g. by a future
    monitor that omits it) degrades to time-freshness only — backward/forward compatible.
    """
    data = _load_json(pathlib.Path(thread_dir) / "winddown_verdict.json")
    if not isinstance(data, dict):
        return None
    ts = _parse_iso(data.get("ts", ""))
    if ts is None:
        return None
    age = (_dt2.datetime.now(_dt2.timezone.utc) - ts).total_seconds()
    if age > _WINDDOWN_VERDICT_FRESH_SECONDS:
        return None
    stored_sha = data.get("text_sha")
    if stored_sha is not None and text is not None and stored_sha != _text_sha(text):
        return None  # verdict was for a different message — do not apply it here
    v = data.get("verdict")
    return v if isinstance(v, bool) else None


def _write_winddown_verdict(thread_dir, verdict: bool, *, text: Optional[str] = None, now=None) -> None:
    """Persist a computed verdict so it warms the cache for the rest of the turn-window
    and is observable (and forward-compatible with a future background monitor reading
    the same file). Tags the message hash so the cache is message-scoped. Best-effort."""
    ts = (now or _dt2.datetime.now(_dt2.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = {"verdict": bool(verdict), "ts": ts}
    if text is not None:
        rec["text_sha"] = _text_sha(text)
    try:
        (pathlib.Path(thread_dir) / "winddown_verdict.json").write_text(json.dumps(rec))
    except OSError:
        pass


def _compute_winddown_verdict_inline(text, thread_dir, *, judge=None, now=None) -> Optional[bool]:
    """Run the local-LLM judge INLINE (bounded timeout, fail-open) and cache its result.

    This is what makes the model layer LIVE without a daemon: the SWE-PRM judge that was
    wired-but-dormant (nothing wrote the verdict file) now runs on demand, in the narrow
    slice where it runs the judge as the sole classifier. Returns the bool verdict or None on
    any error/unclear — a judge problem must NEVER block or crash the hook.
    """
    fn = judge or (lambda t: _wj.model_verdict(t))
    try:
        v = fn(text)
    except Exception:
        return None  # fail-open
    if isinstance(v, bool):
        _write_winddown_verdict(thread_dir, v, text=text, now=now)
        return v
    return None


# Derived/churny beads telemetry: rewritten as a side-effect of ordinary `bd`
# commands and (on a protected main) unpushable, so a modification here is NOT
# "work to finish" — counting it false-positives the Stop gate after any bd call
# (dogfood finding 2026-06-14). issues.jsonl is deliberately NOT here: it is real
# issue state whose sync is legitimately work-remaining.
_BEADS_TELEMETRY_PATHS = frozenset({
    ".beads/interactions.jsonl",
    ".beads/.gate-signal.jsonl",
    ".beads/.gate-waivers.jsonl",
    ".beads/.spec-index.json",
})


def _git_work_remains(cwd: str, run_git=None) -> bool:
    """True iff the repo at `cwd` has uncommitted changes to TRACKED files OR commits not
    pushed to its upstream. Pure-untracked files (scratch/artifacts) do NOT count — they
    would nag nearly every stop in a live working tree (deliberate, documented scope).
    Churny beads telemetry (_BEADS_TELEMETRY_PATHS) is also excluded for the same reason.

    FAIL-OPEN to False: not a git repo / git error / no upstream → no git work detected
    (mirrors _check_bd_queue_implicit degrading to allow; never fabricates a block, never
    raises). The cake veiled-stop — drained bead queue but 4 unpushed commits — is exactly
    the case this exists to catch, so the unpushed-commit signal is load-bearing.
    """
    if not cwd:
        return False
    if run_git is None:
        def run_git(args):
            try:
                r = subprocess.run(
                    ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10,
                )
                return r if r.returncode == 0 else None
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError, NotADirectoryError):
                return None

    status = run_git(["status", "--porcelain"])
    if status is not None:
        for line in status.stdout.splitlines():
            if not line or line.startswith("??"):  # blank or pure-untracked → skip
                continue
            path = line[3:]  # porcelain: "XY <path>"
            if " -> " in path:  # rename: take the destination path
                path = path.split(" -> ", 1)[1]
            if path.strip().strip('"') in _BEADS_TELEMETRY_PATHS:
                continue  # churny derived beads telemetry — not work to finish
            return True  # a real tracked change

    # Unpushed: revisions ahead of the tracking upstream. No upstream → git errors → None
    # → not counted (a branch with no upstream cannot meaningfully be called "unpushed").
    ahead = run_git(["rev-list", "--count", "@{u}..HEAD"])
    if ahead is not None:
        try:
            if int(ahead.stdout.strip() or "0") > 0:
                return True
        except ValueError:
            pass
    return False


def _repo_authorizes_auto_merge(cwd: str) -> bool:
    """True iff the repo at `cwd` declared auto-merge authorization in
    .escapement/repo.json (intended_outcome >= merged AND auto_merge_on_green).
    FAIL-OPEN to False (no reader / no declaration / any error) — never fabricates
    authorization, mirroring the conservative default the reader itself uses."""
    if not cwd:
        return False
    try:
        import repo_outcome  # sibling in harness/bin
    except ImportError:
        return False
    try:
        return bool(repo_outcome.authorizes_auto_merge(repo_outcome.resolve(cwd)))
    except Exception:
        return False


def _open_pr_for_current_branch(cwd: str, run=None) -> Optional[int]:
    """PR number of an OPEN pull request whose head is the current branch, else None.
    FAIL-OPEN to None (no gh / not a repo / no PR / any error) — never fabricates a PR."""
    if not cwd:
        return None
    if run is None:
        def run(args):
            try:
                r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=10)
                return r if r.returncode == 0 else None
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError, NotADirectoryError):
                return None
    br = run(["git", "branch", "--show-current"])
    if br is None:
        return None
    branch = br.stdout.strip()
    if not branch:
        return None
    out = run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"])
    if out is None:
        return None
    try:
        data = json.loads(out.stdout or "[]")
        if isinstance(data, list) and data:
            return int(data[0].get("number"))
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return None
    return None


def _unmerged_automerge_pr(cwd: str, *, authorize_check=None, pr_lookup=None) -> Optional[int]:
    """PR number of an open PR the session must merge before stopping — i.e. the repo
    AUTHORIZES auto-merge AND an open PR exists for the current branch. Else None.

    Only meaningful on the verification_passed (green) path; the caller establishes green,
    so this never force-merges a red PR (design anti-metric #1). Authorization is required
    (design anti-metric #2 — never force a merge in a repo that did not grant it); a fragile
    check keying only on PR presence is exactly what the authorize gate rejects.
    FAIL-OPEN to None on every error path."""
    if not cwd:
        return None
    if authorize_check is None:
        authorize_check = _repo_authorizes_auto_merge
    if not authorize_check(cwd):
        return None
    if pr_lookup is None:
        pr_lookup = _open_pr_for_current_branch
    return pr_lookup(cwd)


def _verification_work_remains(
    cwd: str,
    thread_dir,
    *,
    bd_check=None,
    git_check=None,
    automerge_check=None,
) -> Optional[Tuple[str, str]]:
    """After a `verification_passed` allow, decide whether open work still blocks.

    A passing contract verifies its own narrow oracle; it does NOT prove the
    session is finished. The B3 fix already re-checks the bd queue here — this
    brings the verification_passed path to parity with the conversational
    `_winddown_override` by ALSO checking git work (dirty tracked files / unpushed
    commits): the index-0 shirking miss ("Harness cleared." then stopped with work
    remaining, a drained bead queue but uncommitted git). Returns (decision,
    reason) to block, or None to allow. Deterministic — no LLM-judge dependency,
    so it holds even when the wind-down model is down (fail-open).
    """
    if bd_check is None:
        bd_check = _check_bd_queue_implicit
    if git_check is None:
        git_check = _git_work_remains
    if automerge_check is None:
        automerge_check = _unmerged_automerge_pr
    bd_decision, bd_reason = bd_check(cwd or "", thread_dir=thread_dir)
    if bd_decision == "block":
        return (bd_decision, bd_reason)
    if cwd and git_check(cwd):
        return ("block", "verification_passed_git_work_remains")
    # Deterministic auto-merge backstop: green + repo authorized auto-merge + open PR =>
    # the session must ship it live, not stop at PR-opened. Enforced regardless of whether
    # the agent read the per-repo-outcome rule. Reached only post-green (anti-metric #1).
    if cwd and automerge_check(cwd) is not None:
        return ("block", "verification_passed_unmerged_automerge_pr")
    return None


def _wakeup_work_remains(
    cwd: str,
    thread_dir,
    *,
    bd_check=None,
) -> Optional[Tuple[str, str]]:
    """After a `wakeup_registered` allow, decide whether SESSION-FRESH work still blocks.

    A registered wakeup pauses for an external event (CI, a merge, a scheduled run);
    it does NOT discharge work the session itself created. Without this check the
    wakeup allow returns from main() before `_verification_work_remains`
    (verification_passed path) and `_winddown_override` (conversational path) run, so
    a session can file a session-fresh bead, schedule a trivial deploy/CI-check wakeup,
    and stop with that work abandoned (escapement-51w3; the cro-dashboard
    grain-adaptation deferral).

    Reuses the SAME watermark-scoped oracle the task-mode wakeup path already trusts
    (`_check_bd_queue_implicit`): it blocks only on beads created_at >= the session
    watermark, so unrelated prior-session backlog does NOT trap a completed session.
    That scope is the whole point — an unscoped `bd ready` check would block every
    session in any repo that has a backlog. Returns (decision, reason) to block, or
    None to allow. Deterministic and fail-open (bd unavailable / no watermark ⇒ the
    scoped check itself returns allow ⇒ None here).
    """
    if bd_check is None:
        bd_check = _check_bd_queue_implicit
    bd_decision, bd_reason = bd_check(cwd or "", thread_dir=thread_dir)
    if bd_decision == "block":
        return (bd_decision, bd_reason)
    return None


def _winddown_override(
    reason: str,
    transcript_path: str,
    cwd: str,
    thread_dir,
    *,
    work_check=None,
    judge=None,
) -> Optional[str]:
    """If a `conversational` stop is really a wind-down offer with reversible work
    remaining, return the recovery display to BLOCK with; else None.

    Reversible work = the bd session-scoped queue (work_check) OR git state (unpushed
    commits / dirty tracked files). The semantic judge is the primary classifier: when
    the cache is cold the judge runs inline (bounded, fail-open). A None verdict records
    `winddown_judge_unavailable`; only then can the separate high-confidence outage
    sentinel block the transcript-proven DWDEV-style shapes.

    Scoped to the `conversational` free-pass allow AND the `wakeup_registered` allow
    (escapement-lby9). The wakeup path needs the SEMANTIC backstop for the same reason
    conversational does: the state-based _wakeup_work_remains only catches a
    session-fresh bead left behind — it is blind to a wind-down-shaped final message
    with a clean bd queue but reversible git work ("shipped it, want me to tackle X
    next session?"). The reversible-work gate below still prevents nagging a legitimate
    wakeup pause (waiting on CI, clean tree). Genuine terminals (verification_passed /
    user_released) remain untouched — widening here must NOT leak to them.
    """
    if reason not in ("conversational", "wakeup_registered") or _wj is None:
        return None
    text = _read_last_assistant_message(transcript_path)
    if not text:
        return None
    if work_check is None:  # resolved here, not at def-time (forward ref)
        work_check = _check_bd_queue_implicit
    work_remains = work_check(cwd or "", thread_dir=thread_dir)[0] == "block"
    if not work_remains and cwd:
        # bd queue drained — but unpushed commits / dirty tracked files are also reversible
        # work the agent owns (the cake "nothing outstanding" with 4 unpushed commits).
        work_remains = _git_work_remains(cwd)
    if not work_remains:
        return None  # nothing reversible (bd or git) → legitimate stop; never nag, never judge
    model_offer = _read_cached_winddown_verdict(thread_dir, text=text)
    if model_offer is None:
        # Cache cold — consult the judge inline. There is no general regex floor in the
        # judge/rung path; only the narrow outage sentinel below can act after a None.
        model_offer = _compute_winddown_verdict_inline(text, thread_dir, judge=judge)
    if model_offer is None:
        # Judge unavailable after inline attempt. Fail open (gate-design Rule 2: emit
        # signal so the outage is visible in the half-life review corpus, never silent).
        _log_incident({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": "",
            "decision": "allow",
            "reason": "winddown_judge_unavailable",
            "was_correct": None,
            "notes": "winddown_rung",
        })
        if _wos.high_confidence_outage_winddown(text):
            return _wg.RECOVERY_PROMPT
        return None
    decision, _ = _wj.decide(text, work_remains, model_offer=model_offer)
    return _wg.RECOVERY_PROMPT if decision == "block" else None


def _main_repo_has_beads(cwd: str) -> bool:
    """A3: detect if `cwd` is a LINKED git worktree (`.git` is a FILE) and the
    resolved main repo has `.beads/`. Used to widen the `has_beads_dir` check so
    a foreign beads worktree (which lacks a literal `.beads/` at its own cwd)
    still degrades to BLOCK rather than to the graceful-allow path when bd fails.

    Returns False for:
    - real repos (`.git` is a directory, not a file)
    - plain-git linked worktrees (main repo has no `.beads/`)
    - unreadable / malformed `.git` files

    Fail-open: any OSError → False (never fabricates a block).
    """
    if not cwd:
        return False
    try:
        git_path = pathlib.Path(cwd) / ".git"
        if not git_path.is_file():
            return False
        content = git_path.read_text(encoding="utf-8", errors="replace").strip()
        if not content.startswith("gitdir:"):
            return False
        gitdir_str = content[len("gitdir:"):].strip()
        gitdir = pathlib.Path(gitdir_str)
        if not gitdir.is_absolute():
            gitdir = (pathlib.Path(cwd) / gitdir).resolve()
        # <main>/.git/worktrees/<name> -> parent.parent = <main>/.git -> parent = <main>
        main_repo = gitdir.parent.parent.parent
        return (main_repo / ".beads").is_dir()
    except OSError:
        return False


def _task_mode_in_effect(session_mode) -> bool:
    """Whether queue-drain (task-mode) gating applies to this session.

    True only for a task-mode record that carries a real SCOPE — a claimed
    task_id or its molecule parent_id. A scopeless record (both null, e.g. from a
    `bd ready --claim` the entry hook couldn't parse) is NOT task-mode: gating it
    would run `bd ready` unscoped = the whole-repo backlog, blocking a finished
    session on work that belongs to a different session (bead e9v.11). Such a
    session falls through to the normal contract gate, which still blocks a red
    contract — teeth kept, false whole-repo block removed.
    """
    if not isinstance(session_mode, dict) or session_mode.get("mode") != "task":
        return False
    return bool(session_mode.get("parent_id") or session_mode.get("task_id"))


def _check_task_mode_queue(session_mode: dict, run_bd=None) -> Tuple[str, str]:
    """Run bd ready / bd list in repo_cwd to determine if queue-drain allows stop.

    Returns (decision, reason) where decision is "allow" or "block".

    Capability probe, NOT a directory check. A git worktree has no literal
    `.beads/` directory but `bd` still resolves the shared Dolt DB via the
    redirect file / BEADS_DIR env (see beads-worktree-integration rule). The
    prior implementation short-circuited to ("allow", "task_mode_no_beads_in_cwd")
    whenever `repo_cwd/.beads` was absent — which silently ungated EVERY worktree
    session (2026-06-01 incident: session 75be09cc allowed Stop 8x while a ready
    sibling task remained). We now probe `bd` directly and degrade to allow ONLY
    when bd cannot resolve a queue at all, while still keeping a real beads repo
    (one whose `.beads/` is present) blocked when bd merely hiccups.

    `run_bd` is injectable for testing; in production it defaults to a
    subprocess runner scoped to repo_cwd and the molecule/task parent.
    """
    repo_cwd = session_mode.get("repo_cwd", "")
    # Scope priority: parent_id (molecule root) > task_id (standalone task) > unscoped.
    # parent_id is set when the claimed task has a parent (e.g., a molecule step).
    # task_id is the fallback for standalone/leaf tasks: bd ready --parent <leaf-id>
    # returns [] since leaf tasks have no children, so the gate allows Stop once
    # the leaf task is closed — which is correct. Without scoping, bd ready returns
    # the entire repo backlog, causing derailment into unrelated tasks.
    parent_id = session_mode.get("parent_id") or session_mode.get("task_id")

    if not repo_cwd:
        return ("block", "task_mode_no_cwd")

    # A real beads repo announces itself with a `.beads/` dir; a worktree does
    # not (it uses a redirect / BEADS_DIR). We use this ONLY to decide how to
    # degrade when bd is unavailable — never to skip the queue check.
    # A3 FIX: also treat a linked worktree whose MAIN repo has `.beads/` as a
    # beads context for degradation purposes — so bd-unavailable there still
    # degrades to BLOCK rather than the graceful-allow that opens the laundering
    # channel (the foreign-worktree incident, 2026-06).
    has_beads_dir = (
        (pathlib.Path(repo_cwd) / ".beads").exists()
        or _main_repo_has_beads(repo_cwd)
    )

    if run_bd is None:
        import json as _json

        def run_bd(args: list[str]) -> Optional[list]:
            """Run bd with --json output; returns parsed list or None on failure.

            Returns [] (empty list) when the subprocess exits 0 but produces no
            parseable JSON (the `blocked` subcommand may not exist on older bd
            versions — treat that as "zero blocked beads" rather than a failure).
            Returns None only for genuine subprocess failures (timeout, missing
            binary, non-zero exit with no parseable output).
            """
            cmd = ["bd"] + args + ["--json"] + (["--parent", parent_id] if parent_id else [])
            try:
                r = subprocess.run(cmd, cwd=repo_cwd, capture_output=True, text=True, timeout=15)
                try:
                    return _json.loads(r.stdout)
                except (_json.JSONDecodeError, ValueError):
                    # Subprocess exited (possibly 0) but stdout is not JSON.
                    # Treat exit 0 as an empty result; non-zero as a failure.
                    if r.returncode == 0:
                        return []
                    return None
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                return None

    ready = run_bd(["ready"])
    if ready is None:
        # bd produced no parseable queue. Inside a real beads repo this is a
        # transient error — stay blocked so the agent can't sneak out. Outside
        # one (no .beads/ dir AND bd can't resolve a DB), it's genuinely not a
        # beads context — degrade gracefully so the gate never permanently traps.
        if has_beads_dir:
            return ("block", "task_mode_bd_ready_failed")
        return ("allow", "task_mode_bd_unavailable")
    if len(ready) > 0:
        return ("block", "tasks_remain_in_queue")

    # bd ready empty: check for scoped blocked beads before granting a drain.
    # An empty ready list with ≥1 blocked bead is the laundering hole: the agent
    # filed a blocker, drained ready, and called it a clean stop. We must probe
    # blocked to distinguish a genuine drain from a manufactured one.
    blocked_args = ["blocked"] + (["--parent", parent_id] if parent_id else [])
    blocked = run_bd(blocked_args)
    if blocked is None:
        # bd failed on the blocked query. Inside a real beads repo, fail toward
        # BLOCK — we cannot verify the drain. Outside one (worktree or older bd
        # that lacks the `blocked` subcommand), treat the query as returning [] so
        # the gate degrades gracefully to queue_drained rather than trapping sessions
        # in environments where the blocked probe is unavailable.
        if has_beads_dir:
            return ("block", "task_mode_bd_ready_failed")
        return ("allow", "queue_drained")
    if len(blocked) > 0:
        return ("block", "blocked_tasks_no_wakeup")
    # Genuinely empty: no ready work, no blocked beads in scope — clean drain.
    return ("allow", "queue_drained")


def _check_wakeup_blockers(session_mode: dict, run_bd=None, thread_dir=None) -> Tuple[str, str]:
    """Gate the wakeup-release path on blocker verifiability (R3).

    When a task-mode session would be released by a registered wakeup, this
    function audits every SESSION-FRESH blocked bead for a substantiated blocker
    claim. Scoping (created_at >= the session watermark, via `thread_dir`) mirrors
    `_check_bd_queue_implicit`: a pre-existing dependency-blocked bead from another
    session is not this session's responsibility and must not hold the wakeup gate
    indefinitely. Without a watermark, all blocked beads are audited (fail-safe).
    A bead is satisfied iff it carries a `blocker-verify:` command that exits 0
    (not trivial) OR a substantive `blocker-waiver:` reason (≥20 chars, not a
    placeholder).  Any unsatisfied blocked bead yields
    ("block", "wakeup_blocker_unverified").

    Zero blocked beads → ("allow", "wakeup_no_blockers") — nothing to verify.

    `run_bd` is injectable for testing (same contract as _check_task_mode_queue).
    `user_released` is unconditional and is handled upstream; this function is
    only called when the wakeup path is being evaluated.
    """
    try:
        from blocker_verify import blocker_satisfied  # noqa: PLC0415 — lazy import
    except ImportError:
        # blocker_verify not available — fail open: do not block what was previously
        # allowed. This module is mandatory in R3-complete installs; the test suite
        # ensures it exists. On older installs, degrade gracefully.
        # F5 fix (gate-design Rule 2): emit a signal so the fail-open is observable
        # in the half-life review corpus and never silent.
        _record_gate_signal(
            "allow", "blocker_verify_unavailable", "", "blocker_verify_import_error"
        )
        return ("allow", "blocker_verify_unavailable")

    repo_cwd = session_mode.get("repo_cwd", "")
    parent_id = session_mode.get("parent_id") or session_mode.get("task_id")

    if run_bd is None:
        import json as _json

        def run_bd(args: list) -> Optional[list]:
            cmd = ["bd"] + args + ["--json"] + (["--parent", parent_id] if parent_id else [])
            try:
                r = subprocess.run(cmd, cwd=repo_cwd, capture_output=True, text=True, timeout=15)
                return _json.loads(r.stdout)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError,
                    _json.JSONDecodeError, ValueError):
                return None

    blocked_args = ["blocked"] + (["--parent", parent_id] if parent_id else [])
    blocked = run_bd(blocked_args)
    if blocked is None or len(blocked) == 0:
        # No blocked beads (or bd unavailable): nothing to verify; wakeup stands.
        return ("allow", "wakeup_no_blockers")

    # Scope to session-fresh blocked beads (created_at >= watermark), matching
    # _check_bd_queue_implicit. A pre-existing dependency-blocked bead from another
    # session is not this session's responsibility and must not hold the wakeup gate.
    watermark = (
        resolve_watermark(pathlib.Path(thread_dir)) if thread_dir is not None else None
    )
    if watermark is None:
        scoped_blocked = [b for b in blocked if isinstance(b, dict)]
    else:
        scoped_blocked = [
            b for b in blocked
            if isinstance(b, dict) and _created_at_in_scope(b, watermark)
        ]
    if not scoped_blocked:
        # All blocked beads predate this session → not ours to verify; wakeup stands.
        return ("allow", "wakeup_no_blockers")

    for bead in scoped_blocked:
        result = blocker_satisfied(bead)
        if not result.confirmed:
            return ("block", "wakeup_blocker_unverified")

    return ("allow", "wakeup_blockers_verified")


_IMPLICIT_QUEUE_DISPLAY = (
    "continuation-harness: your contract verify passed, but bd still has unfinished work in "
    "this repo. If any of it is in this session's scope, keep going — do NOT stop to summarize "
    "or to ask the user what to do next. If the only open work is unrelated backlog from other "
    "sessions, do not drain it (that is scope creep): instead close out your own claimed tasks, "
    "or call ScheduleWakeup if you are waiting on something external. "
    "Hint: `bd list --status=in_progress` shows what is still claimed."
)


def _created_at_in_scope(item: dict, watermark: "_dt2.datetime") -> bool:
    """True if `item` is session-fresh (created_at >= watermark).

    FAIL-SAFE: a missing/unparseable created_at returns True (treat as in-scope)
    so an item of unknown age biases toward BLOCK, never toward a premature stop.
    """
    if not isinstance(item, dict):
        return True
    ca = _parse_iso(item.get("created_at", ""))
    if ca is None:
        return True
    if ca.tzinfo is None:
        ca = ca.replace(tzinfo=_dt2.timezone.utc)
    wm = watermark if watermark.tzinfo else watermark.replace(tzinfo=_dt2.timezone.utc)
    return ca >= wm


def _check_bd_queue_implicit(
    cwd: str,
    thread_dir=None,
    run_bd=None,
    watermark=None,
) -> Tuple[str, str]:
    """Watermark-scoped implicit Stop-path (beads 858.2 + 858.4).

    Blocks only on SESSION-FRESH bd work (created_at >= watermark); older backlog
    is treated as not-this-session's and does NOT block (fixes the a2n over-block).
    The query set is {in_progress ∪ ready ∪ open} — dropping `open` re-opens FN-4
    (a session-fresh bead blocked on unmet deps is in neither ready nor in_progress).

    Capability probe (858.4, fixes E-1): no `.beads/`-directory check — a worktree
    has no dir but bd resolves via redirect/BEADS_DIR; degrade to advisory-allow only
    when bd genuinely cannot resolve a queue. `watermark` absent ⇒ advisory-allow
    (never a hard block on unscoped backlog, never now()). `run_bd`/`watermark`
    injectable for tests.
    """
    if not cwd:
        return ("allow", "implicit_queue_no_cwd")

    if watermark is None and thread_dir is not None:
        watermark = resolve_watermark(pathlib.Path(thread_dir))

    if run_bd is None:
        import json as _json

        def run_bd(args: list[str]) -> Optional[list]:
            try:
                r = subprocess.run(
                    ["bd"] + args + ["--json"],
                    cwd=cwd, capture_output=True, text=True, timeout=15,
                )
                return _json.loads(r.stdout)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError,
                    _json.JSONDecodeError, ValueError):
                return None

    # No watermark (session predating this feature) ⇒ cannot scope ⇒ advisory allow.
    if watermark is None:
        return ("allow", "scope_no_watermark")

    # {in_progress ∪ ready ∪ open}, capability-probe: degrade on bd FAILURE only.
    in_progress = run_bd(["list", "--status=in_progress"])
    ready = run_bd(["ready"])
    open_items = run_bd(["list", "--status=open"])
    if in_progress is None or ready is None or open_items is None:
        return ("allow", "scope_bd_failed")

    seen: dict = {}
    for it in list(in_progress) + list(ready) + list(open_items):
        if isinstance(it, dict):
            seen[it.get("id") or id(it)] = it
    if any(_created_at_in_scope(it, watermark) for it in seen.values()):
        return ("block", "implicit_queue_scoped")
    return ("allow", "implicit_queue_scoped_drained")


def _record_gate_signal(decision: str, reason: str, session_id: str, notes: str = "") -> None:
    """Bridge a Stop-gate decision to `.beads/.gate-signal.jsonl` (corpus-bridge).

    harness/bin is state-only and cannot import claude/hooks/_gate_signal, so we
    mirror its line shape + .beads resolution (BEADS_DIR, else walk up from cwd).
    REQUIRED because the half-life toolchain and the running launchd monitor read
    ONLY `.gate-signal.jsonl`; a scope decision logged only to incidents.jsonl is
    invisible to half-life review (the corpus-split gap the 858 panel flagged).
    Best-effort — never fails the hook.
    """
    try:
        beads = None
        env = os.environ.get("BEADS_DIR")
        if env and pathlib.Path(env).is_dir():
            beads = pathlib.Path(env)
        else:
            cwd = pathlib.Path(os.getcwd()).resolve()
            for parent in [cwd, *cwd.parents]:
                if (parent / ".beads").is_dir():
                    beads = parent / ".beads"
                    break
        if beads is None:
            return
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "gate": "continuation-harness",
            "decision": decision,
            "reason": reason,
            "session_id": session_id,
            "extras": {"notes": notes} if notes else {},
        }
        with (beads / ".gate-signal.jsonl").open("a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        pass


def _log_incident(record: dict) -> None:
    try:
        INCIDENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with INCIDENTS_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # Don't fail the hook on logging error.
    # Corpus-bridge: half-life review reads .gate-signal.jsonl, not incidents.jsonl.
    _record_gate_signal(
        record.get("decision", ""),
        record.get("reason", ""),
        record.get("session_id", ""),
        record.get("notes", ""),
    )


def main() -> int:
    payload = _read_payload()

    # Anthropic's stop_hook_active flag prevents infinite block loops.
    if payload.get("stop_hook_active"):
        return 0

    session_id = payload.get("session_id") or "unknown"
    transcript_path = payload.get("transcript_path", "")

    try:
        thread_dir = thread_dir_for_session(session_id, HARNESS_ROOT)
        checkout_id = state_identity(session_id)
    except InvalidActorIdentity as exc:
        reason = f"continuation-harness: invalid_actor_identity. {exc}"
        _log_incident({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": session_id,
            "decision": "block",
            "reason": "invalid_actor_identity",
            "was_correct": None,
            "notes": str(exc),
        })
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    thread_dir.mkdir(parents=True, exist_ok=True)

    # bead e9v.4: stamp this session's checkout identity (worktree root + fresh
    # heartbeat) so concurrent-session collision is detectable. Best-effort; a
    # stamp failure must never affect the Stop decision.
    _now_dt = _dt2.datetime.now(_dt2.timezone.utc)
    try:
        session_isolation.write_checkout(thread_dir, checkout_id, os.getcwd(), _now_dt)
    except Exception:  # noqa: BLE001 — deliberate never-raise-into-the-Stop-hook boundary
        pass

    # B1 fix: read last user message from transcript so _user_released() fires.
    recent_user_message = _read_last_user_message(transcript_path)

    # Task mode: queue-drain is the session-scope stopping criterion.
    # User release and wakeup remain universal overrides (checked via would_block_stop
    # with contract=None, which short-circuits to the universal paths before contract check).
    session_mode = _load_json(thread_dir / "session_mode.json")
    # e9v.11: only a SCOPED task-mode record gates here. A scopeless record
    # (task_id and parent_id both null) is not really task mode — gating it would
    # block on the whole-repo backlog — so it falls through to the contract gate.
    if _task_mode_in_effect(session_mode):
        scheduled = _load_json(thread_dir / "scheduled.json")
        override_state = {
            "contract": None,
            "scheduled": scheduled,
            "recent_user_message": recent_user_message,
        }
        override_decision, override_reason = would_block_stop(override_state)
        # Only the GENUINE universal overrides (wakeup / user-release) short-circuit
        # the task-mode queue check. would_block_stop is called with contract=None
        # here, which now also returns ("allow", "conversational") — that must NOT
        # bypass the queue gate (it would let a task-mode session with ready work
        # stop). Gate strictly on the two real overrides.
        if override_decision == "allow" and override_reason in ("wakeup_registered", "user_released"):
            # F1 wiring (verifier Finding 1): a wakeup override must pass through
            # _check_wakeup_blockers before being allowed — a fabricated blocker bead
            # can launder a permanent stop through the wakeup path.  user_released is
            # unconditional and bypasses the check.
            if override_reason == "wakeup_registered":
                wakeup_blocker_decision, wakeup_blocker_reason = _check_wakeup_blockers(
                    session_mode, thread_dir=thread_dir
                )
                if wakeup_blocker_decision == "block":
                    _log_incident({
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "session_id": session_id,
                        "decision": wakeup_blocker_decision,
                        "reason": wakeup_blocker_reason,
                        "was_correct": None,
                        "notes": "task_mode_wakeup_blocker_check",
                    })
                    display = (
                        _TASK_MODE_DISPLAY.get(wakeup_blocker_reason)
                        or RESUMPTION_PROMPT.format(reason=wakeup_blocker_reason)
                    )
                    print(json.dumps({"decision": "block", "reason": display}))
                    return 0
            # universal overrides apply in task mode too.
            # Tag a wakeup-allow as scope_wakeup_pause so half-life review can count
            # pacing-pause fires vs genuine completion (858.6 / design Step 4 signal).
            _log_incident({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "session_id": session_id,
                "decision": override_decision,
                "reason": override_reason,
                "was_correct": None,
                "notes": ("scope_wakeup_pause" if override_reason == "wakeup_registered"
                          else "task_mode_universal_override"),
            })
            return 0
        decision, reason = _check_task_mode_queue(session_mode)
        _log_incident({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": session_id,
            "decision": decision,
            "reason": reason,
            "was_correct": None,
            "notes": "task_mode",
        })
        if decision == "block":
            display = _TASK_MODE_DISPLAY.get(reason) or RESUMPTION_PROMPT.format(reason=reason)
            print(json.dumps({"decision": "block", "reason": display}))
        return 0

    # No task mode: contract gate.
    # Per continuation-harness spec, the three Stop-permission paths are universal:
    # verification_passed, wakeup_registered, user_released. Sessions that never
    # declared a contract reach those checks via would_block_stop and fall through
    # to ("block", "no_contract") iff none of the three holds. The prior B2 carve-out
    # (no contract.json → silent allow) was an unspec'd inversion of that invariant
    # and made the gate's coverage proportional to whether the agent remembered to
    # call init_contract.py — a presence-only check the gate-design rule forbids.
    state = load_thread_state(thread_dir, recent_user_message=recent_user_message)
    decision, reason = would_block_stop(state)

    # B3 fix + Fix 1: after verification_passed, check for remaining work in cwd —
    # the bd queue (sessions where task-mode wasn't entered, e.g. bd claims inside
    # subagents) AND git work (dirty tracked files / unpushed commits). A green
    # contract verifies one narrow oracle, not a finished session; the git half
    # closes the index-0 shirking miss ("Harness cleared." then stopped with a
    # drained bead queue but uncommitted work). Deterministic — holds when the
    # wind-down judge is down. Universal overrides (user_released, wakeup) bypass.
    if decision == "allow" and reason == "verification_passed":
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = ""
        blocked = _verification_work_remains(cwd, thread_dir)
        if blocked is not None:
            decision, reason = blocked

    # escapement-51w3: a `wakeup_registered` allow bypasses BOTH the
    # verification_passed work-check above and the conversational wind-down rung
    # below — so a session could file session-fresh work, schedule a trivial
    # deploy/CI-check wakeup, and stop with that work abandoned (the cro-dashboard
    # grain-adaptation deferral). Route the wakeup allow through the SAME
    # watermark-scoped queue oracle the task-mode wakeup path already uses
    # (_check_wakeup_blockers → _check_bd_queue_implicit scope). Session-fresh work
    # blocks; unrelated backlog does not (fail-open: no watermark / bd down ⇒ allow).
    winddown_display = None
    if decision == "allow" and reason == "wakeup_registered":
        try:
            cwd_wk = os.getcwd()
        except OSError:
            cwd_wk = ""
        # 1) Deterministic state-check first: session-fresh work left behind (51w3).
        blocked = _wakeup_work_remains(cwd_wk, thread_dir)
        if blocked is not None:
            decision, reason = blocked
        else:
            # 2) Semantic backstop (escapement-lby9): the state-check is blind to a
            # wind-down-shaped final message with a clean bd queue but reversible git
            # work. Run the judge on the wakeup path too — same as conversational.
            winddown_display = _winddown_override(
                "wakeup_registered", transcript_path, cwd_wk, thread_dir
            )
            if winddown_display:
                decision, reason = "block", "winddown_offer_work_remains"

    # Wind-down rung: a `conversational` allow that is actually a wind-down / decision-
    # punt offer WITH reversible work remaining is overridden to a block (closes the
    # would_block_stop.py:176-183 free-pass). Surgical: only the conversational path.
    if decision == "allow" and reason == "conversational":
        try:
            cwd_now = os.getcwd()
        except OSError:
            cwd_now = ""
        winddown_display = _winddown_override(reason, transcript_path, cwd_now, thread_dir)
        if winddown_display:
            decision, reason = "block", "winddown_offer_work_remains"

    _log_incident({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "decision": decision,
        "reason": reason,
        "was_correct": None,
        "notes": (
            "winddown_rung" if reason == "winddown_offer_work_remains"
            else "implicit_queue_check" if reason.startswith("implicit_queue_")
            else ""
        ),
    })

    if decision == "block":
        if winddown_display:
            display = winddown_display
        elif reason.startswith("implicit_queue_"):
            display = _IMPLICIT_QUEUE_DISPLAY
        elif reason == "verification_suppressed":
            display = _VERIFICATION_SUPPRESSED_DISPLAY
        else:
            display = RESUMPTION_PROMPT.format(reason=reason)
        # bead e9v.4: if this red boundary is shared with a live concurrent session,
        # append the worktree-isolation steer — one session's red must not dead-end
        # another's finish. Scoped to the generic unverified-red block (the BLOCK-5
        # case); other reasons carry their own targeted guidance.
        if reason == "no_completion_or_resumption_proof":
            try:
                steer = session_isolation.isolation_steer_for_thread(
                    HARNESS_ROOT, checkout_id, thread_dir, _now_dt
                )
            except Exception:  # noqa: BLE001 — never let the steer crash the Stop decision
                steer = None
            if steer:
                display = display + steer
        out = {"decision": "block", "reason": display}
        print(json.dumps(out))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
