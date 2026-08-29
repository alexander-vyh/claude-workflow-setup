"""Every module a deployed hook imports must exist beside it.

Business outcome
----------------
A gate that is installed but cannot import its own helper does not crash — it
fails open, silently, exactly where it was needed. The user sees a hook that is
"deployed" and a file that grows past the limit anyway.

This has now happened twice in this repo. `git_change_scope.py` carries a
comment in the renderer saying so ("Omitting this sibling makes installed hooks
fail open"), and `_codex_patch.py` would have repeated it: the source tree
imports resolve because every hook sits in `claude/hooks/`, so nothing local
fails. The rendered plugin roots are flat copies of a hand-maintained subset,
and a missing name only shows up in a live session.

Independent source of truth
---------------------------
The rendered plugin directories themselves, read the way Python would resolve
`from X import Y` at hook runtime — not the renderer's SHARED_HOOK_SUPPORT set,
which is the thing that gets forgotten.

Rejects
-------
- A helper added to claude/hooks/ and imported, but never registered for
  vendoring -> the import in the deployed copy resolves to nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HOOKS = ROOT / "claude" / "hooks"

PLUGIN_HOOK_DIRS = [
    ROOT / "plugins" / "escapement-claude" / "hooks",
    ROOT / "plugins" / "escapement" / "claude" / "hooks",
    ROOT / "plugins" / "escapement-pi" / "claude" / "hooks",
]


def sibling_module_names() -> set[str]:
    """Modules that live in claude/hooks/ and can only resolve as siblings."""
    return {p.stem for p in SOURCE_HOOKS.glob("*.py")}


def imported_siblings(path: Path, siblings: set[str]) -> set[str]:
    """Sibling modules this file imports, including inside functions.

    Hooks import helpers lazily (inside try/except, after a sys.path insert) so
    a missing helper degrades instead of crashing. ast.walk sees those too — a
    lazy import is exactly the one that fails silently in production.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a syntax error is another test's job
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            head = node.module.split(".")[0]
            if head in siblings:
                found.add(head)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split(".")[0]
                if head in siblings:
                    found.add(head)
    return found


@pytest.mark.parametrize("hooks_dir", PLUGIN_HOOK_DIRS, ids=lambda p: p.parts[-3])
def test_every_deployed_hook_can_import_its_helpers(hooks_dir: Path):
    if not hooks_dir.is_dir():
        pytest.skip(f"{hooks_dir} is not rendered in this plugin")

    siblings = sibling_module_names()
    present = {p.stem for p in hooks_dir.glob("*.py")}
    missing: list[str] = []

    for deployed in sorted(hooks_dir.glob("*.py")):
        for needed in sorted(imported_siblings(deployed, siblings)):
            if needed not in present:
                missing.append(f"{deployed.name} imports {needed}, not vendored here")

    assert not missing, (
        "deployed hooks would fail open on a missing sibling:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd the module to SHARED_HOOK_SUPPORT in tools/render_agent_surfaces.py."
    )


def test_the_check_would_catch_a_missing_helper(tmp_path):
    """Negative control: prove the detector fires, not just that today is clean."""
    fake = tmp_path / "hooks"
    fake.mkdir()
    (fake / "some_gate.py").write_text(
        "def go():\n    from _gate_signal import record\n    return record\n"
    )
    siblings = sibling_module_names()
    assert "_gate_signal" in imported_siblings(fake / "some_gate.py", siblings)
    assert "_gate_signal" not in {p.stem for p in fake.glob("*.py")}
