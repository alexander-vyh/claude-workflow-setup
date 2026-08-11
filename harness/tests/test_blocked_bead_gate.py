"""Tests for the stop-gate blocker-laundering close-out (R2 + R3).

WHY THIS EXISTS
A 931k-token session quit by laundering its stop through the harness's own
vocabulary: it filed a blocker bead ("blocked on another team's test / Salesforce
delivery timing" — the "other team" did not exist), which drained `bd ready`, so
`_check_task_mode_queue` returned ("allow", "queue_drained"), and the model wrote
"This is a clean, honest stopping point." and stopped. No ScheduleWakeup, blocker
claim validated by nothing. Three holes lined up; R1 (winddown floor) is pinned in
test_winddown_gate.py. This file pins:

  R2 — empty `bd ready` + ≥1 SCOPED blocked bead no longer grants a free
       queue_drained allow; it BLOCKS with ("block", "blocked_tasks_no_wakeup")
       whose display text names ScheduleWakeup / unblock-or-close / user-stop.
       Empty ready + ZERO blocked stays ("allow", "queue_drained"). The blocked
       probe is SCOPED (--parent) like `bd ready`, so a session parked beside
       unrelated blocked backlog still drains.

  R3 — blocker_verify.py: a `blocker-verify: <cmd>` must EXIT 0 (run with bounded
       timeout) to confirm; a `blocker-waiver: <reason>` must be ≥20 chars and not
       a placeholder. Trivial verify commands ("true", ":", "exit 0", empty) are
       rejected WITHOUT execution (value-not-presence, gate-design Rule 3). When
       the release path is a wakeup AND scoped blocked beads exist, every gating
       blocked bead must carry a passing verify OR a valid waiver — else
       ("block", "wakeup_blocker_unverified"). user_released stays unconditional.

FRAGILE IMPLEMENTATIONS THESE TESTS REJECT
- F1 presence-only verify: any nonempty command string "passes" without running.
      -> killed by test_verify_false_command_is_unverified (runs, exits 1) and
         test_trivial_verify_commands_rejected_without_execution.
- F2 display-only gate: add display text but still return "allow".
      -> killed by R2 tests asserting decision == "block", and the R3 integration
         test asserting ("block", "wakeup_blocker_unverified").
- F4 unscoped blocked probe: query repo-wide blocked beads.
      -> killed by test_scoped_blocked_probe_ignores_out_of_scope: the SCOPED
         blocked query returns [] (impl must allow) even though an unscoped query
         would return blocked backlog.
- F5 waiver presence-only: accept any nonempty waiver reason.
      -> killed by test_waiver_placeholder_rejected / test_waiver_too_short_rejected.

Run: python3 -m pytest harness/tests/test_blocked_bead_gate.py -q
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness" / "bin"))

import stop_hook  # noqa: E402


# ---------------------------------------------------------------------------
# Shared run_bd fake. Maps the bd subcommand (first arg) to its parsed list,
# or None to simulate a bd failure. Mirrors test_task_mode_queue._fake_runner
# but also records the args of every call so scope assertions are possible.
# ---------------------------------------------------------------------------

def _fake_runner(responses, calls=None):
    """responses maps bd subcommand -> list|None. `calls` (if a list) records
    every full args vector passed to run_bd, so a test can assert scoping."""

    def run_bd(args):
        if calls is not None:
            calls.append(list(args))
        if args[:1] == ["show"]:
            return [{"id": args[1], "status": "closed"}]
        return responses.get(args[0])

    return run_bd


# ===========================================================================
# R2 — blocked-bead drain requires a wakeup (task mode)
# ===========================================================================

def test_empty_ready_with_blocked_bead_blocks(tmp_path):
    """POSITIVE CONTROL / the hole: ready drained but a scoped blocked bead
    exists and no wakeup is registered -> must BLOCK, not free-allow.

    NOTE: `_check_task_mode_queue` itself does not see scheduled.json, so at this
    layer a blocked bead with no wakeup info must block. The wakeup interplay
    (blocked + wakeup) is exercised at the integration layer below."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": [], "blocked": [{"id": "cake-m95.4.9"}]})
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert (decision, reason) == ("block", "blocked_tasks_no_wakeup"), (
        "empty ready + a scoped blocked bead must BLOCK (the laundering hole); "
        f"got {decision}/{reason}"
    )


def test_empty_ready_zero_blocked_allows(tmp_path):
    """NEGATIVE CONTROL: ready drained AND zero blocked -> queue_drained, unchanged.
    This is the genuine clean-drain that must still allow Stop."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": [], "blocked": []})
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert (decision, reason) == ("allow", "queue_drained"), (
        f"empty ready + zero blocked must still allow Stop; got {decision}/{reason}"
    )


def test_ready_nonempty_does_not_consult_blocked(tmp_path):
    """NEGATIVE CONTROL: ready non-empty -> tasks_remain_in_queue, unchanged. The
    blocked probe must not even be needed (and must not flip this to a new reason)."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": [{"id": "cake-m95.4.2"}], "blocked": [{"id": "x"}]})
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert (decision, reason) == ("block", "tasks_remain_in_queue"), (
        f"ready work must block as before; got {decision}/{reason}"
    )


def test_blocked_probe_is_scoped_by_parent(tmp_path):
    """The blocked-bead probe MUST be scoped via --parent, identically to bd ready.
    Probing repo-wide blocked backlog re-opens the over-reach the team walked back
    (stop_hook: 'Without scoping, bd ready returns the entire repo backlog')."""
    calls: list = []
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": [], "blocked": [{"id": "cake-m95.4.9"}]}, calls=calls)
    stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    blocked_calls = [c for c in calls if c and c[0] == "blocked"]
    assert blocked_calls, "the gate must issue a 'blocked' query when ready is empty"
    # The default production runner appends --parent; with injection the gate must
    # still signal scope. We accept either an explicit --parent in the args OR the
    # gate honoring scope by construction — assert the parent id travels with it.
    assert any("--parent" in c or "cake-m95.4" in c for c in blocked_calls), (
        f"blocked probe must be scoped to the session parent; calls={blocked_calls}"
    )


def test_scoped_blocked_probe_ignores_out_of_scope(tmp_path):
    """F4 KILLER: the SCOPED blocked query returns [] (this session's scope is
    clean) even though the repo has unrelated blocked backlog. The gate must ALLOW
    the drain. An unscoped impl that pulls repo-wide blocked work would block."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    # `blocked` (the scoped probe) returns [] — out-of-scope backlog is invisible
    # to a correctly-scoped query, so the fake simply doesn't surface it.
    run_bd = _fake_runner({"ready": [], "blocked": []})
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert (decision, reason) == ("allow", "queue_drained"), (
        "a session whose scoped blocked queue is empty must drain even when "
        f"unrelated blocked backlog exists repo-wide; got {decision}/{reason}"
    )


def test_blocked_probe_none_inside_beads_repo_blocks(tmp_path):
    """MISSING/UNRESOLVED: ready empty, but the blocked query itself returns None
    (bd hiccup) inside a real beads repo -> fail toward BLOCK (do not silently
    allow a drain we could not verify). Mirrors the ready-None policy."""
    (tmp_path / ".beads").mkdir()
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": [], "blocked": None})
    decision, reason = stop_hook._check_task_mode_queue(session_mode, run_bd=run_bd)
    assert decision == "block", (
        "a blocked-query failure inside a real beads repo must not let the agent "
        f"sneak out via an unverified drain; got {decision}/{reason}"
    )


def test_blocked_display_text_names_schedulewakeup():
    """gate-design Rule 1 (escape IN the denial) + Rule 3 (value not presence).
    The new reason's display text must exist and name the agent-invokable escapes:
    ScheduleWakeup, unblock/close the bead, and the user-stop release valve."""
    display = stop_hook._TASK_MODE_DISPLAY.get("blocked_tasks_no_wakeup")
    assert display, "a display entry for blocked_tasks_no_wakeup must exist"
    low = display.lower()
    assert "schedulewakeup" in low, "denial must name ScheduleWakeup as an escape"
    assert ("unblock" in low or "close" in low), (
        "denial must name unblocking/closing the bead if the blocker is refuted"
    )
    assert "stop" in low, "denial must preserve the user 'stop' release valve"


# ===========================================================================
# R3 — blocker_verify.py: verify-or-waiver, value-not-presence
# ===========================================================================

# The module does not exist yet; importing it here makes every R3 test fail with
# a clean ImportError until the developer creates harness/bin/blocker_verify.py.
try:
    import blocker_verify  # noqa: E402
    _HAVE_BV = True
except Exception:  # pragma: no cover - module not yet implemented
    _HAVE_BV = False

requires_bv = pytest.mark.skipif(
    not _HAVE_BV, reason="harness/bin/blocker_verify.py not yet implemented"
)


def test_blocker_verify_module_exists():
    """TRIPWIRE: an unconditional red until R3 lands. The per-test skipif keeps the
    detailed R3 cases from polluting the report with skips, but this single test
    stays FAILING so R3 cannot be silently dropped (a skipped suite is not a green
    one). The developer turns this green by creating harness/bin/blocker_verify.py
    exposing verify_command / valid_waiver / parse_blocker_spec / blocker_satisfied."""
    assert _HAVE_BV, (
        "harness/bin/blocker_verify.py must exist and expose verify_command, "
        "valid_waiver, parse_blocker_spec, blocker_satisfied"
    )


# --- substance floor: trivial verify commands rejected WITHOUT execution (F1) --

@requires_bv
@pytest.mark.parametrize("cmd", [
    # Original closed-whitelist cases.
    "true", ":", "exit 0", "  ", "", "TRUE", "true ;",
    # Verifier-confirmed BYPASSES (round 1 CONCERN, Finding 2): each of these
    # RUNS and exits 0, so a narrow `frozenset({"true",":","exit 0"})` whitelist
    # lets them through as confirmed=True. The substance floor must be semantic,
    # not a literal-string match — these are all no-ops dressed up.
    "/bin/true", "/usr/bin/true", "true 2>/dev/null", "true && true", "[ 0 -eq 0 ]",
    # SECOND-GENERATION BYPASSES (final review, BOTH reviewers' Finding 2): the
    # round-3 set + the `true && true` / `true 2>/dev/null` patterns still let these
    # through — confirmed empirically as confirmed=True, reason='exit_0'. Each is
    # `true`/`:` reached through a builtin/wrapper/subshell/env-assignment, or a
    # short-circuit / pipe / brace-group whose effective result is the no-op. The
    # floor must recognise the no-op behind the wrapper, NOT enumerate string literals.
    "command true",   # POSIX builtin wrapper (reviewer 1)
    "env true",       # environment-stripped exec of true (reviewer 1)
    "sh -c true",     # subshell whose only command is true (reviewer 1)
    "true||false",    # `||` short-circuit (reviewer 1 + 2): different OPERATOR than
                      # the caught `&&` form — `true` wins, exits 0
    "TRUE=1 true",    # env-assignment prefix in front of the no-op (reviewer 1)
    "bash -c true",   # subshell via bash (reviewer 2)
    "(true)",         # parenthesised subshell (reviewer 2)
    "exec true",      # exec builtin replacing the shell with true (reviewer 2)
    # NOVEL no-op COMPOSITIONS — canaries against a pure-enumeration "fix" (just add
    # the strings above to a bigger frozenset). These are unusual compositions a
    # hand-written list would not anticipate; a SEMANTIC normalizer rejects them, an
    # enumeration does not. Enumeration-only is the NAMED fragile implementation here.
    ":|:",            # `:` piped to `:` — two no-op builtins, exits 0
    "{ true; }",      # brace group around the no-op
])
def test_trivial_verify_commands_rejected_without_execution(cmd):
    """F1/F2 KILLER: a trivial command must be rejected as non-substantive WITHOUT
    being run. `true`/`:`/`exit 0` AND their path-qualified / compound / redirected
    equivalents (`/usr/bin/true`, `true && true`, `[ 0 -eq 0 ]`) all exit 0 if
    executed — a presence-only or literal-whitelist impl wrongly confirms them.
    The floor must recognize the no-op semantically, not by exact string."""
    result = blocker_verify.verify_command(cmd)
    assert result.confirmed is False, (
        f"trivial verify command {cmd!r} must NOT confirm a blocker (it is a no-op "
        "dressed up; the substance floor must be semantic, not a literal whitelist)"
    )
    # The reason must indicate a non-execution rejection (trivial/no-command),
    # NOT an execution-based pass. We accept the family of trivial reasons rather
    # than pin one string, so the dev can choose the label — but it must not be
    # 'exit_0' (that would mean it RAN the no-op and accepted the exit code).
    assert result.reason != "exit_0", (
        f"{cmd!r} must be rejected as trivial BEFORE execution, not run and accepted "
        f"on its exit code; got reason {result.reason!r}"
    )
    assert "trivial" in result.reason or result.reason == "no_command", (
        f"trivial command must be rejected as trivial/no-command; got {result.reason!r}"
    )


# --- over-rejection guard: a semantic floor must not swallow REAL commands ----

@requires_bv
@pytest.mark.parametrize("cmd", [
    "test -f /etc/hosts",   # real filesystem check, exits 0 — must still confirm
    "test -d /",            # real directory check, exits 0
])
def test_substantive_verify_commands_still_confirm(cmd):
    """OVER-REJECTION GUARD (pairs with the trivial-rejection killer above): the
    second-generation floor that rejects `command true` / `env true` / `sh -c true`
    must NOT also start rejecting substantive commands that happen to share a token
    (e.g. `test ...` vs the `[ 0 -eq 0 ]` bracket no-op). A real exit-0 command must
    run and confirm. Defeats an over-broad fix that blacklists by prefix word."""
    result = blocker_verify.verify_command(cmd)
    assert result.confirmed is True, (
        f"a substantive exit-0 verify command must still confirm: {cmd!r}; "
        f"got confirmed={result.confirmed}, reason={result.reason!r}"
    )
    assert result.reason == "exit_0", (
        f"{cmd!r} must confirm by being RUN (reason 'exit_0'), not be mistaken for "
        f"a trivial no-op; got reason {result.reason!r}"
    )


@requires_bv
def test_verify_real_passing_command_confirms():
    """POSITIVE CONTROL: a substantive command that exits 0 confirms the blocker.
    Use a real subprocess with a safe, non-trivial command (checks a path the test
    controls exists) so we observe a real exit code, not a mock."""
    result = blocker_verify.verify_command(
        "test -d /")  # substantive (not in the trivial set), exits 0
    assert result.confirmed is True, (
        f"a real exit-0 verify command must confirm; got {result.reason!r}"
    )


@requires_bv
def test_verify_false_command_is_unverified():
    """NEGATIVE CONTROL + F1 KILLER: a command that RUNS and exits non-zero is
    unverified. A presence-only impl (nonempty string -> pass) would wrongly
    confirm this."""
    result = blocker_verify.verify_command("test -d /no/such/path/xyzzy")
    assert result.confirmed is False, (
        "a verify command that exits non-zero must NOT confirm the blocker"
    )


@requires_bv
def test_verify_command_error_is_unverified_not_confirmed():
    """A command that errors (nonexistent binary) -> unverified, never confirmed.
    The waiver is the escape, so an un-runnable check is not a trap."""
    result = blocker_verify.verify_command("this_binary_does_not_exist_zzz --flag")
    assert result.confirmed is False, "a verify command that errors must not confirm"


# --- waiver substance floor (F5) ---

@requires_bv
@pytest.mark.parametrize("reason", ["tbd", "n/a", "todo", "TODO", "na", "none", "?"])
def test_waiver_placeholder_rejected(reason):
    """F5 KILLER: placeholder waiver reasons are not substance."""
    assert blocker_verify.valid_waiver(reason) is False, (
        f"placeholder waiver {reason!r} must be rejected"
    )


@requires_bv
def test_waiver_too_short_rejected():
    assert blocker_verify.valid_waiver("waiting on x") is False, (
        "a <20-char waiver reason must be rejected (substance floor)"
    )


@requires_bv
def test_waiver_empty_rejected():
    assert blocker_verify.valid_waiver("") is False
    assert blocker_verify.valid_waiver(None) is False


@requires_bv
def test_waiver_substantive_accepted():
    """POSITIVE CONTROL: a real, specific, ≥20-char, non-placeholder reason."""
    reason = "Salesforce sandbox refresh ETA 2026-06-12; cannot script the check, manual confirm next session"
    assert blocker_verify.valid_waiver(reason) is True


# --- bead-text parsing: precedence + extraction ---

@requires_bv
def test_parse_verify_line_from_bead_text():
    """The verify/waiver lines are parsed from a bead's text. Pin the contract:
    a `blocker-verify:` line yields the command string."""
    text = "Blocked on the export job.\nblocker-verify: gh run view 123 --exit-status\nmore notes"
    spec = blocker_verify.parse_blocker_spec(text)
    assert spec.verify_command == "gh run view 123 --exit-status"
    assert spec.waiver_reason is None


@requires_bv
def test_parse_waiver_line_from_bead_text():
    text = "blocker-waiver: external SFDC delivery window, no sandbox to reproduce locally this week"
    spec = blocker_verify.parse_blocker_spec(text)
    assert spec.waiver_reason == "external SFDC delivery window, no sandbox to reproduce locally this week"
    assert spec.verify_command is None


@requires_bv
def test_bare_blocker_claim_has_neither():
    """THE INCIDENT: a bare prose blocker claim has neither a verify nor a waiver
    line. parse must surface that emptiness so the gate can reject it."""
    text = "Blocked on another team's Salesforce test."
    spec = blocker_verify.parse_blocker_spec(text)
    assert spec.verify_command is None and spec.waiver_reason is None


# --- the bead-level gate: confirmed iff passing verify OR valid waiver ---

@requires_bv
def test_bead_with_passing_verify_is_satisfied():
    bead = {"id": "cake-1", "description": "blocker-verify: test -d /"}
    assert blocker_verify.blocker_satisfied(bead).confirmed is True


@requires_bv
def test_bead_with_valid_waiver_is_satisfied():
    bead = {"id": "cake-1", "description":
            "blocker-waiver: SFDC sandbox refresh pending until 2026-06-12, manual reverify scheduled"}
    assert blocker_verify.blocker_satisfied(bead).confirmed is True


@requires_bv
def test_bead_with_bare_claim_is_unsatisfied():
    """THE INCIDENT, end to end at the bead level: a bare blocker claim is NOT
    satisfied -> the gate must treat it as unverified."""
    bead = {"id": "cake-1", "description": "Blocked on another team's Salesforce test."}
    assert blocker_verify.blocker_satisfied(bead).confirmed is False


@requires_bv
def test_bead_with_trivial_verify_is_unsatisfied():
    """F1 at the bead level: `blocker-verify: true` is trivial -> not satisfied,
    not executed."""
    bead = {"id": "cake-1", "description": "blocker-verify: true"}
    assert blocker_verify.blocker_satisfied(bead).confirmed is False


# ===========================================================================
# R3 integration into the stop_hook wakeup path
#
# Contract: when the task-mode release path is a wakeup AND scoped blocked beads
# exist, every gating blocked bead must be satisfied (passing verify OR valid
# waiver) or the gate yields ("block", "wakeup_blocker_unverified"). We exercise
# the integration via stop_hook's task-mode wakeup branch with injected run_bd.
#
# These reference stop_hook._check_wakeup_blockers — a helper the developer adds.
# If they integrate differently, these tests pin the OBSERVABLE contract; adapt
# the call site, never the asserted outcome.
# ===========================================================================

_have_wakeup_helper = hasattr(stop_hook, "_check_wakeup_blockers")
requires_wakeup_helper = pytest.mark.skipif(
    not _have_wakeup_helper,
    reason="stop_hook._check_wakeup_blockers not yet implemented",
)


def test_wakeup_blocker_helper_exists():
    """TRIPWIRE: unconditional red until the R3 integration helper lands, so the
    wakeup-path cases can't be silently skipped. The developer turns this green by
    adding stop_hook._check_wakeup_blockers(session_mode, run_bd=...)."""
    assert _have_wakeup_helper, (
        "stop_hook._check_wakeup_blockers(session_mode, run_bd=...) must exist to "
        "gate the wakeup-release path on blocker verifiability"
    )


@requires_wakeup_helper
def test_wakeup_with_unverified_blocker_blocks(tmp_path):
    """POSITIVE CONTROL / the incident shape: a wakeup is registered, a scoped
    blocked bead exists, but it carries a bare claim -> ("block",
    "wakeup_blocker_unverified"). The wakeup must NOT launder a fabricated blocker."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({
        "ready": [],
        "blocked": [{"id": "cake-m95.4.9", "description": "Blocked on another team's test."}],
    })
    decision, reason = stop_hook._check_wakeup_blockers(session_mode, run_bd=run_bd)
    assert (decision, reason) == ("block", "wakeup_blocker_unverified"), (
        f"wakeup must not release on an unverified blocker; got {decision}/{reason}"
    )


@requires_wakeup_helper
def test_wakeup_with_verified_blocker_allows(tmp_path):
    """POSITIVE CONTROL: wakeup + every scoped blocked bead carries a passing
    verify -> allow (the legitimate 'I'm waiting on a real, checkable blocker')."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({
        "ready": [],
        "blocked": [{"id": "cake-m95.4.9", "description": "blocker-verify: test -d /"}],
    })
    decision, reason = stop_hook._check_wakeup_blockers(session_mode, run_bd=run_bd)
    assert decision == "allow", (
        f"a verified blocker under a wakeup must release; got {decision}/{reason}"
    )


@requires_wakeup_helper
def test_wakeup_with_waivered_blocker_allows(tmp_path):
    """POSITIVE CONTROL: a substantive waiver also releases the wakeup path."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({
        "ready": [],
        "blocked": [{"id": "cake-m95.4.9", "description":
                     "blocker-waiver: SFDC sandbox refresh ETA 2026-06-12, manual reverify next session"}],
    })
    decision, reason = stop_hook._check_wakeup_blockers(session_mode, run_bd=run_bd)
    assert decision == "allow", (
        f"a substantively-waivered blocker under a wakeup must release; got {decision}/{reason}"
    )


@requires_wakeup_helper
def test_wakeup_with_mixed_blockers_blocks_on_the_unverified_one(tmp_path):
    """One verified blocker + one bare-claim blocker -> still BLOCK. EVERY gating
    blocked bead must be satisfied; a single unverified one is the laundering hole."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({
        "ready": [],
        "blocked": [
            {"id": "a", "description": "blocker-verify: test -d /"},
            {"id": "b", "description": "Blocked on another team's test."},
        ],
    })
    decision, reason = stop_hook._check_wakeup_blockers(session_mode, run_bd=run_bd)
    assert (decision, reason) == ("block", "wakeup_blocker_unverified"), (
        f"a single unverified blocker among many must block; got {decision}/{reason}"
    )


@requires_wakeup_helper
def test_wakeup_with_zero_blocked_allows(tmp_path):
    """NEGATIVE CONTROL: wakeup + zero scoped blocked beads -> allow (nothing to
    verify; the wakeup override stands unchanged)."""
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    run_bd = _fake_runner({"ready": [], "blocked": []})
    decision, reason = stop_hook._check_wakeup_blockers(session_mode, run_bd=run_bd)
    assert decision == "allow", (
        f"a wakeup with no blocked beads must release unchanged; got {decision}/{reason}"
    )


# --- NOTE 3: back-compat parity between the two production run_bd helpers --------
#
# `_check_task_mode_queue`'s production run_bd maps `exit 0 + non-JSON stdout -> []`
# (back-compat for an older bd whose `blocked` subcommand is absent), returning None
# only on a genuine failure. `_check_wakeup_blockers`'s production run_bd lacks that
# mapping (it returns None on any JSONDecodeError regardless of exit code). The
# OBSERVABLE consequence is currently SAFE — both paths release on old bd (the wakeup
# path because line `blocked is None or len==0 -> allow`). We pin that OBSERVABLE
# CONTRACT (old bd degrades to a wakeup release) directly against the PRODUCTION
# runner, so the cosmetic asymmetry can never silently become a behavioral divergence
# (e.g. a future change that makes the wakeup path fail-toward-block on None would
# regress old-bd installs into permanent traps — this test would catch it).

@requires_wakeup_helper
@pytest.mark.parametrize("stdout", ["", "usage: bd ...\n", "not json at all"])
def test_wakeup_old_bd_exit0_nonjson_degrades_to_release(stdout, monkeypatch, tmp_path):
    """NOTE 3 PARITY PIN: old bd (the `blocked` subcommand absent) exits 0 and prints
    non-JSON. The PRODUCTION wakeup runner (run_bd=None) must degrade to a release —
    identically to _check_task_mode_queue's back-compat — never trap the session.

    Drives the real subprocess path (mocked) rather than the injected fake, so it pins
    the production runner's old-bd handling, which is exactly where the asymmetry lives.
    Green today (None->allow) and green after any alignment fix that adds the explicit
    exit-0->[] mapping; RED only if a future change makes the wakeup path fail-toward-
    block on old bd."""
    class _FakeProc:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    monkeypatch.setattr(
        stop_hook.subprocess, "run", lambda *a, **k: _FakeProc(0, stdout)
    )
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    decision, reason = stop_hook._check_wakeup_blockers(session_mode, run_bd=None)
    assert decision == "allow", (
        "old bd (exit 0, non-JSON stdout for the absent `blocked` subcommand) must "
        "degrade the wakeup path to a release, mirroring _check_task_mode_queue's "
        f"back-compat; got {decision}/{reason}"
    )


@requires_wakeup_helper
def test_wakeup_genuine_bd_failure_still_releases_safely(monkeypatch, tmp_path):
    """NOTE 3 companion: a GENUINE bd failure (missing binary) on the wakeup path also
    degrades to release (None -> allow). Distinguishes 'old bd, no blocked subcommand'
    from 'bd broken' — both safe-degrade here, so the parity pin above does not
    accidentally demand a block on real failures."""
    def _boom(*a, **k):
        raise FileNotFoundError("bd")

    monkeypatch.setattr(stop_hook.subprocess, "run", _boom)
    session_mode = {"repo_cwd": str(tmp_path), "parent_id": "cake-m95.4"}
    decision, reason = stop_hook._check_wakeup_blockers(session_mode, run_bd=None)
    assert decision == "allow", (
        f"a genuine bd failure on the wakeup path must safe-degrade to release; "
        f"got {decision}/{reason}"
    )


@requires_wakeup_helper
def test_wakeup_unverified_display_names_escapes():
    """gate-design Rule 1: the wakeup_blocker_unverified denial names the fix
    paths — add a blocker-verify command, OR a blocker-waiver reason, OR user-stop."""
    display = stop_hook._TASK_MODE_DISPLAY.get("wakeup_blocker_unverified")
    assert display, "a display entry for wakeup_blocker_unverified must exist"
    low = display.lower()
    assert "blocker-verify" in low, "denial must name the blocker-verify escape"
    assert "blocker-waiver" in low or "waiver" in low, "denial must name the waiver escape"
    assert "stop" in low, "denial must preserve the user 'stop' release valve"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
