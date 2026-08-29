"""Oracle for SessionStart rules injection.

Business outcome
----------------
A session starts already holding the rules that change what an agent does, and
does not spend context on reference material it can read on demand. Before
this, `inject-rules.sh` concatenated every `claude/rules/*.md` in full — the
same context cost for a 70-line worked example as for the sentence that changes
the next action.

Independent source of truth
---------------------------
The generated hook's actual stdout, executed the way Claude Code executes it
(bash, `CLAUDE_PLUGIN_ROOT` set, JSON on stdout). Not the renderer's template
string, and not the marker constants re-derived here — those would be
implementation echoes of the thing under test.

Invalid solution classes this suite rejects
-------------------------------------------
- A rule silently disappearing from the session -> ``test_every_rule_still_reaches_the_session``
- Marked detail still being injected (no saving at all) -> ``test_marked_detail_is_held_back``
- Held-back detail with no way to reach it -> ``test_held_back_rules_say_where_to_read_the_rest``
- Truncating a rule that never opted in -> ``test_unmarked_rule_is_injected_whole``
- Silently eating the tail on an unclosed marker -> ``test_unclosed_marker_injects_the_rule_intact``
- A broken install failing quietly -> ``test_missing_bundle_still_fails_loud``
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "escapement-claude"
INJECTOR = PLUGIN / "hooks" / "inject-rules.sh"
RULES = ROOT / "claude" / "rules"


def inject(plugin_root: Path) -> str:
    """Run the generated hook and return the additionalContext it emits."""
    result = subprocess.run(
        ["bash", str(INJECTOR)],
        capture_output=True,
        text=True,
        env={"CLAUDE_PLUGIN_ROOT": str(plugin_root), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


@pytest.fixture(scope="module")
def injected() -> str:
    return inject(PLUGIN)


def rule_titles() -> list[tuple[str, str]]:
    """(filename, H1 title) for every bundled rule."""
    out = []
    for path in sorted(RULES.glob("*.md")):
        first = next(
            (line for line in path.read_text(encoding="utf-8").splitlines()
             if line.startswith("# ")),
            None,
        )
        assert first, f"{path.name} has no H1 to identify it by"
        out.append((path.name, first[2:].strip()))
    return out


def marked_rules() -> list[Path]:
    return [
        p for p in sorted(RULES.glob("*.md"))
        if "escapement:detail:start" in p.read_text(encoding="utf-8")
    ]


# --- No rule may vanish ----------------------------------------------------

def test_every_rule_still_reaches_the_session(injected):
    """Positive control: holding back detail must never drop a whole rule."""
    for name, title in rule_titles():
        assert title in injected, f"{name}: rule missing from injected context"


def test_imperative_framing_survives(injected):
    assert "OVERRIDE default behavior" in injected


# --- The saving is real ----------------------------------------------------

def injected_slices(injected: str) -> dict[str, str]:
    """The injected context, cut back into per-rule pieces by H1 title.

    Checking a held-back line against the whole bundle gives false failures:
    two rules state the same anti-pattern verbatim, so a line withheld from one
    is legitimately present via the other. The question is per rule.
    """
    titles = [(name, title) for name, title in rule_titles()]
    marks = sorted(
        ((injected.index("# " + title), name) for name, title in titles
         if "# " + title in injected)
    )
    slices = {}
    for n, (start, name) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(injected)
        slices[name] = injected[start:end]
    return slices


def test_marked_detail_is_held_back(injected):
    """Every marked region must be absent from ITS OWN rule's injected slice."""
    slices = injected_slices(injected)
    for path in marked_rules():
        mine = slices[path.name]
        rest = path.read_text(encoding="utf-8")
        while "<!-- escapement:detail:start -->" in rest:
            _, rest = rest.split("<!-- escapement:detail:start -->", 1)
            region, rest = rest.split("<!-- escapement:detail:end -->", 1)
            lines = [ln.strip() for ln in region.splitlines() if len(ln.strip()) > 40]
            assert lines, f"{path.name}: marked a region with nothing substantial in it"
            for line in lines:
                assert line not in mine, (
                    f"{path.name}: held-back line still injected: {line[:60]}"
                )


def test_marked_rules_are_actually_shortened(injected):
    """A marker that strips nothing is decoration. Each marked rule must shrink."""
    slices = injected_slices(injected)
    for path in marked_rules():
        full = len(path.read_text(encoding="utf-8"))
        got = len(slices[path.name])
        assert got < full * 0.9, (
            f"{path.name}: injected {got} of {full} chars — the markers bought almost nothing"
        )


# The session-start context budget, in characters. This is a ratchet: it may
# only ever go DOWN. Injection was 86,210 unbounded, which is what made a rule
# bundle cost more than the code it governs. Holding back reference material
# buys roughly a fifth; trimming the prose itself is what closes the rest, and
# each trim should lower this number rather than bank the slack.
INJECTED_BUDGET = 70_000


def test_injected_bundle_stays_within_budget(injected):
    assert len(injected) <= INJECTED_BUDGET, (
        f"injected {len(injected)} chars exceeds the {INJECTED_BUDGET} budget — "
        f"hold back reference material or trim the prose; do not raise the budget"
    )


def test_held_back_rules_say_where_to_read_the_rest(injected):
    """A rule whose detail is withheld must name the file that still holds it."""
    for path in marked_rules():
        assert path.name in injected, (
            f"{path.name}: detail held back with no path to read it — that is a gate "
            f"without a repair"
        )


# --- Opt-in, and fail-open -------------------------------------------------

def _fake_plugin(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "plugin"
    (root / "rules").mkdir(parents=True)
    for name, body in files.items():
        (root / "rules" / name).write_text(body, encoding="utf-8")
    return root


def test_unmarked_rule_is_injected_whole(tmp_path):
    """Negative control: the default must stay 'inject everything'."""
    body = "# Plain Rule\n\nDo the thing.\n\nAnd then do the other thing entirely.\n"
    context = inject(_fake_plugin(tmp_path, {"plain.md": body}))
    assert "And then do the other thing entirely." in context
    assert "held back" not in context


def test_unclosed_marker_injects_the_rule_intact(tmp_path):
    """A malformed marker must not silently eat the rest of the rule."""
    body = (
        "# Half Marked\n\nAlways do this.\n\n"
        "<!-- escapement:detail:start -->\n\nA worked example that was never closed.\n"
    )
    context = inject(_fake_plugin(tmp_path, {"half.md": body}))
    assert "A worked example that was never closed." in context
    assert "Always do this." in context


def test_detail_region_is_removed_but_neighbours_survive(tmp_path):
    body = (
        "# Bracketed\n\nImperative before.\n\n"
        "<!-- escapement:detail:start -->\n"
        "A long worked example that only matters once you are already doing it.\n"
        "<!-- escapement:detail:end -->\n\n"
        "Imperative after.\n"
    )
    context = inject(_fake_plugin(tmp_path, {"b.md": body}))
    assert "Imperative before." in context
    assert "Imperative after." in context
    assert "only matters once you are already doing it" not in context
    assert "b.md" in context, "must point at the file holding the rest"


def test_missing_bundle_still_fails_loud(tmp_path):
    """A broken install must be visible, not a silently ruleless session."""
    root = tmp_path / "empty"
    (root / "rules").mkdir(parents=True)
    context = inject(root)
    assert "WARNING" in context and "NOT injected" in context
