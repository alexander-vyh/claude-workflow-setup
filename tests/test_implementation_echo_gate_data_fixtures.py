"""Public-hook regressions for nonblocking but visible data-fixture echoes.

These tests execute the real hook in subprocesses against real Git working
trees and inspect both user-visible JSON and the persistent gate-signal corpus.
They intentionally reject both blanket fixture exemption and blanket warning.
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "claude/hooks/implementation_echo_test_gate.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--initial-branch=main")
    _git(repo, "config", "user.email", "oracle@example.com")
    _git(repo, "config", "user.name", "Oracle")
    (repo / ".beads").mkdir()
    return repo


def _opaque_literal(
    tmp_path: Path,
    label: str,
    shape: str = "uuid",
) -> str:
    """Produce an opaque value independently of the hook and its fixtures."""
    seed = f"{tmp_path}:{label}:{shape}"
    if shape == "uuid":
        return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    if shape == "salesforce":
        return "012" + digest[:15]
    if shape == "hex":
        return digest[:32]
    raise ValueError(f"unknown opaque literal shape: {shape}")


def _write_source(repo: Path, *literals: str) -> None:
    (repo / "src").mkdir()
    (repo / "src" / "service.py").write_text(
        "".join(
            f'RECORD_TYPE_ID_{index} = "{literal}"\n'
            for index, literal in enumerate(literals, start=1)
        ),
        encoding="utf-8",
    )


def _run_hook(repo: Path) -> tuple[dict | None, list[dict]]:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(repo),
        "tool_input": {"command": "git commit -m change"},
    }
    env = os.environ.copy()
    env["BEADS_DIR"] = str(repo / ".beads")
    env["GATE_SIGNAL_FALLBACK_DIR"] = str(repo / "fallback-signals")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", str(HOOK)],
        cwd=repo,
        env=env,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        timeout=7,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout) if result.stdout.strip() else None
    signal_path = repo / ".beads/.gate-signal.jsonl"
    signals = (
        [
            json.loads(line)
            for line in signal_path.read_text(encoding="utf-8").splitlines()
        ]
        if signal_path.is_file()
        else []
    )
    return output, signals


def _load_gate():
    name = "implementation_echo_test_gate_data_fixture_contract"
    spec = importlib.util.spec_from_file_location(name, HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(HOOK.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(HOOK.parent))
    return module


@pytest.mark.parametrize(
    ("fixture_path", "fixture_template", "literal_shape"),
    [
        (
            "tests/fixtures/manifest.json",
            '{"required_record_type": "{literal}"}\n',
            "uuid",
        ),
        (
            "spec/data/rows.jsonl",
            '{"record_type": "{literal}"}\n',
            "salesforce",
        ),
        (
            "test/golden/config.yaml",
            'record_type: "{literal}"\n',
            "hex",
        ),
        (
            "__tests__/fixtures/config.yml",
            'record_type: "{literal}"\n',
            "uuid",
        ),
        (
            "tests/data/settings.toml",
            'record_type = "{literal}"\n',
            "salesforce",
        ),
        (
            "spec/fixtures/settings.ini",
            'record_type = "{literal}"\n',
            "hex",
        ),
        (
            "test/data/settings.cfg",
            'record_type = "{literal}"\n',
            "uuid",
        ),
        (
            "__specs__/golden/expected.csv",
            'record_type\n"{literal}"\n',
            "salesforce",
        ),
        (
            "tests/golden/expected.tsv",
            'record_type\n"{literal}"\n',
            "hex",
        ),
    ],
)
def test_shared_literal_in_text_fixture_warns_once_without_blocking(
    tmp_path: Path,
    fixture_path: str,
    fixture_template: str,
    literal_shape: str,
) -> None:
    """Catches blanket exemption, hard-deny retention, and duplicate signals."""
    repo = _repo(tmp_path)
    literal = _opaque_literal(tmp_path, fixture_path, literal_shape)
    _write_source(repo, literal)
    fixture = repo / fixture_path
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        fixture_template.replace("{literal}", literal),
        encoding="utf-8",
    )

    output, signals = _run_hook(repo)

    assert output is not None
    assert "permissionDecision" not in output.get("hookSpecificOutput", {})
    message = output["systemMessage"]
    assert fixture_path in message
    assert "shared-generated-literal" in message
    assert "does not block" in message.lower()

    assert len(signals) == 1
    signal = signals[0]
    assert signal["gate"] == "implementation_echo_test_gate"
    assert signal["decision"] == "allow-with-warning"
    assert signal["extras"]["issue_count"] == 1
    assert signal["extras"]["issue_kinds"] == [
        "data-fixture-shared-generated-literal"
    ]
    assert signal["extras"]["fixture_files"] == [fixture_path]


@pytest.mark.parametrize(
    ("fixture_path", "fixture_template", "literal_shape"),
    [
        ("tests/data/config.yaml", "record_type: {literal}\n", "uuid"),
        ("spec/fixtures/settings.ini", "record_type = {literal}\n", "salesforce"),
        ("test/data/settings.cfg", "record_type = {literal}\n", "hex"),
        ("__tests__/golden/expected.csv", "record_type\n{literal}\n", "uuid"),
        (
            "__specs__/golden/expected.tsv",
            "record_type\tstatus\n{literal}\tok\n",
            "salesforce",
        ),
    ],
)
def test_unquoted_text_fixture_scalar_warns_without_blocking(
    tmp_path: Path,
    fixture_path: str,
    fixture_template: str,
    literal_shape: str,
) -> None:
    """Catches applying the source-code quote extractor to declarative data."""
    repo = _repo(tmp_path)
    literal = _opaque_literal(tmp_path, fixture_path, literal_shape)
    _write_source(repo, literal)
    fixture = repo / fixture_path
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        fixture_template.replace("{literal}", literal),
        encoding="utf-8",
    )

    output, signals = _run_hook(repo)

    assert output is not None
    assert "permissionDecision" not in output.get("hookSpecificOutput", {})
    assert fixture_path in output["systemMessage"]
    assert len(signals) == 1
    assert signals[0]["decision"] == "allow-with-warning"


@pytest.mark.parametrize(
    "test_path",
    [
        "tests/service.test.json",
        "spec/service.spec.yaml",
        "__tests__/service.test.csv",
    ],
)
def test_explicit_test_shaped_data_filename_still_denies(
    tmp_path: Path,
    test_path: str,
) -> None:
    """Catches extension-first classification that downgrades executable tests."""
    repo = _repo(tmp_path)
    literal = _opaque_literal(tmp_path, test_path)
    _write_source(repo, literal)
    test_file = repo / test_path
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        json.dumps({"expected_record_type": literal}) + "\n",
        encoding="utf-8",
    )

    output, signals = _run_hook(repo)

    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert len(signals) == 1
    assert signals[0]["decision"] == "deny"


def test_two_shared_literals_produce_one_decision_level_warning(
    tmp_path: Path,
) -> None:
    """Catches one-warning-per-match implementations and duplicate file lists."""
    repo = _repo(tmp_path)
    first = _opaque_literal(tmp_path, "first")
    second = _opaque_literal(tmp_path, "second", "salesforce")
    third = _opaque_literal(tmp_path, "third", "hex")
    _write_source(repo, first, second, third)
    first_fixture_path = "tests/fixtures/manifest.json"
    first_fixture = repo / first_fixture_path
    first_fixture.parent.mkdir(parents=True)
    first_fixture.write_text(
        json.dumps({"record_types": [first, second]}) + "\n",
        encoding="utf-8",
    )
    second_fixture_path = "spec/data/secondary.json"
    second_fixture = repo / second_fixture_path
    second_fixture.parent.mkdir(parents=True)
    second_fixture.write_text(
        json.dumps({"record_type": third}) + "\n",
        encoding="utf-8",
    )

    output, signals = _run_hook(repo)

    assert output is not None
    assert "permissionDecision" not in output.get("hookSpecificOutput", {})
    assert first_fixture_path in output["systemMessage"]
    assert second_fixture_path in output["systemMessage"]
    assert len(signals) == 1
    assert signals[0]["decision"] == "allow-with-warning"
    assert signals[0]["extras"]["issue_count"] == 3
    assert signals[0]["extras"]["fixture_files"] == [
        second_fixture_path,
        first_fixture_path,
    ]


def test_oversized_fixture_scan_is_nonblocking_and_reports_truncation(
    tmp_path: Path,
) -> None:
    """Catches whole-file reads with no explicit host-deadline budget."""
    repo = _repo(tmp_path)
    early_literal = _opaque_literal(tmp_path, "oversized-fixture-early")
    late_literal = _opaque_literal(tmp_path, "oversized-fixture-late")
    _write_source(repo, early_literal, late_literal)
    fixture_path = "tests/fixtures/oversized.jsonl"
    fixture = repo / fixture_path
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(
        (json.dumps({"record_type": early_literal}) + "\n").encode("utf-8")
        + b'{"padding":"x"}\n' * 700_013
        + (json.dumps({"record_type": late_literal}) + "\n").encode("utf-8")
    )

    output, signals = _run_hook(repo)

    assert output is not None
    assert "permissionDecision" not in output.get("hookSpecificOutput", {})
    assert len(signals) == 1
    assert signals[0]["decision"] == "allow-with-warning"
    assert signals[0]["extras"]["issue_count"] == 1
    assert late_literal not in output["systemMessage"]
    assert signals[0]["extras"]["fixture_scan_truncated_files"] == [
        fixture_path
    ]


def test_fixture_analysis_reads_through_a_finite_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Architecture contract: fixture reads are finite and below the host ceiling."""
    repo = _repo(tmp_path)
    literal = _opaque_literal(tmp_path, "bounded-reader")
    _write_source(repo, literal)
    first_fixture_path = "tests/fixtures/first.yaml"
    first_fixture = repo / first_fixture_path
    first_fixture.parent.mkdir(parents=True)
    first_fixture.write_bytes(
        f"record_type: {literal}\n".encode("utf-8")
        + b"padding: x\n" * 500_017
    )
    second_fixture_path = "spec/fixtures/second.yaml"
    second_fixture = repo / second_fixture_path
    second_fixture.parent.mkdir(parents=True)
    second_fixture.write_bytes(
        b"padding: y\n" * 500_019
    )
    gate = _load_gate()
    original_open = Path.open
    original_builtin_open = builtins.open
    fixture_bytes_read = 0
    guarded_fixtures = {first_fixture, second_fixture}

    class GuardedFixtureReader:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def read(self, size: int = -1):
            nonlocal fixture_bytes_read
            assert size >= 0, "fixture scanner requested an unbounded read"
            result = self.wrapped.read(size)
            fixture_bytes_read += len(result)
            assert fixture_bytes_read <= 9_000_001
            return result

        def __getattr__(self, name: str):
            return getattr(self.wrapped, name)

    def guarded_open(path: Path, *args, **kwargs):
        wrapped = original_open(path, *args, **kwargs)
        if path in guarded_fixtures:
            return GuardedFixtureReader(wrapped)
        return wrapped

    def guarded_builtin_open(file, *args, **kwargs):
        wrapped = original_builtin_open(file, *args, **kwargs)
        if Path(file) in guarded_fixtures:
            return GuardedFixtureReader(wrapped)
        return wrapped

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_builtin_open)

    _issues, _test_files, fixture_issues, _fixture_scan = gate.analyze(
        repo,
        ["src/service.py", first_fixture_path, second_fixture_path],
    )

    assert len(fixture_issues) == 1
    assert fixture_issues[0].filepath == first_fixture_path
    assert 0 < fixture_bytes_read <= 9_000_001
    assert _fixture_scan["fixture_scan_truncated_files"] == [
        second_fixture_path
    ]


def test_late_only_oversized_fixture_is_silent_but_reports_truncation(
    tmp_path: Path,
) -> None:
    """Catches hiding scan truncation when no in-budget literal is found."""
    repo = _repo(tmp_path)
    literal = _opaque_literal(tmp_path, "late-only-oversized")
    _write_source(repo, literal)
    fixture_path = "tests/fixtures/late-only.jsonl"
    fixture = repo / fixture_path
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(
        b'{"padding":"x"}\n' * 700_013
        + (json.dumps({"record_type": literal}) + "\n").encode("utf-8")
    )

    output, signals = _run_hook(repo)

    assert output is None
    assert len(signals) == 1
    assert signals[0]["decision"] == "allow"
    assert signals[0]["extras"]["fixture_scan_truncated_files"] == [
        fixture_path
    ]


def test_fixture_issue_aggregation_is_bounded_and_reports_truncation(
    tmp_path: Path,
) -> None:
    """Catches unbounded issue construction inside the ten-second hook."""
    repo = _repo(tmp_path)
    literals = [
        _opaque_literal(tmp_path, f"bounded-issue-{index}")
        for index in range(267)
    ]
    _write_source(repo, *literals)
    for index, test_root in enumerate(("tests", "spec", "__tests__")):
        fixture = repo / test_root / "fixtures" / f"many-{index}.json"
        fixture.parent.mkdir(parents=True)
        start = index * 89
        fixture.write_text(
            json.dumps({"record_types": literals[start : start + 89]}) + "\n",
            encoding="utf-8",
        )

    output, signals = _run_hook(repo)

    assert output is not None
    assert "permissionDecision" not in output.get("hookSpecificOutput", {})
    assert len(signals) == 1
    signal = signals[0]
    assert signal["decision"] == "allow-with-warning"
    assert 0 < signal["extras"]["issue_count"] <= 128
    assert signal["extras"]["fixture_issue_limit_reached"] is True


def test_executable_test_echo_still_denies(tmp_path: Path) -> None:
    """Catches an implementation that downgrades every echo to a warning."""
    repo = _repo(tmp_path)
    literal = _opaque_literal(tmp_path, "executable-test", "salesforce")
    _write_source(repo, literal)
    test_file = repo / "tests/test_service.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "def test_record_type():\n"
        f'    assert record_type() == "{literal}"\n',
        encoding="utf-8",
    )

    output, signals = _run_hook(repo)

    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "systemMessage" not in output
    assert len(signals) == 1
    assert signals[0]["decision"] == "deny"


def test_unrelated_text_fixture_stays_silent(tmp_path: Path) -> None:
    """Catches an implementation that warns on every changed fixture."""
    repo = _repo(tmp_path)
    source_literal = _opaque_literal(tmp_path, "source-only", "hex")
    fixture_literal = _opaque_literal(tmp_path, "fixture-only", "salesforce")
    _write_source(repo, source_literal)
    fixture = repo / "tests/fixtures/manifest.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps({"required_record_type": fixture_literal}) + "\n",
        encoding="utf-8",
    )

    output, signals = _run_hook(repo)

    assert output is None
    assert len(signals) == 1
    assert signals[0]["decision"] == "allow"


def test_unrelated_unquoted_fixture_scalar_stays_silent(tmp_path: Path) -> None:
    """Catches unconditional warnings in the unquoted-scalar scan path."""
    repo = _repo(tmp_path)
    source_literal = _opaque_literal(tmp_path, "source-only-unquoted", "uuid")
    fixture_literal = _opaque_literal(
        tmp_path,
        "fixture-only-unquoted",
        "salesforce",
    )
    _write_source(repo, source_literal)
    fixture = repo / "tests/data/manifest.yaml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        f"required_record_type: {fixture_literal}\n",
        encoding="utf-8",
    )

    output, signals = _run_hook(repo)

    assert output is None
    assert len(signals) == 1
    assert signals[0]["decision"] == "allow"


def test_two_fixtures_never_treat_each_other_as_production_source(
    tmp_path: Path,
) -> None:
    """Catches reclassifying data fixtures as source files."""
    repo = _repo(tmp_path)
    literal = _opaque_literal(tmp_path, "fixture-only-shared", "salesforce")
    first = repo / "tests/fixtures/manifest.json"
    second = repo / "spec/data/secondary.yaml"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text(
        json.dumps({"required_record_type": literal}) + "\n",
        encoding="utf-8",
    )
    second.write_text(
        f'record_type: "{literal}"\n',
        encoding="utf-8",
    )

    output, signals = _run_hook(repo)

    assert output is None
    assert len(signals) == 1
    assert signals[0]["decision"] == "allow"


def test_signal_write_failure_does_not_turn_fixture_warning_into_a_block(
    tmp_path: Path,
) -> None:
    """Catches direct, enforcement-coupled persistence instead of _gate_signal."""
    repo = _repo(tmp_path)
    literal = _opaque_literal(tmp_path, "unwritable-signal", "hex")
    _write_source(repo, literal)
    fixture = repo / "tests/fixtures/manifest.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps({"required_record_type": literal}) + "\n",
        encoding="utf-8",
    )
    (repo / ".beads/.gate-signal.jsonl").mkdir()

    output, signals = _run_hook(repo)

    assert output is not None
    assert "permissionDecision" not in output.get("hookSpecificOutput", {})
    assert "systemMessage" in output
    assert signals == []


def test_fixture_warning_uses_the_shared_signal_owner_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Architecture contract: persistence stays owned by _gate_signal."""
    repo = _repo(tmp_path)
    literal = _opaque_literal(tmp_path, "signal-owner", "salesforce")
    _write_source(repo, literal)
    fixture = repo / "tests/fixtures/manifest.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps({"required_record_type": literal}) + "\n",
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(repo),
        "tool_input": {"command": "git commit -m change"},
    }
    gate = _load_gate()
    recorded: list[dict] = []
    monkeypatch.setattr(
        gate,
        "_record_signal",
        lambda **fields: recorded.append(fields),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        code = gate.main()

    assert code == 0
    output = json.loads(stdout.getvalue())
    assert "permissionDecision" not in output.get("hookSpecificOutput", {})
    assert "systemMessage" in output
    assert len(recorded) == 1
    assert recorded[0]["gate_name"] == "implementation_echo_test_gate"
    assert recorded[0]["decision"] == "allow-with-warning"
