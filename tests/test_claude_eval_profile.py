import json
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "claude-eval"
SETTINGS = PROFILE / "settings.json"
WORKFLOW_SETTINGS = PROFILE / "settings.workflow.json"
MANIFEST = PROFILE / "manifest.json"
INSTALLER = PROFILE / "install.sh"
DOCTOR = PROFILE / "doctor.py"


def _commands(path: Path = SETTINGS) -> list[str]:
    settings = json.loads(path.read_text())
    return [
        hook["command"]
        for event_items in settings["hooks"].values()
        for item in event_items
        for hook in item.get("hooks", [])
    ]


def _refs(command: str) -> list[str]:
    return re.findall(r"~/.claude/([^\"' ]+)", command)


def _source_for_ref(ref: str) -> Path:
    first = Path(ref).parts[0]
    if first == "hooks":
        return ROOT / "claude" / ref
    if first in {"skills", "rules", "commands", "agents"}:
        return ROOT / "claude" / ref
    if first == "harness":
        return ROOT / ref
    return ROOT / ref


def test_claude_eval_profile_json_is_valid():
    json.loads(SETTINGS.read_text())
    json.loads(WORKFLOW_SETTINGS.read_text())
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["host"] == "claude-code"
    assert manifest["auth_included"] is False


def test_claude_eval_profile_wires_required_gates():
    manifest = json.loads(MANIFEST.read_text())
    commands = _commands()

    for required in manifest["required_hooks"]:
        assert any(required in command for command in commands), required

    settings = json.loads(SETTINGS.read_text())
    assert "SessionStart" not in settings["hooks"]
    assert "Stop" in settings["hooks"]


def test_claude_eval_workflow_profile_wires_escapement_context_and_openspec():
    manifest = json.loads(MANIFEST.read_text())
    commands = _commands(WORKFLOW_SETTINGS)

    for required in manifest["required_workflow_hooks"]:
        assert any(required in command for command in commands), required

    settings = json.loads(WORKFLOW_SETTINGS.read_text())
    assert "SessionStart" in settings["hooks"]
    assert "PreCompact" in settings["hooks"]
    assert "Stop" in settings["hooks"]
    assert all("bd prime" not in command for command in commands)
    assert sum(
        "escapement_session_context.py" in command for command in commands
    ) == 2

    matchers = [
        item.get("matcher")
        for event_items in settings["hooks"].values()
        for item in event_items
    ]
    for matcher in (
        "Bash(bd create:*)",
        "Bash(bd close:*)",
        "Bash(openspec:*)",
        "Bash(git worktree add:*)",
        "Bash(gh pr create:*)",
    ):
        assert matcher in matchers


def test_claude_eval_profile_commands_reference_packaged_sources():
    for command in _commands() + _commands(WORKFLOW_SETTINGS):
        refs = _refs(command)
        assert refs, f"hook command should reference ~/.claude: {command}"
        for ref in refs:
            assert _source_for_ref(ref).exists(), ref


def test_claude_eval_profile_python_hooks_disable_bytecode():
    commands = _commands() + _commands(WORKFLOW_SETTINGS)
    offenders = [command for command in commands if command.startswith("python3 ~/.claude/")]
    assert offenders == []
    assert any(command.startswith("python3 -B ~/.claude/") for command in commands)


def test_claude_eval_profile_has_no_private_auth_or_host_paths():
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (SETTINGS, WORKFLOW_SETTINGS, MANIFEST, PROFILE / "install.sh")
    )
    for forbidden in (
        "/Users/",
        "refresh_token",
        "sessionToken",
        "apiKey",
        "sk-ant-",
    ):
        assert forbidden not in text


def test_claude_eval_profile_installer_is_scratch_safe_by_default():
    installer = PROFILE / "install.sh"
    text = installer.read_text()
    assert "--target PATH" in text
    assert "--profile NAME" in text
    assert "--beads-target PATH" in text
    assert "Refusing to replace existing path without --force" in text
    assert "rm -rf" not in text


def test_claude_eval_workflow_profile_declares_beads_artifacts():
    manifest = json.loads(MANIFEST.read_text())
    assert "beads_formulas" in manifest["installed_surfaces"]
    for path in (
        ROOT / "beads/formulas/mol-feature.formula.json",
        ROOT / "beads/formulas/mol-rapid.formula.json",
        ROOT / "beads/mol-status.sh",
    ):
        assert path.exists()


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("profile", ["gates", "workflow"])
def test_claude_eval_profiles_install_and_doctor_validate(profile, tmp_path):
    target = tmp_path / ".claude"
    beads_target = tmp_path / ".beads"

    install = _run([
        "bash",
        str(INSTALLER),
        "--mode",
        "copy",
        "--profile",
        profile,
        "--target",
        str(target),
        "--beads-target",
        str(beads_target),
    ])

    assert install.returncode == 0, install.stderr
    assert (target / "settings.json").is_file()
    assert (target / "hooks" / "validate_no_shirking.py").is_file()
    assert (target / "harness" / "bin" / "stop_hook.py").is_file()
    assert "/Users/" not in "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in target.rglob("*")
        if path.is_file()
    )

    doctor_args = ["python3", str(DOCTOR), "--target", str(target), "--profile", profile]
    if profile == "workflow":
        doctor_args.extend(["--beads-target", str(beads_target)])
        assert (beads_target / "formulas" / "mol-feature.formula.json").is_file()
    doctor = _run(doctor_args)
    assert doctor.returncode == 0, doctor.stderr


def test_claude_eval_installer_refuses_existing_target_without_force(tmp_path):
    target = tmp_path / ".claude"
    target.mkdir()
    sentinel = target / "settings.json"
    sentinel.write_text("preserve me", encoding="utf-8")

    result = _run([
        "bash",
        str(INSTALLER),
        "--mode",
        "copy",
        "--target",
        str(target),
    ])

    assert result.returncode != 0
    assert "Refusing to replace existing path without --force" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_claude_eval_doctor_fails_when_installed_dependency_missing(tmp_path):
    target = tmp_path / ".claude"
    install = _run([
        "bash",
        str(INSTALLER),
        "--mode",
        "copy",
        "--target",
        str(target),
    ])
    assert install.returncode == 0, install.stderr

    missing = target / "hooks" / "validate_no_shirking.py"
    missing.unlink()

    doctor = _run(["python3", str(DOCTOR), "--target", str(target)])
    assert doctor.returncode != 0
    assert "installed target missing hook dependency" in doctor.stderr
