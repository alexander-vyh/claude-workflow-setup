import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "tools" / "render_agent_surfaces.py"
ORACLE_DOC = "agent-surfaces/onboarding/outcome-oracle.md"
SIMPLIFIER_QUESTION = (
    '2. For each change, ask: "Is this the simplest coherent way to achieve the outcome '
    'without weakening contracts, failure handling, or tests?"'
)
SIBLING_SMELL = (
    "- Reported path is fixed while another consumer of the same invariant remains broken"
)


def run_renderer(*args, root=ROOT):
    return subprocess.run(
        [sys.executable, str(root / "tools" / "render_agent_surfaces.py"), *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


def copy_repo(tmp_path):
    temp_root = tmp_path / "repo"
    shutil.copytree(
        ROOT,
        temp_root,
        ignore=shutil.ignore_patterns(".git", ".worktrees", "__pycache__", ".pytest_cache"),
    )
    return temp_root


def markdown_section(path, heading):
    text = path.read_text()
    marker = f"## {heading}\n"
    assert marker in text, f"{path} missing {marker.strip()}"
    return text.split(marker, 1)[1].split("\n## ", 1)[0]


def contract_paragraph(prefix, root=ROOT):
    """The current wording of the contract paragraph starting with `prefix`.

    Read from the canonical source instead of hard-coded. The mutation tests
    below need a literal anchor to overwrite, but hard-coding that anchor also
    pinned the prose: every intentional rewrite of the contract failed CI
    without changing what the contract means. The claims that must not drift
    are asserted directly in assert_minimum_contract, which is what actually
    rejects a weakened contract.
    """
    section = markdown_section(root / ORACLE_DOC, "Minimum Verified Delivery")
    for paragraph in section.split("\n\n"):
        normalized = " ".join(paragraph.split())
        if normalized.startswith(prefix):
            return normalized
    raise AssertionError(f"{ORACLE_DOC} has no contract paragraph starting {prefix!r}")


MINIMUM_CONTRACT = contract_paragraph("Escapement optimizes for minimum verified delivery:")
DRY_CONTRACT = contract_paragraph("DRY targets duplicated authority")


def replace_normalized(text, required, replacement):
    pattern = r"\s+".join(re.escape(token) for token in required.split())
    mutated, count = re.subn(pattern, replacement, text, count=1)
    assert count == 1
    return mutated


def assert_no_loc_pressure(text):
    scrubbed = text.lower().replace("not the fewest lines or files", "")
    patterns = (
        r"\b(?:prefer|minimi[sz]e)\b[^.\n]{0,80}\b(?:lines?|files?|diff|line count|file count)\b",
        r"\b(?:few(?:er|est)|short(?:er|est))\s+(?:lines?|files?|diff)\b",
        r"\b\d+\s+lines?\b[^.\n]{0,40}\b\d+\s+lines?\b",
        r"\b(?:line|file) count\b",
    )
    for pattern in patterns:
        assert re.search(pattern, scrubbed) is None


def assert_minimum_contract(root):
    """The contract's CLAIMS must survive, in every surface it is rendered into.

    Deliberately not an exact-wording pin. What must hold is that the doc still
    says: smallest *coherent* solution; the thing being delivered is a user or
    business outcome and a green run is not one; reuse is about duplicated
    authority, and centralizing is conditional. Any wording that keeps those
    claims passes; any that drops one fails.
    """
    for rel_path in (ORACLE_DOC, "AGENTS.md", "CLAUDE.md"):
        section = " ".join(markdown_section(root / rel_path, "Minimum Verified Delivery").split())
        lowered = section.lower()

        assert "smallest coherent" in lowered, f"{rel_path}: smallest, but still coherent"
        assert re.search(r"\b(?:user|business) outcome\b", lowered), (
            f"{rel_path}: the outcome must be named as a user/business outcome"
        )
        assert re.search(r"\bgreen\b[^.\n]{0,60}\b(?:run|test|check|suite|pipeline)\b", lowered), (
            f"{rel_path}: must say a green run is not the outcome"
        )
        assert "duplicated authority" in lowered, f"{rel_path}: DRY targets authority, not text"
        assert re.search(r"centraliz\w*\s+(?:when|only|if)\b", lowered), (
            f"{rel_path}: centralizing must stay conditional"
        )
        assert_no_loc_pressure(section)
        for contradiction in (
            "prefers fewer files",
            "always reuse or extend",
            "centralize every consumer",
            "reuse existing owners by default",
        ):
            assert contradiction not in lowered


def assert_simplifier_contract(root):
    for rel_path in (
        "claude/commands/review.md",
        "plugins/escapement-claude/commands/review.md",
    ):
        text = (root / rel_path).read_text()
        simplifier = text.split("### Agent 3: code-simplifier\n", 1)[1]
        strategy = simplifier.split("Review strategy:\n", 1)[1].split("\nYou are on team", 1)[0]
        assert SIMPLIFIER_QUESTION in strategy
        assert_no_loc_pressure(strategy)


def assert_sibling_smell(root):
    for rel_path in (
        "claude/skills/behavioral-test-oracle-review/SKILL.md",
        "plugins/escapement-claude/skills/behavioral-test-oracle-review/SKILL.md",
    ):
        section = markdown_section(root / rel_path, "Common Oracle Smells")
        assert SIBLING_SMELL in section
        lowered = section.lower()
        assert "reported path is sufficient" not in lowered
        assert "sibling consumers need not" not in lowered
        assert re.search(
            r"\breported path\b[^.\n]{0,100}\b(?:alone|sufficient|enough)\b",
            lowered,
        ) is None


def test_engineering_judgment_contract_is_distributed():
    # covers #Clarify minimum verified delivery engineering judgment-regression
    assert_minimum_contract(ROOT)
    assert_simplifier_contract(ROOT)
    assert_sibling_smell(ROOT)


PRE_OUTCOME_WORDING = (
    "Escapement optimizes for minimum verified delivery: the smallest coherent "
    "solution that satisfies the current outcome and its constraints, not the "
    "fewest lines or files. YAGNI forbids speculative structure; it never "
    "weakens the outcome oracle."
)


def test_outcome_must_be_a_user_outcome_not_a_green_run(tmp_path):
    """The wording this contract replaced was not sloppy — it was ambiguous.

    "satisfies the current outcome" reads fine and passes every other check
    here, including the no-LOC-pressure scan. It also lets an agent treat a
    green test run as the outcome. This is the single mutation that isolates
    the user/business-outcome claim: everything else about the paragraph stays
    defensible, and the contract must still be rejected.
    """
    temp_root = copy_repo(tmp_path)
    source = temp_root / ORACLE_DOC
    source.write_text(
        replace_normalized(source.read_text(), MINIMUM_CONTRACT, PRE_OUTCOME_WORDING)
    )
    result = run_renderer(root=temp_root)
    assert result.returncode == 0, result.stderr
    with pytest.raises(AssertionError, match="user/business outcome|green run"):
        assert_minimum_contract(temp_root)


@pytest.mark.parametrize(
    ("rel_path", "required", "replacement", "assertion"),
    (
        (
            "agent-surfaces/onboarding/outcome-oracle.md",
            MINIMUM_CONTRACT,
            "Escapement optimizes for minimum verified delivery: the fewest lines and files.",
            assert_minimum_contract,
        ),
        (
            "agent-surfaces/onboarding/outcome-oracle.md",
            DRY_CONTRACT,
            "Always reuse or extend an existing owner and centralize every consumer.",
            assert_minimum_contract,
        ),
        (
            "claude/commands/review.md",
            SIMPLIFIER_QUESTION,
            '2. For each change, ask: "Could this be 3 lines instead of 30?"',
            assert_simplifier_contract,
        ),
        (
            "claude/skills/behavioral-test-oracle-review/SKILL.md",
            SIBLING_SMELL,
            "- Reported path passes its regression test",
            assert_sibling_smell,
        ),
    ),
)
def test_weakened_contract_fails_after_regeneration(
    tmp_path, rel_path, required, replacement, assertion
):
    temp_root = copy_repo(tmp_path)
    source = temp_root / rel_path
    source.write_text(replace_normalized(source.read_text(), required, replacement))
    result = run_renderer(root=temp_root)
    assert result.returncode == 0, result.stderr
    with pytest.raises(AssertionError):
        assertion(temp_root)


@pytest.mark.parametrize(
    ("rel_path", "required", "contradiction", "assertion"),
    (
        (
            "agent-surfaces/onboarding/outcome-oracle.md",
            DRY_CONTRACT,
            " Always reuse or extend an existing owner and centralize every consumer.",
            assert_minimum_contract,
        ),
        (
            "claude/commands/review.md",
            SIMPLIFIER_QUESTION,
            "\n2a. Prefer one line over several and minimize file count.",
            assert_simplifier_contract,
        ),
        (
            "claude/skills/behavioral-test-oracle-review/SKILL.md",
            SIBLING_SMELL,
            (
                "\n- A passing regression test for the reported path is sufficient; "
                "sibling consumers need not be exercised"
            ),
            assert_sibling_smell,
        ),
        (
            "agent-surfaces/onboarding/outcome-oracle.md",
            MINIMUM_CONTRACT,
            " Prefer smaller files whenever practical.",
            assert_minimum_contract,
        ),
        (
            "agent-surfaces/onboarding/outcome-oracle.md",
            DRY_CONTRACT,
            " Reuse existing owners by default.",
            assert_minimum_contract,
        ),
        (
            "claude/commands/review.md",
            SIMPLIFIER_QUESTION,
            "\n2a. Prefer three lines to thirty when both work.",
            assert_simplifier_contract,
        ),
        (
            "claude/skills/behavioral-test-oracle-review/SKILL.md",
            SIBLING_SMELL,
            "\n- A regression test for the reported path alone is enough",
            assert_sibling_smell,
        ),
    ),
)
def test_approved_text_cannot_launder_a_contradiction(
    tmp_path, rel_path, required, contradiction, assertion
):
    temp_root = copy_repo(tmp_path)
    source = temp_root / rel_path
    source.write_text(
        replace_normalized(source.read_text(), required, required + contradiction)
    )
    result = run_renderer(root=temp_root)
    assert result.returncode == 0, result.stderr
    with pytest.raises(AssertionError):
        assertion(temp_root)
