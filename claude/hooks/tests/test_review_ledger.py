"""Tests for _review_ledger.py — the Claude-only corroboration ledger.

This file exists because a second adversarial review found that the
corroboration this ledger provides was worth nothing in two independent ways,
and neither was visible to any existing test:

  - the dispatch fingerprint was written and never read, so a reviewer that
    read state A corroborated a verdict recorded at state B;
  - the ledger lived in world-writable /tmp and re-validated nothing on read,
    so one shell redirect forged `independent: true` with no Agent dispatch.

Both are attacks, so they are tested as attacks: the tests below construct the
adversary's artifact and assert the ledger refuses it. A test that only
exercises the happy path would have stayed green through both defects — which
is exactly what happened.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[1]
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _review_ledger as ledger  # noqa: E402

BEAD = "escapement-abc1"
FP_A = "a" * 64
FP_B = "b" * 64
REVIEWER = "adversarial-reviewer"


@pytest.fixture(autouse=True)
def ledger_dir(tmp_path, monkeypatch):
    d = tmp_path / "claude-review-gate"
    monkeypatch.setattr(ledger, "LEDGER_DIR", d)
    return d


def _write_raw(ledger_dir: Path, name: str, dispatches: list, mode: int = 0o600):
    ledger_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ledger_dir, 0o700)
    path = ledger_dir / name
    path.write_text(json.dumps({"dispatches": dispatches}))
    os.chmod(path, mode)
    return path


class TestReviewIsBoundToTheStateItRead:
    """The defect: the dispatch fingerprint was stored and never compared."""

    def test_a_dispatch_at_the_current_state_corroborates(self):
        ledger.record_dispatch("s1", [BEAD], REVIEWER, FP_A)
        assert ledger.has_dispatch(BEAD, "s1", FP_A)

    def test_a_dispatch_at_an_earlier_state_does_not(self):
        """Dispatch reviewer at A, rewrite everything, record at B.

        This is the whole attack: the reviewer read code that no longer
        exists, and the gate used to accept its blessing anyway.
        """
        ledger.record_dispatch("s1", [BEAD], REVIEWER, FP_A)
        assert not ledger.has_dispatch(BEAD, "s1", FP_B)

    def test_a_dispatch_for_another_bead_does_not(self):
        ledger.record_dispatch("s1", ["escapement-other"], REVIEWER, FP_A)
        assert not ledger.has_dispatch(BEAD, "s1", FP_A)

    def test_no_fingerprint_still_requires_a_dispatch(self):
        """Outside a git tree staleness is uncheckable — but not waivable."""
        assert not ledger.has_dispatch(BEAD, "s1", None)
        ledger.record_dispatch("s1", [BEAD], REVIEWER, None)
        assert ledger.has_dispatch(BEAD, "s1", None)

    def test_the_scan_path_also_compares_the_fingerprint(self):
        """The session-less fallback must not be the weaker door."""
        ledger.record_dispatch("s1", [BEAD], REVIEWER, FP_A)
        assert ledger.has_dispatch(BEAD, None, FP_A)
        assert not ledger.has_dispatch(BEAD, None, FP_B)


class TestTheLedgerCannotBeForged:
    """The defect: world-writable /tmp, no provenance check, nothing re-validated."""

    def test_a_hand_written_entry_with_a_non_reviewer_type_is_refused(self, ledger_dir):
        """`record_dispatch` filters on write; read must filter too."""
        _write_raw(ledger_dir, "forged.json", [{
            "beads": [BEAD], "subagent_type": "general-purpose", "fingerprint": FP_A,
        }])
        assert not ledger.has_dispatch(BEAD, None, FP_A)

    def test_an_entry_with_no_subagent_type_is_refused(self, ledger_dir):
        _write_raw(ledger_dir, "forged.json", [{
            "beads": [BEAD], "fingerprint": FP_A,
        }])
        assert not ledger.has_dispatch(BEAD, None, FP_A)

    @pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX only")
    def test_a_group_or_world_writable_ledger_is_not_believed(self, ledger_dir):
        """Anyone able to write the file could mint their own independence."""
        _write_raw(ledger_dir, "loose.json", [{
            "beads": [BEAD], "subagent_type": REVIEWER, "fingerprint": FP_A,
        }], mode=0o666)
        assert not ledger.has_dispatch(BEAD, None, FP_A)

    @pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX only")
    def test_a_symlinked_ledger_is_refused(self, ledger_dir, tmp_path):
        """Following one would aim the check at a file the attacker controls."""
        real = tmp_path / "attacker.json"
        real.write_text(json.dumps({"dispatches": [{
            "beads": [BEAD], "subagent_type": REVIEWER, "fingerprint": FP_A,
        }]}))
        ledger_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (ledger_dir / "link.json").symlink_to(real)
        assert not ledger.has_dispatch(BEAD, None, FP_A)

    @pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX only")
    def test_a_loosely_writable_directory_is_not_believed(self, ledger_dir):
        """A writable dir lets an attacker swap the file out from under us."""
        _write_raw(ledger_dir, "ok.json", [{
            "beads": [BEAD], "subagent_type": REVIEWER, "fingerprint": FP_A,
        }])
        os.chmod(ledger_dir, 0o777)
        try:
            assert not ledger.has_dispatch(BEAD, None, FP_A)
        finally:
            os.chmod(ledger_dir, 0o700)


class TestWritePermissions:
    @pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX only")
    def test_recording_creates_a_private_dir_and_file(self, ledger_dir):
        ledger.record_dispatch("s1", [BEAD], REVIEWER, FP_A)
        assert os.stat(ledger_dir).st_mode & 0o077 == 0
        assert os.stat(ledger.ledger_path("s1")).st_mode & 0o077 == 0

    @pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX only")
    def test_a_preexisting_loose_dir_is_tightened_on_write(self, ledger_dir):
        """The directory may predate this version; mkdir's mode is ignored then."""
        ledger_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(ledger_dir, 0o777)
        ledger.record_dispatch("s1", [BEAD], REVIEWER, FP_A)
        assert os.stat(ledger_dir).st_mode & 0o077 == 0


class TestStaleness:
    def test_a_dispatch_older_than_the_bound_is_ignored(self, ledger_dir):
        path = _write_raw(ledger_dir, "old.json", [{
            "beads": [BEAD], "subagent_type": REVIEWER, "fingerprint": FP_A,
        }])
        old = os.stat(path).st_mtime - ledger.MAX_DISPATCH_AGE_SECONDS - 60
        os.utime(path, (old, old))
        assert not ledger.has_dispatch(BEAD, None, FP_A)


class TestConcurrentReviewersCannotBeShoppedFor:
    """Two reviewers, one blocking — the blocking one must win.

    Found by mutation testing, not by reasoning: flipping `record_verdict`'s
    tiebreak from newest to oldest left the whole suite green, which meant the
    choice of *which* concurrent verdict counts was unconstrained. With two
    reviewers dispatched in parallel against the same bead at the same tree
    state, the entries' relative order is arbitrary, so an unconstrained
    tiebreak makes "dispatch two reviewers and record whichever one liked it"
    a working bypass — and it needs no bad faith to happen by accident.
    """

    def _two_reviewers(self, tmp_path, monkeypatch, blocking_first: bool):
        monkeypatch.setattr(ledger, "LEDGER_DIR", tmp_path / "ledger")
        fp = "f" * 64
        for _ in range(2):
            ledger.record_dispatch(
                "s1", ["escapement-abc1"], "escapement:adversarial-reviewer", fp
            )
        blocking = "BLOCKER: the captured verdict is never compared to anything."
        clean = "No blockers. The close path binds the record to the bead id."
        order = [blocking, clean] if blocking_first else [clean, blocking]
        for verdict in order:
            ledger.record_verdict(
                "s1", ["escapement-abc1"], "escapement:adversarial-reviewer", verdict
            )
        return ledger.find_dispatch("escapement-abc1", "s1", fp)

    @pytest.mark.parametrize("blocking_first", [True, False])
    def test_the_blocking_verdict_is_the_one_that_counts(
        self, tmp_path, monkeypatch, blocking_first
    ):
        entry = self._two_reviewers(tmp_path, monkeypatch, blocking_first)
        assert entry is not None
        assert entry["blocking"] is True, (
            "arrival order must not decide whether a blocker is honoured"
        )
        assert "BLOCKER" in entry["verdict"]
