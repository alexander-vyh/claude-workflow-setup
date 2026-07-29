"""Static source-of-truth and generated-plugin parity checks for worktrees."""

from __future__ import annotations

from pathlib import Path


ACTIVE_POLICY_FILES = [
    Path("agent-surfaces/onboarding/beads.md"),
    Path("claude/rules/worktree-discipline.md"),
    Path("claude/rules/agent-teams-default.md"),
    Path("claude/skills/beads-worktree/SKILL.md"),
    Path("claude/skills/beads-execution/SKILL.md"),
    Path(".agents/skills/beads-execution/SKILL.md"),
    Path("claude/hooks/escapement_session_context.py"),
    Path("claude/hooks/root_checkout_guard.py"),
    Path("harness/bin/session_isolation.py"),
    Path("harness/README.md"),
    Path("README.md"),
]


ROOT = Path(__file__).resolve().parents[1]


def _active_policy() -> str:
    missing = [str(path) for path in ACTIVE_POLICY_FILES if not (ROOT / path).is_file()]
    assert not missing, f"active policy files missing: {missing}"
    return "\n".join(
        (ROOT / path).read_text(encoding="utf-8") for path in ACTIVE_POLICY_FILES
    )


def test_active_policy_uses_the_host_neutral_transaction() -> None:
    policy = _active_policy()
    assert "escapement-worktree" in policy
    assert "cake-worktree" not in policy
    assert ".agents/worktree-entrypoint" not in policy
    assert "bd worktree create" not in policy


def test_static_scan_is_limited_to_active_policy_not_historical_artifacts() -> None:
    policy = _active_policy()
    assert "docs/superpowers/specs" not in "\n".join(
        str(path) for path in ACTIVE_POLICY_FILES
    )
    assert "fixtures" not in "\n".join(str(path) for path in ACTIVE_POLICY_FILES)
    assert isinstance(policy, str) and policy


def test_renderer_targets_contain_executable_and_importable_module_for_both_plugins() -> (
    None
):
    for plugin in (
        ROOT / "plugins" / "escapement",
        ROOT / "plugins" / "escapement-claude",
    ):
        assert (plugin / "bin" / "escapement-worktree").is_file()
        assert (plugin / "bin" / "escapement_worktree.py").is_file()


def test_renderer_and_manifest_own_cli_and_module_delivery() -> None:
    renderer = (ROOT / "tools" / "render_agent_surfaces.py").read_text(encoding="utf-8")
    manifest = (ROOT / "agent-surfaces" / "manifest.json").read_text(encoding="utf-8")
    for artifact in ("bin/escapement-worktree", "bin/escapement_worktree.py"):
        assert artifact in renderer, f"renderer must own {artifact} delivery"
        assert artifact in manifest, f"surface manifest must declare {artifact}"


def test_plugin_guard_copies_are_byte_equal_to_canonical_guard() -> None:
    canonical = (ROOT / "claude" / "hooks" / "beads_worktree_guard.py").read_bytes()
    for plugin in (
        ROOT / "plugins" / "escapement",
        ROOT / "plugins" / "escapement-claude",
    ):
        assert (
            plugin / "claude" / "hooks" / "beads_worktree_guard.py"
        ).read_bytes() == canonical


def test_plugin_cli_and_module_copies_are_byte_equal_to_canonical_sources() -> None:
    for filename in ("escapement-worktree", "escapement_worktree.py"):
        canonical = (ROOT / "bin" / filename).read_bytes()
        for plugin in (
            ROOT / "plugins" / "escapement",
            ROOT / "plugins" / "escapement-claude",
        ):
            assert (plugin / "bin" / filename).read_bytes() == canonical


def test_obsolete_generated_location_guard_copies_do_not_exist() -> None:
    for path in (
        ROOT / "claude" / "hooks" / "beads_worktree_location_guard.py",
        ROOT
        / "plugins"
        / "escapement"
        / "claude"
        / "hooks"
        / "beads_worktree_location_guard.py",
        ROOT
        / "plugins"
        / "escapement-claude"
        / "claude"
        / "hooks"
        / "beads_worktree_location_guard.py",
    ):
        assert not path.exists(), path
