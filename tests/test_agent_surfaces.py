import os
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "tools" / "render_agent_surfaces.py"
MANIFEST = ROOT / "agent-surfaces" / "manifest.json"
CODEX_WRAPPER = ROOT / "plugins" / "escapement"
EXPECTED_CODEX_GATE = {
    "event": "PreToolUse",
    "matcher": "Bash",
    "command": "python3 -B claude/hooks/test_oracle_brief_gate.py",
    "timeout": 5,
}
CODEX_FINAL_RESPONSE_GAP_COMMAND = "python3 -B claude/hooks/codex_final_response_gap.py"
CODEX_PLUGIN_FINAL_RESPONSE_GAP_FRAGMENT = 'python3 -B "${PLUGIN_ROOT}/claude/hooks/codex_final_response_gap.py"'
ROOT_CHECKOUT_GUARD_COMMAND = "python3 -B claude/hooks/root_checkout_guard.py"
CLAUDE_PLUGIN_ROOT_CHECKOUT_GUARD_COMMAND = (
    'python3 -B "${CLAUDE_PLUGIN_ROOT}/hooks/root_checkout_guard.py"'
)
CODEX_PLUGIN_ROOT_CHECKOUT_GUARD_FRAGMENT = 'python3 -B "${PLUGIN_ROOT}/claude/hooks/root_checkout_guard.py"'
MINIMUM_VERIFIED_DELIVERY_FRAGMENTS = (
    "Escapement optimizes for minimum verified delivery",
    "YAGNI forbids speculative",
    "never weakens the outcome oracle",
    "current user/business outcome still passes its independent verification",
    "DRY targets duplicated authority, not similar text.",
    "Preserve independent corroborating checks",
    "Add gates only for repeated or high-severity failures with a replayable oracle",
)


def run_renderer(*args, root=ROOT):
    return subprocess.run(
        [sys.executable, str(RENDERER), *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_generated_surfaces_are_current():
    result = run_renderer("--check")
    assert result.returncode == 0, result.stderr


def test_generated_docs_include_minimum_verified_delivery_guidance():
    assert_minimum_verified_delivery_guidance(ROOT)


def test_generated_docs_ban_stop_solicitation():
    for rel_path in ("AGENTS.md", "CLAUDE.md"):
        text = " ".join((ROOT / rel_path).read_text().split())
        assert "Do not ask whether to stop, keep going, wrap, pause" in text
        assert "If there is a next in-scope action, take it." in text


def test_minimum_verified_delivery_guidance_without_oracle_guardrail_fails(tmp_path):
    temp_root = copy_repo(tmp_path)
    for rel_path in ("agent-surfaces/onboarding/outcome-oracle.md", "AGENTS.md", "CLAUDE.md"):
        path = temp_root / rel_path
        path.write_text(
            replace_normalized_phrase(
                path.read_text(),
                "never weakens the outcome oracle",
                "prefers fewer files",
                rel_path,
            )
        )

    with pytest.raises(AssertionError):
        assert_minimum_verified_delivery_guidance(temp_root)


def test_codex_repo_marketplace_points_to_installable_wrapper():
    marketplace_path = ROOT / ".agents" / "plugins" / "marketplace.json"
    assert marketplace_path.exists(), "repo marketplace must expose the Escapement Codex wrapper"

    marketplace = json.loads(marketplace_path.read_text())
    entries = [entry for entry in marketplace["plugins"] if entry["name"] == "escapement"]

    assert marketplace["name"] == "escapement"
    assert entries == [
        {
            "name": "escapement",
            "source": {"source": "local", "path": "./plugins/escapement"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Developer Tools",
        }
    ]


def test_codex_plugin_wrapper_manifest_uses_current_ingestion_contract():
    legacy_manifest = ROOT / ".codex-plugin" / "plugin.json"
    assert not legacy_manifest.exists(), "legacy root Codex manifest is invalid and must not be installable"

    manifest_path = CODEX_WRAPPER / ".codex-plugin" / "plugin.json"
    assert manifest_path.exists(), "Codex wrapper must include .codex-plugin/plugin.json"

    manifest = json.loads(manifest_path.read_text())

    assert manifest["name"] == "escapement"
    assert manifest["version"] == "1.0.0"
    assert manifest["skills"] == "./skills/"
    # Codex 0.144.1 ACCEPTS an explicit hooks key (escapement-z506) — verified by
    # installing a probe plugin declaring `"hooks": "./hooks/hooks.json"`. The old
    # assertion ("validation rejects a hooks field") was stale from an earlier Codex.
    assert manifest["hooks"] == "./hooks/hooks.json", (
        "Codex plugin must declare its hooks explicitly rather than rely on "
        "undocumented default discovery of hooks/hooks.json"
    )
    assert (CODEX_WRAPPER / "skills").is_dir()
    assert (CODEX_WRAPPER / "hooks" / "hooks.json").is_file()


def test_claude_plugin_manifest_has_no_version_so_auto_update_works():
    """The Claude plugin must NOT declare a `version` (escapement-9mki).

    Claude Code resolves an unversioned git-subdir plugin's version from the
    source commit SHA, so every commit to main is a new version and
    `claude plugin update escapement@escapement` actually advances the install.
    A static `version` pins resolution to that literal and makes update a
    permanent no-op.

    Proven 2026-07-10 against a real git-subdir-from-GitHub probe: the
    unversioned install updated 99d69bd -> bf09f86 on a new commit; a
    statically-versioned one reported "already at the latest version".

    Negative control: re-adding `"version"` to the manifest fails this test —
    which is the whole point, since it silently disables auto-update.
    """
    manifest = json.loads(
        (ROOT / "plugins" / "escapement-claude" / ".claude-plugin" / "plugin.json").read_text()
    )
    assert manifest["name"] == "escapement"
    assert "version" not in manifest, (
        "Claude plugin.json declares a `version` — this pins `claude plugin update` "
        "to a no-op and disables auto-update (escapement-9mki). Remove it."
    )
    # The marketplace entry must not smuggle a version back in either (it would
    # override the plugin.json absence per Claude's version-resolution order).
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    entry = next(p for p in marketplace["plugins"] if p["name"] == "escapement")
    assert "version" not in entry, (
        "marketplace entry pins a `version`, overriding the unversioned plugin.json "
        "and re-disabling auto-update (escapement-9mki)"
    )


def test_repo_root_is_a_marketplace_not_a_shadow_plugin():
    """The repo root must expose ONLY a marketplace, never a self-plugin (escapement-hnid).

    The renderer writes the Claude plugin to `plugins/escapement-claude/` and only
    `.claude-plugin/marketplace.json` at the root; it never targets root
    `.claude-plugin/plugin.json` or root `hooks/hooks.json`. Those two files once
    existed at the root as pre-`plugins/`-split vestiges and were NON-rendered, so
    they silently drifted — root `hooks/hooks.json` still carried the pre-#120
    prefix-matcher wiring (Bash(gh pr merge:*)) that the bypass fix removed
    everywhere the renderer actually writes. Their danger is that a `/plugin install`
    pointed straight at the repo root would default-discover them (a plugin.json
    makes root a plugin; hooks/hooks.json is then auto-discovered) and load the stale
    gate wiring. Deleting both makes root unambiguously a marketplace whose sole
    plugin lives in the rendered subdir.

    Negative control: recreating either root orphan fails this test.
    Positive control: the marketplace manifest MUST remain — it is how
    `/plugin marketplace add alexander-vyh/escapement` resolves the git-subdir plugin.
    """
    assert not (ROOT / "hooks" / "hooks.json").exists(), (
        "root hooks/hooks.json is back — it is a NON-rendered orphan that drifts from "
        "the manifest and would be auto-discovered by a root `/plugin install`, loading "
        "stale gate wiring (escapement-hnid). The rendered plugin hooks live at "
        "plugins/escapement-claude/hooks/hooks.json."
    )
    assert not (ROOT / ".claude-plugin" / "plugin.json").exists(), (
        "root .claude-plugin/plugin.json is back — it makes the repo root look "
        "installable as a plugin, shadowing the real rendered manifest at "
        "plugins/escapement-claude/.claude-plugin/plugin.json (escapement-hnid). "
        "Root is a marketplace, not a plugin."
    )
    assert (ROOT / ".claude-plugin" / "marketplace.json").exists(), (
        "root .claude-plugin/marketplace.json is missing — it is the install entrypoint "
        "for `/plugin marketplace add alexander-vyh/escapement` and MUST remain."
    )


def test_codex_plugin_wrapper_contains_current_codex_skills():
    source_skills = {
        path.parent.name: path.read_text(encoding="utf-8")
        for path in sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md"))
    }
    wrapper_skills = {
        path.parent.name: path.read_text(encoding="utf-8")
        for path in sorted((CODEX_WRAPPER / "skills").glob("*/SKILL.md"))
    }

    assert wrapper_skills == source_skills
    assert "openspec-apply-change" in wrapper_skills


def test_codex_plugin_wrapper_hooks_are_self_contained_and_codex_shaped():
    hooks_path = CODEX_WRAPPER / "hooks" / "hooks.json"
    assert hooks_path.exists(), "Codex wrapper must package hooks/hooks.json for plugin discovery"

    hooks_text = hooks_path.read_text()
    for forbidden in ("${CLAUDE_PLUGIN_ROOT}", "~/.claude", "CLAUDE_CODE_SESSION_ID", "ScheduleWakeup", "TeamCreate"):
        assert forbidden not in hooks_text

    hooks = json.loads(hooks_text)["hooks"]
    commands = [
        hook["command"]
        for event_items in hooks.values()
        for item in event_items
        for hook in item["hooks"]
    ]

    assert "bd prime" in commands
    assert any("test_oracle_brief_gate.py" in command for command in commands)
    assert any("implementation_echo_test_gate.py" in command for command in commands)
    assert any("oracle_downgrade_warning_gate.py" in command for command in commands)

    for command in commands:
        if "${PLUGIN_ROOT}/" not in command:
            continue
        rel = command.split("${PLUGIN_ROOT}/", 1)[1].split('"', 1)[0]
        assert (CODEX_WRAPPER / rel).is_file(), f"hook command references missing wrapper file: {rel}"

    manifest = json.loads(MANIFEST.read_text())
    ready_hook_sources = {
        hook["source"]
        for hook in manifest["hooks"]
        if hook.get("source") != "bd" and hook["hosts"]["codex"]["status"] == "ready"
    }
    packaged_hook_sources = {
        path.relative_to(CODEX_WRAPPER).as_posix()
        for path in sorted((CODEX_WRAPPER / "claude" / "hooks").glob("*"))
    }
    assert ready_hook_sources <= packaged_hook_sources


def test_codex_hooks_include_prime_and_behavioral_gate():
    hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text())["hooks"]
    commands = [
        hook["command"]
        for event_items in hooks.values()
        for item in event_items
        for hook in item["hooks"]
    ]
    assert "bd prime" in commands
    assert EXPECTED_CODEX_GATE["command"] in commands


def test_codex_repo_relative_python_hooks_disable_bytecode():
    """Codex hooks run inside the repo; Python hooks must not leave __pycache__ residue."""
    hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text())["hooks"]
    commands = [
        hook["command"]
        for event_items in hooks.values()
        for item in event_items
        for hook in item["hooks"]
    ]

    offenders = [command for command in commands if command.startswith("python3 claude/hooks/")]
    assert offenders == []
    assert any(command.startswith("python3 -B claude/hooks/") for command in commands)

    plugin_hooks = json.loads((CODEX_WRAPPER / "hooks" / "hooks.json").read_text())["hooks"]
    plugin_commands = [
        hook["command"]
        for event_items in plugin_hooks.values()
        for item in event_items
        for hook in item["hooks"]
    ]
    plugin_offenders = [
        command
        for command in plugin_commands
        if command.startswith('python3 "${PLUGIN_ROOT}/claude/hooks/')
    ]
    assert plugin_offenders == []


def test_claude_python_hooks_disable_bytecode():
    """Vendored plugin hooks must not write Python bytecode caches.

    The plugin is the sole owner of Claude hook registration (escapement-ptzz), so
    ``claude/settings.template.json`` registers nothing. The ``-B`` invariant and its
    positive control therefore live on the plugin's hooks.json, not on the template.
    """
    settings = json.loads((ROOT / "claude" / "settings.template.json").read_text())
    setting_commands = [
        hook["command"]
        for event_items in settings["hooks"].values()
        for item in event_items
        for hook in item.get("hooks", [])
    ]
    assert setting_commands == [], (
        "claude/settings.template.json must register no hooks — the plugin owns them. "
        "Dual registration double-fires every gate (escapement-ptzz)."
    )

    plugin_hooks = json.loads((ROOT / "plugins" / "escapement-claude" / "hooks" / "hooks.json").read_text())["hooks"]
    plugin_commands = [
        hook["command"]
        for event_items in plugin_hooks.values()
        for item in event_items
        for hook in item["hooks"]
    ]
    plugin_offenders = [
        command
        for command in plugin_commands
        if command.startswith('python3 "${CLAUDE_PLUGIN_ROOT}/')
    ]
    assert plugin_offenders == []
    # Positive control (migrated from the template): proves the -B check above is
    # scanning a non-empty set of real python hook commands.
    assert any(
        command.startswith('python3 -B "${CLAUDE_PLUGIN_ROOT}/') for command in plugin_commands
    ), "no bytecode-disabled python hook found in the plugin — the -B check is vacuous"


def test_codex_hooks_include_final_response_gap_warning():
    """No Stop GATE is ported to Codex yet; the gap must be explicit at startup.

    Codex 0.144.1 DOES support a `stop` hook event (verified: codex@openai-codex
    registers one and a real `codex exec` printed `hook: Stop`). But escapement's
    continuation-harness Stop gate (harness/bin/stop_hook.py, ~1144 lines) assumes
    Claude Stop payloads, Claude transcript layout, ScheduleWakeup, and ~/.claude
    state — porting it is its own effort, tracked separately. Until that lands,
    escapement ships NO Codex Stop hook, and the SessionStart advisory names the
    gap. This guard prevents a half-baked Stop hook shipping before the real port.
    """
    hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text())["hooks"]

    assert "Stop" not in hooks, (
        "escapement has not ported its Stop gate to Codex yet — do not ship a Codex "
        "Stop hook until the continuation-harness port lands (see the Stop-gate bead)"
    )
    session_start_commands = [
        hook["command"]
        for item in hooks.get("SessionStart", [])
        for hook in item.get("hooks", [])
    ]
    assert CODEX_FINAL_RESPONSE_GAP_COMMAND in session_start_commands


def test_codex_plugin_hooks_include_final_response_gap_warning():
    """The installable Codex wrapper must carry the same startup warning."""
    hooks = json.loads((CODEX_WRAPPER / "hooks" / "hooks.json").read_text())["hooks"]

    assert "Stop" not in hooks, "Codex plugin must not ship unsupported Stop hooks"
    session_start_commands = [
        hook["command"]
        for item in hooks.get("SessionStart", [])
        for hook in item.get("hooks", [])
    ]
    matches = [
        command
        for command in session_start_commands
        if CODEX_PLUGIN_FINAL_RESPONSE_GAP_FRAGMENT in command
    ]
    assert matches, "Codex plugin SessionStart must warn about the final-response Stop gap"

    rel = "claude/hooks/codex_final_response_gap.py"
    assert (CODEX_WRAPPER / rel).is_file(), f"plugin command references missing file: {rel}"


def test_codex_behavioral_gate_has_exact_event_shape():
    hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text())["hooks"]
    entries = hooks.get(EXPECTED_CODEX_GATE["event"], [])
    matches = [
        item
        for item in entries
        if item.get("matcher") == EXPECTED_CODEX_GATE["matcher"]
        for hook in item.get("hooks", [])
        if hook.get("command") == EXPECTED_CODEX_GATE["command"]
        and hook.get("timeout") == EXPECTED_CODEX_GATE["timeout"]
    ]
    assert matches, "Codex Test Oracle Brief gate must be PreToolUse/Bash with timeout"


def test_root_checkout_guard_is_manifested_and_rendered_for_claude_and_codex():
    """Architecture check: the hook must be wired, not merely implemented."""
    manifest = json.loads(MANIFEST.read_text())
    entries = [hook for hook in manifest["hooks"] if hook["id"] == "root_checkout_guard"]
    assert len(entries) == 1, "root_checkout_guard must have exactly one manifest entry"
    entry = entries[0]
    assert entry["source"] == "claude/hooks/root_checkout_guard.py"
    assert entry["hosts"]["codex"]["status"] == "ready"
    assert entry["hosts"]["claude"]["status"] == "ready"

    codex_hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text())["hooks"]
    codex_matchers = {
        item.get("matcher", "")
        for item in codex_hooks.get("PreToolUse", [])
        for hook in item.get("hooks", [])
        if hook.get("command") == ROOT_CHECKOUT_GUARD_COMMAND
    }
    assert {"Bash", "Write", "Edit", "NotebookEdit"} <= codex_matchers

    # The Claude PLUGIN is the sole owner of hook registration (escapement-ptzz).
    # This assertion is re-pointed from settings.template.json, not weakened: the
    # same four matchers must still be wired for the guard to protect the root checkout.
    claude_plugin_hooks = json.loads(
        (ROOT / "plugins" / "escapement-claude" / "hooks" / "hooks.json").read_text()
    )["hooks"]
    claude_matchers = {
        item.get("matcher", "")
        for item in claude_plugin_hooks["PreToolUse"]
        for hook in item.get("hooks", [])
        if hook.get("command") == CLAUDE_PLUGIN_ROOT_CHECKOUT_GUARD_COMMAND
    }
    assert {"Bash", "Write", "Edit", "NotebookEdit"} <= claude_matchers

    plugin_hooks = json.loads((CODEX_WRAPPER / "hooks" / "hooks.json").read_text())["hooks"]
    plugin_commands = [
        hook["command"]
        for item in plugin_hooks.get("PreToolUse", [])
        for hook in item.get("hooks", [])
    ]
    assert CODEX_PLUGIN_ROOT_CHECKOUT_GUARD_FRAGMENT in plugin_commands


def test_codex_generated_surfaces_do_not_use_claude_user_paths():
    forbidden = ("~/.claude", "CLAUDE_CODE_SESSION_ID", "ScheduleWakeup", "TeamCreate")
    text = (
        (ROOT / "AGENTS.md").read_text()
        + (ROOT / ".codex" / "hooks.json").read_text()
        + "\n".join(skill.read_text() for skill in sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md")))
    )
    for needle in forbidden:
        assert needle not in text


def test_codex_ready_hook_without_fixture_fails(tmp_path):
    temp_root = copy_repo(tmp_path)
    manifest_path = temp_root / "agent-surfaces" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for hook in manifest["hooks"]:
        if hook["id"] == "test_oracle_brief_gate":
            hook["hosts"]["codex"]["fixtures"] = []
            break
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert "ready without fixtures" in result.stderr


def test_codex_ready_hook_with_bogus_fixture_fails(tmp_path):
    temp_root = copy_repo(tmp_path)
    manifest_path = temp_root / "agent-surfaces" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for hook in manifest["hooks"]:
        if hook["id"] == "test_oracle_brief_gate":
            hook["hosts"]["codex"]["fixtures"] = ["does/not/exist.py::test_fake"]
            break
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert "fixture does not exist" in result.stderr


def test_codex_ready_hook_with_non_pytest_fixture_fails(tmp_path):
    temp_root = copy_repo(tmp_path)
    manifest_path = temp_root / "agent-surfaces" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for hook in manifest["hooks"]:
        if hook["id"] == "test_oracle_brief_gate":
            hook["hosts"]["codex"]["fixtures"] = ["README.md"]
            break
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert "Codex hook fixture must be a pytest selector" in result.stderr


def test_codex_ready_hook_with_non_codex_fixture_fails(tmp_path):
    temp_root = copy_repo(tmp_path)
    manifest_path = temp_root / "agent-surfaces" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for hook in manifest["hooks"]:
        if hook["id"] == "test_oracle_brief_gate":
            hook["hosts"]["codex"]["fixtures"] = [
                "claude/hooks/tests/test_test_oracle_brief_gate.py::test_claude_edit_blocks_relevant_file_without_brief"
            ]
            break
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert "Codex hook fixture must be Codex-specific" in result.stderr


def test_codex_ready_hook_with_other_hook_codex_fixture_fails(tmp_path):
    temp_root = copy_repo(tmp_path)
    manifest_path = temp_root / "agent-surfaces" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for hook in manifest["hooks"]:
        if hook["id"] == "review_gate":
            hook["hosts"]["codex"] = {
                "status": "ready",
                "events": [
                    {
                        "event": "PreToolUse",
                        "matcher": "Bash",
                        "command": "python3 claude/hooks/review_gate.py",
                        "timeout_seconds": 5,
                    }
                ],
                "fixtures": [
                    "claude/hooks/tests/test_test_oracle_brief_gate.py::test_codex_commit_blocks_changed_code_without_brief"
                ],
            }
            break
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    render_result = run_renderer(root=temp_root)
    assert render_result.returncode == 0, render_result.stderr

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert "Codex hook fixture must match hook source" in result.stderr


def test_codex_behavioral_gate_wrong_event_fails_drift_check(tmp_path):
    temp_root = copy_repo(tmp_path)
    manifest_path = temp_root / "agent-surfaces" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for hook in manifest["hooks"]:
        if hook["id"] == "test_oracle_brief_gate":
            hook["hosts"]["codex"]["events"][0]["event"] = "SessionStart"
            break
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert "generated target drift: .codex/hooks.json" in result.stderr


@pytest.mark.parametrize("target", ["AGENTS.md", "CLAUDE.md", ".codex/hooks.json"])
def test_generated_surface_drift_names_target(tmp_path, target):
    temp_root = copy_repo(tmp_path)
    generated = temp_root / target
    generated.write_text(generated.read_text() + "\nmanual edit\n")

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert f"generated target drift: {target}" in result.stderr


def test_empty_codex_skill_fails(tmp_path):
    temp_root = copy_repo(tmp_path)
    skill = temp_root / ".agents" / "skills" / "openspec-apply-change" / "SKILL.md"
    skill.write_text("---\nname: openspec-apply-change\ndescription: x\n---\n")

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert "empty or too small" in result.stderr


def test_unmanifested_codex_skill_fails(tmp_path):
    temp_root = copy_repo(tmp_path)
    skill_dir = temp_root / ".agents" / "skills" / "untracked-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: untracked-skill\ndescription: OpenSpec helper\n---\n\nRun openspec list.\n"
    )

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert "skill target not listed in manifest" in result.stderr


def test_codex_skill_surfaces_do_not_reference_unavailable_tools():
    for skill in sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        for token in ("TodoWrite", "AskUserQuestion", "Task tool", "subagent_type"):
            assert token not in text, f"{skill} must not reference {token}"


def test_codex_skill_with_claude_only_token_fails(tmp_path):
    temp_root = copy_repo(tmp_path)
    skill = temp_root / ".agents" / "skills" / "openspec-apply-change" / "SKILL.md"
    skill.write_text(skill.read_text() + "\nUse ~/.claude/local-state here.\n")

    result = run_renderer("--check", root=temp_root)

    assert result.returncode != 0
    assert "Codex skill contains forbidden Claude-only token" in result.stderr


def assert_minimum_verified_delivery_guidance(root):
    for rel_path in ("agent-surfaces/onboarding/outcome-oracle.md", "AGENTS.md", "CLAUDE.md"):
        text = " ".join((root / rel_path).read_text().split())
        for fragment in MINIMUM_VERIFIED_DELIVERY_FRAGMENTS:
            assert fragment in text, f"{rel_path} missing minimum verified delivery fragment: {fragment}"


def replace_normalized_phrase(text, required, replacement, rel_path):
    pattern = r"\s+".join(re.escape(token) for token in required.split())
    mutated, count = re.subn(pattern, replacement, text, count=1)
    assert count == 1, f"mutation prerequisite missing from {rel_path}"
    return mutated


CLAUDE_PLUGIN = ROOT / "plugins" / "escapement-claude"


def test_claude_marketplace_tracks_main_for_autoupdate():
    """The Claude marketplace points at this repo via git-subdir, ref main.

    This is what makes the plugin auto-update: every push to main becomes the new
    version. A regression to a pinned tag/sha here would silently freeze updates.
    """
    mkt = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    entry = next(p for p in mkt["plugins"] if p["name"] == "escapement")
    src = entry["source"]
    assert src["source"] == "git-subdir"
    assert src["path"] == "plugins/escapement-claude"
    assert src["ref"] == "main", "marketplace must track main for continuous auto-update"


def test_claude_plugin_hooks_include_sessionstart_rules_injection():
    """The plugin wires its always-on rules via a SessionStart inject hook.

    Negative control: removing the SessionStart injection would drop escapement's
    rules entirely on Codex-less hosts — this asserts it is present and points at
    the bundled inject-rules.sh.
    """
    hooks = json.loads((CLAUDE_PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    session_start = hooks.get("SessionStart", [])
    commands = [h["command"] for item in session_start for h in item["hooks"]]
    assert any("inject-rules.sh" in c for c in commands), "SessionStart must inject the rules"


def test_claude_plugin_hooks_do_not_depend_on_user_local_claude_paths():
    """Plugin install must run bundled hooks, not stale ~/.claude copies."""
    hooks = json.loads((CLAUDE_PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    commands = [
        h["command"]
        for event_items in hooks.values()
        for item in event_items
        for h in item["hooks"]
    ]
    assert all("~/.claude" not in command for command in commands)
    assert 'python3 -B "${CLAUDE_PLUGIN_ROOT}/harness/bin/stop_hook.py"' in commands


def test_claude_plugin_bundles_shared_judge_support():
    """Claude plugin hook copies must include shared semantic-judge dependencies."""
    for name in (
        "_local_judge_client.py",
        "local_judge_health.py",
    ):
        assert (CLAUDE_PLUGIN / "hooks" / name).is_file(), (
            f"Claude plugin hook bundle missing semantic judge support file: {name}"
        )
    for name in (
        "session_isolation.py",
        "stop_hook.py",
        "verify_integrity.py",
        "winddown_judge.py",
        "winddown_gate.py",
        "winddown_outage_sentinel.py",
        "would_block_stop.py",
    ):
        assert (CLAUDE_PLUGIN / "harness" / "bin" / name).is_file(), (
            f"Claude plugin harness bundle missing semantic judge support file: {name}"
        )


def test_claude_plugin_stop_hook_imports_from_bundle(monkeypatch):
    hooks_dir = CLAUDE_PLUGIN / "hooks"
    harness_dir = CLAUDE_PLUGIN / "harness" / "bin"
    monkeypatch.syspath_prepend(str(hooks_dir))
    monkeypatch.syspath_prepend(str(harness_dir))
    spec = importlib.util.spec_from_file_location(
        "plugin_stop_hook_import_check",
        harness_dir / "stop_hook.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._wj is not None
    assert module._wg is not None


def test_claude_plugin_bundles_all_rules():
    """Every claude/rules/*.md is bundled into the plugin so injection is complete."""
    source_rules = {p.name for p in (ROOT / "claude" / "rules").glob("*.md")}
    bundled = {p.name for p in (CLAUDE_PLUGIN / "rules").glob("*.md")}
    assert bundled == source_rules and source_rules, "all rules must be bundled, none dropped"


# escapement-w4sn: the always-on rules must be injected through exactly ONE channel.
# A distinctive phrase that lives in exactly one rule body (continuation-harness.md)
# is the dedup sentinel — it must appear once across the union of both channels.
RULE_DEDUP_PHRASE = "outcome-bias over action-bias"


def _install_dry_run_plan(tmp_home):
    """stdout of `INSTALL.sh --dev --dry-run` against a sandboxed HOME.

    --dev skips the pinned-checkout git clone (hermetic — no network); --dry-run
    prints the planned symlinks (`    link:   <dest> -> <src_rel>`) without touching
    the filesystem. This exercises INSTALL.sh's real PLAN, not a regex on its source.
    """
    result = subprocess.run(
        ["bash", str(ROOT / "INSTALL.sh"), "--dev", "--dry-run"],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_home)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _planned_rule_symlink_sources(plan_stdout):
    """Sources INSTALL.sh would symlink into a `.../rules/*.md` destination."""
    sources = []
    for line in plan_stdout.splitlines():
        if "link:" not in line:
            continue
        _, _, rest = line.partition("link:")
        dest, sep, src_rel = rest.partition("->")
        if not sep:
            continue
        dest, src_rel = dest.strip(), src_rel.strip()
        if "/rules/" in dest and dest.endswith(".md"):
            sources.append(src_rel)
    return sources


def test_install_does_not_symlink_rules_into_claude_dir(tmp_path):
    """INSTALL.sh must NOT symlink claude/rules/*.md into ~/.claude/rules/.

    Regression for escapement-w4sn: those native symlinks loaded every rule body as
    the claudeMd block WHILE the plugin's inject-rules.sh hook injected the same
    bodies — every session paid ~20K tokens of duplicate rules on turn 0. The plugin
    SessionStart hook is now the sole channel. Negative control: this fails against
    the pre-fix INSTALL.sh, which planned 13 such symlinks.
    """
    plan = _install_dry_run_plan(tmp_path)
    # Sanity: the plan is real (other surfaces still symlink), so an empty rules
    # result means "removed", not "dry-run emitted nothing".
    assert "link:" in plan, "dry-run produced no symlink plan at all"
    rule_links = _planned_rule_symlink_sources(plan)
    assert rule_links == [], (
        "INSTALL.sh must not symlink rule bodies into ~/.claude/rules "
        f"(Channel A duplicate); found {rule_links}"
    )


def test_rules_delivered_exactly_once_across_both_channels(tmp_path):
    """Each rule body appears exactly once across the combined injection surface.

    Channel A = rule bodies INSTALL.sh would symlink into ~/.claude/rules (native
    claudeMd load). Channel B = the plugin SessionStart hook's additionalContext.
    The dedup sentinel — a phrase in exactly one rule body — must appear ONCE across
    the union (pre-fix it appeared twice). Positive control: Channel B alone still
    carries it and every bundled rule body, so delivery is not dropped to zero.
    """
    plan = _install_dry_run_plan(tmp_path)
    channel_a = "\n".join(
        (ROOT / src).read_text() for src in _planned_rule_symlink_sources(plan)
    )

    inj = subprocess.run(
        ["bash", str(CLAUDE_PLUGIN / "hooks" / "inject-rules.sh")],
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(CLAUDE_PLUGIN)},
        capture_output=True,
        text=True,
    )
    assert inj.returncode == 0, inj.stderr
    channel_b = json.loads(inj.stdout)["hookSpecificOutput"]["additionalContext"]

    combined = channel_a + "\n" + channel_b
    assert combined.count(RULE_DEDUP_PHRASE) == 1, (
        "rule bodies must be injected exactly once across INSTALL.sh + plugin hook; "
        f"dedup-phrase count = {combined.count(RULE_DEDUP_PHRASE)}"
    )
    # Positive control: the surviving channel still delivers the sentinel AND every
    # bundled rule body verbatim — the fix removed the duplicate, not the rules.
    assert channel_b.count(RULE_DEDUP_PHRASE) == 1
    for rule_file in sorted((CLAUDE_PLUGIN / "rules").glob("*.md")):
        assert rule_file.read_text() in channel_b, (
            f"surviving channel dropped rule body: {rule_file.name}"
        )


def test_claude_plugin_injects_rules_with_imperative_framing(tmp_path):
    """Behavioral: running inject-rules.sh emits SessionStart additionalContext
    carrying the bundled rules AND imperative framing (so injected rules match
    native CLAUDE.md authority). Positive control for the rules-delivery mechanism.
    """
    result = subprocess.run(
        ["bash", str(CLAUDE_PLUGIN / "hooks" / "inject-rules.sh")],
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(CLAUDE_PLUGIN)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "OVERRIDE default behavior" in ctx, "injected rules must carry imperative framing"
    assert len(ctx) > 5000, "rules bundle should be substantial, not a stub"


def test_claude_plugin_inject_rules_fails_loud_on_missing_bundle(tmp_path):
    """Negative control: a missing rules bundle surfaces a WARNING, not a silent
    drop — so a broken install is observable instead of a quiet rules regression.
    """
    result = subprocess.run(
        ["bash", str(CLAUDE_PLUGIN / "hooks" / "inject-rules.sh")],
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(tmp_path / "empty")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "WARNING" in ctx and "NOT injected" in ctx


def copy_repo(tmp_path):
    temp_root = tmp_path / "repo"
    shutil.copytree(
        ROOT,
        temp_root,
        ignore=shutil.ignore_patterns(".git", ".worktrees", "__pycache__", ".pytest_cache"),
    )
    return temp_root


def _claude_skill_status_violations():
    """Skills whose manifest claude.status disagrees with the filesystem.

    Returns (unsupported_but_live, ready_but_unrendered).
    """
    manifest = json.loads(MANIFEST.read_text())
    unsupported_but_live = []
    ready_but_unrendered = []
    for skill in manifest["skills"]:
        sid = skill["id"]
        status = skill.get("hosts", {}).get("claude", {}).get("status")
        live = (ROOT / ".claude" / "skills" / sid).is_dir()
        if status == "unsupported" and live:
            unsupported_but_live.append(sid)
        elif status == "ready" and not live:
            ready_but_unrendered.append(sid)
    return unsupported_but_live, ready_but_unrendered


def test_manifest_claude_status_matches_filesystem():
    """Bidirectional manifest<->filesystem fidelity (spec escapement-mol-741.10,
    requirement #manifest-bidirectional-fidelity).

    NEGATIVE direction: a skill marked claude=unsupported must NOT load live under
    .claude/skills/ (else the manifest lies about what Claude actually loads).
    POSITIVE direction: a skill marked claude=ready must trace to a live Claude surface.

    Negative control: source-command-opsx-* are claude=unsupported AND absent from
    .claude/skills/, so a correct oracle must NOT flag them -- proving it catches the
    real lie (the openspec-* skills), not merely 'any unsupported skill'.
    """
    unsupported_but_live, ready_but_unrendered = _claude_skill_status_violations()
    # Negative control: a correctly-modeled unsupported-and-absent skill is not flagged.
    assert "source-command-opsx-apply" not in unsupported_but_live
    assert not unsupported_but_live, (
        "manifest marks these claude=unsupported but they load live under .claude/skills/: "
        f"{unsupported_but_live}"
    )
    assert not ready_but_unrendered, (
        "manifest marks these claude=ready but no live .claude/skills/ surface exists: "
        f"{ready_but_unrendered}"
    )
