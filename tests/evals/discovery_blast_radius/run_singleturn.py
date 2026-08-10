#!/usr/bin/env python3
"""Run fresh single-turn discovery behavior controls with the canonical skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


EVAL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EVAL_ROOT.parents[2]
SKILL = REPO_ROOT / "claude/skills/discovery/SKILL.md"
MODEL = "gpt-5.6-luna"
FLAGS = [
    "--ephemeral",
    "--ignore-user-config",
    "--sandbox",
    "read-only",
    "--skip-git-repo-check",
]
PREAMBLE = (
    "The complete canonical discovery skill is loaded as developer guidance for "
    "this read-only behavioral evaluation. This isolated fixture has no project "
    "files or existing designs, so treat the project-context scan as complete. Stop "
    "at the first user-input point and never invent a reply. "
)
MATRIX_SCENARIOS = {
    "green_high": [
        "high_metric_authority",
        "high_metric_authority",
        "high_identifier_transition",
        "high_invoice_visibility",
        "high_order_amount_contract",
    ],
    "green_low": [
        "low_metric_copy",
        "low_metric_copy",
        "low_identifier_report",
        "low_invoice_copy",
        "low_order_amount_docs",
    ],
    "green_ambiguous": [
        "ambiguous_ready_term",
        "ambiguous_priority_label",
        "ambiguous_ready_term",
        "ambiguous_priority_label",
        "ambiguous_ready_term",
    ],
    "green_partial": [
        "partial_use_judgment",
        "partial_authority_only",
        "partial_rollout_only",
        "partial_two_choices",
        "partial_no_answer",
    ],
}
RUN_ID_PREFIX = {
    "green_high": "green-high",
    "green_low": "green-low",
    "green_ambiguous": "green-ambiguous-v2",
    "green_partial": "green-partial",
}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_command(
    *,
    codex: str,
    model: str,
    workdir: Path,
    output: Path,
    prompt: str,
) -> list[str]:
    skill_instruction = (
        f"developer_instructions={json.dumps(SKILL.read_text(encoding='utf-8'))}"
    )
    return [
        codex,
        "-a",
        "never",
        "-C",
        str(workdir),
        "exec",
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        skill_instruction,
        "--ephemeral",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "-m",
        model,
        "-o",
        str(output),
        prompt,
    ]


def _scenario_index() -> tuple[dict[str, dict[str, Any]], str]:
    data = json.loads((EVAL_ROOT / "scenarios.json").read_text(encoding="utf-8"))
    return {item["scenario_id"]: item for item in data["scenarios"]}, data[
        "partial_state"
    ]


def _prompt_for(
    matrix: str,
    scenario: dict[str, Any],
    partial_state: str,
) -> str:
    body = (
        partial_state.format(reply=scenario["reply"])
        if matrix == "green_partial"
        else scenario["prompt"]
    )
    return PREAMBLE + body + " Output only the next conversational response."


def _pending_rubric(matrix: str) -> dict[str, Any]:
    return {
        "classification": matrix.removeprefix("green_"),
        "fork_count": 0,
        "alternative_bearing": False,
        "categories": [],
        "solution_commitment_before_forks": False,
        "consequence_probe": False,
        "answer_gate_held": matrix == "green_partial",
        "answer_reflection": False,
        "new_load_bearing_fork_after_draft": False,
        "lightweight": matrix == "green_low",
        "manual_pass": False,
        "notes": "Pending transcript-level manual review.",
    }


def write_new_record(destination: Path, record: dict[str, Any]) -> None:
    """Persist once so an accepted run ID cannot be silently replaced."""

    with destination.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")


def run_one(
    *,
    codex: str,
    matrix: str,
    repetition: int,
    scenario: dict[str, Any],
    partial_state: str,
    output_dir: Path,
) -> Path:
    prompt = _prompt_for(matrix, scenario, partial_state)
    started_at = _now()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="discovery-singleturn-") as temp:
        workdir = Path(temp)
        output = workdir / "response.txt"
        command = build_command(
            codex=codex,
            model=MODEL,
            workdir=workdir,
            output=output,
            prompt=prompt,
        )
        turn_started = _now()
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        turn_completed = _now()
        response = output.read_text(encoding="utf-8") if output.exists() else ""
        if result.returncode != 0:
            raise RuntimeError(
                f"{matrix} repetition {repetition} failed: "
                f"exit={result.returncode} stderr={result.stderr!r}"
            )
    record = {
        "run_id": f"{RUN_ID_PREFIX[matrix]}-r{repetition}",
        "scenario_id": scenario["scenario_id"],
        "matrix": matrix,
        "variant": "final-guidance",
        "repetition": repetition,
        "skill_sha256": hashlib.sha256(SKILL.read_bytes()).hexdigest(),
        "runner": {
            "command": "codex exec",
            "model": MODEL,
            "flags": FLAGS,
            "skill_delivery": "developer_instructions_sha256",
        },
        "started_at": started_at,
        "completed_at": turn_completed,
        "exit_status": result.returncode,
        "turns": [
            {
                "index": 1,
                "started_at": turn_started,
                "completed_at": turn_completed,
                "prompt": prompt,
                "response": response,
            }
        ],
        "rubric": _pending_rubric(matrix),
    }
    destination = output_dir / f"{record['run_id']}.json"
    write_new_record(destination, record)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", default="codex")
    parser.add_argument(
        "--matrix",
        choices=(*MATRIX_SCENARIOS, "all"),
        default="all",
    )
    parser.add_argument("--jobs", type=int, default=5)
    parser.add_argument(
        "--repetition",
        dest="repetitions",
        type=int,
        action="append",
        help="Run only this 1-based matrix repetition; may be repeated.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    scenarios, partial_state = _scenario_index()
    matrices = MATRIX_SCENARIOS if args.matrix == "all" else (args.matrix,)
    tasks = [
        (matrix, repetition, scenarios[scenario_id])
        for matrix in matrices
        for repetition, scenario_id in enumerate(MATRIX_SCENARIOS[matrix], start=1)
        if args.repetitions is None or repetition in args.repetitions
    ]
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {
            executor.submit(
                run_one,
                codex=args.codex,
                matrix=matrix,
                repetition=repetition,
                scenario=scenario,
                partial_state=partial_state,
                output_dir=args.output_dir,
            ): (matrix, repetition)
            for matrix, repetition, scenario in tasks
        }
        for future in as_completed(futures):
            matrix, repetition = futures[future]
            path = future.result()
            print(f"completed {matrix} repetition {repetition}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
