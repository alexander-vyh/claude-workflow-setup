from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FORMULA = ROOT / "beads/formulas/mol-rapid.formula.json"
RULE_SURFACES = (
    ROOT / "claude/rules/tdd-enforcement.md",
    ROOT / "plugins/escapement-claude/rules/tdd-enforcement.md",
)

EXCLUSION_PHRASES = (
    "authorization/security",
    "money or sensitive data",
    "production mutation",
    "schema/migration",
    "public contracts",
    "irreversible external effects",
    "shared infrastructure",
    "root cause",
    "executable outcome oracle",
)
ESCALATION_PHRASES = (
    "protected surface is discovered",
    "the boundary expands",
    "reversibility becomes uncertain",
    "discriminating controls cannot be constructed",
    "root cause remains unresolved",
    "outcome oracle is missing",
)
def normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def instruction_starting(text: str, prefix: str) -> str:
    chunks = re.split(r"\n\s*\n|(?<=[.!?])\s+", text.casefold())
    instructions = [
        re.sub(r"^(?:[-*+]\s+|\d+[.)]\s*)", "", normalized(chunk))
        for chunk in chunks
    ]
    return next(instruction for instruction in instructions if instruction.startswith(prefix))


def assert_escalation_instruction(text: str) -> None:
    instruction = instruction_starting(text, "stop rapid execution")
    assert "current run" in instruction
    assert "move to the full lane" in instruction
    assert all(phrase in instruction for phrase in ESCALATION_PHRASES)


def assert_no_unconditional_independent_review(text: str) -> None:
    review_markers = (
        "review",
        "second opinion",
        "another agent",
        "independent assessment",
        "independent check",
        "independent approval",
        "audit",
        "outside",
    )
    for instruction in re.split(r"\n\s*\n|(?<=[.!?])\s+", text.casefold()):
        instruction = normalized(instruction)
        if not any(marker in instruction for marker in review_markers):
            continue
        requests_evidence = any(
            marker in instruction
            for marker in (
                "request",
                "require",
                "must",
                "receive",
                "second opinion",
                "needs",
            )
        )
        if requests_evidence:
            assert any(marker in instruction for marker in ("only when", " when ", " if "))
            assert all(
                marker not in instruction
                for marker in (
                    "regardless",
                    "irrespective",
                    "each change",
                    "every change",
                    "all changes",
                    "every task",
                )
            )


def assert_no_inverted_instructions(text: str) -> None:
    lowered = normalized(text)
    inversions = (
        r"\b(?:continue|keep|proceed|stay)\b.{0,40}\brapid\b.{0,80}\b(?:protected|trigger|boundary|uncertain|unresolved|missing)\b",
        r"\b(?:always|all|every|mandatory|unconditional|regardless)\b.{0,70}\breview\b",
        r"\breview\b.{0,70}\b(?:always|mandatory|unconditional|regardless)\b",
        r"\bfull lane\b.{0,40}\b(?:optional|later|skip|unnecessary)\b",
        r"\bopen (?:the )?(?:pr|pull request)\b.{0,60}\b(?:immediately|before|unfinished|early)\b",
        r"\bprotected\b.{0,80}\b(?:finish|complete|continue|keep)\b.{0,30}\brapid\b.{0,40}\b(?:afterward|before|first|later)\b",
    )
    assert all(re.search(pattern, lowered) is None for pattern in inversions)
    assert_no_unconditional_independent_review(text)


def assert_formula_contract(formula: dict) -> None:
    assert formula["version"] >= 3
    descriptions = {step["id"]: step["description"] for step in formula["steps"]}
    assert set(descriptions) == {"diagnose", "implement", "verify"}
    assert all(
        phrase in normalized(descriptions["verify"])
        for phrase in ("run the exact workflow", "observed user-facing result", "tests pass")
    )

    review = instruction_starting(descriptions["verify"], "request independent review")
    assert "only when" in review
    assert all(
        phrase in review
        for phrase in ("specialty boundary", "task-maturity gap", "named failure mechanism")
    )

    durability = instruction_starting(descriptions["implement"], "create an early durable")
    assert all(artifact in durability for artifact in ("branch", "commit", "vertical slice"))
    assert "not a pr-readiness signal" in durability

    review_boundary = instruction_starting(descriptions["implement"], "do not open a pr")
    assert all(
        condition in review_boundary
        for condition in (
            "bounded behavior",
            "focused proof",
            "no objective-blocking work",
            "known limitations",
            "remaining landing proof",
        )
    )
    for description in descriptions.values():
        assert_escalation_instruction(description)
    assert_no_inverted_instructions(" ".join(descriptions.values()))


def test_rapid_rule_is_semantically_complete_and_rendered_identically():
    texts = [path.read_text(encoding="utf-8") for path in RULE_SURFACES]
    assert texts[1:] == texts[:1]
    rapid_source = texts[0].split("### Rapid form", 1)[1].split("## Implementation-Echo", 1)[0]
    rapid = normalized(rapid_source)

    for phrase in (
        "independent source of truth",
        "binding constraints",
        "named fragile implementation",
        "negative control",
        "positive control",
        "missing/unresolved handling",
        "exact user-facing verification",
        "observed result",
        "unknown",
        "full",
        *EXCLUSION_PHRASES,
        *ESCALATION_PHRASES,
    ):
        assert phrase in rapid
    assert_escalation_instruction(rapid_source)
    assert_no_inverted_instructions(rapid_source)


def test_mol_rapid_contract_keeps_outcome_proof_but_makes_review_conditional():
    formula = json.loads(FORMULA.read_text(encoding="utf-8"))
    assert_formula_contract(formula)
    assert "dispatch adversarial-reviewer" not in normalized(json.dumps(formula))


def test_mol_rapid_escalates_current_run_and_separates_durability_from_review():
    formula = json.loads(FORMULA.read_text(encoding="utf-8"))
    assert_formula_contract(formula)


@pytest.mark.parametrize(
    ("step_id", "inversion"),
    (
        (
            "diagnose",
            "Keep using rapid mode when a protected trigger or unresolved boundary appears.",
        ),
        (
            "verify",
            "Every change must receive a review regardless of risk or evidence need.",
        ),
        (
            "verify",
            "Each change requires a second opinion from another agent regardless of risk.",
        ),
        (
            "verify",
            "Every task needs an outside audit irrespective of risk.",
        ),
        (
            "implement",
            "If a protected boundary appears, finish the rapid work first and escalate afterward.",
        ),
        (
            "implement",
            "Open the pull request before focused proof so review can begin early.",
        ),
    ),
)
def test_literal_valid_policy_cannot_launder_an_inverted_instruction(step_id, inversion):
    formula = json.loads(FORMULA.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(formula)
    step = next(item for item in mutated["steps"] if item["id"] == step_id)
    step["description"] += "\n\n" + inversion

    with pytest.raises(AssertionError):
        assert_formula_contract(mutated)
