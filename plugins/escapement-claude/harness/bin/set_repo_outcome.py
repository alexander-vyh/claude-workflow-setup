#!/usr/bin/env python3
"""set-repo-outcome — validated authoring helper for `.escapement/repo.json`.

Named as a deferred "Future Increment" in the repo-outcome-authorization design
doc: the declaration previously had to be hand-authored, with no check against a
typo'd `intended_outcome` or a non-boolean `auto_merge_on_green`. This is that
promised helper — it writes a schema-valid file or refuses with a concrete reason,
never a partially-valid one.

With no flags, writes the conservative default (`pr-opened`, no auto-merge) — this
is what `scripts/project-bootstrap.sh`'s `bootstrap_outcome` step invokes when a
repo has no declaration at all, so the file is never silently absent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repo_outcome import INTENDED_OUTCOME_LADDER, declaration_path  # noqa: E402

_MERGE_MIN_INDEX = INTENDED_OUTCOME_LADDER.index("merged")


def build_declaration(
    *,
    intended_outcome: str = "pr-opened",
    auto_merge_on_green: bool = False,
    deploy_on: str | None = None,
    deploy_surface: str | None = None,
    confirm_class: list[str] | None = None,
) -> dict:
    if intended_outcome not in INTENDED_OUTCOME_LADDER:
        raise ValueError(
            f"invalid intended_outcome {intended_outcome!r}; expected one of "
            f"{list(INTENDED_OUTCOME_LADDER)}"
        )
    if auto_merge_on_green and INTENDED_OUTCOME_LADDER.index(intended_outcome) < _MERGE_MIN_INDEX:
        raise ValueError(
            f"auto_merge_on_green=true requires intended_outcome at or above 'merged' "
            f"(got {intended_outcome!r}) — a repo that only wants a PR opened cannot "
            f"also authorize auto-merge; raise --intended-outcome or drop --auto-merge"
        )

    declaration: dict = {
        "intended_outcome": intended_outcome,
        "auto_merge_on_green": bool(auto_merge_on_green),
    }
    if deploy_on or deploy_surface:
        deploy: dict = {}
        if deploy_on:
            deploy["on"] = deploy_on
        if deploy_surface:
            deploy["surface"] = deploy_surface
        declaration["deploy"] = deploy
    if confirm_class:
        declaration["confirm_class"] = list(confirm_class)
    return declaration


def write_declaration(repo_root: Path, declaration: dict, *, force: bool = False) -> Path:
    path = declaration_path(repo_root)
    if path.exists() and not force:
        raise FileExistsError(
            f"{path} already exists — pass --force to overwrite an existing declaration"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(declaration, indent=2) + "\n", encoding="utf-8")
    return path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="repo root to write .escapement/repo.json into")
    parser.add_argument(
        "--intended-outcome",
        default="pr-opened",
        choices=list(INTENDED_OUTCOME_LADDER),
        help="how far 'done' reaches in this repo (default: pr-opened, the conservative default)",
    )
    parser.add_argument(
        "--auto-merge",
        action="store_true",
        help="authorize agents to merge a green PR without asking (requires --intended-outcome merged or higher)",
    )
    parser.add_argument("--deploy-on", default=None, help="informational: what triggers deploy, e.g. push-to-main")
    parser.add_argument("--deploy-surface", default=None, help="informational: the live surface, e.g. 'Cloud Run exec dashboard'")
    parser.add_argument(
        "--confirm-class",
        action="append",
        default=None,
        help="reserved change-kind metadata; stored but not currently enforced (repeatable)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing declaration")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        declaration = build_declaration(
            intended_outcome=args.intended_outcome,
            auto_merge_on_green=args.auto_merge,
            deploy_on=args.deploy_on,
            deploy_surface=args.deploy_surface,
            confirm_class=args.confirm_class,
        )
        path = write_declaration(Path(args.repo_root), declaration, force=args.force)
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"set-repo-outcome: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
