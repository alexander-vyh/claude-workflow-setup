from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "claude" / "skills" / "discovery" / "SKILL.md"
GENERATED = (
    ROOT
    / "plugins"
    / "escapement-claude"
    / "skills"
    / "discovery"
    / "SKILL.md"
)


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _assert_in_order(text: str, fragments: tuple[str, ...]) -> None:
    cursor = -1
    for fragment in fragments:
        position = text.find(fragment, cursor + 1)
        assert position > cursor, f"missing or out of order: {fragment!r}"
        cursor = position


def test_blast_radius_decision_gate_precedes_solution_drafting():
    text = _normalized(CANONICAL)

    _assert_in_order(
        text,
        (
            "## Interaction Depth: Blast Radius and Reversibility",
            "Before architecture, recommendations, rollout plans, tasks, or other solution commitments",
            "ask 2-4 load-bearing forks",
            "Wait for explicit answers to every fork",
            "## Interaction Model: Draft-and-React",
        ),
    )


def test_high_path_requires_category_breadth_and_answer_reflection():
    text = _normalized(CANONICAL)

    assert "at least two" in text
    for category in (
        "authority or ownership",
        "migration or compatibility",
        "enforcement or rollout",
        "rollback or failure policy",
    ):
        assert category in text

    _assert_in_order(
        text,
        (
            "Wait for explicit answers to every fork",
            "If the user answers only some forks",
            "Run one load-bearing completeness pass",
            "reflect those decisions in the draft",
        ),
    )


def test_consequence_threshold_cannot_be_downgraded_by_a_design_request():
    text = _normalized(CANONICAL)

    _assert_in_order(
        text,
        (
            "Take the high-risk path when any one of these consequence tests is true",
            "requires coordinated behavior or acceptance beyond the immediate author",
            "changes a shared meaning, contract, access decision, or persisted state",
            "cannot be restored quickly and completely",
            "The high-risk path is a hard conversational stop",
            "even when the user asked directly for a design",
        ),
    )


def test_high_path_requires_explicit_tradeoffs_and_no_post_draft_blocker():
    text = _normalized(CANONICAL)

    assert "Bare option labels, generic catch-alls" in text
    assert "State each alternative's material benefit and cost or risk" in text
    assert "no load-bearing or skeleton-blocking question may appear after drafting begins" in text
    assert "Do not promote a detail the user explicitly marked deferrable" in text


def test_low_path_retains_lightweight_draft_and_react():
    text = _normalized(CANONICAL)

    assert "Low blast radius + easy to reverse" in text
    assert "Use the normal lightweight draft-and-react path" in text
    assert "Zero questions upfront, one max mid-draft." not in text
    assert "produce artifact drafts, surface one question" not in text
    assert "with the ONE blocking question surfaced" not in text


def test_renderer_keeps_discovery_skill_copy_in_sync():
    assert CANONICAL.read_text(encoding="utf-8") == GENERATED.read_text(encoding="utf-8")
