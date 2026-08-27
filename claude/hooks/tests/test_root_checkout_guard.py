"""Behavioral tests for claude/hooks/root_checkout_guard.py.

The oracle is repo shape, not path strings: a primary checkout has `.git/` and
`.beads/`; a linked worktree has `.git` as a file and must be allowed.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from worktree_policy_oracle import direct_creation_commands


_HOOK_PATH = Path(__file__).resolve().parents[1] / "root_checkout_guard.py"
_WORKTREE_CLI = Path(__file__).resolve().parents[3] / "bin" / "escapement-worktree"
if not _HOOK_PATH.exists():
    pytest.fail(f"root_checkout_guard.py not found at {_HOOK_PATH}")

_spec = importlib.util.spec_from_file_location("root_checkout_guard", _HOOK_PATH)
guard = importlib.util.module_from_spec(_spec)
sys.modules["root_checkout_guard"] = guard
assert _spec.loader is not None
_spec.loader.exec_module(guard)


@pytest.mark.parametrize(
    "command",
    (
        "git worktree add .worktrees/task -b task",
        "git -C /repo worktree add .worktrees/task -b task",
        "git --git-dir /repo/.git --work-tree /repo worktree add /tmp/task",
        "git --git-dir=/repo/.git --work-tree=/repo worktree add /tmp/task",
        "bd --directory /repo worktree create .worktrees/task",
        "bd --directory=/repo worktree create .worktrees/task",
        "bd -C /repo worktree create .worktrees/task",
        "bd -C/repo worktree create .worktrees/task",
        "cd /repo && git -C /repo worktree add .worktrees/task",
        "cd /repo; bd --directory /repo worktree create .worktrees/task",
        "env WORKTREE=/tmp git --git-dir=/repo/.git worktree add /tmp/task",
        "command -- git -C /repo worktree add /tmp/task",
        "env -u GIT_DIR command bd -C /repo worktree create .worktrees/task",
    ),
)
def test_direct_creation_oracle_rejects_selectors_prefixes_and_chains(
    command: str,
) -> None:
    assert direct_creation_commands(f"Fallback: `{command}`") == [command]


@pytest.mark.parametrize(
    "reference",
    (
        'rg "git -C /repo worktree add" docs',
        'printf "%s\\n" "bd --directory /repo worktree create"',
        "echo 'git --git-dir=/repo/.git --work-tree=/repo worktree add /tmp/wt'",
        'python3 audit.py --reference "bd -C /repo worktree create"',
        'Historical reference: "git -C /repo worktree add"',
        'cd /repo && rg "git -C /repo worktree add" docs',
        'env QUERY="bd -C /repo worktree create" command rg "$QUERY" docs',
        '<code>rg "git -C /repo worktree add" docs</code>',
    ),
)
def test_direct_creation_oracle_ignores_quoted_reference_text(
    reference: str,
) -> None:
    assert not direct_creation_commands(f"Documentation check: `{reference}`")


@pytest.mark.parametrize(
    "surface",
    (
        "```bash\nprintf '%s' ready\ngit -C /repo worktree add /tmp/task\n```",
        "```bash\nprintf '%s' ready\nbd --directory /repo worktree create /tmp/task\n```",
        "<code>printf '%s' ready\ngit -C /repo worktree add /tmp/task</code>",
        "<code>printf '%s' ready\nbd --directory /repo worktree create /tmp/task</code>",
        "```bash\nprintf '%s' ready\n\ngit -C /repo worktree add /tmp/task\n```",
        "<code>printf '%s' ready;\nbd --directory /repo worktree create /tmp/task</code>",
    ),
)
def test_direct_creation_oracle_splits_fenced_and_html_newlines(
    surface: str,
) -> None:
    assert direct_creation_commands(surface)


@pytest.mark.parametrize(
    "surface",
    (
        "```bash\nprintf '%s' \"git -C /repo\nworktree add /tmp/task\"\n```",
        "<code>printf '%s' \"bd --directory /repo\nworktree create /tmp/task\"</code>",
        "<code>rg \"git -C /repo\nworktree add /tmp/task\" docs</code>",
        "<code>printf '%s' \"git -C /repo\n\nworktree add /tmp/task\"</code>",
    ),
)
def test_direct_creation_oracle_preserves_quoted_multiline_arguments(
    surface: str,
) -> None:
    assert not direct_creation_commands(surface)


@pytest.mark.parametrize(
    "surface",
    (
        "```bash\nprintf '%s' '&&' git -C /repo worktree add /tmp/task\n```",
        '<code>printf "%s" ";\n" bd --directory /repo worktree create /tmp/task</code>',
        "```bash\nprintf '%s' \\;\\; git -C /repo worktree add /tmp/task\n```",
        "<code>printf '%s' \\&\\& bd --directory /repo worktree create /tmp/task</code>",
    ),
)
def test_direct_creation_oracle_preserves_quoted_and_escaped_separator_runs(
    surface: str,
) -> None:
    assert not direct_creation_commands(surface)


@pytest.mark.parametrize(
    "surface",
    (
        "```bash\nprintf '%s' ready # unmatched \" ; &&\ngit -C /repo worktree add /tmp/task\n```",
        "<code>printf '%s' ready # unmatched ' ; ||\nbd --directory /repo worktree create /tmp/task</code>",
    ),
)
def test_direct_creation_oracle_ignores_comment_syntax_until_newline(
    surface: str,
) -> None:
    assert direct_creation_commands(surface)


@pytest.mark.parametrize(
    "surface",
    (
        "```bash\nprintf '%s' \"# literal\ngit -C /repo worktree add /tmp/task\"\n```",
        "<code>printf '%s' \\#\" literal\nbd --directory /repo worktree create /tmp/task\"</code>",
    ),
)
def test_direct_creation_oracle_preserves_quoted_and_escaped_hashes(
    surface: str,
) -> None:
    assert not direct_creation_commands(surface)


def _run_payload(payload: dict) -> tuple[int, dict, str]:
    stdout = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(json.dumps(payload))),
        patch("sys.stdout", stdout),
        patch.object(guard, "_record_signal", lambda *a, **k: None),
    ):
        try:
            code = guard.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    raw = stdout.getvalue().strip()
    return code or 0, json.loads(raw) if raw else {}, raw


def _write_payload(path: Path, *, cwd: Path, tool_name: str = "Write") -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": str(path), "content": "changed\n"},
        "cwd": str(cwd),
    }


EXPLICIT_EDIT_TOOLS = (
    "Write",
    "Edit",
    "NotebookEdit",
    "MultiEdit",
)

UNREGISTERED_TOOLS = (
    "Bash",
    "mcp__serena__replace_symbol_body",
    "mcp__serena__insert_after_symbol",
    "mcp__serena__insert_before_symbol",
)


def _explicit_edit_payload(tool_name: str, path: Path | str, *, cwd: Path) -> dict:
    path_key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    tool_input: dict = {path_key: str(path)}
    if tool_name == "Write":
        tool_input["content"] = "changed\n"
    elif tool_name == "Edit":
        tool_input.update(old_string="old", new_string="new")
    elif tool_name == "NotebookEdit":
        tool_input.update(cell_id="cell-1", new_source="changed")
    elif tool_name == "MultiEdit":
        tool_input["edits"] = [{"old_string": "old", "new_string": "new"}]
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": str(cwd),
    }


def _decision(output: dict) -> str | None:
    return output.get("hookSpecificOutput", {}).get("permissionDecision")


def _reason(output: dict) -> str:
    return output["hookSpecificOutput"]["permissionDecisionReason"]


def _make_primary_beads_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".beads").mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")
    return repo


def _make_linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / ".beads").mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {main}/.git/worktrees/wt\n", encoding="utf-8")
    (worktree / ".beads").mkdir()
    (worktree / ".beads" / "redirect").write_text(str(main / ".beads"), encoding="utf-8")
    (worktree / "src").mkdir()
    (worktree / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")
    return main, worktree


def _run_packaged_guard(
    tmp_path: Path,
    hook_parts: tuple[str, ...],
    payload: dict,
    *,
    install_cli: bool,
) -> tuple[int, dict, str, Path]:
    plugin = tmp_path / "plugin"
    hook_dir = plugin.joinpath(*hook_parts)
    hook_dir.mkdir(parents=True)
    packaged_hook = hook_dir / _HOOK_PATH.name
    shutil.copyfile(_HOOK_PATH, packaged_hook)
    shutil.copyfile(_HOOK_PATH.parent / "_worktree_cli.py", hook_dir / "_worktree_cli.py")
    cli = plugin / "bin" / "escapement-worktree"
    if install_cli:
        cli.parent.mkdir()
        shutil.copyfile(_WORKTREE_CLI, cli)

    result = subprocess.run(
        [sys.executable, str(packaged_hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    raw = result.stdout.strip()
    return result.returncode, json.loads(raw) if raw else {}, raw, cli


def test_write_to_primary_checkout_file_is_denied(tmp_path):
    repo = _make_primary_beads_repo(tmp_path)

    code, output, raw = _run_payload(_write_payload(repo / "src" / "app.py", cwd=repo))

    assert code == 0
    assert _decision(output) == "deny", raw
    reason = _reason(output)
    assert "primary checkout" in reason
    assert f"python3 -B {_WORKTREE_CLI} create" in reason
    assert f"--repo {repo}" in reason
    assert "--name <task>" in reason
    assert "--branch <branch>" in reason
    assert "then make the change there" in reason


@pytest.mark.parametrize(
    "hook_parts",
    (("claude", "hooks"), ("hooks",)),
    ids=("nested-plugin", "flat-plugin"),
)
def test_packaged_root_denial_uses_its_own_bundled_cli(tmp_path, hook_parts):
    repo = _make_primary_beads_repo(tmp_path)

    code, output, raw, cli = _run_packaged_guard(
        tmp_path,
        hook_parts,
        _write_payload(repo / "src" / "app.py", cwd=repo),
        install_cli=True,
    )

    assert code == 0
    assert _decision(output) == "deny", raw
    reason = _reason(output)
    assert f"python3 -B {cli} create" in reason
    assert f"--repo {repo}" in reason
    assert str(_WORKTREE_CLI) not in reason


@pytest.mark.parametrize(
    "hook_parts",
    (("claude", "hooks"), ("hooks",)),
    ids=("nested-plugin", "flat-plugin"),
)
def test_missing_bundled_cli_keeps_root_denial_and_reports_broken_installation(
    tmp_path,
    hook_parts,
):
    repo = _make_primary_beads_repo(tmp_path)

    code, output, raw, _cli = _run_packaged_guard(
        tmp_path,
        hook_parts,
        _write_payload(repo / "src" / "app.py", cwd=repo),
        install_cli=False,
    )

    assert code == 0
    assert _decision(output) == "deny", raw
    reason = _reason(output)
    assert "broken Escapement installation" in reason
    assert not direct_creation_commands(reason)


@pytest.mark.parametrize("tool_name", EXPLICIT_EDIT_TOOLS)
def test_explicit_edit_to_primary_checkout_denied_when_cwd_outside_repo(
    tmp_path, tool_name
):
    repo = _make_primary_beads_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = _explicit_edit_payload(
        tool_name, repo / "src" / "app.py", cwd=outside
    )

    code, output, raw = _run_payload(payload)

    assert code == 0
    assert _decision(output) == "deny", raw


@pytest.mark.parametrize("tool_name", EXPLICIT_EDIT_TOOLS)
@pytest.mark.parametrize("decoy_order", ("before", "after"))
def test_explicit_edit_uses_tool_canonical_path_despite_surplus_decoy(
    tmp_path, tool_name, decoy_order
):
    repo = _make_primary_beads_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = _explicit_edit_payload(
        tool_name, repo / "src" / "app.py", cwd=outside
    )
    decoy = (
        {"file_path": str(outside / "decoy.py")}
        if tool_name == "NotebookEdit"
        else {"notebook_path": str(outside / "decoy.ipynb")}
    )
    canonical = payload["tool_input"]
    payload["tool_input"] = (
        {**decoy, **canonical}
        if decoy_order == "before"
        else {**canonical, **decoy}
    )

    code, output, raw = _run_payload(payload)

    assert code == 0
    assert _decision(output) == "deny", raw


@pytest.mark.parametrize("tool_name", EXPLICIT_EDIT_TOOLS)
@pytest.mark.parametrize("decoy_order", ("before", "after"))
def test_explicit_edit_ignores_managed_surplus_decoy_when_canonical_path_outside(
    tmp_path, tool_name, decoy_order
):
    repo = _make_primary_beads_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = _explicit_edit_payload(
        tool_name, outside / "actual.py", cwd=outside
    )
    decoy = (
        {"file_path": str(repo / "src" / "app.py")}
        if tool_name == "NotebookEdit"
        else {"notebook_path": str(repo / "src" / "decoy.ipynb")}
    )
    canonical = payload["tool_input"]
    payload["tool_input"] = (
        {**decoy, **canonical}
        if decoy_order == "before"
        else {**canonical, **decoy}
    )

    code, output, raw = _run_payload(payload)

    assert code == 0
    assert output == {}, f"surplus decoy must not control destination; got {raw!r}"


@pytest.mark.parametrize("tool_name", EXPLICIT_EDIT_TOOLS)
def test_explicit_edit_to_linked_worktree_is_allowed(tmp_path, tool_name):
    _main, worktree = _make_linked_worktree(tmp_path)

    code, output, raw = _run_payload(
        _explicit_edit_payload(tool_name, worktree / "src" / "app.py", cwd=worktree)
    )

    assert code == 0
    assert output == {}, f"linked worktree writes must pass untouched; got {raw!r}"


@pytest.mark.parametrize("tool_name", EXPLICIT_EDIT_TOOLS)
def test_explicit_edit_to_outside_path_is_allowed(tmp_path, tool_name):
    repo = _make_primary_beads_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()

    code, output, raw = _run_payload(
        _explicit_edit_payload(tool_name, outside / "file.py", cwd=repo)
    )

    assert code == 0
    assert output == {}, f"outside explicit edit must pass untouched; got {raw!r}"


def test_missing_or_malformed_payload_fails_open():
    code, output, raw = _run_payload({"hook_event_name": "PreToolUse", "tool_name": "Write"})

    assert code == 0
    assert output == {}, f"malformed payload must fail open; got {raw!r}"


@pytest.mark.parametrize("tool_name", UNREGISTERED_TOOLS)
def test_unregistered_tool_is_outside_root_checkout_hard_gate(tmp_path, tool_name):
    class ExplodingToolInput(dict):
        def _explode(self, *_args, **_kwargs):
            raise AssertionError(f"{tool_name} tool_input must not be inspected")

        get = _explode
        items = _explode
        keys = _explode
        values = _explode
        __getitem__ = _explode
        __iter__ = _explode

    calls: list[dict] = []
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": ExplodingToolInput(relative_path="managed/path"),
        "cwd": str(tmp_path),
    }
    stdout = io.StringIO()

    with (
        patch.object(guard.json, "load", return_value=payload),
        patch("sys.stdout", stdout),
        patch.object(guard, "_record_signal", lambda **kwargs: calls.append(kwargs)),
    ):
        assert guard.main() == 0

    assert stdout.getvalue() == ""
    assert calls == []


def test_root_guard_contains_no_shell_classifier_architecture():
    tree = ast.parse(_HOOK_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [
                alias.name for alias in node.names
            ] if isinstance(node, ast.Import) else [node.module or ""]
            assert not ({"shlex", "subprocess"} & set(modules))
        if isinstance(node, ast.Name):
            lowered = node.id.lower()
            assert "bash" not in lowered and "shell" not in lowered
            assert lowered != "command"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lowered = node.name.lower()
            assert not any(part in lowered for part in ("bash", "shell", "command"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in {"Bash", "command"}

    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    assert isinstance(main.body[0], ast.Try)
    assert len(main.body[0].body) == 1
    assert main.body[0].orelse == []
    assert main.body[0].finalbody == []
    assert main.body[0].handlers
    for handler in main.body[0].handlers:
        assert len(handler.body) == 1 and isinstance(handler.body[0], ast.Return)
        returned = handler.body[0].value
        assert isinstance(returned, ast.Constant) and returned.value == 0
    load_statement = main.body[0].body[0]
    assert isinstance(load_statement, ast.Assign)
    assert ast.unparse(load_statement.value) == "json.load(sys.stdin)"
    assert isinstance(main.body[1], ast.If)
    assert ast.unparse(main.body[1].test) == (
        "data.get('hook_event_name') != 'PreToolUse'"
    )
    assert main.body[1].orelse == []
    assert len(main.body[1].body) == 1
    assert isinstance(main.body[1].body[0], ast.Return)
    event_return = main.body[1].body[0].value
    assert isinstance(event_return, ast.Constant) and event_return.value == 0
    tool_name_index = next(
        index
        for index, statement in enumerate(main.body)
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "tool_name"
            for target in statement.targets
        )
    )
    assert tool_name_index == 2
    tool_name_assignment = main.body[tool_name_index]
    assert isinstance(tool_name_assignment, ast.Assign)
    assert ast.unparse(tool_name_assignment.value) == "data.get('tool_name', '')"
    gate = main.body[tool_name_index + 1]
    assert isinstance(gate, ast.If)
    assert isinstance(gate.test, ast.Compare)
    assert isinstance(gate.test.left, ast.Name) and gate.test.left.id == "tool_name"
    assert len(gate.test.ops) == 1 and isinstance(gate.test.ops[0], ast.NotIn)
    assert len(gate.test.comparators) == 1
    comparator = gate.test.comparators[0]
    assert isinstance(comparator, ast.Name) and comparator.id == "GATED_EDIT_TOOLS"
    assert len(gate.body) == 1 and isinstance(gate.body[0], ast.Return)
    returned = gate.body[0].value
    assert isinstance(returned, ast.Constant) and returned.value == 0
    tool_input_index = next(
        index
        for index, statement in enumerate(main.body)
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "tool_input"
            for target in statement.targets
        )
    )
    assert tool_input_index > tool_name_index + 1
