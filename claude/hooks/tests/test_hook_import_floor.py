"""Every hook must import under the oldest interpreter that can invoke it.

Business outcome
----------------
Hooks are invoked as ``python3 -B <hook>``, so the interpreter is whatever PATH
resolves at the moment Claude Code, Codex or Pi runs them. macOS ships
/usr/bin/python3 as 3.9. A hook that raises on import does not announce itself --
it just stops gating, silently, for as long as the wrong interpreter is on PATH.
Five hooks were in that state (discovery-gate, spec_id_enforcement, tdd-gate,
test_reminder, validate_no_shirking): they used PEP 604 ``X | None`` annotations
with no ``from __future__ import annotations``.

Why this is an IMPORT check and not a syntax check
--------------------------------------------------
PEP 604 annotations are evaluated when the ``def`` statement executes, not when
the module is compiled. ``py_compile`` reports every one of these files as fine
under 3.9; only importing surfaces the TypeError. A linter or AST pass would have
given a clean bill of health.

Invalid solution classes rejected here
--------------------------------------
- checking syntax instead of import -> test_a_pep604_hook_without_the_future_import_is_caught
- passing because the runner happens to be new -> the AST check runs on any version
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

HOOKS = pathlib.Path(__file__).resolve().parents[1]
# The floor is macOS's system interpreter, the oldest that can plausibly be
# resolved by a bare `python3` on a supported machine.
FLOOR = (3, 9)


def _uses_pep604_annotations(tree: ast.AST) -> bool:
    """True when the module evaluates an ``X | Y`` annotation at def time."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        annotations = [a.annotation for a in node.args.args if a.annotation]
        annotations += [a.annotation for a in node.args.kwonlyargs if a.annotation]
        if node.returns is not None:
            annotations.append(node.returns)
        for ann in annotations:
            for sub in ast.walk(ann):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                    return True
    return False


def _has_future_annotations(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(a.name == "annotations" for a in node.names):
                return True
    return False


def _hook_files() -> list[pathlib.Path]:
    return sorted(p for p in HOOKS.glob("*.py") if p.is_file())


def test_the_scan_finds_hooks_at_all():
    """Positive control: an empty file list would make the test below vacuous."""
    files = _hook_files()
    assert len(files) > 20, f"expected the hook directory to be populated, found {len(files)}"


@pytest.mark.parametrize("hook", _hook_files(), ids=lambda p: p.name)
def test_pep604_annotations_require_the_future_import(hook: pathlib.Path):
    """A hook using ``X | None`` must import on the floor interpreter.

    This is deliberately an AST check rather than a subprocess import, so it runs
    identically on every CI interpreter instead of only catching the problem when
    the suite happens to be run on an old one.
    """
    tree = ast.parse(hook.read_text(encoding="utf-8", errors="replace"))
    if not _uses_pep604_annotations(tree):
        return
    assert _has_future_annotations(tree), (
        f"{hook.name} evaluates a PEP 604 'X | Y' annotation at def time but has no "
        f"'from __future__ import annotations'. It raises TypeError on import under "
        f"Python {FLOOR[0]}.{FLOOR[1]}, and a hook that fails to import stops gating "
        f"silently. Add the __future__ import."
    )


def test_a_pep604_hook_without_the_future_import_is_caught(tmp_path):
    """Negative control: the detector must actually fire on the bad shape.

    Without this, a bug in _uses_pep604_annotations would make every assertion
    above pass vacuously.
    """
    bad = tmp_path / "bad_hook.py"
    bad.write_text("def f(x: str | None) -> int | None:\n    return None\n")
    tree = ast.parse(bad.read_text())
    assert _uses_pep604_annotations(tree), "detector must see the PEP 604 annotation"
    assert not _has_future_annotations(tree), "detector must see the missing import"

    good = tmp_path / "good_hook.py"
    good.write_text(
        "from __future__ import annotations\n\n"
        "def f(x: str | None) -> int | None:\n    return None\n"
    )
    tree_good = ast.parse(good.read_text())
    assert _uses_pep604_annotations(tree_good)
    assert _has_future_annotations(tree_good), "detector must accept the fixed shape"
