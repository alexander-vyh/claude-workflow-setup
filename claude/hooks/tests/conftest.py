"""Resolve hook modules from THIS repo, not from a deployed copy.

Several hook tests were written when hooks were installed to
``~/.claude/hooks`` and they still do::

    _hooks_dir = Path.home() / ".claude" / "hooks"
    if not _hooks_dir.exists():
        pytest.skip(...)
    sys.path.insert(0, str(_hooks_dir))

Hooks now ship from the plugin (``plugins/escapement-claude/hooks``) and are
run out of the plugin cache, so ``~/.claude/hooks`` no longer contains them.
The directory does still exist — it holds unrelated leftovers like
``statusline.sh`` — so the ``pytest.skip`` guard passes and the imports then
fail with ModuleNotFoundError. That turned 162 tests across five files into
permanent errors rather than the intended skip.

Prepending the repo's own hooks directory here fixes both halves of that:

  - the modules resolve, so the tests actually run again;
  - they resolve to *repo source*, which is what a repo test suite must
    exercise. Resolving to a deployed copy meant a repo-side regression could
    not be caught here even when these tests were green.

Test-only. Production import resolution (hooks invoked by the host with the
plugin hooks dir on sys.path) is untouched. Same reasoning as the root
``conftest.py``, which pins the repo ``_local_judge_client`` for the suite.
"""

import sys
from pathlib import Path

_REPO_HOOKS_DIR = Path(__file__).resolve().parent.parent

if _REPO_HOOKS_DIR.is_dir():
    _entry = str(_REPO_HOOKS_DIR)
    # Prepend so repo source wins over any deployed copy a test module may
    # also push onto sys.path at import time.
    if _entry in sys.path:
        sys.path.remove(_entry)
    sys.path.insert(0, _entry)
