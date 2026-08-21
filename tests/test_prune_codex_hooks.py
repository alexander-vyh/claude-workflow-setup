from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRUNER = ROOT / "scripts" / "prune_codex_hooks.py"
LEGACY_STATUS = {
    "test_oracle_brief_gate.py": "Checking Test Oracle Brief gate",
    "implementation_echo_test_gate.py": "Checking implementation-echo tests",
    "oracle_downgrade_warning_gate.py": "Checking oracle downgrade warnings",
    "beads_worktree_guard.py": "Checking bd worktree location (.worktrees/)",
}


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


def _install_known_legacy_gate(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    shutil.copy2(ROOT / "claude" / "hooks" / name, target)
    return target


def _live_hooks(codex_home: Path, home: Path) -> dict:
    for name in (
        "test_oracle_brief_gate.py",
        "implementation_echo_test_gate.py",
        "oracle_downgrade_warning_gate.py",
    ):
        _install_known_legacy_gate(codex_home / "hooks", name)
    _install_known_legacy_gate(
        home / ".claude" / "hooks", "beads_worktree_guard.py"
    )
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
                            "statusMessage": LEGACY_STATUS["test_oracle_brief_gate.py"],
                            "timeout": 30,
                            "type": "command",
                        },
                        {
                            "command": f'python3 "{codex_home}/hooks/implementation_echo_test_gate.py"',
                            "statusMessage": LEGACY_STATUS[
                                "implementation_echo_test_gate.py"
                            ],
                            "timeout": 30,
                            "type": "command",
                        },
                        {
                            "command": f'python3 "{codex_home}/hooks/oracle_downgrade_warning_gate.py"',
                            "statusMessage": LEGACY_STATUS[
                                "oracle_downgrade_warning_gate.py"
                            ],
                            "timeout": 30,
                            "type": "command",
                        },
                        sifi,
                        pr_guard,
                        {
                            "command": f'python3 "{home}/.claude/hooks/beads_worktree_guard.py"',
                            "statusMessage": LEGACY_STATUS["beads_worktree_guard.py"],
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
    _install_known_legacy_gate(direct.parent, direct.name)
    _install_known_legacy_gate(claude_direct.parent, claude_direct.name)
    live = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "command": f'python3 "{direct}"',
                            "statusMessage": LEGACY_STATUS[direct.name],
                            "timeout": 30,
                            "type": "command",
                        },
                        {
                            "command": f"python3 {claude_direct}",
                            "statusMessage": LEGACY_STATUS[claude_direct.name],
                            "timeout": 10,
                            "type": "command",
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


def test_fingerprinted_hooks_with_wrong_registration_metadata_survive(
    tmp_path: Path,
) -> None:
    module = _module()
    codex_home = tmp_path / ".codex"
    home = tmp_path / "home"
    wrong_event = _install_known_legacy_gate(
        codex_home / "hooks", "test_oracle_brief_gate.py"
    )
    wrong_matcher = _install_known_legacy_gate(
        codex_home / "hooks", "implementation_echo_test_gate.py"
    )
    wrong_timeout = _install_known_legacy_gate(
        codex_home / "hooks", "oracle_downgrade_warning_gate.py"
    )
    wrong_status = _install_known_legacy_gate(
        home / ".claude" / "hooks", "beads_worktree_guard.py"
    )
    live = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "command": f"python3 {wrong_event}",
                            "statusMessage": LEGACY_STATUS[wrong_event.name],
                            "timeout": 30,
                            "type": "command",
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Write",
                    "hooks": [
                        {
                            "command": f"python3 {wrong_matcher}",
                            "statusMessage": LEGACY_STATUS[wrong_matcher.name],
                            "timeout": 30,
                            "type": "command",
                        }
                    ],
                },
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "command": f"python3 {wrong_timeout}",
                            "statusMessage": LEGACY_STATUS[wrong_timeout.name],
                            "timeout": 31,
                            "type": "command",
                        },
                        {
                            "command": f"python3 {wrong_status}",
                            "statusMessage": "personal status",
                            "timeout": 10,
                            "type": "command",
                        },
                        {
                            "command": f"python3 {wrong_event}",
                            "statusMessage": LEGACY_STATUS[wrong_event.name],
                            "timeout": 30,
                            "type": "personal-command",
                        },
                    ],
                },
            ],
        }
    }

    assert module.prune_hooks(
        live,
        module.plugin_owned_gate_scripts(_plugin_hooks()),
        codex_home=codex_home,
        home=home,
    ) == live


def test_personal_and_symlinked_same_name_hooks_inside_roots_survive(
    tmp_path: Path,
) -> None:
    module = _module()
    codex_home = tmp_path / ".codex"
    home = tmp_path / "home"
    personal = codex_home / "hooks" / "test_oracle_brief_gate.py"
    personal.parent.mkdir(parents=True)
    personal.write_text("# personal same-name policy\n", encoding="utf-8")
    symlink = home / ".claude" / "hooks" / "beads_worktree_guard.py"
    symlink.parent.mkdir(parents=True)
    symlink.symlink_to(ROOT / "claude" / "hooks" / "beads_worktree_guard.py")
    live = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "command": f"python3 {personal}",
                            "statusMessage": LEGACY_STATUS[personal.name],
                            "timeout": 30,
                            "type": "command",
                        },
                        {
                            "command": f"python3 {symlink}",
                            "statusMessage": LEGACY_STATUS[symlink.name],
                            "timeout": 10,
                            "type": "command",
                        },
                    ],
                }
            ]
        }
    }

    assert module.prune_hooks(
        live,
        module.plugin_owned_gate_scripts(_plugin_hooks()),
        codex_home=codex_home,
        home=home,
    ) == live


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


def test_write_aborts_if_live_hooks_change_after_inspection(tmp_path: Path) -> None:
    module = _module()
    live_path = tmp_path / "hooks.json"
    backup_path = tmp_path / "hooks.json.backup-test"
    inspected = b'{"hooks": {}}\n'
    concurrent = b'{"hooks": {}, "concurrent": true}\n'
    live_path.write_bytes(concurrent)

    with pytest.raises(RuntimeError, match="changed during migration"):
        module.write_if_unchanged(
            live_path,
            inspected=inspected,
            replacement=b'{"hooks": {"PreToolUse": []}}\n',
            backup_path=backup_path,
        )

    assert live_path.read_bytes() == concurrent
    assert not backup_path.exists()


def test_cli_entrypoint_does_not_overwrite_a_concurrent_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    plugin_path = tmp_path / "plugin-hooks.json"
    live_path = codex_home / "hooks.json"
    plugin_path.write_text(json.dumps(_plugin_hooks()), encoding="utf-8")
    live_path.write_text(
        json.dumps(_live_hooks(codex_home, tmp_path)), encoding="utf-8"
    )
    concurrent = b'{"hooks": {}, "concurrent": true}\n'
    actual_write = module.write_if_unchanged

    def race_before_replace(*args, **kwargs):
        live_path.write_bytes(concurrent)
        return actual_write(*args, **kwargs)

    monkeypatch.setattr(module, "write_if_unchanged", race_before_replace)

    with pytest.raises(RuntimeError, match="changed during migration"):
        module.main(
            [
                str(plugin_path),
                str(live_path),
                "--codex-home",
                str(codex_home),
                "--home",
                str(tmp_path),
            ]
        )

    assert live_path.read_bytes() == concurrent
    assert not list(codex_home.glob("hooks.json.backup-*"))


def test_cli_detects_race_at_prepared_replace_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    plugin_path = tmp_path / "plugin-hooks.json"
    live_path = codex_home / "hooks.json"
    plugin_path.write_text(json.dumps(_plugin_hooks()), encoding="utf-8")
    original = json.dumps(_live_hooks(codex_home, tmp_path)).encode()
    live_path.write_bytes(original)
    concurrent = b'{"hooks": {}, "late_concurrent": true}\n'
    actual_replace = module._replace_prepared

    def race_at_replace(*args, **kwargs):
        live_path.write_bytes(concurrent)
        return actual_replace(*args, **kwargs)

    monkeypatch.setattr(module, "_replace_prepared", race_at_replace)

    with pytest.raises(RuntimeError, match="changed during migration"):
        module.main(
            [
                str(plugin_path),
                str(live_path),
                "--codex-home",
                str(codex_home),
                "--home",
                str(tmp_path),
            ]
        )

    assert live_path.read_bytes() == concurrent
    [conflict] = list(codex_home.glob("hooks.json.conflict-*"))
    assert conflict.read_bytes() == concurrent
    [backup] = list(codex_home.glob("hooks.json.backup-*"))
    assert backup.read_bytes() == original


def test_cli_refuses_to_detach_symlinked_global_config(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    plugin_path = tmp_path / "plugin-hooks.json"
    target = tmp_path / "dotfiles" / "hooks.json"
    target.parent.mkdir()
    plugin_path.write_text(json.dumps(_plugin_hooks()), encoding="utf-8")
    target.write_text(json.dumps(_live_hooks(codex_home, tmp_path)), encoding="utf-8")
    live_path = codex_home / "hooks.json"
    live_path.symlink_to(target)

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
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "symlinked" in result.stderr.lower()
    assert live_path.is_symlink()
    assert target.read_text(encoding="utf-8") == json.dumps(
        _live_hooks(codex_home, tmp_path)
    )
