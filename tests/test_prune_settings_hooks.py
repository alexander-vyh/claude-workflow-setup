"""Tests for scripts/prune_settings_hooks.py (escapement-ptzz).

The pruner removes from a live ``settings.json`` exactly those hook registrations
the Claude plugin now owns, so each gate fires once instead of twice. It must be
surgical: the user's *own* hooks (never shipped by escapement) survive untouched.

Oracle notes
------------
* Matching is **legacy-path aware**: the same script is registered under
  ``~/.claude/hooks/x.py`` in settings and the plugin root in the manifest, but
  an unrelated ``/custom/x.py`` with the same basename is user-owned.
* ``test_preserves_user_owned_hooks`` is the negative control: a pruner that
  removed everything under ``hooks`` would pass a naive "no duplicates" check
  while destroying the user's config.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from prune_settings_hooks import plugin_owned_scripts, prune_hooks  # noqa: E402

PLUGIN = {
    "hooks": {
        "Stop": [{"hooks": [{"command": 'python3 -B "${CLAUDE_PLUGIN_ROOT}/hooks/validate_no_shirking.py"'}]}],
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"command": 'python3 -B "${CLAUDE_PLUGIN_ROOT}/hooks/root_checkout_guard.py"'}],
            }
        ],
    }
}


SESSION_CONTEXT_PLUGIN = {
    "hooks": {
        "PreCompact": [
            {
                "hooks": [
                    {
                        "command": (
                            'python3 -B "${CLAUDE_PLUGIN_ROOT}/hooks/'
                            'escapement_session_context.py"'
                        )
                    }
                ]
            }
        ]
    }
}


def test_plugin_owned_scripts_extracts_basenames():
    assert plugin_owned_scripts(PLUGIN) == {
        "validate_no_shirking.py",
        "root_checkout_guard.py",
    }


def test_prunes_plugin_owned_registration_across_path_styles():
    settings = {
        "hooks": {
            "Stop": [{"hooks": [{"command": "python3 -B ~/.claude/hooks/validate_no_shirking.py"}]}]
        }
    }
    pruned = prune_hooks(settings, plugin_owned_scripts(PLUGIN))
    assert pruned["hooks"] == {}, "plugin-owned Stop hook should be removed, and the empty event dropped"


def test_session_context_migration_prunes_legacy_prime_command():
    settings = {
        "hooks": {
            "PreCompact": [
                {"hooks": [{"command": "bd prime"}]},
            ]
        }
    }
    pruned = prune_hooks(
        settings,
        plugin_owned_scripts(SESSION_CONTEXT_PLUGIN),
    )
    assert pruned["hooks"] == {}


def test_older_plugin_without_session_context_preserves_legacy_prime_command():
    settings = {
        "hooks": {
            "PreCompact": [
                {"matcher": "", "hooks": [{"command": "bd prime"}]},
            ]
        }
    }
    pruned = prune_hooks(settings, plugin_owned_scripts(PLUGIN))
    assert pruned == settings


def test_session_context_migration_preserves_prime_outside_lifecycle_events():
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"command": "bd prime"}],
                },
            ]
        }
    }
    pruned = prune_hooks(
        settings,
        plugin_owned_scripts(SESSION_CONTEXT_PLUGIN),
    )
    assert pruned == settings


def test_session_context_migration_preserves_personal_hook_metadata():
    personal_group = {
        "matcher": "manual",
        "custom": "preserve-me",
        "hooks": [
            {
                "type": "command",
                "command": "~/.claude/hooks/pre-compact-save.sh",
                "timeout": 17,
                "statusMessage": "Saving personal context",
            }
        ],
    }
    settings = {
        "model": "opus",
        "hooks": {
            "PreCompact": [
                personal_group,
                {"hooks": [{"command": "bd prime"}]},
            ]
        },
    }
    pruned = prune_hooks(
        settings,
        plugin_owned_scripts(SESSION_CONTEXT_PLUGIN),
    )
    assert pruned == {
        "model": "opus",
        "hooks": {"PreCompact": [personal_group]},
    }


def test_preserves_user_owned_hooks():
    """NEGATIVE CONTROL: a pruner that empties `hooks` wholesale must fail here."""
    settings = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {"command": "python3 -B ~/.claude/hooks/validate_no_shirking.py"},  # plugin-owned
                        {"command": "python3 -B ~/.claude/hooks/jixia_send_bounce.py"},     # user's own
                    ]
                }
            ]
        }
    }
    pruned = prune_hooks(settings, plugin_owned_scripts(PLUGIN))
    remaining = [h["command"] for g in pruned["hooks"]["Stop"] for h in g["hooks"]]
    assert remaining == ["python3 -B ~/.claude/hooks/jixia_send_bounce.py"]


def test_preserves_same_basename_outside_legacy_global_path():
    settings = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "command": (
                                "python3 -B /opt/personal/hooks/"
                                "validate_no_shirking.py"
                            )
                        }
                    ]
                }
            ]
        }
    }
    assert prune_hooks(settings, plugin_owned_scripts(PLUGIN)) == settings


def test_preserves_same_basename_under_another_home_claude_directory():
    settings = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "python3 -B /opt/personal/.claude/hooks/"
                                "validate_no_shirking.py"
                            ),
                            "timeout": 23,
                        }
                    ]
                }
            ]
        }
    }
    assert prune_hooks(settings, plugin_owned_scripts(PLUGIN)) == settings


def test_preserves_non_hook_settings_keys_verbatim():
    settings = {"model": "opus", "permissions": {"allow": ["Bash"]}, "hooks": {}}
    pruned = prune_hooks(settings, plugin_owned_scripts(PLUGIN))
    assert pruned["model"] == "opus"
    assert pruned["permissions"] == {"allow": ["Bash"]}


def test_is_idempotent():
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"command": "python3 -B ~/.claude/hooks/root_checkout_guard.py"}]}
            ]
        }
    }
    owned = plugin_owned_scripts(PLUGIN)
    once = prune_hooks(settings, owned)
    twice = prune_hooks(once, owned)
    assert once == twice


def test_does_not_mutate_input():
    settings = {"hooks": {"Stop": [{"hooks": [{"command": "python3 -B ~/.claude/hooks/validate_no_shirking.py"}]}]}}
    before = str(settings)
    prune_hooks(settings, plugin_owned_scripts(PLUGIN))
    assert str(settings) == before


def test_prunes_project_bootstrap_which_has_no_hooks_dir_prefix():
    """project-bootstrap.sh sits at ~/.claude/, not ~/.claude/hooks/ — basename matching must still catch it."""
    plugin = {"hooks": {"SessionStart": [{"hooks": [{"command": 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/project-bootstrap.sh"'}]}]}}
    settings = {"hooks": {"SessionStart": [{"hooks": [{"command": "~/.claude/project-bootstrap.sh"}]}]}}
    pruned = prune_hooks(settings, plugin_owned_scripts(plugin))
    assert pruned["hooks"] == {}
