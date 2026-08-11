#!/usr/bin/env python3
"""Validate the compact gate-decision replay corpus and provenance joins."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parents[2]
DECISIONS = {"allow", "ask", "deny"}
SUPPORTED_CELLS = {
    ("tdd_gate", "claude"),
    ("test_oracle_brief_gate", "claude"),
    ("test_oracle_brief_gate", "codex"),
    ("outcome_assertion_gate", "claude"),
    ("outcome_assertion_gate", "codex"),
}


class ValidationError(ValueError):
    """The checked-in replay closure is incomplete or inconsistent."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read {path}: {exc}") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise ValidationError(f"{path} must contain JSON objects")
    return rows


def _unique(
    rows: list[dict[str, Any]], field: str, name: str
) -> dict[str, dict[str, Any]]:
    values = [row.get(field) for row in rows]
    if any(not isinstance(value, str) or not value for value in values):
        raise ValidationError(f"{name} has a missing {field}")
    if len(values) != len(set(values)):
        raise ValidationError(f"{name} has duplicate {field} values")
    return dict(zip(values, rows, strict=True))


def validate(
    corpus_path: Path,
    source_path: Path,
    labels_path: Path,
    receipt_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    cases = read_jsonl(corpus_path)
    sources = _unique(read_jsonl(source_path), "event_id", "source events")
    labels = _unique(read_jsonl(labels_path), "case_id", "labels")
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read reviewer receipt: {exc}") from exc

    case_by_id = _unique(cases, "case_id", "corpus")
    if not 150 <= len(cases) <= 200:
        raise ValidationError(f"corpus must contain 150-200 cases, got {len(cases)}")
    if set(labels) != set(case_by_id):
        raise ValidationError("labels do not join exactly to corpus cases")
    if {row.get("source_event_id") for row in cases} != set(sources):
        raise ValidationError("source events do not join exactly to corpus cases")

    cell_counts = Counter((row.get("gate"), row.get("host")) for row in cases)
    if set(cell_counts) != SUPPORTED_CELLS or set(cell_counts.values()) != {36}:
        raise ValidationError(
            f"supported cell allocation is incomplete: {dict(cell_counts)}"
        )

    source_revision = receipt.get("source_revision")
    policy_revision = receipt.get("policy_revision")
    if not isinstance(source_revision, str) or not source_revision.startswith(
        "sha256:"
    ):
        raise ValidationError("review receipt lacks a source revision")
    if not isinstance(policy_revision, str) or len(policy_revision) != 40:
        raise ValidationError("review receipt lacks a policy revision")
    if receipt.get("source_artifact") != source_path.name:
        raise ValidationError("review receipt names the wrong source artifact")
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != receipt.get(
        "selected_source_sha256"
    ):
        raise ValidationError("selected source digest does not match reviewer receipt")
    if hashlib.sha256(labels_path.read_bytes()).hexdigest() != receipt.get(
        "labels_sha256"
    ):
        raise ValidationError("label digest does not match reviewer receipt")

    receipt_id = receipt.get("receipt_id")
    for case in cases:
        if "expected_decision" in case or "observed_decision" in case:
            raise ValidationError(
                f"{case['case_id']} leaks a decision into the replay input"
            )
        if case.get("policy_revision") != policy_revision:
            raise ValidationError(f"{case['case_id']} has the wrong policy revision")
        if case.get("source_revision") != source_revision:
            raise ValidationError(f"{case['case_id']} has the wrong source revision")
        surface = Path(str(case.get("surface", "")))
        if (
            surface.is_absolute()
            or ".." in surface.parts
            or not (ROOT / surface).is_file()
        ):
            raise ValidationError(f"{case['case_id']} has an invalid public surface")
        source = sources[case["source_event_id"]]
        if source.get("gate") != case.get("gate"):
            raise ValidationError(f"{case['case_id']} does not match its source gate")

        label = labels[case["case_id"]]
        if label.get("expected_decision") not in DECISIONS:
            raise ValidationError(f"{case['case_id']} has an invalid expected decision")
        if label.get("reviewer_receipt_id") != receipt_id or not label.get("rationale"):
            raise ValidationError(
                f"{case['case_id']} lacks independent label provenance"
            )
        cost = label.get("repair_cost")
        if not isinstance(cost, dict) or not isinstance(cost.get("evidence"), list):
            raise ValidationError(f"{case['case_id']} has invalid repair-cost evidence")
        metrics = ("repair_turns", "model_tokens", "wall_ms", "human_interventions")
        for field in metrics:
            value = cost.get(field)
            if value is not None and (type(value) is not int or value < 0):
                raise ValidationError(f"{case['case_id']} has invalid {field}")
        if not any(cost.get(field) is not None for field in metrics) and not cost.get(
            "missing_reason"
        ):
            raise ValidationError(f"{case['case_id']} hides missing repair cost")

    return cases, labels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=EVAL_DIR / "corpus.jsonl")
    parser.add_argument(
        "--source-events", type=Path, default=EVAL_DIR / "source-events.jsonl"
    )
    parser.add_argument("--labels", type=Path, default=EVAL_DIR / "labels.jsonl")
    parser.add_argument(
        "--receipt", type=Path, default=EVAL_DIR / "reviewer-receipt.json"
    )
    args = parser.parse_args()
    try:
        cases, _ = validate(args.corpus, args.source_events, args.labels, args.receipt)
    except ValidationError as exc:
        parser.exit(2, f"validation failed: {exc}\n")
    print(f"validated {len(cases)} gate replay cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
