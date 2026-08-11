"""Behavioral oracle for the compact gate-decision replay corpus."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests" / "evals" / "gate_decision_replay"
CORPUS = EVAL / "corpus.jsonl"
SOURCE_EVENTS = EVAL / "source-events.jsonl"
LABELS = EVAL / "labels.jsonl"
RECEIPT = EVAL / "reviewer-receipt.json"
RUNNER = EVAL / "replay.py"
DECISIONS = ("allow", "ask", "deny")
SUPPORTED_CELLS = {
    ("tdd_gate", "claude"),
    ("test_oracle_brief_gate", "claude"),
    ("test_oracle_brief_gate", "codex"),
    ("outcome_assertion_gate", "claude"),
    ("outcome_assertion_gate", "codex"),
}


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _run_replay(
    result: Path,
    *,
    labels: Path = LABELS,
    receipt: Path = RECEIPT,
    surface: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-B",
        str(RUNNER),
        "--labels",
        str(labels),
        "--receipt",
        str(receipt),
        "--result",
        str(result),
    ]
    if surface:
        command.extend(("--surface", surface))
    env = os.environ.copy()
    env.update({"LC_ALL": "C", "LANG": "C", "TZ": "UTC"})
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def test_corpus_is_stable_production_derived_and_independently_labeled() -> None:
    cases = _read_jsonl(CORPUS)
    sources = {row["event_id"]: row for row in _read_jsonl(SOURCE_EVENTS)}
    labels = {row["case_id"]: row for row in _read_jsonl(LABELS)}
    receipt = json.loads(RECEIPT.read_text())

    assert len(cases) == 180
    assert len({row["case_id"] for row in cases}) == len(cases)
    assert len(sources) == len(cases)
    assert set(labels) == {row["case_id"] for row in cases}
    assert Counter((row["gate"], row["host"]) for row in cases) == {
        cell: 36 for cell in SUPPORTED_CELLS
    }
    assert {row["expected_decision"] for row in labels.values()} == set(DECISIONS)
    assert all(row["source_event_id"] in sources for row in cases)
    assert all(
        row["policy_revision"] == "5d2b43492b81006b289b19f78d81757c39c38529"
        for row in cases
    )
    assert all(
        row["reviewer_receipt_id"] == receipt["receipt_id"] for row in labels.values()
    )
    assert (
        receipt["review_method"] == "independent behavioral review; gate output hidden"
    )
    assert receipt["source_artifact"] == "source-events.jsonl"
    assert receipt["source_revision"].startswith("sha256:")
    assert receipt["labels_sha256"] == hashlib.sha256(LABELS.read_bytes()).hexdigest()
    assert not any(
        "expected_decision" in row or "observed_decision" in row for row in cases
    )

    costs = [row["repair_cost"] for row in labels.values()]
    metrics = ("repair_turns", "model_tokens", "wall_ms", "human_interventions")
    assert any(any(cost.get(field) is not None for field in metrics) for cost in costs)
    assert any(
        not any(cost.get(field) is not None for field in metrics) for cost in costs
    )
    for cost in costs:
        if not any(cost.get(field) is not None for field in metrics):
            assert cost["missing_reason"]
        else:
            assert cost["evidence"]


def test_replay_executes_all_cases_and_reports_exact_matrices(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    completed = _run_replay(output)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text())

    assert report["case_count"] == 180
    assert report["mismatch_count"] == 0
    assert len(report["cases"]) == 180
    assert set(report["decision_matrices"]) == {
        f"{gate}/{host}" for gate, host in SUPPORTED_CELLS
    }
    assert set(report["binary_confusion_matrices"]) == set(report["decision_matrices"])
    assert report["error_classes"] == {"false_positive": {}, "false_negative": {}}

    for name, matrix in report["decision_matrices"].items():
        assert set(matrix) == set(DECISIONS), name
        assert all(set(matrix[expected]) == set(DECISIONS) for expected in DECISIONS)
        assert all(
            type(matrix[expected][observed]) is int and matrix[expected][observed] >= 0
            for expected in DECISIONS
            for observed in DECISIONS
        )
        assert sum(sum(row.values()) for row in matrix.values()) == 36

        binary = report["binary_confusion_matrices"][name]
        assert set(("TP", "TN", "FP", "FN", "case_count")) <= set(binary)
        assert binary["case_count"] == 36
        assert binary["TP"] + binary["TN"] + binary["FP"] + binary["FN"] == 36

    assert report["repair_cost"]["evidenced_cases"] > 0
    assert report["repair_cost"]["missing_cases"] > 0


def test_all_allow_surface_mutant_is_observed_and_fails(tmp_path: Path) -> None:
    hook = tmp_path / "always_allow.py"
    hook.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\n")
    output = tmp_path / "mutant-report.json"

    completed = _run_replay(
        output,
        surface=f"tdd_gate/claude={hook}",
    )

    assert completed.returncode != 0
    report = json.loads(output.read_text())
    assert report["mismatch_count"] > 0
    assert report["error_classes"]["false_negative"]
    rows = [
        row
        for row in report["cases"]
        if row["gate"] == "tdd_gate" and row["host"] == "claude"
    ]
    assert {row["observed_decision"] for row in rows} == {"allow"}


def test_inverted_expected_label_fails(tmp_path: Path) -> None:
    labels = _read_jsonl(LABELS)
    labels[0]["expected_decision"] = (
        "ask" if labels[0]["expected_decision"] == "allow" else "allow"
    )
    bad_labels = tmp_path / "labels.jsonl"
    bad_labels.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in labels)
    )
    copied_receipt = tmp_path / "reviewer-receipt.json"
    shutil.copyfile(RECEIPT, copied_receipt)

    completed = _run_replay(
        tmp_path / "inverted-report.json",
        labels=bad_labels,
        receipt=copied_receipt,
    )

    assert completed.returncode != 0
    assert "label digest" in completed.stderr.lower()


def test_readme_documents_the_exact_one_command_replay() -> None:
    readme = (EVAL / "README.md").read_text()
    assert "python3 -B tests/evals/gate_decision_replay/replay.py" in readme
    assert "--result" in readme
    assert "false-positive" in readme
    assert "repair cost" in readme.lower()
