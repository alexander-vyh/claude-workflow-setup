#!/usr/bin/env python3
"""Schema/cross-state and module-boundary oracle for execution persistence."""

from __future__ import annotations

import ast
import copy
import importlib
import json
import pathlib
import sys

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import execution_ledger as ledger_api  # noqa: E402


def valid_ledger() -> dict:
    return {
        "version": 1,
        "parent_session_id": "parent-7",
        "updated_at": "2026-08-09T20:05:00Z",
        "executions": [
            {
                "bead_id": "escapement-e3ai.2",
                "execution_id": "exec-alpha",
                "host": "codex",
                "agent_name": "worker",
                "native_child_id": "child-native-1",
                "dispatch_tool_use_id": "call-44",
                "attempt": 1,
                "generation": 1,
                "state": "running",
                "queued_at": "2026-08-09T20:00:00Z",
                "started_at": "2026-08-09T20:00:20Z",
                "last_activity_at": "2026-08-09T20:04:00Z",
                "last_activity_kind": "tool_completed",
                "start_deadline": "2026-08-09T20:02:00Z",
                "idle_deadline": "2026-08-09T20:19:00Z",
                "hard_deadline": "2026-08-09T22:00:00Z",
                "reconcile_due": None,
                "terminal_at": None,
                "terminal_reason": None,
                "terminal_event_id": None,
                "result_digest": None,
                "watchdog_id": "watch-exec-alpha",
                "recovery_count": 0,
                "recovery_claim": None,
                "result_application": {
                    "state": "unapplied",
                    "claim": None,
                    "claim_generation": 0,
                    "idempotency_key": "execution:exec-alpha:attempt:1:generation:1",
                    "applied_at": None,
                },
            }
        ],
        "incidents": [],
    }


def applying_terminal() -> dict:
    ledger = valid_ledger()
    item = ledger["executions"][0]
    item["state"] = "terminal"
    item["terminal_at"] = "2026-08-09T20:05:00Z"
    item["terminal_reason"] = "completed"
    item["terminal_event_id"] = "terminal-900"
    item["result_digest"] = "sha256:result-a"
    item["result_application"] = {
        "state": "applying",
        "claim": {
            "owner": "applier-a",
            "execution_id": "exec-alpha",
            "attempt": 1,
            "generation": 1,
            "claim_generation": 1,
            "claimed_at": "2026-08-09T20:06:00Z",
            "expires_at": "2026-08-09T20:06:30Z",
        },
        "claim_generation": 1,
        "idempotency_key": "execution:exec-alpha:attempt:1:generation:1",
        "applied_at": None,
    }
    return ledger


def write(path: pathlib.Path, ledger: dict) -> None:
    path.write_text(json.dumps(ledger))
    path.chmod(0o600)


def test_complete_running_execution_is_trusted_positive_control(tmp_path) -> None:
    ledger = valid_ledger()
    path = tmp_path / "executions.json"
    write(path, ledger)

    assert ledger_api.load_trusted(path, "parent-7") == ledger


@pytest.mark.parametrize(
    (
        "activity_kind",
        "started_at",
        "last_activity_at",
        "idle_deadline",
        "updated_at",
    ),
    [
        (
            "child_started",
            "2026-08-09T20:00:20Z",
            "2026-08-09T20:00:20Z",
            "2026-08-09T20:15:20Z",
            "2026-08-09T20:00:20Z",
        ),
        (
            "checkpoint",
            "2026-08-09T20:00:20Z",
            "2026-08-09T20:04:00Z",
            "2026-08-09T20:19:00Z",
            "2026-08-09T20:04:00Z",
        ),
    ],
)
def test_each_valid_running_activity_variant_is_trusted(
    tmp_path,
    activity_kind,
    started_at,
    last_activity_at,
    idle_deadline,
    updated_at,
) -> None:
    ledger = valid_ledger()
    item = ledger["executions"][0]
    item["started_at"] = started_at
    item["last_activity_at"] = last_activity_at
    item["last_activity_kind"] = activity_kind
    item["idle_deadline"] = idle_deadline
    ledger["updated_at"] = updated_at
    path = tmp_path / "executions.json"
    write(path, ledger)

    assert ledger_api.load_trusted(path, "parent-7") == ledger


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("native_child_id", None),
        ("native_child_id", ""),
        ("started_at", None),
        ("last_activity_at", None),
        ("last_activity_kind", None),
        ("last_activity_kind", "status_polled"),
        ("last_activity_kind", "tool_started"),
        ("last_activity_kind", "semantic_annotation"),
        ("started_at", "2026-08-09T20:04:01Z"),
        ("idle_deadline", "2026-08-09T20:18:59Z"),
        ("idle_deadline", "2026-08-09T20:19:01Z"),
    ],
)
def test_each_invalid_running_state_invariant_is_unresolved(
    tmp_path, field, invalid_value
) -> None:
    ledger = valid_ledger()
    ledger["executions"][0][field] = invalid_value
    path = tmp_path / "executions.json"
    write(path, ledger)

    assert ledger_api.load_trusted(path, "parent-7") is None


@pytest.mark.parametrize(
    "field_path",
    [
        ("version",),
        ("executions", 0, "attempt"),
        ("executions", 0, "generation"),
        ("executions", 0, "recovery_count"),
        ("executions", 0, "result_application", "claim_generation"),
    ],
)
def test_boolean_is_never_a_valid_integer_field(tmp_path, field_path) -> None:
    ledger = valid_ledger()
    target = ledger
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = True
    execution = ledger["executions"][0]
    execution["result_application"]["idempotency_key"] = (
        f"execution:exec-alpha:attempt:{execution['attempt']}:"
        f"generation:{execution['generation']}"
    )
    path = tmp_path / "executions.json"
    write(path, ledger)
    assert ledger_api.load_trusted(path, "parent-7") is None


@pytest.mark.parametrize(
    "claim_field",
    ["attempt", "generation", "claim_generation"],
)
def test_boolean_is_rejected_inside_application_claim(tmp_path, claim_field) -> None:
    ledger = applying_terminal()
    ledger["executions"][0]["result_application"]["claim"][claim_field] = True
    path = tmp_path / "executions.json"
    write(path, ledger)
    assert ledger_api.load_trusted(path, "parent-7") is None


@pytest.mark.parametrize("claim_field", ["attempt", "generation"])
def test_boolean_is_rejected_inside_recovery_claim(tmp_path, claim_field) -> None:
    ledger = valid_ledger()
    ledger["executions"][0]["recovery_claim"] = {
        "owner": "supervisor-a",
        "execution_id": "exec-alpha",
        "attempt": 1,
        "generation": 1,
        "claimed_at": "2026-08-09T20:06:00Z",
        "expires_at": "2026-08-09T20:06:30Z",
    }
    ledger["executions"][0]["recovery_claim"][claim_field] = True
    path = tmp_path / "executions.json"
    write(path, ledger)
    assert ledger_api.load_trusted(path, "parent-7") is None


@pytest.mark.parametrize(
    "mutation",
    [
        "applying_on_running",
        "applying_without_result",
        "terminal_without_native_child",
        "terminal_without_terminal_time",
        "running_with_terminal_evidence",
        "applied_without_result",
    ],
)
def test_cross_state_application_and_terminal_inconsistency_is_unresolved(
    tmp_path, mutation
) -> None:
    ledger = applying_terminal()
    item = ledger["executions"][0]
    if mutation == "applying_on_running":
        item["state"] = "running"
        item["terminal_at"] = None
        item["terminal_reason"] = None
        item["terminal_event_id"] = None
    elif mutation == "applying_without_result":
        item["result_digest"] = None
    elif mutation == "terminal_without_native_child":
        item["native_child_id"] = None
    elif mutation == "terminal_without_terminal_time":
        item["terminal_at"] = None
    elif mutation == "running_with_terminal_evidence":
        item["state"] = "running"
        item["result_application"] = valid_ledger()["executions"][0][
            "result_application"
        ]
    elif mutation == "applied_without_result":
        item["result_digest"] = None
        item["result_application"]["state"] = "applied"
        item["result_application"]["claim"] = None
        item["result_application"]["applied_at"] = "2026-08-09T20:07:00Z"
    path = tmp_path / "executions.json"
    write(path, ledger)
    assert ledger_api.load_trusted(path, "parent-7") is None


def test_validation_and_store_are_owned_by_focused_sibling_modules() -> None:
    validation = importlib.import_module("execution_validation")
    store = importlib.import_module("execution_store")

    assert validation.is_valid_ledger(valid_ledger()) is True
    invalid = copy.deepcopy(valid_ledger())
    invalid["version"] = True
    assert validation.is_valid_ledger(invalid) is False
    assert ledger_api.load_trusted is store.load_trusted
    assert ledger_api.mutate_atomic is store.mutate_atomic
    assert store.load_trusted.__module__ == "execution_store"
    assert store.mutate_atomic.__module__ == "execution_store"


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _defined_functions(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text())


def _top_level_function(path: pathlib.Path, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in _tree(path).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1, f"{name} must be defined exactly once in {path.name}"
    return matches[0]


def _import_bindings(tree: ast.AST) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bindings[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bindings


def _qualified_name(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        owner = _qualified_name(node.value, bindings)
        return f"{owner}.{node.attr}" if owner else None
    return None


def _semantic_references(path: pathlib.Path) -> set[str]:
    tree = _tree(path)
    bindings = _import_bindings(tree)
    references = {
        name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Call, ast.Attribute))
        if (
            name := _qualified_name(
                node.func if isinstance(node, ast.Call) else node, bindings
            )
        )
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _qualified_name(node.func, bindings) == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = _qualified_name(node.args[0], bindings)
            if owner:
                references.add(f"{owner}.{node.args[1].value}")
    return references


def _local_execution_dependencies(path: pathlib.Path) -> set[str]:
    return {
        imported.split(".")[0]
        for imported in _imports(path)
        if imported.split(".")[0].startswith("execution_")
    }


def test_extracted_modules_have_one_way_ownership_not_token_forwarding() -> None:
    ledger_path = BIN / "execution_ledger.py"
    validation_path = BIN / "execution_validation.py"
    store_path = BIN / "execution_store.py"

    assert "execution_ledger" not in _imports(validation_path)
    assert "execution_ledger" not in _imports(store_path)
    assert "execution_store" in _imports(ledger_path)
    assert "execution_validation" in _imports(store_path)
    assert _defined_functions(ledger_path).isdisjoint(
        {
            "_valid_timestamp",
            "_valid_claim",
            "_valid_execution",
            "_valid_ledger",
            "is_valid_ledger",
            "load_trusted",
            "mutate_atomic",
        }
    )


def test_execution_module_dependencies_match_one_way_allowlist() -> None:
    expected = {
        "execution_validation.py": set(),
        "execution_store.py": {"execution_validation"},
        "execution_ledger.py": {"execution_store"},
        "result_application.py": {"execution_store"},
    }
    assert {
        filename: _local_execution_dependencies(BIN / filename) for filename in expected
    } == expected


def test_execution_modules_reject_dynamic_import_escape_hatches() -> None:
    paths = [
        BIN / "execution_validation.py",
        BIN / "execution_store.py",
        BIN / "execution_ledger.py",
        BIN / "result_application.py",
    ]
    for path in paths:
        imports = _imports(path)
        references = _semantic_references(path)
        assert not any(
            name == "importlib" or name.startswith("importlib.") for name in imports
        ), path
        assert "__import__" not in references, path
        assert "importlib.import_module" not in references, path


def test_public_validation_and_store_functions_own_nontrivial_implementation() -> None:
    validation_path = BIN / "execution_validation.py"
    store_path = BIN / "execution_store.py"
    validation_tree = _tree(validation_path)
    store_tree = _tree(store_path)
    validation_bindings = _import_bindings(validation_tree)
    store_bindings = _import_bindings(store_tree)
    validator = _top_level_function(validation_path, "is_valid_ledger")
    loader = _top_level_function(store_path, "load_trusted")
    mutator = _top_level_function(store_path, "mutate_atomic")

    validator_calls = {
        _qualified_name(node.func, validation_bindings)
        for node in ast.walk(validator)
        if isinstance(node, ast.Call)
    }
    validator_strings = {
        node.value
        for node in ast.walk(validator)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "isinstance" in validator_calls
    assert len([node for node in ast.walk(validator) if isinstance(node, ast.If)]) >= 2
    assert {
        "version",
        "parent_session_id",
        "updated_at",
        "executions",
        "incidents",
    } <= validator_strings

    loader_references = {
        name
        for node in ast.walk(loader)
        if isinstance(node, (ast.Call, ast.Attribute))
        if (
            name := _qualified_name(
                node.func if isinstance(node, ast.Call) else node,
                store_bindings,
            )
        )
    }
    assert "trusted_source.is_trusted_file" in loader_references
    assert "execution_validation.is_valid_ledger" in loader_references

    mutator_references = {
        name
        for node in ast.walk(mutator)
        if isinstance(node, (ast.Call, ast.Attribute))
        if (
            name := _qualified_name(
                node.func if isinstance(node, ast.Call) else node,
                store_bindings,
            )
        )
    }
    assert {
        "fcntl.flock",
        "tempfile.NamedTemporaryFile",
        "os.fsync",
        "os.replace",
    } <= mutator_references


def test_public_application_boundary_directly_uses_the_trusted_loader() -> None:
    application_path = BIN / "result_application.py"
    application_tree = _tree(application_path)
    bindings = _import_bindings(application_tree)
    boundary = _top_level_function(application_path, "apply_verified_result")
    references = {
        name
        for node in ast.walk(boundary)
        if isinstance(node, (ast.Call, ast.Attribute))
        if (
            name := _qualified_name(
                node.func if isinstance(node, ast.Call) else node,
                bindings,
            )
        )
    }

    assert "execution_store.load_trusted" in references


def test_storage_primitives_exist_only_in_execution_store() -> None:
    paths = {
        "execution_validation.py": BIN / "execution_validation.py",
        "execution_store.py": BIN / "execution_store.py",
        "execution_ledger.py": BIN / "execution_ledger.py",
        "result_application.py": BIN / "result_application.py",
    }
    storage_primitives = {
        "fcntl.flock",
        "tempfile.NamedTemporaryFile",
        "os.replace",
        "os.fsync",
        "os.open",
        "os.fdopen",
        "trusted_source.is_trusted_file",
    }
    references = {
        name: _semantic_references(path) & storage_primitives
        for name, path in paths.items()
    }
    assert references["execution_store.py"] >= {
        "fcntl.flock",
        "tempfile.NamedTemporaryFile",
        "os.replace",
        "os.fsync",
        "trusted_source.is_trusted_file",
    }
    assert all(
        not used for name, used in references.items() if name != "execution_store.py"
    ), references


def test_execution_modules_stay_below_repository_soft_line_limit() -> None:
    paths = [
        BIN / "execution_ledger.py",
        BIN / "execution_validation.py",
        BIN / "execution_store.py",
        BIN / "result_application.py",
    ]
    counts = {path.name: len(path.read_text().splitlines()) for path in paths}
    assert all(count <= 500 for count in counts.values()), counts
