#!/usr/bin/env python3
"""Derive a continuation-harness contract from a beads issue (fxh.10).

Collapses the write-side "triplicate authoring" lean violation: a unit of work no
longer needs its goal + oracle hand-authored a third time via
`init_contract.py --goal --verify`. Instead the bead declares its oracle ONCE, in
the place acceptance criteria already live, and the contract is derived from it.

Convention
----------
A bead declares its machine oracle as a fenced ```verify block inside its
`acceptance_criteria` (the text returned by `bd show <id> --json`):

    Creating a tracked task produces a valid harness contract with ZERO
    additional hand-authoring.

    ```verify
    python3 -m pytest harness/tests/test_contract_derivation.py -q
    ```

`goal` is taken from the bead title; `verification_command` is the extracted
command; `source` is "bead-derived".

Fail-closed (never-suppress + gate-design Rule 3)
-------------------------------------------------
A bead with no ```verify block — or a trivial one (`true` / `:` / `echo x`) —
raises OracleNotDeclared and writes NOTHING. Derivation never invents a passing
oracle; the same `is_trivial_oracle` guard that screens hand-authored contracts
screens derived ones, so there is one definition of "real oracle".

Usage:
  derive_contract.py --bead <issue-id>
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from init_contract import build_contract, is_trivial_oracle  # noqa: E402
from would_block_stop import InvalidActorIdentity, harness_home, thread_dir_for_session  # noqa: E402

# A fenced block whose info string is exactly `verify` (optionally surrounded by
# whitespace). The captured group is the command body. Non-greedy so the first
# block wins and the closing fence is the nearest one.
_VERIFY_BLOCK_RE = re.compile(
    r"```[ \t]*verify[ \t]*\r?\n(.*?)\r?\n```",
    re.DOTALL | re.IGNORECASE,
)


class OracleNotDeclared(Exception):
    """Raised when a bead declares no usable oracle — derivation fails closed."""


def extract_verify_oracle(acceptance_criteria: "str | None") -> "str | None":
    """Return the command inside the bead's ```verify block, or None if absent.

    Only a fence tagged `verify` counts — a plain ``` example block is ignored, so
    illustrative code in acceptance criteria is never mistaken for an oracle.
    """
    if not acceptance_criteria or not isinstance(acceptance_criteria, str):
        return None
    m = _VERIFY_BLOCK_RE.search(acceptance_criteria)
    if not m:
        return None
    command = m.group(1).strip()
    return command or None


def derive_contract(bead: dict, *, session_id: "str | None" = None) -> dict:
    """Build a contract dict from a bead record (shape of `bd show --json`[0]).

    Raises OracleNotDeclared if the bead declares no oracle or a trivial one — the
    caller must NOT fall back to writing a passing contract.
    """
    acceptance = bead.get("acceptance_criteria") or bead.get("acceptance")
    oracle = extract_verify_oracle(acceptance)
    if oracle is None:
        raise OracleNotDeclared(
            f"bead {bead.get('id', '<unknown>')!r} declares no ```verify oracle in its "
            "acceptance criteria. Add a fenced ```verify block whose command's exit code "
            "proves the outcome — derivation will not invent one."
        )
    trivial_reason = is_trivial_oracle(oracle)
    if trivial_reason is not None:
        raise OracleNotDeclared(
            f"bead {bead.get('id', '<unknown>')!r} ```verify oracle is not a real oracle: "
            f"{trivial_reason}"
        )

    goal = (bead.get("title") or "").strip()
    bead_id = bead.get("id")
    return build_contract(
        goal,
        oracle,
        source="bead-derived",
        session_id=session_id,
        thread_id=bead_id,
    )


def fetch_bead(bead_id: str) -> dict:
    """Fetch a bead record via `bd show <id> --json` (first element)."""
    proc = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    if isinstance(data, list):
        if not data:
            raise OracleNotDeclared(f"bead {bead_id!r} not found")
        return data[0]
    return data


def main(argv: list[str], _fetch=fetch_bead) -> int:
    parser = argparse.ArgumentParser(description="Derive a harness contract from a bead.")
    parser.add_argument("--bead", required=True, help="Beads issue id to derive from.")
    args = parser.parse_args(argv)

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    try:
        bead = _fetch(args.bead)
        contract = derive_contract(bead, session_id=session_id)
    except OracleNotDeclared as exc:
        # Fail closed: write nothing, non-zero exit. The Stop gate stays blocked
        # rather than unlocking on a phantom oracle.
        print(f"refusing to derive contract: {exc}", file=sys.stderr)
        return 2
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"could not read bead {args.bead!r}: {exc}", file=sys.stderr)
        return 1

    try:
        thread_dir = thread_dir_for_session(session_id, harness_home())
    except InvalidActorIdentity as exc:
        print(f"refusing to derive contract: invalid actor identity: {exc}", file=sys.stderr)
        return 2
    thread_dir.mkdir(parents=True, exist_ok=True)
    out = thread_dir / "contract.json"
    with out.open("w") as f:
        json.dump(contract, f, indent=2)

    print(f"contract derived from {args.bead} -> {out}")
    print(json.dumps(contract, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
