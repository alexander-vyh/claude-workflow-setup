from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tools import render_agent_surfaces as surface_renderer


ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "agent-surfaces" / "manifest.json"
CANONICAL_HOOK = ROOT / "claude" / "hooks" / "test_oracle_brief_gate.py"
CLAUDE_HOOKS = (
    pytest.param(CANONICAL_HOOK, id="canonical-claude"),
    pytest.param(
        ROOT / "plugins" / "escapement-claude" / "hooks" / CANONICAL_HOOK.name,
        id="rendered-claude",
    ),
)
CODEX_HOOKS = (
    pytest.param(CANONICAL_HOOK, id="canonical-codex"),
    pytest.param(
        ROOT / "plugins" / "escapement" / "claude" / "hooks" / CANONICAL_HOOK.name,
        id="rendered-codex",
    ),
)
BRIEF_RELATIVE_PATH = Path(".agent/runtime/test-oracle-brief.md")
SIGNAL_RELATIVE_PATH = Path(".beads/.gate-signal.jsonl")
ENTRYPOINT_SOFT_LINE_LIMIT = 500
EXTRACTED_POLICY_MODULES = (
    "test_oracle_brief_policy.py",
    "test_oracle_brief_landing.py",
)
POLICY_PUBLIC_APIS = ("classify_edit_target", "brief_status")
LANDING_PUBLIC_APIS = ("landing_context",)
TRANSFERRED_PUBLIC_RESPONSIBILITIES = (
    "brief_status",
    "changed_files_for_landing",
    "command_contains_finishing_action",
    "is_relevant_file",
    "missing_brief_sections",
    "resolve_target_path",
)

REQUIRED_SECTIONS = (
    "Business invariant",
    "Independent source of truth",
    "Solution constraints",
    "Invalid solution classes",
    "Fragile implementation to reject",
    "Negative control",
    "Positive control",
    "Missing/unresolved handling",
    "Final outcome verification",
)

SUBSTANTIVE_BRIEF = """\
## Business invariant
Relevant source edits require a reviewed behavioral oracle.

## Independent source of truth
The public hook decision and appended signal row determine correctness.

## Solution constraints
Claude edits may ask, while landing commands must remain hard denied.

## Invalid solution classes
A wording-only change that still denies an edit is invalid.

## Fragile implementation to reject
Do not special-case only the Write payload.

## Negative control
A missing brief must ask before editing source code.

## Positive control
A complete brief must allow the same source edit.

## Missing/unresolved handling
Missing or placeholder-only content fails closed to an ask.

## Final outcome verification
Execute canonical and rendered hooks and inspect their JSON and signal rows.
"""

CONCISE_VALID_BRIEF = """\
## Business invariant
Users receive approval.

## Independent source of truth
Public JSON proves.

## Solution constraints
Landing stays denied.

## Invalid solution classes
Bypasses remain invalid.

## Fragile implementation to reject
Hardcoding must fail.

## Negative control
Missing briefs ask.

## Positive control
Valid briefs allow.

## Missing/unresolved handling
Missing data blocks.

## Final outcome verification
Run public hooks.
"""


def _manifest_hook() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return next(item for item in manifest["hooks"] if item["id"] == "test_oracle_brief_gate")


def _registered_tools(host: str) -> tuple[str, ...]:
    events = _manifest_hook()["hosts"][host]["events"]
    assert len(events) == 1
    assert events[0]["event"] == "PreToolUse"
    return tuple(events[0]["matcher"].split("|"))


EDIT_TOOL_INPUTS = {
    "Write": ("file_path", "src/app.py"),
    "Edit": ("file_path", "src/app.py"),
    "NotebookEdit": ("notebook_path", "src/analysis.ipynb"),
    "mcp__serena__replace_symbol_body": ("relative_path", "src/app.py"),
    "mcp__serena__insert_after_symbol": ("relative_path", "src/app.py"),
    "mcp__serena__insert_before_symbol": ("relative_path", "src/app.py"),
}

LANDING_COMMANDS = (
    pytest.param("git commit -m change", id="git-commit"),
    pytest.param("git push origin HEAD", id="git-push"),
    pytest.param("gh pr create --title change --body tested", id="gh-pr-create"),
    pytest.param("gh pr merge 42", id="gh-pr-merge"),
    pytest.param("bd close escapement-example", id="bd-close"),
)
REGISTERED_EDIT_TOOL_CONTRACTS = tuple(
    (tool_name, *EDIT_TOOL_INPUTS[tool_name])
    for tool_name in _registered_tools("claude")
    if tool_name in EDIT_TOOL_INPUTS
)
EDGE_PATH_SURFACES = tuple(
    pytest.param(
        tool_name,
        path_key,
        target_relative,
        tool_name == "Write",
        id=f"{'absolute' if tool_name == 'Write' else 'cwd-relative'}-{tool_name}",
    )
    for tool_name, path_key, target_relative in REGISTERED_EDIT_TOOL_CONTRACTS
)


def _placeholder_brief(*bodies: str) -> str:
    assert len(bodies) == len(REQUIRED_SECTIONS)
    return "\n".join(
        f"## {section}\n{body}\n"
        for section, body in zip(REQUIRED_SECTIONS, bodies, strict=True)
    )


MIXED_PLACEHOLDER_BRIEF = _placeholder_brief(
    "tBd",
    "TODO",
    "n/A",
    "NA",
    "???",
    "Coming Soon",
    "-",
    "- \n* \n1. ",
    "todo",
)

MIXED_TRIVIAL_BRIEF = _placeholder_brief(
    "x",
    "explanation",
    "same same same same",
    "two words",
    "## TODO",
    "-",
    "???",
    "- x",
    "x y z",
)

PLAUSIBLE_NONSENSE_BRIEF = _placeholder_brief(*(["alpha beta gamma"] * 9))
IDENTICAL_BOILERPLATE_BRIEF = _placeholder_brief(
    *(["This section explains the required outcome."] * 9)
)
MISASSIGNED_SECTION_BRIEF = _placeholder_brief(
    "Public JSON proves.",
    "Landing stays denied.",
    "Bypasses remain invalid.",
    "Hardcoding must fail.",
    "Missing briefs ask.",
    "Valid briefs allow.",
    "Missing data blocks.",
    "Run public hooks.",
    "Users receive approval.",
)

INVALID_BRIEFS = (
    pytest.param("", id="empty"),
    pytest.param(" \n\t\n", id="whitespace"),
    pytest.param(
        SUBSTANTIVE_BRIEF.replace("## Final outcome verification", "## Verification"),
        id="missing-section",
    ),
    pytest.param(_placeholder_brief(*(["TBD"] * 9)), id="tbd"),
    pytest.param(_placeholder_brief(*(["todo"] * 9)), id="todo-case-insensitive"),
    pytest.param(_placeholder_brief(*(["N/A"] * 9)), id="n-a"),
    pytest.param(_placeholder_brief(*(["na"] * 9)), id="na-case-insensitive"),
    pytest.param(_placeholder_brief(*(["???"] * 9)), id="question-marks"),
    pytest.param(_placeholder_brief(*(["COMING SOON"] * 9)), id="coming-soon"),
    pytest.param(_placeholder_brief(*(["-"] * 9)), id="dash"),
    pytest.param(_placeholder_brief(*(["- \n* \n1. "] * 9)), id="empty-list-bodies"),
    pytest.param(MIXED_PLACEHOLDER_BRIEF, id="mixed-placeholders"),
    pytest.param(_placeholder_brief(*(["x"] * 9)), id="one-character"),
    pytest.param(_placeholder_brief(*(["explanation"] * 9)), id="one-token"),
    pytest.param(
        _placeholder_brief(*(["outcome outcome outcome outcome"] * 9)),
        id="repeated-token",
    ),
    pytest.param(_placeholder_brief(*(["two words"] * 9)), id="two-token"),
    pytest.param(_placeholder_brief(*(["x y z"] * 9)), id="three-one-letter-tokens"),
    pytest.param(_placeholder_brief(*(["## TODO"] * 9)), id="heading-placeholder"),
    pytest.param(MIXED_TRIVIAL_BRIEF, id="mixed-trivial"),
    pytest.param(PLAUSIBLE_NONSENSE_BRIEF, id="plausible-length-nonsense"),
    pytest.param(IDENTICAL_BOILERPLATE_BRIEF, id="identical-boilerplate"),
)

_SUBSTANTIVE_SECTION_BODIES = (
    "Relevant source edits require a reviewed behavioral oracle.",
    "The public hook decision and appended signal row determine correctness.",
    "Claude edits may ask, while landing commands must remain hard denied.",
    "A wording-only change that still denies an edit is invalid.",
    "Do not special-case only the Write payload.",
    "A missing brief must ask before editing source code.",
    "A complete brief must allow the same source edit.",
    "Missing or placeholder-only content fails closed to an ask.",
    "Execute canonical and rendered hooks and inspect their JSON and signal rows.",
)
_SINGLE_PLACEHOLDERS = ("TBD", "todo", "N/A", "na", "???", "COMING SOON", "-", "- \n* \n1. ", "TBD")
SINGLE_PLACEHOLDER_BRIEFS = tuple(
    pytest.param(
        _placeholder_brief(
            *(
                placeholder if body_index == section_index else body
                for body_index, body in enumerate(_SUBSTANTIVE_SECTION_BODIES)
            )
        ),
        id=f"placeholder-only-{section.lower().replace('/', '-').replace(' ', '-')}",
    )
    for section_index, (section, placeholder) in enumerate(
        zip(REQUIRED_SECTIONS, _SINGLE_PLACEHOLDERS, strict=True)
    )
)

_SINGLE_TRIVIAL_BODIES = (
    "x",
    "explanation",
    "same same same",
    "two words",
    "## TODO",
    "- x",
    "x y z",
    "oneword",
    "repeat repeat repeat",
)
SINGLE_TRIVIAL_BRIEFS = tuple(
    pytest.param(
        _placeholder_brief(
            *(
                trivial if body_index == section_index else body
                for body_index, body in enumerate(_SUBSTANTIVE_SECTION_BODIES)
            )
        ),
        id=f"trivial-only-{section.lower().replace('/', '-').replace(' ', '-')}",
    )
    for section_index, (section, trivial) in enumerate(
        zip(REQUIRED_SECTIONS, _SINGLE_TRIVIAL_BODIES, strict=True)
    )
)


def init_repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".beads").mkdir()
    return tmp_path


def write_target(repo: Path, relative_path: str) -> Path:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('behavior')\n", encoding="utf-8")
    return target


def write_brief(repo: Path, content: str) -> None:
    brief = repo / BRIEF_RELATIVE_PATH
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(content, encoding="utf-8")


def signal_rows(repo: Path) -> list[dict]:
    path = repo / SIGNAL_RELATIVE_PATH
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run_hook(hook_path: Path, repo: Path, payload: dict) -> tuple[subprocess.CompletedProcess[str], dict | None, list[dict]]:
    before = signal_rows(repo)
    env = os.environ.copy()
    env["BEADS_DIR"] = str(repo / ".beads")
    env["CLAUDE_CODE_SESSION_ID"] = payload["session_id"]
    env["GATE_SIGNAL_FALLBACK_DIR"] = str(repo / ".signal-fallback")
    result = subprocess.run(
        [sys.executable, "-B", str(hook_path)],
        cwd=repo,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    stdout = result.stdout.strip()
    output = json.loads(stdout) if stdout else None
    after = signal_rows(repo)
    return result, output, after[len(before) :]


def edit_payload(repo: Path, tool_name: str) -> tuple[dict, str]:
    path_key, relative_path = EDIT_TOOL_INPUTS[tool_name]
    target = write_target(repo, relative_path)
    path_value = relative_path if path_key == "relative_path" else str(target)
    return (
        {
            "session_id": "session-edit-abc",
            "cwd": str(repo),
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {path_key: path_value},
        },
        relative_path,
    )


def write_payload(repo: Path, raw_path: str, session_id: str = "session-edit-abc") -> dict:
    return {
        "session_id": session_id,
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": raw_path},
    }


def path_payload(repo: Path, tool_name: str, path_key: str, raw_path: str) -> dict:
    return {
        "session_id": "session-edit-abc",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {path_key: raw_path},
    }


def landing_payload(repo: Path, command: str) -> dict:
    return {
        "session_id": "session-land-abc",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def assert_decision(result: subprocess.CompletedProcess[str], output: dict | None, decision: str) -> str:
    assert result.returncode == 0, result.stderr
    assert output is not None
    assert set(output) == {"hookSpecificOutput"}
    decision_output = output["hookSpecificOutput"]
    assert decision_output["hookEventName"] == "PreToolUse"
    assert decision_output["permissionDecision"] == decision
    return decision_output["permissionDecisionReason"]


def assert_signal(
    rows: list[dict],
    *,
    decision: str,
    tool: str,
    target: str,
    category: str,
) -> None:
    assert len(rows) == 1, "one hook decision must append exactly one correlated signal"
    row = rows[0]
    assert row["gate"] == "test_oracle_brief_gate"
    assert row["decision"] == decision
    expected_session_id = "session-land-abc" if tool == "Bash" else "session-edit-abc"
    assert row["session_id"] == expected_session_id
    expected_extras = {
        "tool": tool,
        "target": target,
        "category": category,
    }
    if tool == "Bash":
        expected_extras.update({"surface": "landing-command", "file_count": 1})
    assert row["extras"] == expected_extras
    assert row["event_type"] == "signal"
    serialized = json.dumps(row).lower()
    assert "accepted" not in serialized
    assert "rejected" not in serialized
    assert "approved" not in serialized
    assert "override" not in serialized
    reason = row["reason"].lower()
    expected_reason_terms = {
        "missing-brief": ("missing", "test oracle brief"),
        "invalid-brief": ("test oracle brief", "required", "content"),
        "valid-brief": ("oracle brief", "valid"),
    }
    assert all(term in reason for term in expected_reason_terms[category]), reason


def assert_honest_ask_reason(reason: str) -> None:
    normalized = reason.lower()
    assert "this ask decision is recorded" in normalized
    assert "cannot observe or record a later host approval or rejection" in normalized
    assert "proceed" not in normalized
    assert "override" not in normalized
    assert "approved" not in normalized
    assert "accepted" not in normalized


def test_manifest_payload_table_covers_every_registered_surface():
    registered_edit_tools = set(_registered_tools("claude"))
    assert registered_edit_tools == set(EDIT_TOOL_INPUTS)
    assert {contract[0] for contract in REGISTERED_EDIT_TOOL_CONTRACTS} == registered_edit_tools
    assert _registered_tools("codex") == ("Bash",)


def test_canonical_entrypoint_stays_below_repo_soft_line_boundary():
    line_count = len(CANONICAL_HOOK.read_text(encoding="utf-8").splitlines())
    assert line_count <= ENTRYPOINT_SOFT_LINE_LIMIT, (
        f"canonical hook has {line_count} lines; extract cohesive policy below "
        f"the {ENTRYPOINT_SOFT_LINE_LIMIT}-line soft boundary"
    )


@pytest.mark.parametrize("module_name", EXTRACTED_POLICY_MODULES)
def test_extracted_policy_module_is_renderer_owned_and_host_equal(module_name):
    source = ROOT / "claude" / "hooks" / module_name
    codex_copy = ROOT / "plugins" / "escapement" / "claude" / "hooks" / module_name
    claude_copy = ROOT / "plugins" / "escapement-claude" / "hooks" / module_name

    assert source.is_file(), f"missing extracted canonical policy module: {source}"
    source_key = source.relative_to(ROOT).as_posix()
    assert source_key in surface_renderer.SHARED_HOOK_SUPPORT
    assert codex_copy.read_bytes() == source.read_bytes()
    assert claude_copy.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("module_name", EXTRACTED_POLICY_MODULES)
def test_each_extracted_policy_module_stays_below_repo_soft_line_boundary(module_name):
    source = ROOT / "claude" / "hooks" / module_name

    assert source.is_file(), f"missing extracted canonical policy module: {source}"
    line_count = len(source.read_text(encoding="utf-8").splitlines())
    assert line_count <= ENTRYPOINT_SOFT_LINE_LIMIT, (
        f"{module_name} has {line_count} lines; moving the monolith does not satisfy "
        f"the {ENTRYPOINT_SOFT_LINE_LIMIT}-line module boundary"
    )


def _entrypoint_tree() -> ast.Module:
    return ast.parse(CANONICAL_HOOK.read_text(encoding="utf-8"))


def test_entrypoint_imports_and_invokes_extracted_public_policy_apis():
    tree = _entrypoint_tree()
    imports = {
        node.module: {alias.name for alias in node.names}
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    }
    invoked_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert imports.get("test_oracle_brief_policy", set()) >= set(POLICY_PUBLIC_APIS)
    assert imports.get("test_oracle_brief_landing", set()) >= set(LANDING_PUBLIC_APIS)
    assert invoked_names >= set(POLICY_PUBLIC_APIS + LANDING_PUBLIC_APIS)


def test_edit_and_landing_handlers_invoke_the_same_imported_brief_status():
    tree = _entrypoint_tree()
    imported_policy_names = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "test_oracle_brief_policy"
        for alias in node.names
    }
    top_level_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "brief_status" in imported_policy_names
    for handler_name in ("handle_edit_gate", "handle_bash_landing_gate"):
        handler = top_level_functions[handler_name]
        called_names = {
            node.func.id
            for node in ast.walk(handler)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "brief_status" in called_names, (
            f"{handler_name} must use the shared imported brief-status policy"
        )


def test_entrypoint_no_longer_defines_transferred_public_responsibilities():
    defined_names = {
        node.name
        for node in _entrypoint_tree().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert defined_names.isdisjoint(TRANSFERRED_PUBLIC_RESPONSIBILITIES), (
        "canonical entrypoint still owns extracted policy or landing responsibilities: "
        f"{sorted(defined_names & set(TRANSFERRED_PUBLIC_RESPONSIBILITIES))}"
    )


@pytest.mark.parametrize("module_name", EXTRACTED_POLICY_MODULES)
def test_extracted_policy_modules_never_delegate_back_to_entrypoint(module_name):
    source = ROOT / "claude" / "hooks" / module_name
    assert source.is_file(), f"missing extracted canonical policy module: {source}"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    dynamic_loader_names = {
        "import_module",
        "run_module",
        "run_path",
        "spec_from_file_location",
    }
    dynamic_loader_arguments = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and (
            isinstance(node.func, ast.Name) and node.func.id in dynamic_loader_names
            or isinstance(node.func, ast.Attribute)
            and node.func.attr in dynamic_loader_names
        )
    }

    entrypoint_module = CANONICAL_HOOK.stem
    assert all(entrypoint_module not in name for name in imported_modules)
    assert all(entrypoint_module not in value for value in dynamic_loader_arguments)


def _load_extracted_module(module_name: str):
    source = ROOT / "claude" / "hooks" / f"{module_name}.py"
    assert source.is_file(), f"missing extracted canonical policy module: {source}"
    spec = importlib.util.spec_from_file_location(module_name, source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extracted_policy_public_apis_own_path_and_brief_classification(tmp_path):
    policy = _load_extracted_module("test_oracle_brief_policy")
    repo = init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "src").mkdir()

    repo_root, target, relevant = policy.classify_edit_target(
        str(repo / "docs" / ".." / "src" / "new.py"), str(repo)
    )

    assert repo_root == repo.resolve()
    assert target == "src/new.py"
    assert relevant is True

    write_brief(repo, CONCISE_VALID_BRIEF)
    assert policy.brief_status(repo) == (True, None, "valid-brief")

    write_brief(repo, PLAUSIBLE_NONSENSE_BRIEF)
    ok, reason, category = policy.brief_status(repo)
    assert ok is False
    assert "content" in reason.lower()
    assert category == "invalid-brief"


def test_extracted_landing_public_api_owns_command_and_changed_file_context(tmp_path):
    landing = _load_extracted_module("test_oracle_brief_landing")
    repo = init_repo(tmp_path)
    write_target(repo, "src/app.py")

    repo_root, changed_files = landing.landing_context("git commit -m change", str(repo))

    assert repo_root == repo.resolve()
    assert changed_files == ["src/app.py"]
    assert landing.landing_context("pytest", str(repo)) is None


def test_claude_edit_blocks_relevant_file_without_brief(tmp_path):
    """Manifest fixture: the former hard deny must become an approvable ask."""
    repo = init_repo(tmp_path)
    payload, target = edit_payload(repo, "Write")

    result, output, rows = run_hook(CANONICAL_HOOK, repo, payload)

    reason = assert_decision(result, output, "ask")
    assert_honest_ask_reason(reason)
    assert_signal(
        rows,
        decision="ask",
        tool="Write",
        target=target,
        category="missing-brief",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize("tool_name", _registered_tools("claude"))
@pytest.mark.parametrize("brief_content", [None, MIXED_TRIVIAL_BRIEF], ids=["absent", "trivial"])
def test_every_registered_claude_edit_surface_asks_for_absent_or_trivial_brief(
    tmp_path, hook_path, tool_name, brief_content
):
    repo = init_repo(tmp_path)
    if brief_content is not None:
        write_brief(repo, brief_content)
    payload, target = edit_payload(repo, tool_name)

    result, output, rows = run_hook(hook_path, repo, payload)

    reason = assert_decision(result, output, "ask")
    assert_honest_ask_reason(reason)
    assert_signal(
        rows,
        decision="ask",
        tool=tool_name,
        target=target,
        category="missing-brief" if brief_content is None else "invalid-brief",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize("brief_content", INVALID_BRIEFS)
def test_placeholder_or_incomplete_brief_never_satisfies_edit_gate(tmp_path, hook_path, brief_content):
    repo = init_repo(tmp_path)
    write_brief(repo, brief_content)
    payload, target = edit_payload(repo, "Write")

    result, output, rows = run_hook(hook_path, repo, payload)

    reason = assert_decision(result, output, "ask")
    assert_honest_ask_reason(reason)
    assert_signal(
        rows,
        decision="ask",
        tool="Write",
        target=target,
        category="invalid-brief",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize("brief_content", SINGLE_PLACEHOLDER_BRIEFS)
def test_one_placeholder_section_invalidates_otherwise_substantive_brief(
    tmp_path, hook_path, brief_content
):
    repo = init_repo(tmp_path)
    write_brief(repo, brief_content)
    payload, target = edit_payload(repo, "Write")

    result, output, rows = run_hook(hook_path, repo, payload)

    reason = assert_decision(result, output, "ask")
    assert_honest_ask_reason(reason)
    assert_signal(
        rows,
        decision="ask",
        tool="Write",
        target=target,
        category="invalid-brief",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize("brief_content", SINGLE_TRIVIAL_BRIEFS)
def test_one_trivial_section_invalidates_otherwise_substantive_brief(
    tmp_path, hook_path, brief_content
):
    repo = init_repo(tmp_path)
    write_brief(repo, brief_content)
    payload, target = edit_payload(repo, "Write")

    result, output, rows = run_hook(hook_path, repo, payload)

    reason = assert_decision(result, output, "ask")
    assert_honest_ask_reason(reason)
    assert_signal(
        rows,
        decision="ask",
        tool="Write",
        target=target,
        category="invalid-brief",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize("tool_name", _registered_tools("claude"))
def test_concise_meaningful_brief_allows_every_registered_edit_surface(tmp_path, hook_path, tool_name):
    repo = init_repo(tmp_path)
    write_brief(repo, CONCISE_VALID_BRIEF)
    payload, target = edit_payload(repo, tool_name)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert_signal(
        rows,
        decision="allow",
        tool=tool_name,
        target=target,
        category="valid-brief",
    )


def test_claude_edit_allows_relevant_file_with_valid_brief(tmp_path):
    """Manifest fixture: valid content, rather than headings alone, allows."""
    repo = init_repo(tmp_path)
    write_brief(repo, SUBSTANTIVE_BRIEF)
    payload, _ = edit_payload(repo, "Edit")

    result, output, _ = run_hook(CANONICAL_HOOK, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
def test_section_responsive_brief_passes_but_same_bodies_under_wrong_headings_fail(
    tmp_path, hook_path
):
    repo = init_repo(tmp_path)
    payload, _ = edit_payload(repo, "Write")
    write_brief(repo, CONCISE_VALID_BRIEF)

    valid_result, valid_output, _ = run_hook(hook_path, repo, payload)

    assert valid_result.returncode == 0, valid_result.stderr
    assert valid_output is None

    write_brief(repo, MISASSIGNED_SECTION_BRIEF)
    invalid_result, invalid_output, _ = run_hook(hook_path, repo, payload)

    assert_decision(invalid_result, invalid_output, "ask")


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize(
    "tool_name,path_key,target_relative,use_absolute", EDGE_PATH_SURFACES
)
def test_dotdot_path_is_classified_by_resolved_source_target(
    tmp_path, hook_path, tool_name, path_key, target_relative, use_absolute
):
    repo = init_repo(tmp_path)
    write_target(repo, target_relative)
    (repo / "docs").mkdir()
    relative_input = (Path("docs") / ".." / target_relative).as_posix()
    raw_path = str(repo / relative_input) if use_absolute else relative_input
    payload = path_payload(repo, tool_name, path_key, raw_path)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert_decision(result, output, "ask")
    assert_signal(
        rows,
        decision="ask",
        tool=tool_name,
        target=target_relative,
        category="missing-brief",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize(
    "tool_name,path_key,target_relative,use_absolute", EDGE_PATH_SURFACES
)
def test_symlink_under_exempt_directory_is_classified_by_resolved_source_target(
    tmp_path, hook_path, tool_name, path_key, target_relative, use_absolute
):
    repo = init_repo(tmp_path)
    target = write_target(repo, target_relative)
    alias_relative = f"docs/source-alias{Path(target_relative).suffix}"
    alias = repo / alias_relative
    alias.parent.mkdir(parents=True)
    alias.symlink_to(target)
    raw_path = str(alias) if use_absolute else alias_relative
    payload = path_payload(repo, tool_name, path_key, raw_path)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert_decision(result, output, "ask")
    assert_signal(
        rows,
        decision="ask",
        tool=tool_name,
        target=target_relative,
        category="missing-brief",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize(
    "tool_name,path_key,target_relative,use_absolute", EDGE_PATH_SURFACES
)
def test_nonexistent_dotdot_path_is_classified_by_normalized_source_target(
    tmp_path, hook_path, tool_name, path_key, target_relative, use_absolute
):
    repo = init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "src").mkdir()
    target_path = Path(target_relative)
    nonexistent_relative = target_path.with_name(f"new{target_path.suffix}").as_posix()
    target = repo / nonexistent_relative
    assert not target.exists()
    relative_input = (Path("docs") / ".." / nonexistent_relative).as_posix()
    raw_path = str(repo / relative_input) if use_absolute else relative_input
    payload = path_payload(repo, tool_name, path_key, raw_path)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert_decision(result, output, "ask")
    assert_signal(
        rows,
        decision="ask",
        tool=tool_name,
        target=nonexistent_relative,
        category="missing-brief",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize(
    "tool_name,path_key,target_relative,use_absolute", EDGE_PATH_SURFACES
)
def test_dangling_symlink_under_exempt_directory_uses_intended_source_target(
    tmp_path, hook_path, tool_name, path_key, target_relative, use_absolute
):
    repo = init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "src").mkdir()
    target_path = Path(target_relative)
    nonexistent_relative = target_path.with_name(f"new{target_path.suffix}").as_posix()
    alias_relative = f"docs/new-alias{target_path.suffix}"
    alias = repo / alias_relative
    alias.symlink_to(Path("..") / nonexistent_relative)
    assert alias.is_symlink()
    assert not alias.exists()
    raw_path = str(alias) if use_absolute else alias_relative
    payload = path_payload(repo, tool_name, path_key, raw_path)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert_decision(result, output, "ask")
    assert_signal(
        rows,
        decision="ask",
        tool=tool_name,
        target=nonexistent_relative,
        category="missing-brief",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize(
    "tool_name,path_key,target_relative,use_absolute", EDGE_PATH_SURFACES
)
def test_exempt_document_edit_is_untouched(
    tmp_path, hook_path, tool_name, path_key, target_relative, use_absolute
):
    repo = init_repo(tmp_path)
    docs_relative = "docs/exempt.md"
    target = write_target(repo, docs_relative)
    raw_path = str(target) if use_absolute else docs_relative
    payload = path_payload(repo, tool_name, path_key, raw_path)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert rows == []


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize(
    "tool_name,path_key,target_relative,use_absolute", EDGE_PATH_SURFACES
)
def test_nonexistent_document_directly_under_docs_remains_exempt(
    tmp_path, hook_path, tool_name, path_key, target_relative, use_absolute
):
    repo = init_repo(tmp_path)
    (repo / "docs").mkdir()
    docs_relative = "docs/new.md"
    target = repo / docs_relative
    assert not target.exists()
    raw_path = str(target) if use_absolute else docs_relative
    payload = path_payload(repo, tool_name, path_key, raw_path)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert rows == []


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
def test_outside_repository_target_is_untouched(tmp_path, hook_path):
    repo = init_repo(tmp_path / "repo")
    outside = write_target(tmp_path / "outside", "app.py")
    payload = write_payload(repo, str(outside), session_id="session-outside")

    result, output, rows = run_hook(hook_path, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert rows == []


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
def test_unsupported_path_payload_is_untouched(tmp_path, hook_path):
    repo = init_repo(tmp_path)
    target = write_target(repo, "src/app.py")
    payload = {
        "session_id": "session-unsupported",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"path": str(target)},
    }

    result, output, rows = run_hook(hook_path, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert rows == []


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
def test_non_registered_edit_tool_is_untouched(tmp_path, hook_path):
    repo = init_repo(tmp_path)
    target = write_target(repo, "src/app.py")
    payload = {
        "session_id": "session-read",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(target)},
    }

    result, output, rows = run_hook(hook_path, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert rows == []


def test_codex_commit_blocks_changed_code_without_brief(tmp_path):
    repo = init_repo(tmp_path)
    write_target(repo, "src/app.py")
    payload = landing_payload(repo, "git commit -m change")

    result, output, rows = run_hook(CANONICAL_HOOK, repo, payload)

    reason = assert_decision(result, output, "deny")
    assert "src/app.py" in reason
    assert "say 'proceed'" not in reason
    assert_signal(
        rows,
        decision="deny",
        tool="Bash",
        target="src/app.py",
        category="missing-brief",
    )


@pytest.mark.parametrize("hook_path", CODEX_HOOKS)
@pytest.mark.parametrize("command", LANDING_COMMANDS)
@pytest.mark.parametrize("brief_content", [None, MIXED_PLACEHOLDER_BRIEF], ids=["absent", "invalid"])
def test_every_landing_route_remains_hard_denied_for_absent_or_invalid_brief(
    tmp_path, hook_path, command, brief_content
):
    repo = init_repo(tmp_path)
    write_target(repo, "src/app.py")
    if brief_content is not None:
        write_brief(repo, brief_content)
    payload = landing_payload(repo, command)

    result, output, rows = run_hook(hook_path, repo, payload)

    reason = assert_decision(result, output, "deny")
    assert "say 'proceed'" not in reason
    assert_signal(
        rows,
        decision="deny",
        tool="Bash",
        target="src/app.py",
        category="missing-brief" if brief_content is None else "invalid-brief",
    )


@pytest.mark.parametrize("hook_path", CODEX_HOOKS)
@pytest.mark.parametrize("command", LANDING_COMMANDS)
@pytest.mark.parametrize(
    "brief_content",
    [PLAUSIBLE_NONSENSE_BRIEF, MISASSIGNED_SECTION_BRIEF],
    ids=["plausible-nonsense", "misassigned-headings"],
)
def test_every_landing_route_uses_the_same_semantic_brief_policy(
    tmp_path, hook_path, command, brief_content
):
    repo = init_repo(tmp_path)
    write_target(repo, "src/app.py")
    write_brief(repo, brief_content)
    payload = landing_payload(repo, command)

    result, output, rows = run_hook(hook_path, repo, payload)

    reason = assert_decision(result, output, "deny")
    assert "say 'proceed'" not in reason
    assert_signal(
        rows,
        decision="deny",
        tool="Bash",
        target="src/app.py",
        category="invalid-brief",
    )


def test_codex_commit_allows_changed_code_with_valid_brief(tmp_path):
    repo = init_repo(tmp_path)
    write_target(repo, "src/app.py")
    write_brief(repo, SUBSTANTIVE_BRIEF)
    payload = landing_payload(repo, "git commit -m change")

    result, output, rows = run_hook(CANONICAL_HOOK, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert_signal(
        rows,
        decision="allow",
        tool="Bash",
        target="src/app.py",
        category="valid-brief",
    )


@pytest.mark.parametrize("hook_path", CODEX_HOOKS)
@pytest.mark.parametrize("command", LANDING_COMMANDS)
def test_every_landing_route_allows_concise_meaningful_brief(
    tmp_path, hook_path, command
):
    repo = init_repo(tmp_path)
    write_target(repo, "src/app.py")
    write_brief(repo, CONCISE_VALID_BRIEF)
    payload = landing_payload(repo, command)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert_signal(
        rows,
        decision="allow",
        tool="Bash",
        target="src/app.py",
        category="valid-brief",
    )


@pytest.mark.parametrize("hook_path", CODEX_HOOKS)
def test_non_finishing_bash_is_untouched(tmp_path, hook_path):
    repo = init_repo(tmp_path)
    write_target(repo, "src/app.py")
    payload = landing_payload(repo, "pytest")

    result, output, rows = run_hook(hook_path, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert rows == []


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'title with x | y'",
        "git commit -m 'title with x && y'",
        "git commit -m 'title with x || y'",
        "git commit -m 'title with x; y'",
        "git -C . commit -m ok",
        "gh --repo owner/repo pr create --title 'x | y' --body body",
        "gh -R owner/repo pr merge 12",
        "gh --hostname github.example.com pr create --title title --body body",
        "if git commit -m x; then echo ok; fi",
        "echo `git commit -m x`",
        "echo $(git commit -m x)",
        "sh -c 'git commit -m x'",
        "env FOO=1 git commit -m x",
        "command git commit -m x",
    ],
)
def test_existing_complex_landing_invocations_remain_denied(tmp_path, command):
    repo = init_repo(tmp_path)
    write_target(repo, "src/app.py")
    payload = landing_payload(repo, command)

    result, output, rows = run_hook(CANONICAL_HOOK, repo, payload)

    assert_decision(result, output, "deny")
    assert len(rows) == 1


def test_missing_signal_support_fails_soft_with_visible_diagnostic(tmp_path):
    repo = init_repo(tmp_path / "repo")
    isolated_hook = tmp_path / "isolated" / CANONICAL_HOOK.name
    isolated_hook.parent.mkdir()
    shutil.copyfile(CANONICAL_HOOK, isolated_hook)
    for module_name in EXTRACTED_POLICY_MODULES:
        shutil.copyfile(
            ROOT / "claude" / "hooks" / module_name,
            isolated_hook.parent / module_name,
        )
    payload, _ = edit_payload(repo, "Write")

    result, output, rows = run_hook(isolated_hook, repo, payload)

    assert_decision(result, output, "ask")
    assert rows == []
    stderr_lines = result.stderr.strip().splitlines()
    assert len(stderr_lines) == 1
    assert "gate signal unavailable" in stderr_lines[0].lower()


def test_unwritable_signal_store_keeps_edit_ask_and_emits_one_diagnostic(tmp_path):
    repo = init_repo(tmp_path)
    (repo / SIGNAL_RELATIVE_PATH).mkdir()
    payload, _ = edit_payload(repo, "Write")

    result, output, rows = run_hook(CANONICAL_HOOK, repo, payload)

    assert_decision(result, output, "ask")
    assert rows == []
    stderr_lines = result.stderr.strip().splitlines()
    assert len(stderr_lines) == 1
    assert "gate signal unavailable" in stderr_lines[0].lower()


def test_unwritable_signal_store_keeps_landing_deny_and_emits_one_diagnostic(tmp_path):
    repo = init_repo(tmp_path)
    write_target(repo, "src/app.py")
    (repo / SIGNAL_RELATIVE_PATH).mkdir()
    payload = landing_payload(repo, "git commit -m change")

    result, output, rows = run_hook(CANONICAL_HOOK, repo, payload)

    assert_decision(result, output, "deny")
    assert rows == []
    stderr_lines = result.stderr.strip().splitlines()
    assert len(stderr_lines) == 1
    assert "gate signal unavailable" in stderr_lines[0].lower()
