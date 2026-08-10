#!/usr/bin/env python3
"""Validate the durable discovery blast-radius behavioral corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


EXPECTED_MODEL = "gpt-5.6-luna"
SINGLE_TURN_FLAGS = [
    "--ephemeral",
    "--ignore-user-config",
    "--sandbox",
    "read-only",
    "--skip-git-repo-check",
]
MULTI_TURN_FLAGS = [
    "--ignore-user-config",
    "--sandbox",
    "read-only",
    "--skip-git-repo-check",
    "--json",
]
RESUME_FLAGS = ["--ignore-user-config", "--skip-git-repo-check", "--json"]
REQUIRED_RUBRIC_FIELDS = {
    "classification",
    "fork_count",
    "alternative_bearing",
    "categories",
    "solution_commitment_before_forks",
    "consequence_probe",
    "answer_gate_held",
    "answer_reflection",
    "new_load_bearing_fork_after_draft",
    "lightweight",
    "manual_pass",
    "notes",
}
CATEGORIES = {
    "authority_or_ownership": ("authority", "ownership", "owner"),
    "migration_or_compatibility": ("migration", "compatibility", "cutover"),
    "enforcement_or_rollout": ("enforcement", "rollout", "publication"),
    "rollback_or_failure_policy": ("rollback", "failure policy", "fail closed"),
}
COMMITMENT = re.compile(
    r"(?im)^(?:draft|initial|proposed) (?:direction|design)|"
    r"^the (?:initial|proposed) direction|"
    r"^the (?:service|system|walking skeleton) (?:would|will|should)|"
    r"^the walking skeleton|^create a |^establish a |^introduce the "
)
LATE_FORK = re.compile(
    r"(?i)(?:one|another|the) remaining (?:load-bearing )?(?:fork|decision)|"
    r"remaining design decision|one decision (?:still )?remains|decision still unblocks|"
    r"which option do you choose|who should own|"
    r"\[skeleton-blocking\](?!\*{0,2}\s*(?:none|no)\b)|"
    r"must be resolved before implementation"
)
OBVIOUS_RISK_WORDS = {
    "architecture",
    "breaking api",
    "destructive migration",
    "high blast",
    "hard to reverse",
    "security boundary",
}


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_text(content: str) -> str:
    return _sha256_bytes(content.encode("utf-8"))


def _record_sha256(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_fork_offset(response: str) -> int | None:
    match = re.search(r"(?m)^\s*1[.)]\s+", response)
    return match.start() if match else None


def _fork_chunks(response: str) -> list[str]:
    matches = list(re.finditer(r"(?m)^\s*([1-9])[.)]\s+", response))
    chunks: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(response)
        chunks.append(response[match.start() : end])
    return chunks


def _has_alternatives(chunk: str) -> bool:
    generic = re.compile(
        r"(?i)^\W*(?:another|other|something else|use your judgment|tbd)\b"
    )
    options: list[str] = []
    for raw_line in chunk.splitlines():
        for part in re.split(r"\*\*\s+\*\*", raw_line):
            line = re.sub(r"^\s*(?:[1-9][.)]|[-*]|[A-C][.)])\s*", "", part)
            if (
                len(line.split()) >= 6
                and (
                    (
                        (" — " in line or " – " in line or ": " in line)
                        and (";" in line or " but " in line.lower())
                    )
                    or (";" in line and " but " in line.lower())
                )
            ):
                options.append(line)
    return len(options) >= 2 and all(not generic.search(option) for option in options)


def _derived_categories(response: str) -> set[str]:
    lowered = response.lower()
    return {
        category
        for category, terms in CATEGORIES.items()
        if any(term in lowered for term in terms)
    }


def _has_solution_commitment(text: str) -> bool:
    return bool(COMMITMENT.search(text))


def validate_skill_contract(skill_text: str) -> list[str]:
    """Reject contradictory interaction guidance, not just missing phrases."""

    errors: list[str] = []
    normalized = " ".join(skill_text.split()).lower()
    if "## interaction depth: blast radius and reversibility" not in normalized:
        errors.append("skill contract: blast-radius gate missing")
    if re.search(
        r"classif(?:y|ication).{0,80}(?:only|when).{0,80}(?:contains|keyword|topic word)",
        normalized,
    ):
        errors.append("skill contract: keyword-only risk routing contradiction")
    if re.search(
        r"(?:draft|design|recommend).{0,80}(?:before|then).{0,80}(?:ask|resolve).{0,30}fork",
        normalized,
    ):
        errors.append("skill contract: draft-before-forks contradiction")
    for contradiction in (
        "zero questions upfront, one max mid-draft",
        "produce artifact drafts, surface one question",
        "with the one blocking question surfaced",
    ):
        if contradiction in normalized:
            errors.append(f"skill contract: unconditional low-path rule remains: {contradiction}")
    gate = normalized.find("## interaction depth: blast radius and reversibility")
    interaction = normalized.find("## interaction model: draft-and-react")
    if gate < 0 or interaction < 0 or gate >= interaction:
        errors.append("skill contract: blast-radius gate must precede drafting model")
    return errors


def load_corpus(eval_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    manifest = json.loads((eval_root / "manifest.json").read_text(encoding="utf-8"))
    scenarios = json.loads((eval_root / "scenarios.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (eval_root / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, records, scenarios


def _validate_metadata(
    record: dict[str, Any], manifest: dict[str, Any], run_id: str, errors: list[str]
) -> None:
    if not run_id:
        errors.append("record missing run_id")
    if record.get("skill_sha256") not in {
        manifest.get("skill_sha256"),
        manifest.get("baseline_skill_sha256"),
    }:
        errors.append(f"{run_id}: wrong skill SHA")
    runner = record.get("runner", {})
    if runner.get("model") != EXPECTED_MODEL:
        errors.append(f"{run_id}: wrong runner model")
    if runner.get("skill_delivery") != "developer_instructions_sha256":
        errors.append(f"{run_id}: wrong skill-delivery method")
    matrix = record.get("matrix", "")
    expected_flags = (
        MULTI_TURN_FLAGS
        if matrix in {"green_strict", "green_federated"}
        else SINGLE_TURN_FLAGS
    )
    if runner.get("flags") != expected_flags:
        errors.append(f"{run_id}: wrong runner flags")
    if matrix in {"green_strict", "green_federated"} and runner.get(
        "resume_flags"
    ) != RESUME_FLAGS:
        errors.append(f"{run_id}: wrong resume runner flags")
    if record.get("exit_status") != 0:
        errors.append(f"{run_id}: nonzero exit status")
    started = _parse_time(record.get("started_at"))
    completed = _parse_time(record.get("completed_at"))
    if started is None or completed is None or started > completed:
        errors.append(f"{run_id}: invalid timestamps")
    turns = record.get("turns")
    expected_turns = 2 if matrix in {"green_strict", "green_federated"} else 1
    if not isinstance(turns, list) or len(turns) != expected_turns:
        errors.append(f"{run_id}: wrong ordered turn count")
    elif [turn.get("index") for turn in turns] != list(range(1, expected_turns + 1)):
        errors.append(f"{run_id}: turns are not ordered")
    if expected_turns == 2 and not record.get("thread_id"):
        errors.append(f"{run_id}: resumed conversation missing thread_id")
    rubric = record.get("rubric", {})
    missing = REQUIRED_RUBRIC_FIELDS - set(rubric)
    if missing:
        errors.append(f"{run_id}: incomplete rubric fields: {sorted(missing)}")


def _validate_high_turn(run_id: str, response: str, rubric: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    boundary = _first_fork_offset(response)
    prefix = response if boundary is None else response[:boundary]
    chunks = _fork_chunks(response)
    if _has_solution_commitment(prefix):
        errors.append(f"{run_id}: solution commitment before required forks")
    if not 2 <= len(chunks) <= 4:
        errors.append(f"{run_id}: expected 2-4 forks, found {len(chunks)}")
    if chunks and not all(_has_alternatives(chunk) for chunk in chunks):
        errors.append(f"{run_id}: forks are not all alternative-bearing")
    derived = _derived_categories(response)
    declared = set(rubric.get("categories", []))
    if len(declared) < 2 or not declared <= derived:
        errors.append(f"{run_id}: insufficient or unsupported category breadth")
    if rubric.get("fork_count") != len(chunks):
        errors.append(f"{run_id}: scorecard fork count disagrees with transcript")
    if rubric.get("alternative_bearing") is not True:
        errors.append(f"{run_id}: scorecard does not require alternative-bearing forks")
    if rubric.get("solution_commitment_before_forks") is not False:
        errors.append(f"{run_id}: scorecard permits a solution commitment before forks")
    return errors


def _validate_reflection(run_id: str, matrix: str, response: str) -> list[str]:
    lowered = response.lower()
    groups = (
        [
            ("cfo",),
            ("two-week", "two week"),
            ("hard-block", "hard block", "block publication", "publication blocking"),
            ("restore", "restor"),
            ("last approved", "prior approved"),
        ]
        if matrix == "green_strict"
        else [
            ("council",),
            ("warning",),
            ("two cycles", "two reporting cycles"),
            ("pause",),
            ("both definitions", "canonical and legacy", "legacy definitions"),
        ]
    )
    errors: list[str] = []
    if any(not any(term in lowered for term in group) for group in groups):
        errors.append(f"{run_id}: missing answer reflection for {matrix}")
    design_markers = ("draft", "design", "walking skeleton", "system", "boundary")
    marker_positions = [lowered.find(marker) for marker in design_markers if marker in lowered]
    if not marker_positions:
        errors.append(f"{run_id}: answer reflection has no draft or design direction")
    else:
        suffix = response[min(marker_positions) :]
        if LATE_FORK.search(suffix):
            errors.append(f"{run_id}: load-bearing fork after drafting")
    return errors


def _validate_behavior(record: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    run_id = record.get("run_id", "<missing>")
    matrix = record.get("matrix")
    rubric = record.get("rubric", {})
    turns = record.get("turns", [])
    if not turns:
        return []
    response = turns[0].get("response", "")
    errors: list[str] = []
    if matrix in {"green_high", "green_strict", "green_federated"}:
        errors.extend(_validate_high_turn(run_id, response, rubric))
    elif matrix == "baseline_high":
        boundary = _first_fork_offset(response)
        prefix = response if boundary is None else response[:boundary]
        if not _has_solution_commitment(prefix):
            errors.append(f"{run_id}: baseline high control did not exhibit draft-first failure")
        if rubric.get("manual_pass") is not False:
            errors.append(f"{run_id}: baseline high must be scored as expected failure")
    elif matrix in {"baseline_low", "green_low"}:
        if len(_fork_chunks(response)) > 1 or "load-bearing" in response.lower():
            errors.append(f"{run_id}: low-risk control received heavy interview")
        if rubric.get("lightweight") is not True:
            errors.append(f"{run_id}: low-risk scorecard is not lightweight")
    elif matrix == "green_ambiguous":
        lowered = response.lower()
        if "costly or impossible to undo" not in lowered or "affected" not in lowered:
            errors.append(f"{run_id}: ambiguous case did not use consequence probe")
        if _has_solution_commitment(response):
            errors.append(f"{run_id}: ambiguous case drafted before consequence probe")
    elif matrix == "green_partial":
        if _has_solution_commitment(response):
            errors.append(f"{run_id}: partial-answer gate allowed drafting")
        if not any(word in response.lower() for word in ("remain", "unresolved", "choose")):
            errors.append(f"{run_id}: partial-answer gate did not request unresolved choices")
    if matrix in {"green_strict", "green_federated"} and len(turns) == 2:
        errors.extend(_validate_reflection(run_id, matrix, turns[1].get("response", "")))
        if rubric.get("answer_reflection") is not True:
            errors.append(f"{run_id}: scorecard omits answer reflection")
        if rubric.get("new_load_bearing_fork_after_draft") is not False:
            errors.append(f"{run_id}: scorecard permits late load-bearing fork")
    if matrix != "baseline_high" and rubric.get("manual_pass") is not True:
        errors.append(f"{run_id}: final/control transcript is not manually passed")
    return errors


def validate_records(
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    scenarios: dict[str, Any],
    skill_text: str,
    eval_root: Path,
) -> list[str]:
    errors = validate_skill_contract(skill_text)
    current_sha = _sha256_text(skill_text)
    if manifest.get("skill_sha256") != current_sha:
        errors.append("manifest skill SHA does not match canonical skill")
    expected_ids = manifest.get("run_ids", [])
    actual_ids = [record.get("run_id") for record in records]
    duplicates = [run_id for run_id, count in Counter(actual_ids).items() if count > 1]
    if duplicates:
        errors.append(f"duplicate run_id values: {duplicates}")
    if set(actual_ids) != set(expected_ids):
        errors.append("manifest run_ids do not match corpus run_id values")
    declared_hashes = manifest.get("record_sha256", {})
    if set(declared_hashes) != set(expected_ids):
        errors.append("manifest record hashes do not match declared run IDs")
    matrix_counts = Counter(record.get("matrix") for record in records)
    if dict(matrix_counts) != manifest.get("expected_matrix"):
        errors.append(f"wrong matrix counts: {dict(matrix_counts)}")
    superseded = {item["scenario_id"] for item in manifest.get("superseded_runs", [])}
    scenario_index = {
        item["scenario_id"]: item for item in scenarios.get("scenarios", [])
    }
    known_scenarios = set(scenario_index)
    for record in records:
        run_id = record.get("run_id", "<missing>")
        if declared_hashes.get(run_id) != _record_sha256(record):
            errors.append(f"{run_id}: record hash does not match immutable manifest")
        _validate_metadata(record, manifest, run_id, errors)
        expected_skill = (
            manifest.get("baseline_skill_sha256")
            if record.get("matrix", "").startswith("baseline_")
            else manifest.get("skill_sha256")
        )
        if record.get("skill_sha256") != expected_skill:
            errors.append(f"{run_id}: wrong skill SHA for matrix variant")
        scenario_id = record.get("scenario_id")
        if scenario_id in superseded:
            errors.append(f"{run_id}: superseded ambiguity scenario selected")
        elif scenario_id not in known_scenarios:
            errors.append(f"{run_id}: unknown scenario_id {scenario_id!r}")
        elif record.get("turns"):
            scenario = scenario_index[scenario_id]
            expected_prompt_fragment = scenario.get("prompt", scenario.get("reply", ""))
            if expected_prompt_fragment not in record["turns"][0].get("prompt", ""):
                errors.append(f"{run_id}: scenario prompt does not match scenario_id")
        errors.extend(_validate_behavior(record, manifest))
        text = json.dumps(record)
        if "/Users/" in text or "sk-" in text or "refresh_token" in text:
            errors.append(f"{run_id}: transcript is not redacted")
    multi = [
        record
        for record in records
        if record.get("matrix") in {"green_strict", "green_federated"}
    ]
    if multi:
        first_prompts = {record["turns"][0]["prompt"] for record in multi}
        if len(first_prompts) != 1:
            errors.append("strict/federated runs do not share the same first-turn prompt")
        thread_ids = [record.get("thread_id") for record in multi]
        if len(set(thread_ids)) != len(thread_ids):
            errors.append("multi-turn runs reuse a thread_id instead of fresh sessions")
    ambiguity_domains = {
        record.get("scenario_id")
        for record in records
        if record.get("matrix") == "green_ambiguous"
    }
    if len(ambiguity_domains) < 2:
        errors.append("ambiguous corpus must span at least two domains")
    high_prompts = [
        record["turns"][0]["prompt"].lower()
        for record in records
        if record.get("matrix") == "green_high"
    ]
    for prompt in high_prompts:
        hits = sorted(word for word in OBVIOUS_RISK_WORDS if word in prompt)
        if hits:
            errors.append(f"high-risk corpus relies on obvious risk keywords: {hits}")
    return errors


def validate_corpus(eval_root: Path, repo_root: Path | None = None) -> list[str]:
    repo_root = repo_root or eval_root.parents[2]
    manifest, records, scenarios = load_corpus(eval_root)
    skill_text = (repo_root / "claude/skills/discovery/SKILL.md").read_text(encoding="utf-8")
    errors = validate_records(manifest, records, scenarios, skill_text, eval_root)
    corpus_bytes = (eval_root / "corpus.jsonl").read_bytes()
    if manifest.get("corpus_sha256") != _sha256_bytes(corpus_bytes):
        errors.append("manifest corpus SHA does not match corpus.jsonl")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args()
    errors = validate_corpus(args.eval_root, args.repo_root)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    manifest, records, _ = load_corpus(args.eval_root)
    print(f"PASS: {len(records)} runs; skill {manifest['skill_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
