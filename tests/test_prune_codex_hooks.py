from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRUNER = ROOT / "scripts" / "prune_codex_hooks.py"


def _module():
    assert PRUNER.is_file(), "the Codex global-hook migration is missing"
    spec = importlib.util.spec_from_file_location("prune_codex_hooks", PRUNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin_hooks() -> dict:
    command = (
        'python3 -B "${PLUGIN_ROOT}/claude/hooks/codex_pretool_dispatch.py" '
        "--gate claude/hooks/test_oracle_brief_gate.py "
        "--gate claude/hooks/implementation_echo_test_gate.py "
        "--gate claude/hooks/oracle_downgrade_warning_gate.py "
        "--gate claude/hooks/beads_worktree_guard.py"
    )
    return {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}
            ]
        }
    }


def _live_hooks(codex_home: Path, home: Path) -> dict:
    sifi = {
        "command": "/usr/bin/python3 /repo/.git/codex-hooks/sifi_pr_policy.py",
        "timeout": 30,
        "statusMessage": "Checking Sifi policy",
        "type": "command",
    }
    pr_guard = {
        "command": f'python3 "{codex_home}/hooks/pr_create_guard.py"',
        "timeout": 30,
        "statusMessage": "Checking independent PR guard",
        "type": "command",
    }
    return {
        "owner": "preserve-top-level",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "groupMetadata": "preserve-group",
                    "hooks": [
                        {
                            "command": f'python3 "{codex_home}/hooks/test_oracle_brief_gate.py"',
                            "timeout": 30,
                            "type": "command",
                        },
                        {
                            "command": f'python3 "{codex_home}/hooks/implementation_echo_test_gate.py"',
                            "timeout": 30,
                            "type": "command",
                        },
                        {
                            "command": f'python3 "{codex_home}/hooks/oracle_downgrade_warning_gate.py"',
                            "timeout": 30,
                            "type": "command",
                        },
                        sifi,
                        pr_guard,
                        {
                            "command": f'python3 "{home}/.claude/hooks/beads_worktree_guard.py"',
                            "timeout": 10,
                            "type": "command",
                        },
                    ],
                }
            ]
        },
    }


def test_prunes_only_dispatcher_declared_legacy_escapement_hooks(
    tmp_path: Path,
) -> None:
    module = _module()
    codex_home = tmp_path / ".codex"
    home = tmp_path / "home"
    live = _live_hooks(codex_home, home)
    before = copy.deepcopy(live)

    pruned = module.prune_hooks(
        live,
        module.plugin_owned_gate_scripts(_plugin_hooks()),
        codex_home=codex_home,
        home=home,
    )

    assert live == before, "migration must not mutate its input"
    assert pruned["owner"] == "preserve-top-level"
    [group] = pruned["hooks"]["PreToolUse"]
    assert group["matcher"] == "Bash"
    assert group["groupMetadata"] == "preserve-group"
    remaining = [hook["statusMessage"] for hook in group["hooks"]]
    assert remaining == ["Checking Sifi policy", "Checking independent PR guard"]
    assert module.prune_hooks(
        pruned,
        module.plugin_owned_gate_scripts(_plugin_hooks()),
        codex_home=codex_home,
        home=home,
    ) == pruned


def test_same_basename_outside_recognized_legacy_roots_survives(tmp_path: Path) -> None:
    module = _module()
    personal = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "command": (
                                "python3 /opt/personal/hooks/"
                                "test_oracle_brief_gate.py"
                            )
                        }
                    ],
                }
            ]
        }
    }

    assert module.prune_hooks(
        personal,
        module.plugin_owned_gate_scripts(_plugin_hooks()),
        codex_home=tmp_path / ".codex",
        home=tmp_path,
    ) == personal


def test_only_direct_python_children_of_legacy_roots_are_pruned(tmp_path: Path) -> None:
    module = _module()
    codex_home = tmp_path / ".codex"
    home = tmp_path / "home"
    direct = codex_home / "hooks" / "test_oracle_brief_gate.py"
    nested = codex_home / "hooks" / "nested" / "test_oracle_brief_gate.py"
    claude_direct = home / ".claude" / "hooks" / "beads_worktree_guard.py"
    live = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "command": f'python3 -B "{direct}" --mode advisory',
                            "statusMessage": "remove quoted direct child",
                        },
                        {
                            "command": f"python3 {claude_direct}",
                            "statusMessage": "remove Claude direct child",
                        },
                        {
                            "command": f"python3 {nested}",
                            "statusMessage": "preserve nested child",
                        },
                        {
                            "command": f"bash {direct}",
                            "statusMessage": "preserve non-Python command",
                        },
                        {
                            "command": f"python3 {direct} && touch /tmp/unrelated",
                            "statusMessage": "preserve ampersand compound",
                        },
                        {
                            "command": f"python3 {direct}; touch /tmp/unrelated",
                            "statusMessage": "preserve semicolon compound",
                        },
                        {
                            "command": f"python3 {direct} || true",
                            "statusMessage": "preserve or compound",
                        },
                        {
                            "command": f"python3 {direct} | tee /tmp/unrelated",
                            "statusMessage": "preserve pipe compound",
                        },
                        {
                            "command": f"python3 {direct} > /tmp/unrelated",
                            "statusMessage": "preserve redirection compound",
                        },
                    ],
                }
            ]
        }
    }

    pruned = module.prune_hooks(
        live,
        module.plugin_owned_gate_scripts(_plugin_hooks()),
        codex_home=codex_home,
        home=home,
    )

    [group] = pruned["hooks"]["PreToolUse"]
    assert [hook["statusMessage"] for hook in group["hooks"]] == [
        "preserve nested child",
        "preserve non-Python command",
        "preserve ampersand compound",
        "preserve semicolon compound",
        "preserve or compound",
        "preserve pipe compound",
        "preserve redirection compound",
    ]


def test_cli_backs_up_original_bytes_and_is_idempotent(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    plugin_path = tmp_path / "plugin-hooks.json"
    live_path = codex_home / "hooks.json"
    plugin_path.write_text(json.dumps(_plugin_hooks(), indent=2) + "\n", encoding="utf-8")
    original = json.dumps(_live_hooks(codex_home, tmp_path), indent=2) + "\n"
    live_path.write_text(original, encoding="utf-8")

    command = [
        sys.executable,
        str(PRUNER),
        str(plugin_path),
        str(live_path),
        "--codex-home",
        str(codex_home),
        "--home",
        str(tmp_path),
    ]
    first = subprocess.run(command, text=True, capture_output=True, check=False)
    second = subprocess.run(command, text=True, capture_output=True, check=False)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    backups = list(codex_home.glob("hooks.json.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    assert "already clean" in second.stdout


def test_cli_dry_run_is_byte_exact_and_creates_no_backup(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    plugin_path = tmp_path / "plugin-hooks.json"
    live_path = codex_home / "hooks.json"
    plugin_path.write_text(json.dumps(_plugin_hooks()), encoding="utf-8")
    original = json.dumps(_live_hooks(codex_home, tmp_path), indent=7) + "\n"
    live_path.write_text(original, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(PRUNER),
            str(plugin_path),
            str(live_path),
            "--codex-home",
            str(codex_home),
            "--home",
            str(tmp_path),
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert live_path.read_text(encoding="utf-8") == original
    assert not list(codex_home.glob("hooks.json.backup-*"))
    assert "would remove" in result.stdout.lower()
