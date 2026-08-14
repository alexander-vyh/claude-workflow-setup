from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
HOOK_DIR = ROOT / "claude" / "hooks"
BRIEF_PATH = Path(".agent/runtime/test-oracle-brief.md")
CANONICAL_HOOK = HOOK_DIR / "test_oracle_brief_gate.py"
PUBLIC_HOOKS = (
    pytest.param(CANONICAL_HOOK, id="canonical"),
    pytest.param(
        ROOT / "plugins/escapement-claude/hooks/test_oracle_brief_gate.py",
        id="rendered-claude",
    ),
    pytest.param(
        ROOT / "plugins/escapement/claude/hooks/test_oracle_brief_gate.py",
        id="rendered-codex",
    ),
)

PROTECTED_FIELDS = (
    "Authorization/security",
    "Money or sensitive data",
    "Production mutation",
    "Schema/migration",
    "Public contracts",
    "Irreversible external effects",
    "Shared infrastructure",
)
COMPACT_FIELDS = (
    "Outcome",
    "Independent source of truth",
    "Binding constraints",
    "Named fragile implementation",
    "Negative control",
    "Positive control",
    "Missing/unresolved handling",
    "Exact user-facing verification",
)
REVIEW_FIELDS = (
    "Focused proof result",
    "Objective blockers",
    "Known limitations",
    "Remaining landing proof",
)
INVARIANT_FIELDS = (
    "Outcome",
    "Independent source of truth",
    "Binding constraints",
    *PROTECTED_FIELDS,
    "Root cause",
)
CHALLENGE_FIELDS = (
    "Named fragile implementation",
    "Negative control",
    "Positive control",
    "Missing/unresolved handling",
)

FULL_BRIEF = """\
## Business invariant
Users receive the expected public hook decision.

## Independent source of truth
The public JSON decision and fixture inputs determine correctness.

## Solution constraints
Edit asks and landing denials must preserve existing host behavior.

## Invalid solution classes
A headings-only bypass is invalid even if a happy path passes.

## Fragile implementation to reject
Accepting any three headings without semantic fields must fail.

## Negative control
A protected compact brief must be rejected.

## Positive control
A valid full brief must continue to pass.

## Missing/unresolved handling
Missing evidence fails closed to the full workflow.

## Final outcome verification
Run the public hook fixture and inspect the returned JSON decision.
"""


def _load_module(name: str):
    path = HOOK_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = _load_module("test_oracle_brief_policy")
landing = _load_module("test_oracle_brief_landing")


def init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".beads").mkdir()
    source = tmp_path / "src/app.py"
    source.parent.mkdir()
    source.write_text("print('rapid')\n", encoding="utf-8")
    return tmp_path


def write_brief(repo: Path, content: str) -> None:
    path = repo / BRIEF_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def rapid_brief(
    *,
    overrides: dict[str, str] | None = None,
    omit: str | None = None,
    duplicate: tuple[str, str] | None = None,
    review_ready: bool = False,
    observed: str | None = None,
) -> str:
    values = {
        "Outcome": "Users can use a complete compact brief without losing the oracle.",
        "Independent source of truth": "Public hook JSON from independent fixture repositories determines correctness.",
        "Binding constraints": "Full briefs remain valid and protected work must fail closed.",
        **{field: "no" for field in PROTECTED_FIELDS},
        "Root cause": "Command: python3 -m pytest claude/hooks/tests/test_test_oracle_brief_rapid.py -q; Expected: only the nine-heading form is accepted; Actual: only the nine-heading form is accepted; Match: yes",
        "Named fragile implementation": "accept any document that merely has the three rapid headings",
        "Negative control": "an authorization/security value of unknown must reject rapid mode",
        "Positive control": "Command: python3 -m pytest claude/hooks/tests/test_test_oracle_brief_rapid.py -q; Expected: complete rapid brief returns an allow result",
        "Missing/unresolved handling": "missing or unknown evidence fails closed to the full form",
        "Exact user-facing verification": "Command: python3 -m pytest claude/hooks/tests/test_test_oracle_brief_rapid.py -q; Expected: valid rapid allows and protected rapid rejects",
    }
    if review_ready:
        values.update(
            {
                "Focused proof result": "Expected: valid rapid allows and protected rapid rejects; Actual: valid rapid allows and protected rapid rejects; Match: yes",
                "Objective blockers": "none",
                "Known limitations": "none",
                "Remaining landing proof": "Command: python3 -m pytest claude/hooks/tests/test_test_oracle_brief_rapid.py --installed -q; Expected: valid rapid allows and protected rapid rejects",
            }
        )
    if observed is not None:
        values["Observed result"] = observed
    values.update(overrides or {})

    invariant = [
        "Outcome",
        "Independent source of truth",
        "Binding constraints",
        *PROTECTED_FIELDS,
        "Root cause",
    ]
    challenge = (
        "Named fragile implementation",
        "Negative control",
        "Positive control",
        "Missing/unresolved handling",
    )
    proof = ["Exact user-facing verification"]
    if review_ready:
        proof.extend(
            (
                "Focused proof result",
                "Objective blockers",
                "Known limitations",
                "Remaining landing proof",
            )
        )
    if observed is not None:
        proof.append("Observed result")

    def render_section(heading: str, labels: tuple[str, ...] | list[str]) -> str:
        lines = [f"## {heading}"]
        for label in labels:
            if label == omit:
                continue
            lines.append(f"{label}: {values[label]}")
            if duplicate is not None and label == duplicate[0]:
                lines.append(f"{label}: {duplicate[1]}")
        return "\n".join(lines)

    return "\n\n".join(
        (
            render_section("Business invariant", invariant),
            render_section("Negative control", challenge),
            render_section("Final outcome verification", proof),
        )
    ) + "\n"


def status(repo: Path, brief: str, stage: str = "edit") -> tuple[bool, str | None, str]:
    write_brief(repo, brief)
    return policy.brief_status(repo, stage=stage)


def move_field(brief: str, label: str, destination: str) -> str:
    lines = brief.splitlines()
    source_index = next(index for index, line in enumerate(lines) if line.startswith(label + ":"))
    field_line = lines.pop(source_index)
    destination_index = lines.index(f"## {destination}") + 1
    lines.insert(destination_index, field_line)
    return "\n".join(lines) + "\n"


def assert_invalid_rapid(result: tuple[bool, str | None, str]) -> None:
    ok, reason, category = result
    assert ok is False
    assert category == "invalid-rapid-brief"
    assert reason is not None and "full" in reason.casefold()


def test_full_form_is_classified_before_rapid_form(tmp_path):
    repo = init_repo(tmp_path)

    assert status(repo, FULL_BRIEF, stage="final") == (True, None, "valid-brief")


def test_complete_rapid_form_is_valid_for_edit_and_durable_stages(tmp_path):
    repo = init_repo(tmp_path)
    brief = rapid_brief()

    assert status(repo, brief, stage="edit") == (True, None, "valid-rapid-brief")
    assert status(repo, brief, stage="durable") == (True, None, "valid-rapid-brief")


def test_three_headings_without_semantic_fields_kills_named_shortcut(tmp_path):
    repo = init_repo(tmp_path)
    headings_only = """\
## Business invariant
Users receive a result.
## Negative control
Missing work fails.
## Final outcome verification
Run the workflow.
"""

    assert_invalid_rapid(status(repo, headings_only))


def test_partial_hybrid_cannot_launder_an_incomplete_full_form(tmp_path):
    repo = init_repo(tmp_path)
    hybrid = FULL_BRIEF.split("## Positive control", 1)[0]

    ok, _reason, _category = status(repo, hybrid)
    assert ok is False


@pytest.mark.parametrize("field", COMPACT_FIELDS)
def test_missing_compact_semantic_field_fails_closed(tmp_path, field):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(status(repo, rapid_brief(omit=field)))


@pytest.mark.parametrize("field", COMPACT_FIELDS)
@pytest.mark.parametrize("placeholder", ("TBD", "N/A"))
def test_placeholder_compact_semantic_field_fails_closed(tmp_path, field, placeholder):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(status(repo, rapid_brief(overrides={field: placeholder})))


def test_duplicate_compact_semantic_content_fails_closed(tmp_path):
    repo = init_repo(tmp_path)
    repeated = "The same generic boilerplate claims the workflow is correct."

    assert_invalid_rapid(
        status(
            repo,
            rapid_brief(
                overrides={
                    "Independent source of truth": repeated,
                    "Binding constraints": repeated,
                }
            ),
        )
    )


@pytest.mark.parametrize("field", COMPACT_FIELDS)
def test_duplicate_compact_field_label_fails_closed(tmp_path, field):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(
        status(repo, rapid_brief(duplicate=(field, "a conflicting second answer")))
    )


@pytest.mark.parametrize(
    ("field", "destination"),
    (
        *((field, "Final outcome verification") for field in INVARIANT_FIELDS),
        *((field, "Business invariant") for field in CHALLENGE_FIELDS),
        ("Exact user-facing verification", "Negative control"),
    ),
)
def test_compact_field_under_wrong_section_fails_closed(tmp_path, field, destination):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(status(repo, move_field(rapid_brief(), field, destination)))


@pytest.mark.parametrize("field", (*PROTECTED_FIELDS, "Root cause"))
@pytest.mark.parametrize("case", ("affirmative", "unknown", "missing", "duplicate"))
def test_every_rapid_eligibility_field_fails_closed_for_nonvalid_dispositions(
    tmp_path, field, case
):
    repo = init_repo(tmp_path)
    valid = (
        "Command: python3 -m pytest rapid.py -q; Expected: legacy parser rejects rapid; "
        "Actual: legacy parser rejects rapid; Match: yes"
    )
    bad = {
        "affirmative": "unresolved" if field == "Root cause" else "yes",
        "unknown": "unknown",
        "missing": None,
        "duplicate": "unknown",
    }[case]
    brief = rapid_brief(
        omit=field if case == "missing" else None,
        overrides={} if bad is None else {field: bad},
        duplicate=(field, valid) if case == "duplicate" else None,
    )

    assert_invalid_rapid(status(repo, brief))


def test_rapid_lane_requires_concrete_controls_instead_of_inapplicability_claims(tmp_path):
    repo = init_repo(tmp_path)
    brief = rapid_brief(
        overrides={
            "Positive control": "Not applicable because this decision has no output collection that drop-all could suppress.",
            "Missing/unresolved handling": "Not applicable because the workflow reads no lookup or optional source data.",
        }
    )

    assert_invalid_rapid(status(repo, brief))


@pytest.mark.parametrize(
    "proof",
    (
        "Look at it later and confirm it seems fine.",
        "Inspect the change.",
        "Run tests.",
        "Verify behavior manually.",
        "Use `not-a-command` as the exact user-facing verification proof.",
        "Command: this is not executable; Expected: valid user outcome appears correctly",
        "Command: this sentence is --fictional; Expected: valid user outcome appears correctly",
        "Query: please select an account from the dropdown; Expected: eligible account appears correctly",
        "Query: SELECT decision FROM gate_results then ask an agent instead of executing it; Expected: one allow row",
        "Report: vague report words here; Expected: Eligibility column says eligible",
    ),
)
def test_planned_proof_must_name_an_executable_user_facing_flow(tmp_path, proof):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(
        status(repo, rapid_brief(overrides={"Exact user-facing verification": proof}))
    )


@pytest.mark.parametrize(
    "proof",
    (
        "Command: python3 -m pytest rapid_hook_fixture.py --case=valid; Expected: permissionDecision is allow",
        "Query: SELECT decision FROM gate_results WHERE case_id = 'rapid-valid'; Expected: one allow row",
        "API: GET /v1/rapid-path/status; Expected: 200 with eligible true",
        "Report: Run python3 -m rapid_path_report --case=valid; Expected: Eligibility column says eligible",
        "UI: Open Settings > Rapid Path and submit the valid fixture; Expected: Eligible banner appears",
    ),
)
def test_planned_proof_accepts_exact_public_flow_shapes(tmp_path, proof):
    repo = init_repo(tmp_path)

    assert status(
        repo, rapid_brief(overrides={"Exact user-facing verification": proof})
    ) == (True, None, "valid-rapid-brief")


def test_review_and_final_stages_require_progressively_stronger_proof(tmp_path):
    repo = init_repo(tmp_path)
    planned = rapid_brief()
    review_ready = rapid_brief(review_ready=True)
    observed = rapid_brief(
        review_ready=True,
        observed="Expected: valid rapid allows and protected rapid rejects; Actual: valid rapid allows and protected rapid rejects; Match: yes",
    )

    assert_invalid_rapid(status(repo, planned, stage="review"))
    assert status(repo, review_ready, stage="review") == (True, None, "valid-rapid-brief")
    assert_invalid_rapid(status(repo, review_ready, stage="final"))
    assert status(repo, observed, stage="final") == (True, None, "valid-rapid-brief")


def test_review_stage_rejects_an_objective_blocker(tmp_path):
    repo = init_repo(tmp_path)
    brief = rapid_brief(
        review_ready=True,
        overrides={"Objective blockers": "none — except the user-facing behavior still fails"},
    )

    assert_invalid_rapid(status(repo, brief, stage="review"))


def test_review_stage_ties_focused_proof_to_the_planned_outcome(tmp_path):
    repo = init_repo(tmp_path)
    brief = rapid_brief(
        review_ready=True,
        overrides={
            "Focused proof result": "Expected: two plus two equals four; Actual: two plus two equals four; Match: yes"
        },
    )

    assert_invalid_rapid(status(repo, brief, stage="review"))


def test_review_stage_does_not_launder_a_blocker_as_a_limitation(tmp_path):
    repo = init_repo(tmp_path)
    brief = rapid_brief(
        review_ready=True,
        overrides={"Known limitations": "The public user flow is still completely broken today."},
    )

    assert_invalid_rapid(status(repo, brief, stage="review"))


@pytest.mark.parametrize(
    ("field", "contradiction", "stage"),
    (
        ("Root cause", "known — the cause remains completely mysterious today", "edit"),
        (
            "Root cause",
            "Command: this sentence is --fictional; Expected: legacy parser accepts only full briefs; Actual: legacy parser accepts only full briefs; Match: yes",
            "edit",
        ),
        (
            "Objective blockers",
            "none — the actual public behavior still fails badly",
            "review",
        ),
    ),
)
def test_disposition_explanation_cannot_contradict_its_enum(
    tmp_path, field, contradiction, stage
):
    repo = init_repo(tmp_path)
    brief = rapid_brief(review_ready=stage == "review", overrides={field: contradiction})

    assert_invalid_rapid(status(repo, brief, stage=stage))


@pytest.mark.parametrize(
    ("field", "tautology"),
    (
        ("Positive control", "Not applicable because not applicable here."),
        (
            "Missing/unresolved handling",
            "No missing issue because none applies here.",
        ),
    ),
)
def test_inapplicability_claims_require_the_full_lane(tmp_path, field, tautology):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(status(repo, rapid_brief(overrides={field: tautology})))


@pytest.mark.parametrize(
    "control",
    (
        "Command: python3 -m pytest valid_case.py -q; Expected: valid output is never preserved and every result is discarded",
        "Valid output is always empty because every successful result is dropped",
    ),
)
def test_positive_control_rejects_a_negated_drop_all_control(tmp_path, control):
    repo = init_repo(tmp_path)
    brief = rapid_brief(
        overrides={
            "Positive control": control
        }
    )

    assert_invalid_rapid(status(repo, brief))


def test_observed_result_must_equal_the_planned_expected_result(tmp_path):
    repo = init_repo(tmp_path)
    brief = rapid_brief(
        review_ready=True,
        observed="Expected: valid request returns status 200; Actual: valid request returns status 500; Match: yes",
    )

    assert_invalid_rapid(status(repo, brief, stage="final"))


@pytest.mark.parametrize("field", REVIEW_FIELDS)
def test_review_stage_requires_every_review_readiness_field(tmp_path, field):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(status(repo, rapid_brief(review_ready=True, omit=field), stage="review"))


@pytest.mark.parametrize("field", REVIEW_FIELDS)
@pytest.mark.parametrize("placeholder", ("TBD", "N/A"))
def test_review_stage_rejects_placeholder_readiness_evidence(
    tmp_path, field, placeholder
):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(
        status(
            repo,
            rapid_brief(review_ready=True, overrides={field: placeholder}),
            stage="review",
        )
    )


@pytest.mark.parametrize("field", REVIEW_FIELDS)
def test_review_stage_rejects_duplicate_readiness_fields(tmp_path, field):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(
        status(
            repo,
            rapid_brief(review_ready=True, duplicate=(field, "a conflicting second answer")),
            stage="review",
        )
    )


def test_review_stage_rejects_repeated_readiness_boilerplate(tmp_path):
    repo = init_repo(tmp_path)
    repeated = "The same generic boilerplate says review can happen now."

    assert_invalid_rapid(
        status(
            repo,
            rapid_brief(
                review_ready=True,
                overrides={
                    "Known limitations": repeated,
                    "Remaining landing proof": repeated,
                },
            ),
            stage="review",
        )
    )


@pytest.mark.parametrize(
    "observed",
    (
        "",
        "TBD",
        "Will run the public workflow after merge.",
        "`python3 -m pytest claude/hooks/tests/test_test_oracle_brief_rapid.py -q`",
        "Tests pass.",
        "The user-facing result was not checked and remains unknown.",
    ),
)
def test_final_stage_rejects_nonobservations(tmp_path, observed):
    repo = init_repo(tmp_path)

    assert_invalid_rapid(
        status(repo, rapid_brief(review_ready=True, observed=observed), stage="final")
    )


@pytest.mark.parametrize(
    ("command", "stage"),
    (
        ("git commit -m rapid", "durable"),
        ("git push origin HEAD", "durable"),
        ("gh pr create --title rapid", "review"),
        ("gh pr merge --squash", "final"),
        ("bd close escapement-123", "final"),
        ("git commit -m rapid\ngh pr merge --squash", "final"),
        ("git commit -m rapid & gh pr create --title rapid", "review"),
        ("git commit -m rapid |& gh pr merge --squash", "final"),
        ("git commit -m rapid; eval gh pr merge --squash", "final"),
    ),
)
def test_finishing_commands_map_to_delivery_stage(command, stage):
    assert landing.landing_stage(command) == stage


def signal_rows(repo: Path) -> list[dict]:
    path = repo / ".beads/.gate-signal.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run_hook(hook_path: Path, repo: Path, payload: dict) -> tuple[dict | None, list[dict]]:
    before = signal_rows(repo)
    env = os.environ.copy()
    env["BEADS_DIR"] = str(repo / ".beads")
    env["CLAUDE_CODE_SESSION_ID"] = "rapid-public-fixture"
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
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout) if result.stdout.strip() else None
    return output, signal_rows(repo)[len(before) :]


def assert_signal(rows: list[dict], category: str, stage: str) -> None:
    assert len(rows) == 1
    assert rows[0]["gate"] == "test_oracle_brief_gate"
    assert rows[0]["extras"]["category"] == category
    assert rows[0]["extras"]["stage"] == stage


@pytest.mark.parametrize("hook_path", PUBLIC_HOOKS)
def test_public_hooks_apply_rapid_edit_durable_review_and_final_stages(tmp_path, hook_path):
    repo = init_repo(tmp_path)
    edit_payload = {
        "session_id": "rapid-public-fixture",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(repo / "src/app.py")},
    }

    write_brief(repo, rapid_brief())
    output, rows = run_hook(hook_path, repo, edit_payload)
    assert output is None
    assert_signal(rows, "valid-rapid-brief", "edit")
    durable = {**edit_payload, "tool_name": "Bash", "tool_input": {"command": "git commit -m rapid"}}
    output, rows = run_hook(hook_path, repo, durable)
    assert output is None
    assert_signal(rows, "valid-rapid-brief", "durable")

    review = {**durable, "tool_input": {"command": "gh pr create --title rapid"}}
    output, rows = run_hook(hook_path, repo, review)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert_signal(rows, "invalid-rapid-brief", "review")

    write_brief(repo, rapid_brief(review_ready=True))
    output, rows = run_hook(hook_path, repo, review)
    assert output is None
    assert_signal(rows, "valid-rapid-brief", "review")

    final = {**durable, "tool_input": {"command": "bd close escapement-123"}}
    output, rows = run_hook(hook_path, repo, final)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert_signal(rows, "invalid-rapid-brief", "final")

    write_brief(
        repo,
        rapid_brief(
            review_ready=True,
            observed="Expected: valid rapid allows and protected rapid rejects; Actual: valid rapid allows and protected rapid rejects; Match: yes",
        ),
    )
    output, rows = run_hook(hook_path, repo, final)
    assert output is None
    assert_signal(rows, "valid-rapid-brief", "final")


@pytest.mark.parametrize(
    "command",
    ("git commit -m rapid\ngh pr merge --squash", "git commit -m rapid; eval gh pr merge --squash"),
)
@pytest.mark.parametrize("hook_path", PUBLIC_HOOKS)
def test_public_hook_uses_strongest_stage_in_compound_command(tmp_path, hook_path, command):
    repo = init_repo(tmp_path)
    write_brief(repo, rapid_brief())
    payload = {
        "session_id": "rapid-public-fixture",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }

    output, rows = run_hook(hook_path, repo, payload)

    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert_signal(rows, "invalid-rapid-brief", "final")


@pytest.mark.parametrize("hook_path", PUBLIC_HOOKS)
def test_public_hook_uses_final_stage_after_pipe_stderr_operator(tmp_path, hook_path):
    repo = init_repo(tmp_path)
    write_brief(repo, rapid_brief())
    payload = {
        "session_id": "rapid-public-fixture",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m rapid |& gh pr merge --squash"},
    }

    output, rows = run_hook(hook_path, repo, payload)

    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert_signal(rows, "invalid-rapid-brief", "final")
