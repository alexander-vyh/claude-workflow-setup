#!/usr/bin/env python3
"""Assemble reviewed discovery evaluation records into the committed corpus."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVAL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EVAL_ROOT.parents[2]


def _baseline_skill(eval_root: Path) -> Path:
    return eval_root / "baseline-discovery-skill.md"


BASELINE_SKILL = _baseline_skill(EVAL_ROOT)
FINAL_SKILL = REPO_ROOT / "claude/skills/discovery/SKILL.md"


def _load_validator():
    path = EVAL_ROOT / "validate_corpus.py"
    spec = importlib.util.spec_from_file_location("discovery_corpus_validator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load corpus validator from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()
BASELINE_MAP = {
    "baseline_high": [
        ("red-v2-metric-1.txt", "high_metric_authority"),
        ("red-v2-metric-2.txt", "high_metric_authority"),
        ("red-v2-data-1.txt", "high_identifier_transition"),
        ("red-v2-access-1.txt", "high_invoice_visibility"),
        ("red-v2-api-1.txt", "high_order_amount_contract"),
    ],
    "baseline_low": [
        ("red-low-metric-1.txt", "low_metric_copy"),
        ("red-low-metric-2.txt", "low_metric_copy"),
        ("red-low-data-1.txt", "low_identifier_report"),
        ("red-low-access-1.txt", "low_invoice_copy"),
        ("red-low-api-1.txt", "low_order_amount_docs"),
    ],
}
EXPECTED_MATRIX = {
    "baseline_high": 5,
    "baseline_low": 5,
    "green_high": 5,
    "green_low": 5,
    "green_ambiguous": 5,
    "green_partial": 5,
    "green_strict": 5,
    "green_federated": 5,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_sha(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _last_built_records() -> list[dict[str, Any]]:
    corpus = EVAL_ROOT / "corpus.jsonl"
    return [
        json.loads(line)
        for line in corpus.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _manifest_timestamp(records: list[dict[str, Any]]) -> str:
    if not records:
        raise ValueError("cannot timestamp an empty corpus")
    return max(record["completed_at"] for record in records)


def _scenario_index() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    data = json.loads((EVAL_ROOT / "scenarios.json").read_text(encoding="utf-8"))
    return {item["scenario_id"]: item for item in data["scenarios"]}, data


def _time_from_file(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat().replace(
        "+00:00", "Z"
    )


def _score(record: dict[str, Any]) -> dict[str, Any]:
    matrix = record["matrix"]
    response = record["turns"][0]["response"]
    chunks = validator._fork_chunks(response)
    categories = sorted(validator._derived_categories(response))
    final = not matrix.startswith("baseline_")
    rubric = {
        "classification": matrix.removeprefix("green_").removeprefix("baseline_"),
        "fork_count": len(chunks),
        "alternative_bearing": bool(chunks)
        and all(validator._has_alternatives(chunk) for chunk in chunks),
        "categories": categories,
        "solution_commitment_before_forks": False,
        "consequence_probe": matrix == "green_ambiguous",
        "answer_gate_held": matrix
        in {"green_high", "green_partial", "green_strict", "green_federated"},
        "answer_reflection": matrix in {"green_strict", "green_federated"},
        "new_load_bearing_fork_after_draft": False,
        "lightweight": matrix in {"baseline_low", "green_low"},
        "manual_pass": final or matrix == "baseline_low",
        "notes": "Transcript manually reviewed against the committed rubric.",
    }
    if matrix == "baseline_high":
        rubric.update(
            {
                "alternative_bearing": False,
                "solution_commitment_before_forks": True,
                "answer_gate_held": False,
                "manual_pass": False,
                "notes": (
                    "Expected RED control: solution direction or walking skeleton "
                    "preceded a single question."
                ),
            }
        )
    return rubric


def _baseline_records(
    baseline_dir: Path,
    scenarios: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    baseline_sha = _sha(BASELINE_SKILL)
    for matrix, entries in BASELINE_MAP.items():
        for repetition, (filename, scenario_id) in enumerate(entries, start=1):
            path = baseline_dir / filename
            timestamp = _time_from_file(path)
            prompt = scenarios[scenario_id]["prompt"]
            record = {
                "run_id": f"{matrix.replace('_', '-')}-r{repetition}",
                "scenario_id": scenario_id,
                "matrix": matrix,
                "variant": "baseline-guidance",
                "repetition": repetition,
                "skill_sha256": baseline_sha,
                "runner": {
                    "command": "codex exec",
                    "model": validator.EXPECTED_MODEL,
                    "flags": validator.SINGLE_TURN_FLAGS,
                    "skill_delivery": "developer_instructions_sha256",
                },
                "started_at": timestamp,
                "completed_at": timestamp,
                "exit_status": 0,
                "turns": [
                    {
                        "index": 1,
                        "started_at": timestamp,
                        "completed_at": timestamp,
                        "prompt": prompt,
                        "response": path.read_text(encoding="utf-8"),
                    }
                ],
            }
            record["rubric"] = _score(record)
            records.append(record)
    return records


def _load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    for record in records:
        record["rubric"] = _score(record)
    return records


def build(
    *,
    baseline_dir: Path,
    single_dir: Path,
    replacement_dir: Path,
    multiturn_dir: Path,
) -> tuple[Path, Path]:
    scenarios, scenario_data = _scenario_index()
    records = _baseline_records(baseline_dir, scenarios)
    records.extend(
        _load_records(
            sorted(single_dir.glob("green-high-*.json"))
            + sorted(single_dir.glob("green-low-*.json"))
            + sorted(replacement_dir.glob("green-ambiguous-v2-*.json"))
            + sorted(replacement_dir.glob("green-partial-*.json"))
            + sorted(multiturn_dir.glob("green-strict-*.json"))
            + sorted(multiturn_dir.glob("green-federated-*.json"))
        )
    )
    order = {matrix: index for index, matrix in enumerate(EXPECTED_MATRIX)}
    records.sort(key=lambda record: (order[record["matrix"]], record["repetition"]))
    corpus = EVAL_ROOT / "corpus.jsonl"
    corpus_text = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    corpus.write_text(corpus_text, encoding="utf-8")
    manifest = {
        "version": 1,
        "created_at": _manifest_timestamp(records),
        "skill_sha256": _sha(FINAL_SKILL),
        "baseline_skill_sha256": _sha(BASELINE_SKILL),
        "corpus_sha256": hashlib.sha256(corpus_text.encode()).hexdigest(),
        "record_sha256": {
            record["run_id"]: _record_sha(record) for record in records
        },
        "expected_matrix": EXPECTED_MATRIX,
        "runner_contract": {
            "model": validator.EXPECTED_MODEL,
            "single_turn_flags": validator.SINGLE_TURN_FLAGS,
            "multi_turn_flags": validator.MULTI_TURN_FLAGS,
            "resume_flags": validator.RESUME_FLAGS,
            "skill_delivery": "developer_instructions_sha256",
        },
        "rubric_fields": sorted(validator.REQUIRED_RUBRIC_FIELDS),
        "run_ids": [record["run_id"] for record in records],
        "superseded_runs": scenario_data["superseded_scenarios"],
        "notes": (
            "The v2 ambiguity IDs replace discarded export/shared-event fixtures; "
            "only ready-term and priority-label unknown-consequence runs are counted."
        ),
    }
    manifest_path = EVAL_ROOT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return corpus, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--single-dir", type=Path, required=True)
    parser.add_argument("--replacement-dir", type=Path, required=True)
    parser.add_argument("--multiturn-dir", type=Path, required=True)
    args = parser.parse_args()
    corpus, manifest = build(
        baseline_dir=args.baseline_dir,
        single_dir=args.single_dir,
        replacement_dir=args.replacement_dir,
        multiturn_dir=args.multiturn_dir,
    )
    print(f"wrote {corpus}")
    print(f"wrote {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
