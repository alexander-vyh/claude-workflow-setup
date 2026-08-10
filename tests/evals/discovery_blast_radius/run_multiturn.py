#!/usr/bin/env python3
"""Run ordered two-turn discovery evaluations in resumable Codex sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
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
FIRST_FLAGS = [
    "--ignore-user-config",
    "--sandbox",
    "read-only",
    "--skip-git-repo-check",
    "--json",
]
RESUME_FLAGS = ["--ignore-user-config", "--skip-git-repo-check", "--json"]
PREAMBLE = (
    "The complete canonical discovery skill is loaded as developer guidance for "
    "this read-only behavioral evaluation. This isolated fixture has no project "
    "files or existing designs, so treat the project-context scan as complete. Stop "
    "at the first user-input point and never invent a reply. "
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _walk_thread_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("thread_id", "session_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and len(candidate) >= 32:
                return candidate
        for child in value.values():
            found = _walk_thread_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _walk_thread_id(child)
            if found:
                return found
    return None


def extract_thread_id(output: str) -> str:
    for line in output.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        thread_id = _walk_thread_id(payload)
        if thread_id:
            return thread_id
    raise ValueError("codex JSON stream did not contain a thread/session id")


def build_commands(
    *,
    codex: str,
    model: str,
    workdir: Path,
    first_output: Path,
    second_output: Path,
    thread_id: str,
    first_prompt: str,
    second_prompt: str,
) -> tuple[list[str], list[str]]:
    skill_instruction = f"developer_instructions={json.dumps(SKILL.read_text(encoding='utf-8'))}"
    first = [
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
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "-m",
        model,
        "--json",
        "-o",
        str(first_output),
        first_prompt,
    ]
    resume = [
        codex,
        "-a",
        "never",
        "-C",
        str(workdir),
        "exec",
        "resume",
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        skill_instruction,
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--json",
        "-m",
        model,
        "-o",
        str(second_output),
        thread_id,
        second_prompt,
    ]
    return first, resume


def _scenario_data() -> tuple[str, dict[str, str]]:
    scenarios = json.loads((EVAL_ROOT / "scenarios.json").read_text(encoding="utf-8"))
    prompt = next(
        item["prompt"]
        for item in scenarios["scenarios"]
        if item["scenario_id"] == "differential_metric"
    )
    return prompt, scenarios["answer_sets"]


def _rubric(first_response: str) -> dict[str, Any]:
    # Transcript-level manual review must change manual_pass to true before corpus use.
    fork_count = len(
        __import__("re").findall(r"(?m)^\s*[1-9][.)]\s+", first_response)
    )
    lowered = first_response.lower()
    categories = []
    for category, words in {
        "authority_or_ownership": ("authority", "ownership", "owner"),
        "migration_or_compatibility": ("migration", "compatibility", "cutover"),
        "enforcement_or_rollout": ("enforcement", "rollout", "publication"),
        "rollback_or_failure_policy": ("rollback", "failure policy", "fail closed"),
    }.items():
        if any(word in lowered for word in words):
            categories.append(category)
    return {
        "classification": "high",
        "fork_count": fork_count,
        "alternative_bearing": False,
        "categories": categories,
        "solution_commitment_before_forks": False,
        "consequence_probe": False,
        "answer_gate_held": True,
        "answer_reflection": False,
        "new_load_bearing_fork_after_draft": False,
        "lightweight": False,
        "manual_pass": False,
        "notes": "Pending transcript-level manual review.",
    }


def run_one(
    *,
    codex: str,
    variant: str,
    repetition: int,
    output_dir: Path,
) -> Path:
    first_task, answers = _scenario_data()
    first_prompt = PREAMBLE + first_task + " Output only the next conversational response."
    second_prompt = answers[variant] + " Output only the next conversational response."
    started_at = _now()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="discovery-multiturn-") as temp:
        temp_root = Path(temp)
        skill_dir = temp_root / "discovery"
        skill_dir.mkdir()
        shutil.copy2(SKILL, skill_dir / "SKILL.md")
        first_output = temp_root / "turn1.txt"
        second_output = temp_root / "turn2.txt"
        placeholder = "00000000-0000-0000-0000-000000000000"
        first_command, _ = build_commands(
            codex=codex,
            model=MODEL,
            workdir=temp_root,
            first_output=first_output,
            second_output=second_output,
            thread_id=placeholder,
            first_prompt=first_prompt,
            second_prompt=second_prompt,
        )
        first_started = _now()
        first = subprocess.run(first_command, capture_output=True, text=True, check=False)
        first_completed = _now()
        thread_id = extract_thread_id(first.stdout)
        _, resume_command = build_commands(
            codex=codex,
            model=MODEL,
            workdir=temp_root,
            first_output=first_output,
            second_output=second_output,
            thread_id=thread_id,
            first_prompt=first_prompt,
            second_prompt=second_prompt,
        )
        second_started = _now()
        second = subprocess.run(resume_command, capture_output=True, text=True, check=False)
        second_completed = _now()
        first_response = first_output.read_text(encoding="utf-8") if first_output.exists() else ""
        second_response = second_output.read_text(encoding="utf-8") if second_output.exists() else ""
        if first.returncode != 0 or second.returncode != 0:
            raise RuntimeError(
                f"{variant} repetition {repetition} failed: "
                f"turn1={first.returncode} stderr={first.stderr!r}; "
                f"turn2={second.returncode} stderr={second.stderr!r}"
            )
    rubric = _rubric(first_response)
    record = {
        "run_id": f"green-{variant}-r{repetition}",
        "scenario_id": "differential_metric",
        "matrix": f"green_{variant}",
        "variant": "final-guidance",
        "repetition": repetition,
        "skill_sha256": hashlib.sha256(SKILL.read_bytes()).hexdigest(),
        "runner": {
            "command": "codex exec + codex exec resume",
            "model": MODEL,
            "flags": FIRST_FLAGS,
            "resume_flags": RESUME_FLAGS,
            "skill_delivery": "developer_instructions_sha256",
        },
        "thread_id": thread_id,
        "started_at": started_at,
        "completed_at": second_completed,
        "exit_status": first.returncode or second.returncode,
        "turns": [
            {
                "index": 1,
                "started_at": first_started,
                "completed_at": first_completed,
                "prompt": first_prompt,
                "response": first_response,
            },
            {
                "index": 2,
                "started_at": second_started,
                "completed_at": second_completed,
                "prompt": second_prompt,
                "response": second_response,
            },
        ],
        "rubric": rubric,
    }
    destination = output_dir / f"{record['run_id']}.json"
    destination.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--variant", choices=("strict", "federated", "both"), default="both")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--jobs", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    variants = ("strict", "federated") if args.variant == "both" else (args.variant,)
    tasks = [(variant, repetition) for variant in variants for repetition in range(1, args.repetitions + 1)]
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {
            executor.submit(
                run_one,
                codex=args.codex,
                variant=variant,
                repetition=repetition,
                output_dir=args.output_dir,
            ): (variant, repetition)
            for variant, repetition in tasks
        }
        for future in as_completed(futures):
            variant, repetition = futures[future]
            path = future.result()
            print(f"completed {variant} repetition {repetition}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
