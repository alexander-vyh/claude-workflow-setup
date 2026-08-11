#!/usr/bin/env python3
"""Replay independently labeled gate decisions through shipped hook surfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from validate_corpus import DECISIONS, EVAL_DIR, ROOT, ValidationError, validate


VALID_BRIEF = """# Replay brief
## Business invariant
behavior is independently verified
## Independent source of truth
the labeled source event
## Solution constraints
stdlib and public hooks
## Invalid solution classes
vacuous runners
## Fragile implementation to reject
copying labels
## Negative control
always allow
## Positive control
specific outcomes
## Missing/unresolved handling
fail closed
## Final outcome verification
run replay.py
"""


def _git(cwd: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")


def _base_repository(path: Path) -> None:
    (path / "src").mkdir(parents=True)
    (path / "tests").mkdir()
    (path / "src" / "app.py").write_text("VALUE = 1\n")
    (path / "tests" / "test_app.py").write_text(
        "def test_value():\n    assert 1 == 1\n"
    )
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "replay@example.invalid")
    _git(path, "config", "user.name", "Gate Replay")
    _git(path, "config", "maintenance.auto", "false")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "replay baseline")


def _prepare_case(base: Path, path: Path, fixture: str) -> None:
    shutil.copytree(base, path)
    if fixture == "test_modified":
        (path / "tests" / "test_app.py").write_text(
            "def test_value():\n    assert 2 == 2\n"
        )
    elif fixture == "no_test_modified" or fixture == "missing_brief_edit":
        return
    elif fixture in {"valid_brief", "valid_brief_landing"}:
        brief = path / ".agent" / "runtime" / "test-oracle-brief.md"
        brief.parent.mkdir(parents=True)
        brief.write_text(VALID_BRIEF)
        if fixture == "valid_brief_landing":
            (path / "src" / "app.py").write_text("VALUE = 2\n")
    elif fixture == "missing_brief_landing":
        (path / "src" / "app.py").write_text("VALUE = 2\n")
    elif fixture == "outcome_assertion":
        (path / "tests" / "test_app.py").write_text(
            "def test_value():\n    result = 75\n    assert result == 75\n"
        )
    elif fixture == "structural_assertion":
        (path / "tests" / "test_app.py").write_text(
            "def test_value():\n    result = [75]\n    assert result is not None\n    assert len(result) > 0\n"
        )
    else:
        raise ValidationError(f"unknown replay fixture: {fixture}")


def _replace_repo(value: Any, repo: Path) -> Any:
    if isinstance(value, str):
        return value.replace("{repo}", str(repo))
    if isinstance(value, list):
        return [_replace_repo(item, repo) for item in value]
    if isinstance(value, dict):
        return {key: _replace_repo(item, repo) for key, item in value.items()}
    return value


def _decision(stdout: str) -> str:
    if not stdout.strip():
        return "allow"
    try:
        output = json.loads(stdout)
        decision = output["hookSpecificOutput"]["permissionDecision"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"hook emitted invalid decision JSON: {stdout!r}") from exc
    if decision not in DECISIONS:
        raise RuntimeError(f"hook emitted unsupported decision: {decision!r}")
    return decision


def _observe(
    case: dict[str, Any], repo: Path, surface: Path, signal_dir: Path
) -> tuple[str, dict[str, Any]]:
    payload = _replace_repo(case["payload"], repo)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(repo / ".home"),
            "GATE_SIGNAL_FALLBACK_DIR": str(signal_dir),
            "LC_ALL": "C",
            "LANG": "C",
            "TZ": "UTC",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    env.pop("BEADS_DIR", None)
    completed = subprocess.run(
        [sys.executable, "-B", str(surface)],
        cwd=repo,
        env=env,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{case['case_id']} hook exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return _decision(completed.stdout), {
        "surface": str(surface.relative_to(ROOT))
        if surface.is_relative_to(ROOT)
        else str(surface),
        "surface_sha256": hashlib.sha256(surface.read_bytes()).hexdigest(),
        "exit_status": completed.returncode,
    }


def _empty_matrix() -> dict[str, dict[str, int]]:
    return {
        expected: {observed: 0 for observed in sorted(DECISIONS)}
        for expected in sorted(DECISIONS)
    }


def _binary(matrix: dict[str, dict[str, int]]) -> dict[str, int | float | None]:
    tp = sum(
        matrix[expected][observed]
        for expected in ("ask", "deny")
        for observed in ("ask", "deny")
    )
    tn = matrix["allow"]["allow"]
    fp = sum(matrix["allow"][observed] for observed in ("ask", "deny"))
    fn = sum(matrix[expected]["allow"] for expected in ("ask", "deny"))
    return {
        "TP": tp,
        "TN": tn,
        "FP": fp,
        "FN": fn,
        "case_count": tp + tn + fp + fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def _surface_overrides(values: list[str]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for value in values:
        key, separator, raw_path = value.partition("=")
        path = Path(raw_path).resolve()
        if not separator or "/" not in key or not path.is_file():
            raise ValidationError(f"invalid --surface override: {value}")
        overrides[key] = path
    return overrides


def replay(
    cases: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    overrides: dict[str, Path],
) -> dict[str, Any]:
    matrices: defaultdict[str, dict[str, dict[str, int]]] = defaultdict(_empty_matrix)
    errors = {"false_positive": Counter(), "false_negative": Counter()}
    rows: list[dict[str, Any]] = []
    metrics = ("repair_turns", "model_tokens", "wall_ms", "human_interventions")
    cost_counts = Counter()
    cost_totals = Counter()

    with tempfile.TemporaryDirectory(prefix="gate-decision-replay-") as raw_tmp:
        temp = Path(raw_tmp)
        base = temp / "base"
        base.mkdir()
        _base_repository(base)
        signal_dir = temp / "signals"

        for index, case in enumerate(cases):
            case_repo = temp / f"case-{index:04d}"
            _prepare_case(base, case_repo, case["fixture"])
            name = f"{case['gate']}/{case['host']}"
            surface = overrides.get(name, ROOT / case["surface"])
            observed, execution = _observe(case, case_repo, surface, signal_dir)
            label = labels[case["case_id"]]
            expected = label["expected_decision"]
            matrices[name][expected][observed] += 1

            if expected == "allow" and observed != "allow":
                errors["false_positive"][label["false_positive_class"]] += 1
            elif expected != "allow" and observed == "allow":
                errors["false_negative"][label["false_negative_class"]] += 1

            cost = label["repair_cost"]
            evidenced = False
            for field in metrics:
                value = cost.get(field)
                if value is not None:
                    evidenced = True
                    cost_counts[field] += 1
                    cost_totals[field] += value
            cost_counts["evidenced_cases" if evidenced else "missing_cases"] += 1
            rows.append(
                {
                    "case_id": case["case_id"],
                    "gate": case["gate"],
                    "host": case["host"],
                    "expected_decision": expected,
                    "observed_decision": observed,
                    "matches": observed == expected,
                    "repair_cost": cost,
                    "execution": execution,
                }
            )

    ordered_matrices = {name: matrices[name] for name in sorted(matrices)}
    mismatch_count = sum(not row["matches"] for row in rows)
    return {
        "schema_version": 1,
        "case_count": len(rows),
        "mismatch_count": mismatch_count,
        "cases": rows,
        "decision_matrices": ordered_matrices,
        "binary_confusion_matrices": {
            name: _binary(matrix) for name, matrix in ordered_matrices.items()
        },
        "error_classes": {
            kind: dict(sorted(counts.items())) for kind, counts in errors.items()
        },
        "repair_cost": {
            "evidenced_cases": cost_counts["evidenced_cases"],
            "missing_cases": cost_counts["missing_cases"],
            "available_fields": {field: cost_counts[field] for field in metrics},
            "totals": {
                field: cost_totals[field] for field in metrics if cost_counts[field]
            },
        },
    }


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
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument(
        "--surface", action="append", default=[], metavar="GATE/HOST=PATH"
    )
    args = parser.parse_args()
    try:
        cases, labels = validate(
            args.corpus, args.source_events, args.labels, args.receipt
        )
        report = replay(cases, labels, _surface_overrides(args.surface))
    except (ValidationError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"replay failed: {exc}\n")
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        f"replayed {report['case_count']} cases; "
        f"mismatches={report['mismatch_count']}; report={args.result}"
    )
    return 1 if report["mismatch_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
