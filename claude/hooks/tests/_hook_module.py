"""One owner for loading a hook module into a test.

Hook files are loaded by path (several have hyphens and none are importable as
packages), and each loader registers the result in ``sys.modules`` so that
``mock.patch("<name>.attr")`` can resolve. When two test files load the same hook
under the same name, the second registration silently replaces the first, and the
first file's patches then apply to a module object its tests never call. That is
invisible: it depends on collection order, so it passes locally under one
ordering and fails in CI under another. It cost 10 green-to-red tests in
escapement #214.

``load_hook`` reuses an existing registration when it came from the same file, so
every test file shares one module object regardless of import order.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType


def load_hook(name: str, path: str | pathlib.Path) -> ModuleType:
    """Load the hook at ``path`` as module ``name``, reusing any equivalent load.

    Reuse requires the registered module to have come from the same file, so a
    layout that deliberately loads a different copy under the same name still
    gets its own object rather than silently inheriting someone else's.
    """
    resolved = pathlib.Path(path).resolve()
    existing = sys.modules.get(name)
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file and pathlib.Path(existing_file).resolve() == resolved:
            return existing

    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load hook {name} from {resolved}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
