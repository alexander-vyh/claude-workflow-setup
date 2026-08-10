import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "tests" / "evals" / "discovery_blast_radius"
VALIDATOR = EVAL_ROOT / "validate_corpus.py"
RUNNER = EVAL_ROOT / "run_multiturn.py"
SINGLE_RUNNER = EVAL_ROOT / "run_singleturn.py"
BUILDER = EVAL_ROOT / "build_corpus.py"


def _validator():
    assert VALIDATOR.is_file(), "behavioral corpus validator is missing"
    spec = importlib.util.spec_from_file_location("discovery_corpus_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _inputs():
    validator = _validator()
    manifest, records, scenarios = validator.load_corpus(EVAL_ROOT)
    skill_text = (ROOT / "claude/skills/discovery/SKILL.md").read_text(encoding="utf-8")
    return validator, manifest, records, scenarios, skill_text


def _runner():
    assert RUNNER.is_file(), "ordered multi-turn runner is missing"
    spec = importlib.util.spec_from_file_location("discovery_multiturn_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _single_runner():
    assert SINGLE_RUNNER.is_file(), "single-turn runner is missing"
    spec = importlib.util.spec_from_file_location(
        "discovery_singleturn_runner", SINGLE_RUNNER
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _builder():
    assert BUILDER.is_file(), "corpus builder is missing"
    spec = importlib.util.spec_from_file_location("discovery_corpus_builder", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_committed_discovery_behavioral_corpus_is_valid():
    validator = _validator()
    assert validator.validate_corpus(EVAL_ROOT, ROOT) == []


def test_validator_kills_keyword_router_plus_draft_first_skill_mutant():
    validator, manifest, records, scenarios, skill_text = _inputs()
    mutant = skill_text.replace(
        "## Interaction Model: Draft-and-React",
        (
            "**Shortcut mutant:** Classify high risk only when the prompt contains "
            "architecture, migration, security, or breaking API. Draft a recommended "
            "solution before asking the forks.\n\n"
            "## Interaction Model: Draft-and-React"
        ),
        1,
    )

    contract_errors = validator.validate_skill_contract(mutant)
    assert any("keyword-only risk routing" in error for error in contract_errors)
    assert any("draft-before-forks" in error for error in contract_errors)

    corpus_errors = validator.validate_records(
        manifest, records, scenarios, mutant, EVAL_ROOT
    )
    assert any("skill SHA" in error for error in corpus_errors)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("draft_before_forks", "solution commitment before required forks"),
        ("generic_questions", "expected 2-4 forks"),
        ("bare_alternatives", "alternative-bearing"),
        ("missing_categories", "category breadth"),
        ("partial_drafts", "partial-answer gate"),
        ("ignored_answers", "answer reflection"),
        ("late_load_bearing_fork", "load-bearing fork after drafting"),
        ("late_skeleton_blocker", "load-bearing fork after drafting"),
        ("superseded_ambiguity", "superseded ambiguity"),
        ("ambiguous_single_domain", "at least two domains"),
        ("duplicate_run_id", "duplicate run_id"),
        ("overwritten_record", "record hash"),
        ("wrong_scenario_prompt", "scenario prompt"),
        ("wrong_skill_hash", "skill SHA"),
        ("wrong_model", "runner model"),
        ("wrong_flags", "runner flags"),
        ("missing_run_id", "run_id"),
        ("failed_exit", "exit status"),
        ("incomplete_rubric", "rubric fields"),
    ],
)
def test_validator_rejects_oracle_breaks(mutation, expected):
    validator, manifest, records, scenarios, skill_text = _inputs()
    manifest = copy.deepcopy(manifest)
    records = copy.deepcopy(records)

    high = next(record for record in records if record["matrix"] == "green_high")
    partial = next(record for record in records if record["matrix"] == "green_partial")
    differential = next(
        record for record in records if record["matrix"] == "green_strict"
    )
    ambiguous = next(
        record for record in records if record["matrix"] == "green_ambiguous"
    )

    if mutation == "draft_before_forks":
        high["turns"][0]["response"] = (
            "Draft direction: establish a canonical service and walking skeleton. "
            "Which rollout should we use?"
        )
    elif mutation == "generic_questions":
        high["turns"][0]["response"] = (
            "Before designing: What do you think? Should I proceed?"
        )
    elif mutation == "bare_alternatives":
        high["turns"][0]["response"] = (
            "Before designing:\n\n"
            "1. Ownership\n- Central registry\n- Another governance model\n\n"
            "2. Rollout\n- Block immediately\n- Warn first"
        )
        high["rubric"]["fork_count"] = 2
        high["rubric"]["categories"] = [
            "authority_or_ownership",
            "enforcement_or_rollout",
        ]
    elif mutation == "missing_categories":
        high["rubric"]["categories"] = ["authority_or_ownership"]
    elif mutation == "partial_drafts":
        partial["turns"][0]["response"] += (
            "\n\nDraft direction: establish the canonical service now."
        )
    elif mutation == "ignored_answers":
        differential["turns"][1]["response"] = (
            "Draft: each team owns its own definition; rollout is immediate; "
            "there is no rollback."
        )
    elif mutation == "late_load_bearing_fork":
        differential["turns"][1]["response"] += (
            "\n\nOne remaining load-bearing decision: who owns source exceptions?"
        )
    elif mutation == "late_skeleton_blocker":
        differential["turns"][1]["response"] += (
            "\n\n[SKELETON-BLOCKING] Which reports and materiality threshold "
            "must be selected before implementation?"
        )
    elif mutation == "superseded_ambiguity":
        ambiguous["scenario_id"] = manifest["superseded_runs"][0]["scenario_id"]
    elif mutation == "ambiguous_single_domain":
        ready = next(
            item
            for item in scenarios["scenarios"]
            if item["scenario_id"] == "ambiguous_ready_term"
        )
        for record in records:
            if record["matrix"] == "green_ambiguous":
                record["scenario_id"] = ready["scenario_id"]
                record["turns"][0]["prompt"] = ready["prompt"]
    elif mutation == "duplicate_run_id":
        records[1]["run_id"] = records[0]["run_id"]
    elif mutation == "overwritten_record":
        high["turns"][0]["response"] += "\nSilently replaced after acceptance."
    elif mutation == "wrong_scenario_prompt":
        high["scenario_id"] = "low_metric_copy"
    elif mutation == "wrong_skill_hash":
        high["skill_sha256"] = "0" * 64
    elif mutation == "wrong_model":
        high["runner"]["model"] = "unexpected-model"
    elif mutation == "wrong_flags":
        high["runner"]["flags"] = ["--unsafe"]
    elif mutation == "missing_run_id":
        high.pop("run_id")
    elif mutation == "failed_exit":
        high["exit_status"] = 1
    elif mutation == "incomplete_rubric":
        high["rubric"].pop("solution_commitment_before_forks")

    errors = validator.validate_records(
        manifest, records, scenarios, skill_text, EVAL_ROOT
    )
    assert any(expected in error for error in errors), errors


def test_corpus_manifest_declares_complete_repeated_matrix():
    validator, manifest, records, _scenarios, _skill_text = _inputs()
    assert manifest["expected_matrix"] == {
        "baseline_high": 5,
        "baseline_low": 5,
        "green_high": 5,
        "green_low": 5,
        "green_ambiguous": 5,
        "green_partial": 5,
        "green_strict": 5,
        "green_federated": 5,
    }
    assert len(records) == 40
    assert sum(record["matrix"] == "green_strict" for record in records) == 5
    assert sum(record["matrix"] == "green_federated" for record in records) == 5
    assert all(
        len(record["turns"]) == 2
        for record in records
        if record["matrix"] in {"green_strict", "green_federated"}
    )


def test_baseline_provenance_is_repo_relative_and_immutable():
    builder = _builder()
    manifest = json.loads((EVAL_ROOT / "manifest.json").read_text(encoding="utf-8"))
    main_layout = Path("/fresh/escapement/tests/evals/discovery_blast_radius")

    assert builder.BASELINE_SKILL == EVAL_ROOT / "baseline-discovery-skill.md"
    assert builder._baseline_skill(main_layout) == (
        main_layout / "baseline-discovery-skill.md"
    )
    assert builder.BASELINE_SKILL.is_file()
    assert builder._sha(builder.BASELINE_SKILL) == manifest["baseline_skill_sha256"]
    assert builder._manifest_timestamp(builder._last_built_records()) == manifest[
        "created_at"
    ]


def test_multiturn_runner_extracts_thread_and_resumes_without_ephemeral_flag():
    runner = _runner()
    output = "\n".join(
        [
            '{"type":"thread.started","thread_id":"019f0000-0000-7000-8000-000000000001"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"Forks"}}',
        ]
    )
    assert runner.extract_thread_id(output) == "019f0000-0000-7000-8000-000000000001"

    first, resume = runner.build_commands(
        codex="codex",
        model="gpt-5.6-luna",
        workdir=Path("/private/tmp/eval"),
        first_output=Path("/private/tmp/turn1.txt"),
        second_output=Path("/private/tmp/turn2.txt"),
        thread_id="019f0000-0000-7000-8000-000000000001",
        first_prompt="prompt",
        second_prompt="answers",
    )
    assert "--ephemeral" not in first
    assert "--ephemeral" not in resume
    assert "--skip-git-repo-check" in resume
    assert any(
        value.startswith("developer_instructions=") for value in first
    ), "first turn must load the canonical skill as developer guidance"
    assert any(
        value.startswith("developer_instructions=") for value in resume
    ), "resumed turn must reload the canonical skill as developer guidance"
    assert "resume" in resume
    assert resume[-1] == "answers"


def test_singleturn_runner_is_fresh_read_only_and_skill_bound():
    runner = _single_runner()
    command = runner.build_command(
        codex="codex",
        model="gpt-5.6-luna",
        workdir=Path("/private/tmp/eval"),
        output=Path("/private/tmp/output.txt"),
        prompt="prompt",
    )

    assert "--ephemeral" in command
    assert "read-only" in command
    assert "--ignore-user-config" in command
    assert any(value.startswith("developer_instructions=") for value in command)
    assert command[-1] == "prompt"


def test_singleturn_runner_refuses_to_overwrite_a_record(tmp_path):
    runner = _single_runner()
    destination = tmp_path / "accepted.json"
    runner.write_new_record(destination, {"run_id": "immutable-r1"})

    with pytest.raises(FileExistsError):
        runner.write_new_record(destination, {"run_id": "replacement-r1"})


def test_transcript_parser_rejects_observed_bare_options_and_late_blocker():
    validator = _validator()
    bare = (
        "1. Ownership\n"
        "- Central registry\n"
        "- Another governance model\n"
    )

    assert not validator._has_alternatives(bare)
    assert validator._has_solution_commitment(
        "The initial direction is a governed canonical service."
    )
    assert validator.LATE_FORK.search(
        "Draft design\n\n[SKELETON-BLOCKING] Which reports come first?"
    )
    assert not validator.LATE_FORK.search(
        "Open questions\n\n[SKELETON-BLOCKING] None."
    )

    compact_tradeoffs = (
        "1. Ownership — CFO-controlled registry; strong authority, but slow review.\n"
        "   Joint council — wider buy-in but shared approval can delay decisions."
    )
    assert validator._has_alternatives(compact_tradeoffs)

    same_line_tradeoffs = (
        "1. **Compatibility — dual support; safer migration, but more complexity.** "
        "**Immediate cutover — simpler contract; faster convergence, but outage risk.**"
    )
    assert validator._has_alternatives(same_line_tradeoffs)
