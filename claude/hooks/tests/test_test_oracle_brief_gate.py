from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


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
    if not path.exists():
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


def edit_payload(repo: Path, tool_name: str, invocation_id: str = "toolu_edit_123") -> tuple[dict, str]:
    path_key, relative_path = EDIT_TOOL_INPUTS[tool_name]
    target = write_target(repo, relative_path)
    path_value = relative_path if path_key == "relative_path" else str(target)
    return (
        {
            "session_id": "session-edit-abc",
            "tool_use_id": invocation_id,
            "cwd": str(repo),
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {path_key: path_value},
        },
        relative_path,
    )


def landing_payload(repo: Path, command: str, invocation_id: str = "toolu_land_123") -> dict:
    return {
        "session_id": "session-land-abc",
        "tool_use_id": invocation_id,
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
    invocation_id: str,
) -> None:
    assert len(rows) == 1, "one hook decision must append exactly one correlated signal"
    row = rows[0]
    assert row["gate"] == "test_oracle_brief_gate"
    assert row["decision"] == decision
    expected_session_id = "session-land-abc" if tool == "Bash" else "session-edit-abc"
    assert row["session_id"] == expected_session_id
    assert row["extras"]["tool"] == tool
    assert row["extras"]["target"] == target
    assert row["extras"]["category"] == category
    assert row["extras"]["invocation_id"] == invocation_id
    reason = row["reason"].lower()
    expected_reason_terms = {
        "missing-brief": ("missing", "test oracle brief"),
        "invalid-brief": ("test oracle brief", "required", "content"),
        "valid-brief": ("oracle brief", "valid"),
    }
    assert all(term in reason for term in expected_reason_terms[category]), reason


def test_manifest_payload_table_covers_every_registered_surface():
    assert set(_registered_tools("claude")) == set(EDIT_TOOL_INPUTS)
    assert _registered_tools("codex") == ("Bash",)


def test_claude_edit_blocks_relevant_file_without_brief(tmp_path):
    """Manifest fixture: the former hard deny must become an approvable ask."""
    repo = init_repo(tmp_path)
    payload, target = edit_payload(repo, "Write")

    result, output, rows = run_hook(CANONICAL_HOOK, repo, payload)

    reason = assert_decision(result, output, "ask")
    assert "proceed" in reason.lower()
    assert_signal(
        rows,
        decision="ask",
        tool="Write",
        target=target,
        category="missing-brief",
        invocation_id="toolu_edit_123",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize("tool_name", _registered_tools("claude"))
@pytest.mark.parametrize("brief_content", [None, MIXED_PLACEHOLDER_BRIEF], ids=["absent", "invalid"])
def test_every_registered_claude_edit_surface_asks_for_absent_or_invalid_brief(
    tmp_path, hook_path, tool_name, brief_content
):
    repo = init_repo(tmp_path)
    if brief_content is not None:
        write_brief(repo, brief_content)
    invocation_id = f"toolu_{tool_name}_456"
    payload, target = edit_payload(repo, tool_name, invocation_id)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert_decision(result, output, "ask")
    assert_signal(
        rows,
        decision="ask",
        tool=tool_name,
        target=target,
        category="missing-brief" if brief_content is None else "invalid-brief",
        invocation_id=invocation_id,
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize("brief_content", INVALID_BRIEFS)
def test_placeholder_or_incomplete_brief_never_satisfies_edit_gate(tmp_path, hook_path, brief_content):
    repo = init_repo(tmp_path)
    write_brief(repo, brief_content)
    payload, target = edit_payload(repo, "Write", "toolu_invalid_789")

    result, output, rows = run_hook(hook_path, repo, payload)

    assert_decision(result, output, "ask")
    assert_signal(
        rows,
        decision="ask",
        tool="Write",
        target=target,
        category="invalid-brief",
        invocation_id="toolu_invalid_789",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize("brief_content", SINGLE_PLACEHOLDER_BRIEFS)
def test_one_placeholder_section_invalidates_otherwise_substantive_brief(
    tmp_path, hook_path, brief_content
):
    repo = init_repo(tmp_path)
    write_brief(repo, brief_content)
    payload, target = edit_payload(repo, "Write", "toolu_one_placeholder")

    result, output, rows = run_hook(hook_path, repo, payload)

    assert_decision(result, output, "ask")
    assert_signal(
        rows,
        decision="ask",
        tool="Write",
        target=target,
        category="invalid-brief",
        invocation_id="toolu_one_placeholder",
    )


@pytest.mark.parametrize("hook_path", CLAUDE_HOOKS)
@pytest.mark.parametrize("tool_name", _registered_tools("claude"))
def test_substantive_brief_allows_every_registered_edit_surface(tmp_path, hook_path, tool_name):
    repo = init_repo(tmp_path)
    write_brief(repo, SUBSTANTIVE_BRIEF)
    invocation_id = f"toolu_valid_{tool_name}"
    payload, target = edit_payload(repo, tool_name, invocation_id)

    result, output, rows = run_hook(hook_path, repo, payload)

    assert result.returncode == 0, result.stderr
    assert output is None
    assert_signal(
        rows,
        decision="allow",
        tool=tool_name,
        target=target,
        category="valid-brief",
        invocation_id=invocation_id,
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
def test_exempt_document_edit_is_untouched(tmp_path, hook_path):
    repo = init_repo(tmp_path)
    target = write_target(repo, "docs/plan.md")
    payload = {
        "session_id": "session-doc",
        "tool_use_id": "toolu_doc",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(target)},
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
        "tool_use_id": "toolu_read",
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
        invocation_id="toolu_land_123",
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
    invocation_id = "toolu_route_456"
    payload = landing_payload(repo, command, invocation_id)

    result, output, rows = run_hook(hook_path, repo, payload)

    reason = assert_decision(result, output, "deny")
    assert "say 'proceed'" not in reason
    assert_signal(
        rows,
        decision="deny",
        tool="Bash",
        target="src/app.py",
        category="missing-brief" if brief_content is None else "invalid-brief",
        invocation_id=invocation_id,
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
        invocation_id="toolu_land_123",
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
    payload = landing_payload(repo, command, "toolu_complex")

    result, output, rows = run_hook(CANONICAL_HOOK, repo, payload)

    assert_decision(result, output, "deny")
    assert len(rows) == 1


def test_missing_signal_support_fails_soft_with_visible_diagnostic(tmp_path):
    repo = init_repo(tmp_path / "repo")
    isolated_hook = tmp_path / "isolated" / CANONICAL_HOOK.name
    isolated_hook.parent.mkdir()
    shutil.copyfile(CANONICAL_HOOK, isolated_hook)
    payload, _ = edit_payload(repo, "Write", "toolu_no_sink")

    result, output, rows = run_hook(isolated_hook, repo, payload)

    assert_decision(result, output, "ask")
    assert rows == []
    assert "gate signal unavailable" in result.stderr.lower()
