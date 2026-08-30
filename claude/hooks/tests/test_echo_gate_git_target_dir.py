"""git_target_dir resolves the repo a finishing git command actually targets.

Recovered from pinned-deploy drift (2026-06-14): the echo-test gate resolved
repo_root from the session cwd, so `git -C <worktree> commit` (or `cd <wt> &&
git commit`) scanned the wrong repo — a cross-repo false positive. git_target_dir
honors the command's target.
"""
import importlib.util
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "implementation_echo_test_gate.py"
# Shared loader: reusing an equivalent registration keeps every test file on one
# module object, so mock.patch("<name>.*") cannot be redirected by import order.
from _hook_module import load_hook

gate = load_hook("implementation_echo_test_gate", HOOK)


def test_git_dash_C_absolute():
    assert gate.git_target_dir("git -C /repo/wt commit -m x", "/session") == "/repo/wt"


def test_git_dash_C_relative_resolves_against_cwd():
    assert gate.git_target_dir("git -C sub commit -m x", "/session") == "/session/sub"


def test_leading_cd_target():
    assert gate.git_target_dir("cd /repo/wt && git commit -m x", "/session") == "/repo/wt"


def test_plain_commit_falls_back_to_cwd():
    assert gate.git_target_dir("git commit -m x", "/session") == "/session"


def test_unparseable_command_falls_back():
    assert gate.git_target_dir('git commit -m "unterminated', "/session") == "/session"
