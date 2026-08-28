#!/usr/bin/env python3
"""Record an independent review verdict against a bead.

This is the escape path `review_gate.py` names in every denial, so it has to be
runnable by an agent with no extra setup and no user escalation:

    python3 -B <this file> record --bead <id> --findings-file <path>
    python3 -B <this file> record --bead <id> --findings "<verdict text>"

It writes the verdict into Beads (`metadata.escapement_review`) together with a
git work fingerprint, which is what lets the gate later distinguish "this work
was reviewed" from "some earlier version of this work was reviewed".

Independence stamping is capability-honest. On Claude, an `Agent` PreToolUse
hook has already logged whether a structurally isolated reviewer was dispatched
for this bead, so the record is stamped `independent: true` and the gate can
insist on it. On Codex no such event exists, so the record is stamped
`independent: "unverified"` — an accurate statement of what the host can
observe, not a silent pass disguised as a check.

Refusing to write is the point of the validation here: a record that fails the
substance bar is rejected at the moment it is created, with the reason, rather
than accepted and then denied later at `bd close` where the context that
produced it is gone.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_ledger import has_dispatch  # noqa: E402
from _review_record import (  # noqa: E402
    INDEPENDENT_REVIEWER_TYPES,
    build_record,
    validate_findings,
    work_fingerprint,
    write_record,
)


def _read_findings(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Return (findings, error)."""
    if args.findings_file:
        path = Path(args.findings_file)
        if not path.is_file():
            return None, f"--findings-file {args.findings_file!r} does not exist."
        try:
            return path.read_text(encoding="utf-8", errors="replace"), None
        except OSError as exc:
            return None, f"could not read {args.findings_file!r}: {exc}"
    return args.findings, None


def cmd_record(args: argparse.Namespace) -> int:
    bead_id = args.bead.strip()
    if not bead_id:
        print("error: --bead is required", file=sys.stderr)
        return 2

    findings, err = _read_findings(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    ok, verr = validate_findings(findings, bead_id)
    if not ok:
        print(
            f"error: refusing to record a review of {bead_id}: {verr}",
            file=sys.stderr,
        )
        return 1

    reviewer = (args.reviewer or "").strip() or "unspecified"

    # Corroboration is only claimable where the host can observe a dispatch,
    # AND only for a dispatch that read THIS state of the work. Passing the
    # fingerprint is what binds review->record: without it, an implementer
    # could have a reviewer read state A, rewrite everything, and record at
    # state B with the reviewer's blessing still attached.
    fingerprint = work_fingerprint(args.cwd)
    if has_dispatch(bead_id, os.environ.get("CLAUDE_SESSION_ID"), fingerprint):
        independent: object = True
    else:
        independent = "unverified"

    record = build_record(
        bead_id=bead_id,
        findings=findings or "",
        reviewer=reviewer,
        fingerprint=fingerprint,
        recorded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        host=args.host,
    )
    record["independent"] = independent

    if not write_record(bead_id, record, cwd=args.cwd):
        print(
            f"error: could not write the review record to {bead_id}. Is `bd` "
            "available and is this a beads workspace?",
            file=sys.stderr,
        )
        return 1

    print(f"Recorded independent review of {bead_id} (reviewer={reviewer}).")
    if independent is not True:
        print(
            "  independence: unverified — no isolated reviewer dispatch matching "
            "the current state of the work was observable for this bead.\n"
            "  Either no reviewer was dispatched, or the code changed after it "
            "read it — a reviewer that read an earlier state does not vouch for "
            "this one.\n"
            "  On Claude, dispatch one of "
            f"{', '.join(sorted(INDEPENDENT_REVIEWER_TYPES))} naming {bead_id} "
            "and re-record without editing in between; on Codex this is the "
            "honest maximum."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="escapement-review",
        description="Record an independent review verdict against a bead.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="record a review verdict")
    rec.add_argument("--bead", required=True, help="bead id under review")
    source = rec.add_mutually_exclusive_group(required=True)
    source.add_argument("--findings-file", help="path to the reviewer's verdict")
    source.add_argument("--findings", help="the reviewer's verdict as text")
    rec.add_argument(
        "--reviewer",
        help="reviewer identity, e.g. adversarial-reviewer",
    )
    rec.add_argument("--cwd", help="work tree to fingerprint (default: cwd)")
    rec.add_argument("--host", default="cli", help="recording host label")
    rec.set_defaults(func=cmd_record)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
