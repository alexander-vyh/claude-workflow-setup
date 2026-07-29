"""Codex-payload proof for the host-neutral direct-worktree guard."""

from __future__ import annotations

import importlib.util
import io
import json
import hashlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


HOOK_PATH = Path(__file__).resolve().parents[1] / "beads_worktree_guard.py"
PLUGIN_CLI = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "escapement"
    / "bin"
    / "escapement-worktree"
)


def _guard():
    spec = importlib.util.spec_from_file_location(
        "codex_worktree_entrypoint_guard", HOOK_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "oracle@example.test"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Oracle"], cwd=path, check=True)
    (path / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)
    return path


def _run_codex(command: str) -> tuple[int, dict]:
    gate = _guard()
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    captured = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(json.dumps(payload))),
        patch("sys.stdout", captured),
        patch.object(gate, "_record_signal", lambda *args, **kwargs: None),
    ):
        try:
            code = gate.main()
        except SystemExit as exc:
            code = exc.code or 0
    return code or 0, json.loads(
        captured.getvalue()
    ) if captured.getvalue().strip() else {}


def _git_state(repo: Path) -> tuple[str, str, tuple[tuple[str, str, str], ...]]:
    refs = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    worktrees = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    filesystem = tuple(
        sorted(
            (
                str(path.relative_to(repo)),
                "symlink"
                if path.is_symlink()
                else "directory"
                if path.is_dir()
                else "file",
                hashlib.sha256(
                    path.readlink().as_posix().encode()
                    if path.is_symlink()
                    else path.read_bytes()
                    if path.is_file()
                    else b""
                ).hexdigest(),
            )
            for path in repo.rglob("*")
            if ".git" not in path.parts
        )
    )
    return refs, worktrees, filesystem


def test_codex_real_direct_creation_is_denied(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    before = _git_state(repo)
    code, output = _run_codex("git worktree add .worktrees/x -b feature/x")
    assert code == 0
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert _git_state(repo) == before


def test_codex_quoted_text_is_allowed(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    code, output = _run_codex("echo 'git worktree add .worktrees/x -b feature/x'")
    assert code == 0
    assert output == {}


def test_codex_repair_names_existing_bundled_cli(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    _, output = _run_codex("bd worktree create .worktrees/x --branch feature/x")
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert PLUGIN_CLI.is_file(), "rendered Codex plugin must contain the bundled CLI"
    assert f"{PLUGIN_CLI} create" in reason
