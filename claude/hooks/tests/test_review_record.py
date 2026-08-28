"""Tests for _review_record.py and the escapement_review recording CLI.

test_review_gate.py mocks the fingerprint and the Beads read so it can test the
*decision*. That leaves the two things the decision rests on unverified, so
they are tested here against real behaviour instead:

  - work_fingerprint() against a real git work tree, because "the review is
    stale" is only a true statement if the fingerprint actually moves when the
    work moves, and actually holds still when it does not.
  - the recording CLI's refusal path, because a record that fails the substance
    bar must be rejected where it is created, not silently stored and denied
    later at `bd close` with the authoring context gone.

These are deliberately independent of the gate's own tests: if the fingerprint
silently degraded to a constant, the gate suite would still be green (it mocks
it) while the shipped gate stopped detecting stale reviews entirely.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[1]
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _review_record as rr  # noqa: E402
import escapement_review as cli  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A real git work tree: a `main` baseline plus a feature branch.

    Shaped like actual Escapement work — a worktree on a task branch off the
    default branch — because that is what makes the merge-base fingerprint
    meaningful. On `main` itself there is nothing to diff against, and the
    digest degrades to HEAD-relative.
    """
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "app.py").write_text("def total(x):\n    return x\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


class TestWorkFingerprint:
    def test_identical_across_calls(self, repo):
        """A re-run with no edits must not spuriously invalidate a review."""
        assert rr.work_fingerprint(str(repo)) == rr.work_fingerprint(str(repo))

    def test_moves_when_tracked_code_changes(self, repo):
        before = rr.work_fingerprint(str(repo))
        (repo / "app.py").write_text("def total(x):\n    return x * 2\n")
        assert rr.work_fingerprint(str(repo)) != before

    def test_moves_when_a_new_file_appears(self, repo):
        before = rr.work_fingerprint(str(repo))
        (repo / "extra.py").write_text("x = 1\n")
        assert rr.work_fingerprint(str(repo)) != before

    def test_moves_when_an_untracked_file_is_edited(self, repo):
        """The regression that mocked gate tests could not catch.

        In a fresh worktree most new work is untracked until the first commit.
        An earlier version hashed untracked *paths* only, so rewriting a new
        module after review left the fingerprint unchanged and the gate
        happily closed work that no one had reviewed in its final form.
        """
        (repo / "new_module.py").write_text("def total(x):\n    return x\n")
        before = rr.work_fingerprint(str(repo))
        (repo / "new_module.py").write_text("def total(x):\n    return x * 2\n")
        assert rr.work_fingerprint(str(repo)) != before

    def test_large_untracked_file_still_detects_a_length_change(self, repo):
        """Above the size cap, contents are skipped but length still counts."""
        big = repo / "big.bin"
        big.write_bytes(b"x" * (rr._MAX_UNTRACKED_BYTES + 10))
        before = rr.work_fingerprint(str(repo))
        big.write_bytes(b"x" * (rr._MAX_UNTRACKED_BYTES + 20))
        assert rr.work_fingerprint(str(repo)) != before

    def test_unreadable_untracked_path_does_not_raise(self, repo):
        """A crashing fingerprint would block every close in the repository."""
        (repo / "dangling").symlink_to(repo / "nope")
        assert rr.work_fingerprint(str(repo)) is not None

    def test_holds_still_when_the_same_work_is_committed(self, repo):
        """review -> commit -> close must not be denied.

        This assertion used to run the other way, asserting that committing
        invalidated the review — which enshrined the single most damaging false
        positive in the gate as if it were the spec. Committing does not change
        the code; it changes where the code is stored. The fingerprint is taken
        against the merge-base precisely so that the ordinary sequence review,
        commit, push, close is not refused every single time.
        """
        (repo / "app.py").write_text("def total(x):\n    return x * 2\n")
        before = rr.work_fingerprint(str(repo))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "change")
        assert rr.work_fingerprint(str(repo)) == before

    def test_still_moves_when_committed_work_is_then_edited(self, repo):
        """Holding still across a commit must not blunt real staleness."""
        (repo / "app.py").write_text("def total(x):\n    return x * 2\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "change")
        before = rr.work_fingerprint(str(repo))
        (repo / "app.py").write_text("def total(x):\n    return x * 99\n")
        assert rr.work_fingerprint(str(repo)) != before

    def test_ignores_gitignored_noise(self, repo):
        """Build output must not invalidate an otherwise-current review."""
        (repo / ".gitignore").write_text("build/\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "ignore")
        before = rr.work_fingerprint(str(repo))
        (repo / "build").mkdir()
        (repo / "build" / "out.o").write_text("binary")
        assert rr.work_fingerprint(str(repo)) == before

    def test_none_outside_a_git_tree(self, tmp_path):
        """Unknowable is not the same as changed — callers must see None."""
        assert rr.work_fingerprint(str(tmp_path / "nowhere")) is None

    def test_changed_paths_reports_the_edited_file(self, repo):
        (repo / "app.py").write_text("def total(x):\n    return x * 3\n")
        assert "app.py" in rr.changed_paths_since(str(repo))

    def test_changed_paths_reports_untracked_work_too(self, repo):
        """Otherwise a stale denial names unrelated files as the cause."""
        (repo / "new_module.py").write_text("x = 1\n")
        assert "new_module.py" in rr.changed_paths_since(str(repo))


class TestFindingsValidation:
    def test_accepts_a_real_verdict(self):
        ok, err = rr.validate_findings(
            "Checked the close path against the acceptance criteria. Bead "
            "binding works; the stale branch fires on a follow-up edit. One "
            "concern: waiver reasons are not compared against the bead title.",
            "escapement-abc1",
        )
        assert ok, err

    @pytest.mark.parametrize("stamp", [
        "lgtm", "LGTM.", "looks good to me", "no findings", "approved",
        "  clean  ", "n/a", "tbd",
    ])
    def test_rejects_rubber_stamps(self, stamp):
        ok, _ = rr.validate_findings(stamp, "escapement-abc1")
        assert not ok

    def test_rejects_text_below_the_substance_bar(self):
        ok, err = rr.validate_findings("Reviewed it and it seems fine to me.",
                                       "escapement-abc1")
        assert not ok
        assert "too short" in err

    def test_rejects_a_verdict_that_only_echoes_the_bead_title(self):
        title = (
            "review_gate: require independent, bead-bound, non-stale review "
            "before bd close, denying rather than asking so it works headless"
        )
        ok, err = rr.validate_findings(title, "escapement-abc1", bead_title=title)
        assert not ok
        assert "echo" in err

    def test_rejection_message_says_what_to_do(self):
        """Internal transparency: a refusal must be repairable from its text."""
        _, err = rr.validate_findings("lgtm", "escapement-abc1")
        assert "what the reviewer actually examined" in err


class TestWaiverValidation:
    def test_accepts_a_reasoned_waiver(self):
        ok, _ = rr.validate_waiver_reason(
            "Docs-only change to a README; no behavior and no oracle affected.",
            "escapement-abc1",
        )
        assert ok

    @pytest.mark.parametrize("reason", ["tbd", "n/a", "", "   ", "too short"])
    def test_rejects_placeholders_and_stubs(self, reason):
        ok, _ = rr.validate_waiver_reason(reason, "escapement-abc1")
        assert not ok

    def test_rejects_a_reason_that_is_just_the_bead_id(self):
        ok, _ = rr.validate_waiver_reason("escapement-abc1", "escapement-abc1")
        assert not ok


class TestBeadIdExtraction:
    def test_finds_ids_across_fields(self):
        assert rr.extract_bead_ids(
            "audit escapement-abc1", None, "also covers cake-4cq.1.1"
        ) == ["escapement-abc1", "cake-4cq.1.1"]

    def test_deduplicates(self):
        assert rr.extract_bead_ids(
            "escapement-abc1", "escapement-abc1"
        ) == ["escapement-abc1"]

    def test_finds_hyphenated_prefixes(self):
        assert "escapement-mol-4ef" in rr.extract_bead_ids("close escapement-mol-4ef")


class TestRecordingCLI:
    def _run(self, argv, monkeypatch, written=None):
        calls = []

        def fake_write(bead_id, record, cwd=None):
            calls.append((bead_id, record))
            return True

        monkeypatch.setattr(cli, "write_record", fake_write)
        monkeypatch.setattr(cli, "work_fingerprint", lambda _c=None: FP)
        code = cli.main(argv)
        if written is not None:
            written.extend(calls)
        return code, calls

    def test_refuses_to_store_a_rubber_stamp(self, monkeypatch, capsys):
        code, calls = self._run(
            ["record", "--bead", "escapement-abc1", "--findings", "lgtm"],
            monkeypatch,
        )
        assert code == 1
        assert calls == [], "a rubber stamp must never reach the bead"
        assert "refusing to record" in capsys.readouterr().err

    def test_stores_a_substantive_verdict(self, monkeypatch):
        code, calls = self._run(
            ["record", "--bead", "escapement-abc1", "--findings", LONG,
             "--reviewer", "adversarial-reviewer"],
            monkeypatch,
        )
        assert code == 0
        assert calls[0][0] == "escapement-abc1"
        assert calls[0][1]["findings"] == LONG
        assert calls[0][1]["fingerprint"] == FP

    def test_marks_independence_unverified_without_a_dispatch(self, monkeypatch):
        monkeypatch.setattr(cli, "has_dispatch", lambda _b, _s=None: False)
        _, calls = self._run(
            ["record", "--bead", "escapement-abc1", "--findings", LONG],
            monkeypatch,
        )
        assert calls[0][1]["independent"] == "unverified"

    def test_marks_independence_true_with_a_dispatch(self, monkeypatch):
        monkeypatch.setattr(cli, "has_dispatch", lambda _b, _s=None: True)
        _, calls = self._run(
            ["record", "--bead", "escapement-abc1", "--findings", LONG],
            monkeypatch,
        )
        assert calls[0][1]["independent"] is True

    def test_reads_findings_from_a_file(self, monkeypatch, tmp_path):
        verdict = tmp_path / "verdict.md"
        verdict.write_text(LONG)
        code, calls = self._run(
            ["record", "--bead", "escapement-abc1", "--findings-file", str(verdict)],
            monkeypatch,
        )
        assert code == 0
        assert calls[0][1]["findings"] == LONG

    def test_missing_findings_file_is_reported(self, monkeypatch, capsys):
        code, calls = self._run(
            ["record", "--bead", "escapement-abc1", "--findings-file", "/no/such"],
            monkeypatch,
        )
        assert code == 2
        assert calls == []
        assert "does not exist" in capsys.readouterr().err


FP = "f" * 64
LONG = (
    "Read the close path against the bead's acceptance criteria. Bead binding "
    "and the stale-fingerprint branch both behave as specified. One residual "
    "concern: a waiver reason that paraphrases the bead title still passes."
)


class TestReviewerAllowlistMatchesTheAgentRegistry:
    """An allowlist that names no dispatchable agent is silent drift.

    `code-reviewer` was listed and is not a registered agent here, so it could
    never have satisfied the gate. The reverse — renaming a real agent — is
    worse: `independent: true` becomes unreachable, every close starts failing
    the corroboration check, and every mocked test stays green.
    """

    def _registered(self) -> set[str]:
        agents_dir = _HOOKS_DIR.parent / "agents"
        names = set()
        for path in agents_dir.glob("*.md"):
            for line in path.read_text(encoding="utf-8").splitlines()[:20]:
                if line.startswith("name:"):
                    names.add(line.split(":", 1)[1].strip())
                    break
        return names

    def test_every_allowlisted_type_names_a_real_agent(self):
        registered = self._registered()
        assert registered, "no agent definitions found — the guard is vacuous"
        for entry in rr.INDEPENDENT_REVIEWER_TYPES:
            bare = entry.split(":", 1)[-1]
            assert bare in registered, (
                f"{entry!r} is allowlisted as an independent reviewer but no "
                f"agent defines it. Registered: {sorted(registered)}"
            )

    def test_the_adversarial_reviewer_is_allowlisted(self):
        """The reverse drift: a rename must not silently disarm the gate."""
        assert "adversarial-reviewer" in rr.INDEPENDENT_REVIEWER_TYPES
        assert "escapement:adversarial-reviewer" in rr.INDEPENDENT_REVIEWER_TYPES


class TestStoreAvailability:
    def test_a_bad_bead_id_is_not_an_unavailable_store(self):
        """The split that closed the `-r "prose"` bypass.

        `bd show <not-a-bead>` fails exactly like `bd` being absent. Collapsing
        the two meant any unresolvable target fell through the fail-open path
        and closed unchecked.
        """
        assert rr.store_available() is True
        assert rr.read_record("definitely-not-a-real-bead-xyz9") is None

    def test_unavailable_when_bd_cannot_run(self, monkeypatch):
        monkeypatch.setattr(rr, "_run", lambda *_a, **_k: None)
        assert rr.read_record("escapement-abc1") is rr.UNAVAILABLE
