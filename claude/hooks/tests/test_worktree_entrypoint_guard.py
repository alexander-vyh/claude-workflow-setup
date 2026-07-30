"""Host-neutral guard oracle: direct creation is denied and repaired by CLI."""

from __future__ import annotations

import importlib.util
import io
import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

HOOK_PATH = Path(__file__).resolve().parents[1] / "beads_worktree_guard.py"


def _load_guard(path: Path = HOOK_PATH):
    spec = importlib.util.spec_from_file_location("worktree_entrypoint_guard", path)
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


def run_hook(
    command: str, *, cwd: Path, hook_path: Path = HOOK_PATH
) -> tuple[int, dict]:
    gate = _load_guard(hook_path)
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
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


def _reason(output: dict) -> str:
    detail = output["hookSpecificOutput"]
    assert detail["permissionDecision"] == "deny"
    return detail["permissionDecisionReason"]


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


def test_git_worktree_add_is_redirected_in_plain_git_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "plain")
    before = _git_state(repo)
    _, output = run_hook("git worktree add .worktrees/x -b feature/x", cwd=repo)
    reason = _reason(output)
    assert "escapement-worktree create" in reason
    assert f"--repo {repo}" in reason
    assert _git_state(repo) == before


def test_bd_worktree_create_is_redirected_in_beads_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "beads")
    (repo / ".beads").mkdir()
    before = _git_state(repo)
    _, output = run_hook("bd worktree create .worktrees/x --branch feature/x", cwd=repo)
    assert "escapement-worktree create" in _reason(output)
    assert _git_state(repo) == before


def test_literal_cd_routes_repair_to_target_repo_not_payload_cwd(
    tmp_path: Path,
) -> None:
    cake = _repo(tmp_path / "cake")
    dashboards = _repo(tmp_path / "dashboards")
    command = f"cd {dashboards} && git worktree add .worktrees/x -b feature/x"
    _, output = run_hook(command, cwd=cake)
    reason = _reason(output)
    assert f"--repo {dashboards}" in reason
    assert f"--repo {cake}" not in reason


def test_git_dash_c_routes_repair_to_target_repo(tmp_path: Path) -> None:
    cake = _repo(tmp_path / "cake")
    dashboards = _repo(tmp_path / "dashboards")
    _, output = run_hook(
        f"git -C {dashboards} worktree add .worktrees/x -b feature/x", cwd=cake
    )
    reason = _reason(output)
    assert f"--repo {dashboards}" in reason
    assert f"--repo {cake}" not in reason


def test_quoted_git_worktree_text_is_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    code, output = run_hook("printf '%s\\n' 'git worktree add .worktrees/x'", cwd=repo)
    assert code == 0 and output == {}


def test_quoted_bd_worktree_text_is_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    code, output = run_hook(
        "printf '%s\\n' 'bd worktree create .worktrees/x'", cwd=repo
    )
    assert code == 0 and output == {}


def test_malformed_shell_text_fails_open(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    code, output = run_hook('git worktree add "unterminated', cwd=repo)
    assert code == 0 and output == {}


def test_cli_invocation_is_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    code, output = run_hook(
        f"escapement-worktree create --repo {repo} --name x --branch feature/x",
        cwd=repo,
    )
    assert code == 0 and output == {}


def test_missing_bundled_cli_keeps_direct_creation_denied(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    broken_hooks = tmp_path / "broken-plugin" / "claude" / "hooks"
    shutil.copytree(HOOK_PATH.parent, broken_hooks)
    _, output = run_hook(
        "git worktree add .worktrees/x -b feature/x",
        cwd=repo,
        hook_path=broken_hooks / HOOK_PATH.name,
    )
    reason = _reason(output)
    assert "installation" in reason.lower()


def test_non_creation_git_and_bd_commands_are_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    for command in ("git status", "git worktree list", "bd list", "bd show example"):
        code, output = run_hook(command, cwd=repo)
        assert code == 0 and output == {}, command
