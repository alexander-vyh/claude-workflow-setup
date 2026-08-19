"""Unit tests for the repository-owned ``claude/hooks/tdd-gate.py``.

Tests:
- is_test_file: correctly classifies test file paths
- is_exempt_file: correctly classifies exempt files (config, docs, scripts)
- main: exemption fast-paths, git/test-dir guards, TDD nudge logic

Run from anywhere with:
  python -m pytest claude/hooks/tests/test_tdd_gate.py -v
"""

import importlib
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

_hooks_dir = Path(__file__).resolve().parents[1]

if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

# tdd-gate uses a hyphen — import via importlib
_spec = importlib.util.spec_from_file_location("tdd_gate", _hooks_dir / "tdd-gate.py")
tdd_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tdd_gate)
# Register in sys.modules so patch() can resolve "tdd_gate.*" targets.
sys.modules["tdd_gate"] = tdd_gate

is_test_file = tdd_gate.is_test_file
is_exempt_file = tdd_gate.is_exempt_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERENA_EDIT_TOOLS = {
    "mcp__serena__replace_symbol_body",
    "mcp__serena__insert_after_symbol",
    "mcp__serena__insert_before_symbol",
}


def _run(tool_name: str, file_path: str) -> tuple[bool, dict | None]:
    """Run main() with a PreToolUse gated-tool event.

    Returns (asked, output_dict) where asked=True means the hook emitted an
    'ask' permissionDecision. Returns (False, None) on allow (exit 0, no JSON).
    """
    # Serena tools use relative_path, not file_path.
    path_key = "relative_path" if tool_name in _SERENA_EDIT_TOOLS else "file_path"
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {path_key: file_path},
    }
    stdout_capture = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        with patch("sys.stdout", stdout_capture):
            try:
                tdd_gate.main()
            except SystemExit as exc:
                if exc.code == 2:
                    return True, None  # hard block (shouldn't happen for tdd-gate)

    out = stdout_capture.getvalue().strip()
    if out:
        data = json.loads(out)
        decision = (
            data.get("hookSpecificOutput", {}).get("permissionDecision", "")
        )
        if decision == "ask":
            return True, data
    return False, None


def _run_allowed(tool_name: str, file_path: str) -> bool:
    """Return True if the hook allowed (no ask, no block)."""
    asked, _ = _run(tool_name, file_path)
    return not asked


# ---------------------------------------------------------------------------
# is_test_file
# ---------------------------------------------------------------------------

class TestIsTestFile:
    def test_python_test_prefix(self):
        assert is_test_file("test_foo.py")

    def test_python_test_suffix(self):
        assert is_test_file("foo_test.py")

    def test_conftest(self):
        assert is_test_file("conftest.py")

    def test_js_spec(self):
        assert is_test_file("foo.spec.js")

    def test_ts_test(self):
        assert is_test_file("foo.test.ts")

    def test_go_test(self):
        assert is_test_file("handler_test.go")

    def test_in_tests_dir(self):
        assert is_test_file("tests/helpers.py")

    def test_in_test_dir(self):
        assert is_test_file("test/utils.rb")

    def test_in_dunder_tests(self):
        assert is_test_file("__tests__/Button.tsx")

    def test_implementation_file_not_test(self):
        assert not is_test_file("src/auth.py")

    def test_models_not_test(self):
        assert not is_test_file("app/models/user.rb")


# ---------------------------------------------------------------------------
# is_exempt_file
# ---------------------------------------------------------------------------

class TestIsExemptFile:
    def test_toml_exempt(self):
        assert is_exempt_file("pyproject.toml")

    def test_yaml_exempt(self):
        assert is_exempt_file("config.yaml")

    def test_json_exempt(self):
        assert is_exempt_file("package.json")

    def test_md_exempt(self):
        assert is_exempt_file("README.md")

    def test_sh_exempt(self):
        assert is_exempt_file("deploy.sh")

    def test_sql_exempt(self):
        assert is_exempt_file("migrations/0001.sql")

    def test_scripts_dir_exempt(self):
        assert is_exempt_file("scripts/seed.py")

    def test_bin_dir_exempt(self):
        assert is_exempt_file("bin/run.py")

    def test_spike_dir_exempt(self):
        assert is_exempt_file("spike/experiment.py")

    def test_dunder_init_exempt(self):
        assert is_exempt_file("src/__init__.py")

    def test_implementation_py_not_exempt(self):
        assert not is_exempt_file("src/auth.py")

    def test_tsx_not_exempt(self):
        assert not is_exempt_file("components/Button.tsx")


# ---------------------------------------------------------------------------
# main() — fast-path exemptions (no git calls needed)
# ---------------------------------------------------------------------------

class TestMainExemptions:
    def test_non_pretooluse_allowed(self):
        payload = {"hook_event_name": "Stop", "tool_name": "Write",
                   "tool_input": {"file_path": "/repo/src/app.py"}}
        with patch("sys.stdin", io.StringIO(json.dumps(payload))):
            result = tdd_gate.main()
        assert result == 0

    def test_non_write_edit_allowed(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Read",
                   "tool_input": {"file_path": "/repo/src/app.py"}}
        with patch("sys.stdin", io.StringIO(json.dumps(payload))):
            result = tdd_gate.main()
        assert result == 0

    def test_missing_file_path_allowed(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                   "tool_input": {}}
        with patch("sys.stdin", io.StringIO(json.dumps(payload))):
            result = tdd_gate.main()
        assert result == 0

    def test_writing_test_file_always_allowed(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=[]),
        ):
            assert _run_allowed("Write", "/repo/tests/test_auth.py")

    def test_exempt_extension_allowed(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
        ):
            assert _run_allowed("Write", "/repo/pyproject.toml")

    def test_exempt_directory_allowed(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
        ):
            assert _run_allowed("Write", "/repo/scripts/seed.py")

    def test_init_file_allowed(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
        ):
            assert _run_allowed("Write", "/repo/src/__init__.py")


# ---------------------------------------------------------------------------
# main() — git and test-dir guards
# ---------------------------------------------------------------------------

class TestMainGitGuards:
    def test_no_git_repo_allowed(self):
        with patch("tdd_gate.find_git_root", return_value=None):
            assert _run_allowed("Write", "/tmp/scratch/app.py")

    def test_no_tests_dir_and_no_project_manifest_allowed(self):
        """A docs or config repo is not "actual dev" — stay quiet there.

        Explicit spec change: test-infrastructure absence alone no longer
        exempts a repo, because that made the gate silent on every greenfield
        project's first write. A project manifest now also counts.
        """
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=False),
            patch("tdd_gate.has_project_manifest", return_value=False),
        ):
            assert _run_allowed("Write", "/repo/src/app.py")


# ---------------------------------------------------------------------------
# main() — TDD enforcement
# ---------------------------------------------------------------------------

class TestMainTDDEnforcement:
    def test_no_test_changes_asks(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=["src/other.py"]),
        ):
            asked, output = _run("Write", "/repo/src/app.py")
        assert asked
        assert "TDD" in json.dumps(output)

    def test_test_file_modified_allowed(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=["tests/test_app.py"]),
        ):
            assert _run_allowed("Write", "/repo/src/app.py")

    def test_staged_test_file_allowed(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=["tests/test_auth.py", "src/other.py"]),
        ):
            assert _run_allowed("Edit", "/repo/src/auth.py")

    def test_edit_tool_also_enforced(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=[]),
        ):
            asked, _ = _run("Edit", "/repo/src/models.py")
        assert asked

    def test_ask_message_includes_filename(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=[]),
        ):
            asked, output = _run("Write", "/repo/src/app.py")
        assert asked
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        assert "src/app.py" in reason

    def test_ask_decision_not_deny(self):
        # tdd-gate uses 'ask', never 'deny' — user can always override
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=[]),
        ):
            asked, output = _run("Write", "/repo/src/app.py")
        assert asked
        decision = output["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"
        assert decision != "deny"

    def test_decision_honored_exactly_once(self):
        """CANONICAL DECISION CONTRACT: the gate signals its decision with a
        single mechanism — one permissionDecision JSON document on stdout AND
        exit 0 (NOT exit 2). A JSON decision *plus* a non-zero exit is a
        contradictory double-signal; asserting exit 0 rejects that shape, and
        ``json.loads`` raises on two stacked documents, rejecting a doubled
        signal. This is the regression guard for fxh.7.
        """
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/repo/src/app.py"},
        }
        stdout_capture = io.StringIO()
        exit_code = 0
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=[]),
            patch("sys.stdin", io.StringIO(json.dumps(payload))),
            patch("sys.stdout", stdout_capture),
        ):
            try:
                ret = tdd_gate.main()
                exit_code = ret if ret is not None else 0
            except SystemExit as exc:
                exit_code = exc.code or 0
        # exit 0, not 2 — the decision rides on stdout JSON, not the exit code
        assert exit_code == 0, (
            "decision is carried by the stdout JSON, not exit 2 — "
            "a permissionDecision JSON plus a non-zero exit is a double-signal"
        )
        out = stdout_capture.getvalue().strip()
        # exactly one JSON document: json.loads raises on two stacked documents
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "ask"


# ---------------------------------------------------------------------------
# Serena edit tools (replace_symbol_body, insert_after_symbol, insert_before_symbol)
# and NotebookEdit are gated alongside Write/Edit.
# ---------------------------------------------------------------------------

class TestGatedSerenaTools:
    def test_serena_replace_symbol_body_gated(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=[]),
        ):
            asked, _ = _run("mcp__serena__replace_symbol_body", "/repo/src/auth.py")
        assert asked

    def test_serena_insert_after_symbol_gated(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=[]),
        ):
            asked, _ = _run("mcp__serena__insert_after_symbol", "/repo/src/models.py")
        assert asked

    def test_serena_with_test_file_modified_allowed(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=["tests/test_auth.py"]),
        ):
            assert _run_allowed("mcp__serena__replace_symbol_body", "/repo/src/auth.py")

    def test_notebook_edit_gated(self):
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=[]),
        ):
            asked, _ = _run("NotebookEdit", "/repo/src/analysis.py")
        assert asked


# ---------------------------------------------------------------------------
# Expanded test-infrastructure detection
# ---------------------------------------------------------------------------

class TestExpandedTestInfraDetection:
    def test_test_dir_singular_allowed(self):
        """test/ (singular) should count as test infra."""
        with (
            patch("tdd_gate.find_git_root", return_value="/repo"),
            patch("tdd_gate.has_tests_directory", return_value=True),
            patch("tdd_gate.get_modified_files", return_value=[]),
        ):
            asked, _ = _run("Write", "/repo/src/app.py")
        assert asked  # still no test changes, but infra exists

    def test_cargo_toml_triggers_infra_detection(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "Cargo.toml").write_text("[package]\nname = \"foo\"")
            assert tdd_gate.has_tests_directory(tmpdir)

    def test_go_mod_triggers_infra_detection(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "go.mod").write_text("module example.com/foo\n\ngo 1.21")
            assert tdd_gate.has_tests_directory(tmpdir)

    def test_mix_exs_triggers_infra_detection(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "mix.exs").write_text("defmodule Foo.MixProject do\nend")
            assert tdd_gate.has_tests_directory(tmpdir)

    def test_package_json_with_test_script_triggers_infra(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg = {"name": "foo", "scripts": {"test": "jest"}}
            (Path(tmpdir) / "package.json").write_text(json.dumps(pkg))
            assert tdd_gate.has_tests_directory(tmpdir)

    def test_package_json_without_test_script_no_infra(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg = {"name": "foo", "scripts": {"build": "tsc"}}
            (Path(tmpdir) / "package.json").write_text(json.dumps(pkg))
            # No test script AND no tests/ dir → no infra
            assert not tdd_gate.has_tests_directory(tmpdir)

    def test_spec_dir_triggers_infra_detection(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "spec").mkdir()
            assert tdd_gate.has_tests_directory(tmpdir)

    def test_no_markers_returns_false(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            assert not tdd_gate.has_tests_directory(tmpdir)


# ---------------------------------------------------------------------------
# main() — greenfield projects
#
# A gate that only fires once tests/ already exists is a regression guard, not
# a genesis guard: it stays silent for exactly the writes that decide whether
# a project has tests at all. These use a real git repo on disk rather than
# patching the git helpers, so the shape being asserted is the shape a fresh
# project actually has.
# ---------------------------------------------------------------------------

class TestGreenfieldProjects:
    @staticmethod
    def _repo(tmp_path: Path, *relative_paths: str) -> Path:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        for relative in relative_paths:
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        return tmp_path

    def test_first_write_in_a_fresh_python_project_asks(self, tmp_path):
        repo = self._repo(tmp_path, "pyproject.toml")
        with patch("tdd_gate.get_modified_files", return_value=[]):
            asked, output = _run("Write", str(repo / "slot_validator" / "validator.py"))
        assert asked
        assert "TDD" in json.dumps(output)

    def test_second_write_still_asks_while_no_test_exists(self, tmp_path):
        """The gap compounds: having skipped tests once, the repo still has no
        tests/ dir, so the old exemption would keep the gate silent forever."""
        repo = self._repo(tmp_path, "pyproject.toml", "slot_validator/validator.py")
        with patch("tdd_gate.get_modified_files", return_value=["slot_validator/validator.py"]):
            asked, _ = _run("Write", str(repo / "slot_validator" / "rules.py"))
        assert asked

    def test_writing_the_test_first_is_allowed_before_any_tests_dir_exists(self, tmp_path):
        repo = self._repo(tmp_path, "pyproject.toml")
        assert _run_allowed("Write", str(repo / "tests" / "test_validator.py"))

    def test_manifest_project_with_a_touched_test_file_is_allowed(self, tmp_path):
        repo = self._repo(tmp_path, "pyproject.toml")
        with patch("tdd_gate.get_modified_files", return_value=["tests/test_validator.py"]):
            assert _run_allowed("Write", str(repo / "slot_validator" / "validator.py"))

    def test_docs_only_repo_stays_quiet(self, tmp_path):
        """Negative control: no manifest, no tests — not a code project."""
        repo = self._repo(tmp_path, "README.md", "docs/guide.md")
        with patch("tdd_gate.get_modified_files", return_value=[]):
            assert _run_allowed("Write", str(repo / "tools" / "render.py"))

    def test_repo_with_only_a_dotfile_stays_quiet(self, tmp_path):
        repo = self._repo(tmp_path, ".gitignore")
        with patch("tdd_gate.get_modified_files", return_value=[]):
            assert _run_allowed("Write", str(repo / "app.py"))

    def test_exempt_file_types_are_still_exempt_in_a_manifest_project(self, tmp_path):
        repo = self._repo(tmp_path, "pyproject.toml")
        with patch("tdd_gate.get_modified_files", return_value=[]):
            assert _run_allowed("Write", str(repo / "config.yaml"))


class TestProjectManifestDetection:
    @staticmethod
    def _root(tmp_path: Path, name: str) -> str:
        (tmp_path / name).write_text("", encoding="utf-8")
        return str(tmp_path)

    def test_recognizes_common_manifests(self, tmp_path):
        for index, name in enumerate((
            "pyproject.toml", "setup.py", "package.json",
            "Cargo.toml", "go.mod", "Gemfile", "pom.xml",
        )):
            root = tmp_path / str(index)
            root.mkdir()
            assert tdd_gate.has_project_manifest(self._root(root, name)), name

    def test_ignores_prose_and_config(self, tmp_path):
        for index, name in enumerate(("README.md", "LICENSE", ".gitignore", "notes.txt")):
            root = tmp_path / f"n{index}"
            root.mkdir()
            assert not tdd_gate.has_project_manifest(self._root(root, name)), name

    def test_directory_named_like_a_manifest_does_not_count(self, tmp_path):
        (tmp_path / "package.json").mkdir()
        assert not tdd_gate.has_project_manifest(str(tmp_path))
