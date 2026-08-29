"""Independent contract tests for Escapement's mission and delegated authority.

The expected mission and capability order come from the approved OpenSpec change,
not from the renderer or identity JSON under test. Surface inventories are likewise
test-owned so a renderer cannot make a forgotten surface disappear from the oracle.
"""

from __future__ import annotations

import json
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "tools" / "render_agent_surfaces.py"
IDENTITY = Path("agent-surfaces/identity.json")
MANIFEST = Path("agent-surfaces/manifest.json")

APPROVED_MISSION = (
    "Escapement converts available agent capacity plus delegated authority into "
    "verified, delivered outcomes while reserving human attention for consequential choices."
)
APPROVED_CAPABILITIES = (
    "Intent and authority",
    "Design and specification",
    "Executable dependency-aware work breakdown",
    "Capacity allocation",
    "Isolated execution",
    "Action-local continuation and repair",
    "Independent outcome verification",
    "Authorized landing and delivery",
    "Learning and feedback",
)

MISSION_SURFACES = (
    Path("agent-surfaces/onboarding/shared.md"),
    Path("README.md"),
    Path("docs/VOCABULARY.md"),
    Path("docs/NAMING.md"),
    Path("docs/deck.html"),
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("plugins/escapement/.codex-plugin/plugin.json"),
    Path("plugins/escapement-claude/.claude-plugin/plugin.json"),
    Path(".claude-plugin/marketplace.json"),
)
GENERATED_IDENTITY_SURFACES = (
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("plugins/escapement/.codex-plugin/plugin.json"),
    Path("plugins/escapement-claude/.claude-plugin/plugin.json"),
    Path(".claude-plugin/marketplace.json"),
)
CAPABILITY_SURFACES = (
    IDENTITY,
    Path("agent-surfaces/onboarding/shared.md"),
    Path("README.md"),
    Path("docs/deck.html"),
)
PUBLIC_AUTHORED_SURFACES = (
    Path("README.md"),
    Path("docs/VOCABULARY.md"),
    Path("docs/NAMING.md"),
    Path("docs/deck.html"),
)
CORE_IDENTITY_START = "<!-- escapement:core-identity:start -->"
CORE_IDENTITY_END = "<!-- escapement:core-identity:end -->"
CORE_IDENTITY_SURFACES = (
    Path("agent-surfaces/onboarding/shared.md"),
    *PUBLIC_AUTHORED_SURFACES,
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
)
FORBIDDEN_CORE_TOKENS = (
    "OpenSpec",
    "Beads",
    "GitHub",
    "Git worktrees",
    "Git",
    "Claude Code",
    "Codex",
    "Pi",
    "OPA",
    "Cedar",
)
LEGACY_IDENTITY_FRAGMENTS = (
    "agentic workflow system built on top of",
    "host-neutral workflow layer",
    "OpenSpec, opinionated",
    "shared workflow for Claude Code and Codex",
    "workflow built on OpenSpec",
)
MUTABLE_COUNT_RE = re.compile(
    r"\b(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|forty-six)\s*(?:-|\s)?(?:step|steps|skill|skills|hook|hooks|"
    r"hook scripts|human gates)\b",
    re.IGNORECASE,
)

EXPECTED_SUPPORT_CLAIMS = {
    "merge-green-status": "unsupported",
    "confirm-class-enforcement": "reserved",
    "deploy-execution": "informational",
    "codex-final-response-interception": "guidance-only",
}
EXPECTED_SUPPORT_REASONS = {
    "merge-green-status": (
        "The merge authorization hook resolves repository-declared merge authority but "
        "does not observe pull-request check or green status."
    ),
    "confirm-class-enforcement": (
        "Repository confirmation classes are stored but are not currently enforced by "
        "the merge authorization hook."
    ),
    "deploy-execution": (
        "Repository deploy metadata is surfaced as outcome context and does not execute "
        "or independently authorize a deployment command."
    ),
    "codex-final-response-interception": (
        "The installed Codex adapter exposes no Stop or final-response hook; durable work "
        "state and SessionStart guidance support continuation without native interception."
    ),
}
EXPECTED_ADAPTER_MAPPING = {
    "Design and specification": ["OpenSpec"],
    "Executable dependency-aware work breakdown": ["Beads"],
    "Isolated execution": ["Git worktrees"],
    "Capacity allocation": ["Claude Code", "Codex"],
    "Authorized landing and delivery": ["GitHub"],
}
ADAPTER_MAPPING_START = "<!-- escapement:adapter-mapping:start -->"
ADAPTER_MAPPING_END = "<!-- escapement:adapter-mapping:end -->"
ADAPTER_MAPPING_SURFACES = (
    Path("agent-surfaces/onboarding/shared.md"),
    Path("README.md"),
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
)
SUPPORT_REGION_START = "<!-- escapement:support-claims:start"
SUPPORT_REGION_END = "<!-- escapement:support-claims:end -->"
SUPPORT_CLAIM_SURFACES = (
    Path("agent-surfaces/onboarding/authority.md"),
    Path("README.md"),
    Path("docs/VOCABULARY.md"),
    Path("docs/deck.html"),
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("claude/rules/outcome-ownership.md"),
    Path("claude/rules/continuation-harness.md"),
)
PARAPHRASED_FALSE_SUPPORT_CLAIMS = (
    "The merge gate checks GitHub PR status and blocks every merge until required checks pass.",
    "A non-empty confirmation class is actively required before the merge command can proceed.",
    "The configured deployment metadata runs its command automatically after merge.",
    "The Codex adapter catches final answers and forces the session to resume unfinished work.",
    "The merge gate rejects red pull requests.",
    "Every confirm_class demands confirmation before merge.",
    "Repository policy launches the deployment command after landing.",
    "Codex automatically resumes unfinished work when an agent tries to end the session.",
    "The merge gate does not merely report red CI; it prevents the merge.",
    "Escapement refuses merges unless GitHub reports every required check successful.",
    "Every configured confirmation class is honored before a merge.",
    "The deployment configuration launches the documented release command once the pull request lands.",
    "In Codex, an attempted final reply is rejected and the agent is resumed.",
    "Escapement permits landing only after CI succeeds.",
    "A configured confirmation category forces human acknowledgement before landing.",
    "The repository declaration initiates its release after landing.",
    "Codex reopens unfinished execution whenever the agent concludes its reply.",
)

FALSE_PUBLIC_CORE_DEFINITIONS = (
    "Escapement is fundamentally built on OpenSpec, which defines the product itself.",
    "Beads owns and governs the workflow core.",
    "Git worktrees are the foundation of the Escapement system.",
    "Without OpenSpec there is no Escapement.",
    "Escapement identity comes from Beads.",
    "Claude Code is indispensable to Escapement.",
    "OpenSpec is the engine at Escapement's heart.",
)

REQUIRED_WINDDOWN_ORDINARY_CLASSES = {
    "worktree-create",
    "inspect-edit",
    "test-lint-build",
    "commit",
    "push",
    "pull-request",
    "ci-review-repair",
    "merge",
    "deploy-verify",
}
REQUIRED_WINDDOWN_BOUNDARY_CLASSES = {
    "changed-intent",
    "material-outcome-tradeoff",
    "undelegated-repository",
    "undelegated-account-audience",
    "new-privilege-credential",
    "destructive-shared-effect",
    "enforced-confirmation-class",
    "unsafe-owner-overlap",
    "missing-landing-path",
}

FALSE_SUPPORT_CLAIM_MUTATIONS = (
    (
        "merge-green-status",
        "Escapement mechanically verifies that every pull request is green before allowing merge.",
        (
            (Path("agent-surfaces/onboarding/authority.md"), Path("agent-surfaces/onboarding/authority.md")),
            (Path("README.md"), Path("README.md")),
            (Path("agent-surfaces/onboarding/authority.md"), Path("AGENTS.md")),
            (Path("agent-surfaces/onboarding/authority.md"), Path("CLAUDE.md")),
            (Path("claude/rules/outcome-ownership.md"), Path("claude/rules/outcome-ownership.md")),
            (Path("claude/rules/continuation-harness.md"), Path("claude/rules/continuation-harness.md")),
        ),
    ),
    (
        "confirm-class-enforcement",
        "Escapement currently enforces every configured confirm_class before merge.",
        (
            (Path("agent-surfaces/onboarding/authority.md"), Path("agent-surfaces/onboarding/authority.md")),
            (Path("README.md"), Path("README.md")),
            (Path("agent-surfaces/onboarding/authority.md"), Path("AGENTS.md")),
            (Path("agent-surfaces/onboarding/authority.md"), Path("CLAUDE.md")),
            (Path("claude/rules/outcome-ownership.md"), Path("claude/rules/outcome-ownership.md")),
            (Path("claude/rules/continuation-harness.md"), Path("claude/rules/continuation-harness.md")),
        ),
    ),
    (
        "deploy-execution",
        "Repository deploy metadata authorizes and executes the configured deployment command.",
        (
            (Path("agent-surfaces/onboarding/authority.md"), Path("agent-surfaces/onboarding/authority.md")),
            (Path("README.md"), Path("README.md")),
            (Path("docs/VOCABULARY.md"), Path("docs/VOCABULARY.md")),
            (Path("docs/deck.html"), Path("docs/deck.html")),
            (Path("agent-surfaces/onboarding/authority.md"), Path("AGENTS.md")),
            (Path("agent-surfaces/onboarding/authority.md"), Path("CLAUDE.md")),
            (Path("claude/rules/continuation-harness.md"), Path("claude/rules/continuation-harness.md")),
        ),
    ),
    (
        "codex-final-response-interception",
        "Codex mechanically intercepts final responses and prevents premature stopping.",
        (
            (Path("agent-surfaces/onboarding/hosts/codex.md"), Path("agent-surfaces/onboarding/hosts/codex.md")),
            (Path("README.md"), Path("README.md")),
            (Path("docs/deck.html"), Path("docs/deck.html")),
            (Path("agent-surfaces/onboarding/hosts/codex.md"), Path("AGENTS.md")),
        ),
    ),
)

DOCTRINE_REQUIREMENTS = {
    Path("agent-surfaces/onboarding/authority.md"): (
        "Delegating an outcome delegates its ordinary means",
        "causally blocks",
        "adjacent discoveries",
        "independent authorized work",
        "input_required",
    ),
    Path("claude/rules/outcome-ownership.md"): (
        "causally blocks the delegated outcome",
        "adjacent discoveries",
    ),
    Path("claude/rules/agent-teams-default.md"): (
        "causally blocks the delegated outcome",
        "adjacent discoveries",
    ),
    Path("claude/rules/molecule-awareness.md"): (
        "unresolved consequential choice",
        "already included in the delegated outcome",
    ),
    Path("claude/rules/continuation-harness.md"): (
        "blocks only that action and its dependents",
        "independent authorized work continues",
        "reserved and not currently enforced",
    ),
    Path("harness/bin/winddown_judge.py"): (
        "already authorized by the delegated outcome",
        "not categorically human-only",
    ),
    Path("harness/bin/winddown_gate.py"): (
        "already authorized by the delegated outcome",
        "not categorically human-only",
    ),
    Path("agent-surfaces/openspec/apply.md"): (
        "Task Blocked; Independent Work Continuing",
        "block only that task and its dependents",
        "every remaining authorized route",
    ),
    Path("agent-surfaces/openspec/explore.md"): (
        "delegated outcome already includes formalization",
        "without soliciting scope expansion",
    ),
    Path("claude/skills/beads-execution/SKILL.md"): (
        "block only the affected task",
        "continue independent ready tasks",
        "every remaining route",
    ),
}


def _copy_repo(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    shutil.copytree(
        ROOT,
        target,
        ignore=shutil.ignore_patterns(
            ".git",
            ".worktrees",
            ".agent-surface-stage-*",
            "__pycache__",
            ".pytest_cache",
        ),
    )
    return target


def _run_renderer(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "tools" / "render_agent_surfaces.py"), *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def _load_renderer_module(root: Path = ROOT):
    module_name = f"mission_contract_renderer_{id(root)}"
    spec = importlib.util.spec_from_file_location(module_name, RENDERER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _rendered_targets(root: Path) -> dict[Path, str]:
    module = _load_renderer_module(root)
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    identity = json.loads((root / IDENTITY).read_text(encoding="utf-8"))
    return module.rendered_targets(root, manifest, identity)


def _text(root: Path, relative: Path) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _assert_ordered(text: str, fragments: tuple[str, ...], path: Path) -> None:
    cursor = -1
    for fragment in fragments:
        position = text.find(fragment, cursor + 1)
        assert position > cursor, f"{path} missing or reorders capability: {fragment}"
        cursor = position


def test_identity_contract_matches_approved_semantics():
    identity = json.loads((ROOT / IDENTITY).read_text(encoding="utf-8"))
    assert identity["mission"] == APPROVED_MISSION
    assert tuple(identity["capabilities"]) == APPROVED_CAPABILITIES


@pytest.mark.parametrize("surface", MISSION_SURFACES, ids=str)
def test_all_identity_bearing_surfaces_agree(surface: Path):
    content = _text(ROOT, surface)
    assert APPROVED_MISSION in content, f"{surface} is missing the canonical mission"
    for legacy in LEGACY_IDENTITY_FRAGMENTS:
        assert legacy.lower() not in content.lower(), f"{surface} retains legacy identity: {legacy}"


@pytest.mark.parametrize("surface", MISSION_SURFACES, ids=str)
def test_each_identity_surface_mutation_fails_and_names_path(tmp_path: Path, surface: Path):
    root = _copy_repo(tmp_path)
    path = root / surface
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            APPROVED_MISSION,
            "Escapement is a workflow built on OpenSpec.",
            1,
        ),
        encoding="utf-8",
    )
    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert surface.as_posix() in result.stderr


def test_renderer_consumes_canonical_identity_source(tmp_path: Path):
    root = _copy_repo(tmp_path)
    sentinel = (
        "Escapement converts delegated intent and available capacity into sentinel-verified "
        "outcomes while preserving consequential human judgment."
    )
    identity_path = root / IDENTITY
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["mission"] = sentinel
    identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")

    targets = _rendered_targets(root)
    for surface in GENERATED_IDENTITY_SURFACES:
        assert sentinel in targets[root / surface], f"renderer ignored identity source for {surface}"


def test_renderer_consumes_canonical_capability_source(tmp_path: Path):
    """Break caught: generated instructions copy capability prose instead of identity data."""

    root = _copy_repo(tmp_path)
    sentinel = "Sentinel design and specification"
    identity_path = root / IDENTITY
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["capabilities"][1] = sentinel
    identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")

    targets = _rendered_targets(root)
    for surface in (Path("AGENTS.md"), Path("CLAUDE.md")):
        assert sentinel in targets[root / surface], f"renderer ignored capabilities for {surface}"


@pytest.mark.parametrize("surface", CAPABILITY_SURFACES, ids=str)
def test_each_capability_surface_contains_ordered_chain(surface: Path):
    _assert_ordered(_text(ROOT, surface), APPROVED_CAPABILITIES, surface)


@pytest.mark.parametrize("surface", CAPABILITY_SURFACES, ids=str)
def test_each_capability_surface_mutation_fails_and_names_path(tmp_path: Path, surface: Path):
    root = _copy_repo(tmp_path)
    path = root / surface
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace(APPROVED_CAPABILITIES[1], "Generic planning", 1), encoding="utf-8")
    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert surface.as_posix() in result.stderr


@pytest.mark.parametrize("token", FORBIDDEN_CORE_TOKENS)
def test_core_identity_rejects_tool_and_client_names(tmp_path: Path, token: str):
    root = _copy_repo(tmp_path)
    identity_path = root / IDENTITY
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["mission"] = f"{identity['mission']} Powered by {token}."
    identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "agent-surfaces/identity.json" in result.stderr


@pytest.mark.parametrize(
    "token",
    ("OpenSpec", "Beads", "Git worktrees", "GitHub", "Claude Code", "Codex", "Pi", "OPA", "Cedar"),
)
@pytest.mark.parametrize("field", ("mission", "short_description", "operating_model", "principles"))
def test_all_core_identity_fields_reject_adapter_ownership(
    tmp_path: Path, field: str, token: str
):
    """Break caught: a non-mission core field makes a replaceable tool the authority."""

    root = _copy_repo(tmp_path)
    identity_path = root / IDENTITY
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    false_core_claim = f"{token} owns every workflow decision."
    if isinstance(identity[field], list):
        identity[field].append(false_core_claim)
    else:
        identity[field] = false_core_claim
    identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")

    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "agent-surfaces/identity.json" in result.stderr


def test_core_token_matching_does_not_reject_incidental_substrings(tmp_path: Path):
    root = _copy_repo(tmp_path)
    identity_path = root / IDENTITY
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["principles"].append("Pipeline flow stays bounded by delegated authority")
    identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")

    result = _run_renderer(root, "--check")
    assert result.returncode == 0, result.stderr


def test_adapter_sections_preserve_current_tool_mappings():
    identity = json.loads((ROOT / IDENTITY).read_text(encoding="utf-8"))
    assert identity["current_adapters"] == EXPECTED_ADAPTER_MAPPING
    mapping_text = json.dumps(identity["current_adapters"])
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for token in ("OpenSpec", "Beads", "Git worktrees", "Claude Code", "Codex", "GitHub"):
        assert token in mapping_text
        assert token in readme


@pytest.mark.parametrize("surface", ADAPTER_MAPPING_SURFACES, ids=str)
def test_each_adapter_mapping_surface_preserves_exact_pairs(surface: Path):
    content = _text(ROOT, surface)
    assert ADAPTER_MAPPING_START in content and ADAPTER_MAPPING_END in content
    region = content.split(ADAPTER_MAPPING_START, 1)[1].split(ADAPTER_MAPPING_END, 1)[0]
    lines = [line for line in region.splitlines() if line.strip()]
    for capability, tools in EXPECTED_ADAPTER_MAPPING.items():
        matching = [line for line in lines if capability in line]
        assert len(matching) == 1, f"{surface} must map {capability} exactly once"
        assert all(tool in matching[0] for tool in tools), (
            f"{surface} maps {capability} to the wrong adapter"
        )


@pytest.mark.parametrize("surface", ADAPTER_MAPPING_SURFACES[:2], ids=str)
def test_adapter_mapping_region_rejects_surplus_capability(surface: Path, tmp_path: Path):
    root = _copy_repo(tmp_path)
    path = root / surface
    content = path.read_text(encoding="utf-8")
    before, remainder = content.split(ADAPTER_MAPPING_END, 1)
    path.write_text(
        f"{before}\n- Learning and feedback | GitHub\n{ADAPTER_MAPPING_END}{remainder}",
        encoding="utf-8",
    )

    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert surface.as_posix() in result.stderr


@pytest.mark.parametrize("surface", ADAPTER_MAPPING_SURFACES[:2], ids=str)
def test_public_adapter_tool_swap_fails_and_names_surface(tmp_path: Path, surface: Path):
    root = _copy_repo(tmp_path)
    path = root / surface
    content = path.read_text(encoding="utf-8")
    before, remainder = content.split(ADAPTER_MAPPING_START, 1)
    region, after = remainder.split(ADAPTER_MAPPING_END, 1)
    region = region.replace("OpenSpec", "__LANDING__").replace("GitHub", "OpenSpec")
    region = region.replace("__LANDING__", "GitHub")
    path.write_text(
        f"{before}{ADAPTER_MAPPING_START}{region}{ADAPTER_MAPPING_END}{after}",
        encoding="utf-8",
    )
    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert surface.as_posix() in result.stderr


@pytest.mark.parametrize("position", ("inside", "after"))
@pytest.mark.parametrize("false_claim", FALSE_PUBLIC_CORE_DEFINITIONS)
def test_public_core_definition_paraphrases_fail_validation(
    tmp_path: Path, false_claim: str, position: str
):
    """Break caught: public prose promotes a current adapter into product identity."""

    root = _copy_repo(tmp_path)
    path = root / "README.md"
    content = path.read_text(encoding="utf-8")
    if position == "inside":
        before, after = content.split(CORE_IDENTITY_END, 1)
        content = f"{before}\n{false_claim}\n{CORE_IDENTITY_END}{after}"
    else:
        content = f"{content}\n{false_claim}\n"
    path.write_text(content, encoding="utf-8")
    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "README.md" in result.stderr


@pytest.mark.parametrize("surface", CORE_IDENTITY_SURFACES, ids=str)
def test_core_identity_regions_are_explicit_and_adapter_neutral(surface: Path):
    content = _text(ROOT, surface)
    assert content.count(CORE_IDENTITY_START) == 1
    assert content.count(CORE_IDENTITY_END) == 1
    region = content.split(CORE_IDENTITY_START, 1)[1].split(CORE_IDENTITY_END, 1)[0]
    for token in FORBIDDEN_CORE_TOKENS:
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", region, re.I) is None


@pytest.mark.parametrize("surface", CORE_IDENTITY_SURFACES[:2], ids=str)
def test_adapter_named_inside_core_identity_region_fails(surface: Path, tmp_path: Path):
    root = _copy_repo(tmp_path)
    path = root / surface
    content = path.read_text(encoding="utf-8")
    before, after = content.split(CORE_IDENTITY_END, 1)
    path.write_text(
        f"{before}\nOpenSpec is the engine at Escapement's heart.\n"
        f"{CORE_IDENTITY_END}{after}",
        encoding="utf-8",
    )

    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert surface.as_posix() in result.stderr


def test_canonical_mission_must_be_inside_core_region(tmp_path: Path):
    root = _copy_repo(tmp_path)
    path = root / "README.md"
    content = path.read_text(encoding="utf-8")
    region = content.split(CORE_IDENTITY_START, 1)[1].split(CORE_IDENTITY_END, 1)[0]
    path.write_text(
        content.replace(region, "\nEscapement coordinates work.\n", 1)
        + f"\n{APPROVED_MISSION}\n",
        encoding="utf-8",
    )

    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "README.md" in result.stderr


def test_wrong_capability_to_adapter_mapping_fails_validation(tmp_path: Path):
    """Break caught: tools remain present but are assigned authority they do not own."""

    root = _copy_repo(tmp_path)
    identity_path = root / IDENTITY
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["current_adapters"]["Design and specification"] = ["GitHub"]
    identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")

    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "agent-surfaces/identity.json" in result.stderr


@pytest.mark.parametrize("surface,fragments", DOCTRINE_REQUIREMENTS.items(), ids=lambda item: str(item))
def test_delegated_authority_and_action_local_continuation(surface: Path, fragments: tuple[str, ...]):
    # Whitespace-normalized: these are prose fragments, and the surfaces are
    # hard-wrapped markdown. Matching raw text made a fragment "missing" the
    # moment a reflow moved a line break through the middle of it, which says
    # nothing about whether the doctrine is still stated.
    content = " ".join(_text(ROOT, surface).split())
    for fragment in fragments:
        assert " ".join(fragment.split()) in content, (
            f"{surface} missing doctrine fragment: {fragment}"
        )


def test_support_claims_match_executed_point_of_effect_controls(tmp_path: Path):
    manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    actual = {claim["id"]: claim["status"] for claim in manifest["support_claims"]}
    assert actual == EXPECTED_SUPPORT_CLAIMS

    repo = tmp_path / "repo"
    (repo / ".escapement").mkdir(parents=True)
    deploy_sentinel = tmp_path / "deploy-command-ran"
    declaration = {
        "intended_outcome": "merged-and-deployed",
        "auto_merge_on_green": True,
        "confirm_class": ["db-migration"],
        "deploy": {"command": f"touch {deploy_sentinel}"},
    }
    (repo / ".escapement" / "repo.json").write_text(json.dumps(declaration), encoding="utf-8")
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge 123 --squash"},
        "cwd": str(repo),
    }
    merge = subprocess.run(
        [sys.executable, "-B", str(ROOT / "claude/hooks/merge_authorization_gate.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert merge.returncode == 0 and merge.stdout == "", (
        "current gate unexpectedly observed green status or enforced confirm_class"
    )
    assert not deploy_sentinel.exists(), "merge authorization executed informational deploy metadata"

    probe = subprocess.run(
        [sys.executable, "-B", str(ROOT / "claude/hooks/codex_final_response_gap.py")],
        input=json.dumps({"hook_event_name": "SessionStart"}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert probe.returncode == 0
    assert "no Stop/final-response hook" in probe.stdout
    codex_hooks = json.loads((ROOT / "plugins/escapement/hooks/hooks.json").read_text())["hooks"]
    assert "Stop" not in codex_hooks

    sys.path.insert(0, str(ROOT / "harness/bin"))
    import repo_outcome  # type: ignore

    with_deploy = repo_outcome.resolve(repo)
    declaration.pop("deploy")
    (repo / ".escapement" / "repo.json").write_text(json.dumps(declaration), encoding="utf-8")
    without_deploy = repo_outcome.resolve(repo)
    assert with_deploy.deploy == {"command": f"touch {deploy_sentinel}"}
    assert without_deploy.deploy is None
    assert repo_outcome.authorizes_auto_merge(with_deploy) == repo_outcome.authorizes_auto_merge(
        without_deploy
    )


def test_support_manifest_reasons_cannot_self_certify_false_behavior(tmp_path: Path):
    root = _copy_repo(tmp_path)
    manifest_path = root / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["support_claims"][0]["reason"] = (
        "Escapement enforces green pull-request status and prevents merge when CI is red."
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "agent-surfaces/manifest.json" in result.stderr


def test_support_manifest_rejects_duplicate_claim_id(tmp_path: Path):
    root = _copy_repo(tmp_path)
    manifest_path = root / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["support_claims"].append(dict(manifest["support_claims"][0]))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "duplicate support claim" in result.stderr


def test_reserved_confirm_class_is_not_documented_as_enforced_runtime_behavior():
    repo_outcome_source = _text(ROOT, Path("harness/bin/repo_outcome.py"))
    authoring_source = _text(ROOT, Path("harness/bin/set_repo_outcome.py"))
    assert "narrow set that still asks" not in repo_outcome_source
    assert "still draws one confirm" not in authoring_source
    assert "reserved metadata; not yet enforced" in repo_outcome_source
    assert "stored but not currently enforced" in authoring_source
    assert "confirm_class_absolute" not in repo_outcome_source


@pytest.mark.parametrize("surface", SUPPORT_CLAIM_SURFACES, ids=str)
def test_support_claim_surfaces_carry_structured_status_region(surface: Path):
    content = _text(ROOT, surface)
    assert SUPPORT_REGION_START in content and SUPPORT_REGION_END in content
    region = content.split(SUPPORT_REGION_START, 1)[1].split(SUPPORT_REGION_END, 1)[0]
    for claim_id, status in EXPECTED_SUPPORT_CLAIMS.items():
        assert f"{claim_id}={status}" in region, f"{surface} omits {claim_id}={status}"
        reason = EXPECTED_SUPPORT_REASONS[claim_id]
        assert f"{claim_id}-reason={reason}" in region, (
            f"{surface} omits the evidence reason for {claim_id}"
        )


def test_support_claim_region_rejects_unstructured_extra_claim(tmp_path: Path):
    root = _copy_repo(tmp_path)
    path = root / "README.md"
    content = path.read_text(encoding="utf-8")
    before, after = content.split(SUPPORT_REGION_END, 1)
    path.write_text(
        f"{before}\nEscapement refuses merges unless CI succeeds.\n"
        f"{SUPPORT_REGION_END}{after}",
        encoding="utf-8",
    )

    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "README.md" in result.stderr


def test_support_claim_surface_rejects_duplicate_region(tmp_path: Path):
    root = _copy_repo(tmp_path)
    path = root / "README.md"
    content = path.read_text(encoding="utf-8")
    start = content.index(SUPPORT_REGION_START)
    end = content.index(SUPPORT_REGION_END, start) + len(SUPPORT_REGION_END)
    path.write_text(content + "\n" + content[start:end] + "\n", encoding="utf-8")

    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "README.md" in result.stderr


def test_beads_execution_finish_follows_repository_declared_outcome():
    surfaces = (
        Path("claude/skills/beads-execution/SKILL.md"),
        Path("plugins/escapement-claude/skills/beads-execution/SKILL.md"),
    )
    forbidden = (
        "continue with the next ready task or stop",
        "PR-only",
        "never merges to main",
        "Do **not** merge to main",
        "never merge to main",
    )
    for surface in surfaces:
        content = _text(ROOT, surface)
        for phrase in forbidden:
            assert phrase not in content, f"{surface} retains stop-oriented finish: {phrase}"
        assert ".escapement/repo.json" in content
        assert "harness/bin/repo_outcome.py" in content
        assert "merged-and-deployed" in content


@pytest.mark.parametrize("position", ("before", "inside", "after"))
@pytest.mark.parametrize("false_claim", PARAPHRASED_FALSE_SUPPORT_CLAIMS)
def test_paraphrased_false_support_claims_fail_validation(
    tmp_path: Path, false_claim: str, position: str
):
    """Break caught: unsupported prose is inserted into the authoritative claim region."""

    root = _copy_repo(tmp_path)
    path = root / "README.md"
    content = path.read_text(encoding="utf-8")
    if position == "inside":
        before, after = content.split(SUPPORT_REGION_END, 1)
        content = f"{before}\n{false_claim}\n{SUPPORT_REGION_END}{after}"
    elif position == "before":
        before, after = content.split(SUPPORT_REGION_START, 1)
        content = f"{before}\n{false_claim}\n{SUPPORT_REGION_START}{after}"
    else:
        content = f"{content}\n{false_claim}\n"
    path.write_text(content, encoding="utf-8")
    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert "README.md" in result.stderr


@pytest.mark.parametrize(
    "claim_id,false_claim,mutation_surface,expected_surface",
    [
        (claim_id, false_claim, mutation_surface, expected_surface)
        for claim_id, false_claim, surface_pairs in FALSE_SUPPORT_CLAIM_MUTATIONS
        for mutation_surface, expected_surface in surface_pairs
    ],
    ids=lambda value: str(value),
)
def test_false_support_claim_mutations_fail_and_name_path(
    tmp_path: Path,
    claim_id: str,
    false_claim: str,
    mutation_surface: Path,
    expected_surface: Path,
):
    root = _copy_repo(tmp_path)
    path = root / expected_surface
    content = path.read_text(encoding="utf-8")
    if SUPPORT_REGION_END in content:
        before, after = content.split(SUPPORT_REGION_END, 1)
        content = f"{before}\n{false_claim}\n{SUPPORT_REGION_END}{after}"
    else:
        content = f"{content}\n{false_claim}\n"
    path.write_text(content, encoding="utf-8")
    result = _run_renderer(root, "--check")
    assert result.returncode != 0, f"validator accepted false support claim {claim_id}"
    assert expected_surface.as_posix() in result.stderr


@pytest.mark.parametrize("surface", PUBLIC_AUTHORED_SURFACES, ids=str)
def test_authored_surfaces_have_no_hand_maintained_inventory_counts(surface: Path):
    match = MUTABLE_COUNT_RE.search(_text(ROOT, surface))
    assert match is None, f"{surface} contains mutable inventory count: {match.group(0) if match else ''}"


@pytest.mark.parametrize("surface", PUBLIC_AUTHORED_SURFACES, ids=str)
@pytest.mark.parametrize("mutant", ("eight skills", "46 hooks", "9 steps", "9-step workflow"))
def test_count_mutations_fail_and_name_path(tmp_path: Path, surface: Path, mutant: str):
    root = _copy_repo(tmp_path)
    path = root / surface
    path.write_text(path.read_text(encoding="utf-8") + f"\n{mutant}\n", encoding="utf-8")
    result = _run_renderer(root, "--check")
    assert result.returncode != 0
    assert surface.as_posix() in result.stderr


def test_invalid_identity_does_not_delete_existing_generated_tree(tmp_path: Path):
    """Break caught: renderer deletes the published tree before validating inputs."""

    root = _copy_repo(tmp_path)
    sentinel = root / "plugins" / "escapement" / "preserve-on-render-failure"
    sentinel.write_text("existing generated state\n", encoding="utf-8")
    identity_path = root / IDENTITY
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["mission"] = ""
    identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")

    result = _run_renderer(root)
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "existing generated state\n"


@pytest.mark.parametrize("invalid_kind", ("capability-order", "adapter-mapping", "support-status"))
def test_semantically_invalid_input_preserves_all_published_trees(
    tmp_path: Path, invalid_kind: str
):
    root = _copy_repo(tmp_path)
    sentinels = (
        root / "plugins" / "escapement" / "preserve-on-render-failure",
        root / "plugins" / "escapement-claude" / "preserve-on-render-failure",
    )
    for sentinel in sentinels:
        sentinel.write_text("existing generated state\n", encoding="utf-8")

    if invalid_kind == "support-status":
        manifest_path = root / MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["support_claims"][0]["status"] = "ready"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    else:
        identity_path = root / IDENTITY
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        if invalid_kind == "capability-order":
            identity["capabilities"][0], identity["capabilities"][1] = (
                identity["capabilities"][1],
                identity["capabilities"][0],
            )
        else:
            identity["current_adapters"]["Design and specification"] = ["GitHub"]
        identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")

    result = _run_renderer(root)
    assert result.returncode != 0
    for sentinel in sentinels:
        assert sentinel.read_text(encoding="utf-8") == "existing generated state\n"


def test_publish_failure_rolls_back_every_existing_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _copy_repo(tmp_path)
    original_agents = "existing AGENTS surface\n"
    (root / "AGENTS.md").write_text(original_agents, encoding="utf-8")
    sentinel = root / "plugins" / "escapement" / "preserve-on-publish-failure"
    sentinel.write_text("existing plugin tree\n", encoding="utf-8")
    renderer = _load_renderer_module(root)
    real_replace = renderer.os.replace
    replace_calls = 0

    def fail_once_during_publication(source, destination):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 4:
            raise OSError("injected publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(renderer.os, "replace", fail_once_during_publication)
    result = renderer.render(root, root / MANIFEST)

    assert result != 0
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == original_agents
    assert sentinel.read_text(encoding="utf-8") == "existing plugin tree\n"


def test_winddown_fixture_covers_all_ordinary_means_and_consequential_boundaries():
    cases = json.loads(
        (ROOT / "harness/tests/fixtures/winddown_labeled.json").read_text(encoding="utf-8")
    )
    ordinary = {case.get("authority_class") for case in cases if case["expect"] == "block"}
    boundaries = {case.get("boundary_class") for case in cases if case["expect"] == "allow"}
    assert REQUIRED_WINDDOWN_ORDINARY_CLASSES <= ordinary
    assert REQUIRED_WINDDOWN_BOUNDARY_CLASSES <= boundaries


def test_credential_boundary_never_requests_secret_material_in_chat():
    fixture = _text(ROOT, Path("harness/tests/fixtures/winddown_labeled.json"))
    judge = _text(ROOT, Path("harness/bin/winddown_judge.py"))
    for content in (fixture, judge):
        assert "paste the token" not in content
        assert "Do not send credential material in chat" in content
