#!/usr/bin/env python3
"""Run the isolated installed-host delegation canary."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from delegation_canary_evidence import CanaryFailure, parse_records, plugin_files, verify_unmanaged
from delegation_canary_lifecycle import load_api, verify_managed, write_mode


def host_version(claude_bin: Path, env: dict[str, str], timeout: int) -> str:
    result = subprocess.run(
        [str(claude_bin), "--version"], capture_output=True, text=True,
        env=env, timeout=timeout,
    )
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", result.stdout)
    if result.returncode != 0 or match is None:
        raise CanaryFailure("host_capability_unresolved")
    return match.group(1)


def resolve_claude_bin(value: str) -> Path:
    has_separator = os.sep in value or bool(os.altsep and os.altsep in value)
    selected = Path(value).expanduser().resolve() if has_separator else None
    if selected is None:
        found = shutil.which(value)
        selected = Path(found).resolve() if found else None
    if selected is None or not selected.is_file() or not os.access(selected, os.X_OK):
        raise CanaryFailure("host_capability_unresolved")
    return selected


def run_claude(
    claude_bin: Path, candidate_root: Path, repo: Path, config: Path,
    harness: Path, session_id: str, prompt: str, env: dict[str, str], timeout: int,
) -> list[dict]:
    run_env = {
        **env,
        "HARNESS_ROOT": str(harness),
        "CONTINUATION_HARNESS_HOME": str(harness),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DISABLE_AUTOUPDATER": "1",
    }
    result = subprocess.run(
        [
            str(claude_bin), "--print", "--verbose", "--output-format", "stream-json",
            "--forward-subagent-text", "--include-hook-events",
            "--no-session-persistence", "--permission-mode", "bypassPermissions",
            "--dangerously-skip-permissions", "--setting-sources", "",
            "--settings", str(config / "settings.json"), "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--plugin-dir", str(candidate_root),
            "--session-id", session_id, prompt,
        ],
        cwd=repo, env=run_env, capture_output=True, text=True, timeout=timeout,
    )
    (config / f"{session_id}.stdout.jsonl").write_text(result.stdout, encoding="utf-8")
    (config / f"{session_id}.stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise CanaryFailure("native_agent_first_attempt_failed")
    records = parse_records(result.stdout)
    if not records:
        raise CanaryFailure("host_capability_unresolved")
    return records


def prepare_scratch(root: Path) -> tuple[Path, Path, Path]:
    config, harness, repo = root / "config", root / "harness", root / "repo"
    for path in (config, harness, repo, repo / ".beads"):
        path.mkdir(parents=True, exist_ok=True)
    settings = config / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    settings.chmod(0o600)
    (repo / ".beads" / "config.yaml").write_text("issue-prefix: canary\n", encoding="utf-8")
    return config, harness, repo


def unmanaged_prompt() -> str:
    return (
        "Use Agent exactly once in the background on the first attempt. Name it "
        "canary-unmanaged, use subagent_type general-purpose, ask it to return "
        "UNMANAGED_CHILD_OK, wait for its terminal notification, then answer "
        "UNMANAGED_OK. Do not call any other tool."
    )


def managed_prompt(dependency: str) -> str:
    return (
        "Launch exactly three Agent calls in the background before waiting for any "
        "result. Name them canary-child-1, canary-child-2, canary-child-3 and use "
        "subagent_type general-purpose. Tell child 1 to sleep 8 seconds with Bash, "
        f"SendMessage the exact token {dependency} to canary-child-2, then return "
        "it. Tell child 2 to sleep 12 seconds, then return the dependency token it "
        "received by peer message. Tell child 3 to sleep 12 seconds and return its "
        "name. Wait for all three terminal notifications. Then attempt one background "
        "Agent named xncx-no-spawn with subagent_type nonexistent-agent-type-xyz so "
        "the native dispatch aborts before binding. Conclude with CANARY_COMPLETE and "
        "include the exact dependency token delivered by child 1. Do not call other tools."
    )


def execute(args) -> dict:
    source_root = args.source_root.expanduser().resolve()
    candidate_root = args.candidate_root.expanduser().resolve()
    scratch_root = args.scratch_root.expanduser().resolve()
    claude_bin = resolve_claude_bin(args.claude_bin)
    source_plugin = source_root / "plugins" / "escapement-claude"
    if plugin_files(source_plugin) != plugin_files(candidate_root):
        raise CanaryFailure("installed_surface_drift")
    config, harness, repo = prepare_scratch(scratch_root)
    environment = os.environ.copy()
    version = host_version(claude_bin, environment, args.timeout)
    if args.expected_version and version != args.expected_version:
        raise CanaryFailure("host_capability_unresolved")
    unmanaged = run_claude(
        claude_bin, candidate_root, repo, config, harness,
        str(uuid.uuid4()), unmanaged_prompt(), environment, args.timeout,
    )
    unmanaged_result = verify_unmanaged(unmanaged, harness, version, candidate_root)
    managed_session = str(uuid.uuid4())
    write_mode(harness / "threads" / managed_session, managed_session, repo)
    dependency = f"DEPENDENCY-{uuid.uuid4().hex[:12].upper()}"
    managed = run_claude(
        claude_bin, candidate_root, repo, config, harness,
        managed_session, managed_prompt(dependency), environment, args.timeout,
    )
    managed_result = verify_managed(
        managed, scratch_root, repo, version, candidate_root,
        load_api(candidate_root),
    )
    return {
        "status": "pass", "host_version": version,
        "unmanaged": unmanaged_result, "managed": managed_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--expected-version")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(execute(args), sort_keys=True))
        return 0
    except (CanaryFailure, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        reason = exc.reason if isinstance(exc, CanaryFailure) else "canary_runtime_failed"
        print(json.dumps({"status": "fail", "reason": reason}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
