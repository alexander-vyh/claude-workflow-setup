"""Tests for claude/hooks/_gate_signal.py — the Rule-2 persistence backbone.

`_gate_signal.record()` is the canonical implementation of gate-design
Rule 2 ("every gate must produce persistent signal"). Every gate calls it
at every decision point, so its two load-bearing behaviors must hold:

  1. Positive control — when a `.beads/` directory exists, `record()` MUST
     append exactly one well-formed JSON line to `.beads/.gate-signal.jsonl`
     whose parsed fields match the call. If this regresses, the entire
     waiver/decision corpus silently stops accumulating.

  2. Negative control — when no `.beads/` directory is locatable, `record()`
     MUST fail SOFT: it must not raise, and it must not create a stray
     signal file. The module docstring promises "A failed record never
     blocks a real gate decision" — a crash here would invert priorities and
     turn logging into an enforcement-killing bug.

Isolation: every test runs inside a fresh tmp dir (via `os.chdir`) with
`BEADS_DIR` and `CLAUDE_CODE_SESSION_ID` controlled, so the real
`.beads/.gate-signal.jsonl` is never touched.

Run from anywhere:
  python3 -m pytest claude/hooks/tests/test_gate_signal.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_hooks_dir = Path(__file__).resolve().parent.parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

import _gate_signal  # noqa: E402
from _gate_signal import (  # noqa: E402
    _SIGNAL_FILENAME,
    _WAIVER_FILENAME,
    record,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Run inside an isolated tmp dir with signal-affecting env cleared.

    Clears BEADS_DIR and CLAUDE_CODE_SESSION_ID so the test controls them
    explicitly, and chdir's into a tmp dir that has NO `.beads/` ancestor
    (tmp_path lives under the OS temp root, not the repo), guaranteeing the
    real signal store is never written. Also redirects the user-level
    fallback sink (GATE_SIGNAL_FALLBACK_DIR) into the tmp tree by default so a
    no-`.beads/` test can never write the REAL `~/.claude/harness/` store;
    fallback-specific tests override this var with their own tmp subdir.
    Returns the tmp dir.
    """
    monkeypatch.delenv("BEADS_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv(
        _gate_signal._FALLBACK_DIR_ENV, str(tmp_path / "default-fallback")
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Positive control: a well-formed entry is written and parses correctly.
# ---------------------------------------------------------------------------

def test_record_writes_wellformed_entry(isolated_env, monkeypatch):
    """With a real .beads/ dir, record() appends one parseable JSON line
    carrying every field the call supplied."""
    beads_dir = isolated_env / ".beads"
    beads_dir.mkdir()
    # Point resolution at this dir explicitly via BEADS_DIR so we don't
    # depend on the CWD walk-up finding it.
    monkeypatch.setenv("BEADS_DIR", str(beads_dir))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-abc-123")

    persisted = record(
        gate_name="spec_id_enforcement",
        decision="deny",
        reason="placeholder value 'none'",
        command="bd create --type=task --spec-id none",
    )

    assert persisted is True
    signal_file = beads_dir / _SIGNAL_FILENAME
    assert signal_file.is_file(), "record() must create the signal file"

    lines = signal_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, "exactly one entry should be appended"

    entry = json.loads(lines[0])
    assert entry["gate"] == "spec_id_enforcement"
    assert entry["decision"] == "deny"
    assert entry["reason"] == "placeholder value 'none'"
    assert entry["session_id"] == "session-abc-123"
    assert entry["extras"] == {
        "command": "bd create --type=task --spec-id none"
    }
    # ts must be a parseable ISO-8601 UTC timestamp (the corpus is sorted
    # and half-life-reviewed by time; an unparseable ts breaks that).
    assert entry["ts"].endswith("+00:00"), entry["ts"]
    from datetime import datetime

    datetime.fromisoformat(entry["ts"])  # raises if malformed


def test_record_appends_does_not_truncate(isolated_env, monkeypatch):
    """A second record() call must append, never overwrite — the corpus
    accumulates (gate-design Rule 1: 'reasons accumulate, not evaporate')."""
    beads_dir = isolated_env / ".beads"
    beads_dir.mkdir()
    monkeypatch.setenv("BEADS_DIR", str(beads_dir))

    record(gate_name="g1", decision="allow", reason="first")
    record(gate_name="g2", decision="deny", reason="second")

    lines = (beads_dir / _SIGNAL_FILENAME).read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["reason"] == "first"
    assert json.loads(lines[1])["reason"] == "second"


def test_record_omits_session_id_when_unset(isolated_env, monkeypatch):
    """session_id is only emitted when CLAUDE_CODE_SESSION_ID is set —
    avoids polluting entries with a null/empty session key."""
    beads_dir = isolated_env / ".beads"
    beads_dir.mkdir()
    monkeypatch.setenv("BEADS_DIR", str(beads_dir))
    # CLAUDE_CODE_SESSION_ID is cleared by the fixture.

    record(gate_name="g", decision="allow")

    entry = json.loads(
        (beads_dir / _SIGNAL_FILENAME).read_text(encoding="utf-8").strip()
    )
    assert "session_id" not in entry
    # extras key omitted entirely when no extras passed.
    assert "extras" not in entry


# ---------------------------------------------------------------------------
# Negative control: no .beads/ anywhere → fail soft, no crash, no .beads-style
# signal file. (Signal is NOT lost — it diverts to the fallback sink, which is
# asserted by the dedicated fallback tests below; here we pin that the primary
# .beads store is never fabricated.)
# ---------------------------------------------------------------------------

def test_record_fails_soft_when_beads_absent(isolated_env, monkeypatch):
    """When no .beads/ dir is locatable, record() must return without raising
    AND must not fabricate a `.beads`-style signal file (`_SIGNAL_FILENAME`).
    A crash here would let a logging failure block a gate decision. Signal is
    preserved via the fallback sink — see
    test_record_falls_back_when_beads_unresolvable for that guarantee."""
    # BEADS_DIR is cleared by the fixture; point it at a non-existent dir
    # to also exercise the "BEADS_DIR set but not a directory" branch.
    monkeypatch.setenv("BEADS_DIR", str(isolated_env / "does-not-exist"))

    # Must not raise.
    record(
        gate_name="spec_id_enforcement",
        decision="deny",
        reason="diverts to the fallback, not the .beads store",
        command="bd create ...",
    )

    # No `.beads`-style signal file should have been fabricated under the tmp
    # tree (the fallback uses a distinct filename and a distinct directory).
    strays = list(isolated_env.rglob(_SIGNAL_FILENAME))
    assert strays == [], f"no .beads signal file should be written; found {strays}"


def test_record_fails_soft_on_unwritable_path(isolated_env, monkeypatch):
    """If the resolved .beads path exists but writing fails (e.g. the
    signal 'file' is actually a directory), record() still must not
    raise — I/O errors are swallowed by design."""
    beads_dir = isolated_env / ".beads"
    beads_dir.mkdir()
    # Make the signal target a directory so open(..., "a") raises IsADirectoryError.
    (beads_dir / _SIGNAL_FILENAME).mkdir()
    monkeypatch.setenv("BEADS_DIR", str(beads_dir))

    # Must not raise despite the open() failing.
    persisted = record(gate_name="g", decision="allow", reason="io error path")

    assert persisted is False
    # The directory we created is still a directory (no file clobbered it).
    assert (beads_dir / _SIGNAL_FILENAME).is_dir()


def test_resolve_signal_path_returns_none_when_absent(isolated_env, monkeypatch):
    """Direct check on the resolver: with no .beads/ and BEADS_DIR unset,
    it returns None (the sentinel record() uses to skip logging)."""
    # Fixture clears BEADS_DIR and chdirs into a .beads-free tmp dir.
    assert _gate_signal._resolve_signal_path() is None


# ---------------------------------------------------------------------------
# Waiver dual-write: event_type="waiver" must land in BOTH stores.
# ---------------------------------------------------------------------------

def test_waiver_event_dual_writes_to_both_stores(isolated_env, monkeypatch):
    """A waiver event must append a well-formed entry to BOTH the unified
    signal store AND the dedicated waiver corpus (gate-design 'Standard
    waiver convention'). The waiver store is the labeled training data the
    half-life review reads; the signal store keeps the full timeline."""
    beads_dir = isolated_env / ".beads"
    beads_dir.mkdir()
    monkeypatch.setenv("BEADS_DIR", str(beads_dir))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-waiver-1")

    record(
        gate_name="spec_id_enforcement",
        decision="waiver-accepted",
        reason="legacy module predates the spec registry; tracked in cake-99",
        event_type="waiver",
        command="bd close --spec-waiver '...'",
    )

    signal_file = beads_dir / _SIGNAL_FILENAME
    waiver_file = beads_dir / _WAIVER_FILENAME
    assert signal_file.is_file(), "waiver must also write the unified signal store"
    assert waiver_file.is_file(), "waiver must write the dedicated waiver corpus"

    signal_lines = signal_file.read_text(encoding="utf-8").splitlines()
    waiver_lines = waiver_file.read_text(encoding="utf-8").splitlines()
    assert len(signal_lines) == 1, "exactly one signal entry"
    assert len(waiver_lines) == 1, "exactly one waiver entry"

    # Both stores must carry the SAME well-formed record.
    for raw in (signal_lines[0], waiver_lines[0]):
        entry = json.loads(raw)
        assert entry["gate"] == "spec_id_enforcement"
        assert entry["decision"] == "waiver-accepted"
        assert entry["event_type"] == "waiver"
        assert (
            entry["reason"]
            == "legacy module predates the spec registry; tracked in cake-99"
        )
        assert entry["session_id"] == "session-waiver-1"
        assert entry["extras"] == {"command": "bd close --spec-waiver '...'"}


def test_non_waiver_event_does_not_touch_waiver_store(isolated_env, monkeypatch):
    """Negative control for the dual-write: a plain (default 'signal') event
    must NOT write the waiver corpus. The waiver store stays the clean,
    reasoned-exception-only corpus the half-life review greps."""
    beads_dir = isolated_env / ".beads"
    beads_dir.mkdir()
    monkeypatch.setenv("BEADS_DIR", str(beads_dir))

    record(gate_name="g", decision="deny", reason="ordinary deny, not a waiver")

    assert (beads_dir / _SIGNAL_FILENAME).is_file()
    assert not (beads_dir / _WAIVER_FILENAME).exists(), (
        "a non-waiver event must not create the waiver corpus"
    )


# ---------------------------------------------------------------------------
# Fallback sink: when the primary .beads/ path is unresolvable, signal must
# NOT be lost — it lands in a user-level fallback (gate-signal single point
# of failure remediation; see docs/reconciliation-rules.md Conflict 2).
# ---------------------------------------------------------------------------

def test_record_falls_back_when_beads_unresolvable(isolated_env, monkeypatch):
    """When the primary signal path cannot be resolved (no .beads/ anywhere),
    record() must NOT drop the entry — it writes to the user-level fallback
    sink. Losing signal silently is exactly the single-point-of-failure the
    fallback exists to close."""
    # No .beads/ ancestor (fixture chdirs into a beads-free tmp dir) and
    # BEADS_DIR points nowhere real, so the primary resolver returns None.
    monkeypatch.setenv("BEADS_DIR", str(isolated_env / "does-not-exist"))
    # Redirect the fallback into the tmp tree so the real ~/.claude store
    # is never polluted.
    fallback_dir = isolated_env / "fallback-home"
    monkeypatch.setenv(_gate_signal._FALLBACK_DIR_ENV, str(fallback_dir))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-fallback-1")

    persisted = record(
        gate_name="spec_id_enforcement",
        decision="deny",
        reason="must be preserved in the fallback, not dropped",
        command="bd create ...",
    )

    assert persisted is True
    fallback_file = fallback_dir / _gate_signal._FALLBACK_FILENAME
    assert fallback_file.is_file(), (
        "record() must write the fallback sink when .beads/ is unresolvable"
    )
    lines = fallback_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, "exactly one fallback entry"
    entry = json.loads(lines[0])
    assert entry["gate"] == "spec_id_enforcement"
    assert entry["decision"] == "deny"
    assert entry["reason"] == "must be preserved in the fallback, not dropped"
    assert entry["session_id"] == "session-fallback-1"
    assert entry["extras"] == {"command": "bd create ..."}

    # And NO stray .beads-style signal file was created (the fallback is the
    # only sink in this context).
    strays = list(isolated_env.rglob(_SIGNAL_FILENAME))
    assert strays == [], f"no .beads signal file should exist; found {strays}"


def test_primary_beads_path_preferred_over_fallback(isolated_env, monkeypatch):
    """Positive control for the fallback's precedence: when .beads/ IS
    resolvable, the entry goes to the primary store and the fallback sink is
    left untouched. The fallback is a last resort, not the default path."""
    beads_dir = isolated_env / ".beads"
    beads_dir.mkdir()
    monkeypatch.setenv("BEADS_DIR", str(beads_dir))
    fallback_dir = isolated_env / "fallback-home"
    monkeypatch.setenv(_gate_signal._FALLBACK_DIR_ENV, str(fallback_dir))

    record(gate_name="g", decision="allow", reason="should go to .beads")

    assert (beads_dir / _SIGNAL_FILENAME).is_file()
    fallback_file = fallback_dir / _gate_signal._FALLBACK_FILENAME
    assert not fallback_file.exists(), (
        "fallback must stay untouched when the primary store is available"
    )


def test_record_fails_soft_when_fallback_also_unwritable(isolated_env, monkeypatch):
    """The fallback must not become a NEW way to crash a gate. If even the
    fallback path is unwritable (its target is a directory), record() must
    still return without raising — fail-soft is preserved end to end."""
    monkeypatch.setenv("BEADS_DIR", str(isolated_env / "does-not-exist"))
    fallback_dir = isolated_env / "fallback-home"
    fallback_dir.mkdir()
    # Make the fallback target itself a directory so open(..., "a") raises.
    (fallback_dir / _gate_signal._FALLBACK_FILENAME).mkdir()
    monkeypatch.setenv(_gate_signal._FALLBACK_DIR_ENV, str(fallback_dir))

    # Must not raise despite both primary and fallback being unwritable.
    persisted = record(gate_name="g", decision="deny", reason="both sinks broken")

    assert persisted is False
    # The directory we created is still a directory (nothing clobbered it).
    assert (fallback_dir / _gate_signal._FALLBACK_FILENAME).is_dir()
