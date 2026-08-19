#!/usr/bin/env python3
"""Recurrence / half-life analysis over the gate-signal log.

Closes the gate-signal *learning loop*. Per
`claude/rules/gate-design.md` Rule 2 every gate appends a record to
`.beads/.gate-signal.jsonl`, and per the bureaucracy principle's
**Operating Rule 1** ("every rule has a half-life") the half-life
review must look at *which reasons recur* to decide whether a gate's
heuristic should be updated or the rule itself revised. Writing
signal that nobody aggregates is petrification wearing a learning-loop
costume — this script is the missing consumer.

It is deliberately distinct from its sibling readers:

  - `gate_signal_query.py` lists per-gate counts and sample reasons for
    one repo / window. It does not cluster recurring reasons or rank
    them by recency.
  - `gate_signal_monitor.py` aggregates raw counts across every repo
    under ~/GitHub. It does not produce a recurrence ranking or a
    half-life verdict per reason.

This script answers the two questions Operating Rule 1 actually asks:

  1. **Recurrence** — which gates fire most (firing frequency), and
     which *reason texts* recur across firings? A reason that recurs
     many times is labeled training data: the gate's heuristic is
     hitting the same case repeatedly and may want to be updated.
  2. **Half-life** (``--half-life``) — for each recurring reason, is it
     *still* recurring recently or has it gone quiet? A reason that
     recurs and is still active argues for revising the heuristic now;
     a reason that recurred historically but has been silent for a long
     time is a candidate for retirement (the rule it protected may be
     petrified). The half-life report ranks recurring reasons by both
     recurrence count and recency so the reviewer can triage.

## Usage

    python3 claude/bin/gate_signal_analysis.py            # recurrence report
    python3 claude/bin/gate_signal_analysis.py --half-life # + half-life triage
    python3 claude/bin/gate_signal_analysis.py --since 30d
    python3 claude/bin/gate_signal_analysis.py --json
    python3 claude/bin/gate_signal_analysis.py --self-test # behavioral controls

## Failure behavior

Fails *soft* when the signal file is missing or empty: it prints a
notice and still emits the report headers (with zero rows) so the
learning loop is observably closed even before any signal exists, and
the script always exits 0 in that case. A missing log is a normal
early state, not an error — mirrors the fail-soft contract of the
writer in `claude/hooks/_gate_signal.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_SIGNAL_FILENAME = ".gate-signal.jsonl"
_BEADS_DIR_ENV = "BEADS_DIR"

# Decisions that carry the labeled-corpus reasons Operating Rule 1
# cares about most. A waiver reason is an asserted exception; a deny
# reason is the case the gate keeps catching. Both are the signal the
# half-life review reads. 'allow'/'nudge' reasons are boilerplate
# ("named agent on team") and would drown the recurrence ranking, so
# the recurrence analysis weights the corpus decisions by default.
_CORPUS_DECISIONS = frozenset({"deny", "waiver-accepted", "ask"})


def _resolve_signal_path() -> Path | None:
    """Find the .beads directory and return the signal file path.

    Mirrors `_gate_signal._resolve_signal_path`: prefer BEADS_DIR, else
    walk up from CWD looking for .beads/. Returns None if none found.
    """
    beads_dir_env = os.environ.get(_BEADS_DIR_ENV)
    if beads_dir_env:
        candidate = Path(beads_dir_env)
        if candidate.is_dir():
            return candidate / _SIGNAL_FILENAME

    cwd = Path(os.getcwd()).resolve()
    for parent in [cwd, *cwd.parents]:
        beads = parent / ".beads"
        if beads.is_dir():
            return beads / _SIGNAL_FILENAME

    return None


def _signal_sources() -> list[Path]:
    """Every existing signal file, unioned across both sinks.

    Defers to claude/hooks/_gate_signal.py, which owns path resolution on the
    write side. The mirror above covers only `.beads/`, so on its own it
    reports a half-corpus wherever gates fired without a beads dir.
    """
    hooks_dir = Path(__file__).resolve().parent.parent / "hooks"
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))
    try:
        from _gate_signal import signal_sources  # type: ignore
    except ImportError:
        primary = _resolve_signal_path()
        return [primary] if primary is not None and primary.is_file() else []
    return signal_sources()


def _parse_since(token: str) -> timedelta:
    """Parse '1d', '7d', '24h', '30m' into a timedelta."""
    if not token:
        return timedelta(days=365)
    suffix_map = {"d": "days", "h": "hours", "m": "minutes"}
    suffix = token[-1].lower()
    if suffix not in suffix_map:
        raise ValueError(f"unrecognized suffix in --since '{token}'")
    try:
        value = int(token[:-1])
    except ValueError as e:
        raise ValueError(f"non-integer value in --since '{token}'") from e
    return timedelta(**{suffix_map[suffix]: value})


def _normalize_reason(reason: str) -> str:
    """Collapse a reason text to a recurrence key.

    Two reason strings recur "the same way" even when they embed
    different volatile literals (a placeholder token, a char count, a
    file path, a quoted value). Normalizing those out lets the
    recurrence ranking see that e.g. five distinct
    "waiver reason '<x>' is a placeholder" denials are one recurring
    case, which is exactly the signal the half-life review wants.
    """
    r = reason.strip().lower()
    # quoted literals -> placeholder
    r = re.sub(r"'[^']*'", "'<v>'", r)
    r = re.sub(r'"[^"]*"', '"<v>"', r)
    # numbers (char counts, line numbers, ids) -> N
    r = re.sub(r"\d+", "N", r)
    # filesystem-ish paths -> <path>
    r = re.sub(r"\S+/\S+", "<path>", r)
    # collapse whitespace
    r = re.sub(r"\s+", " ", r).strip()
    return r


def _parse_ts(ts_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _read_entries(signal_path: Path, since: timedelta) -> list[dict[str, Any]]:
    """Read JSONL entries within the time window. Fail soft on bad lines."""
    if not signal_path.is_file():
        return []

    cutoff = datetime.now(timezone.utc) - since
    entries: list[dict[str, Any]] = []
    with open(signal_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # skip corrupted lines; logging is best-effort
            ts = _parse_ts(entry.get("ts", ""))
            if ts is None or ts < cutoff:
                continue
            entries.append(entry)
    return entries


def analyze(
    entries: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute the recurrence / half-life model from signal entries.

    Returns a dict with:
      - frequency: firings per gate (all decisions)
      - recurrence: ranked list of normalized reasons that recur
        (count >= 2) drawn from the corpus decisions, each carrying the
        gate(s) it came from, total count, and the days since it last
        fired.
      - half_life: per recurring reason, a triage verdict:
          * 'active'   — recurs and fired within the last 7 days
                         (update the heuristic; this case is live)
          * 'cooling'  — recurs but last fired 7-30 days ago
          * 'dormant'  — recurs historically but silent >30 days
                         (candidate for retirement; possibly petrified)
    """
    now = now or datetime.now(timezone.utc)

    frequency: Counter[str] = Counter()
    # normalized-reason -> aggregate
    agg: dict[str, dict[str, Any]] = {}

    for entry in entries:
        gate = entry.get("gate", "?")
        decision = entry.get("decision", "?")
        frequency[gate] += 1

        if decision not in _CORPUS_DECISIONS:
            continue
        reason = entry.get("reason", "")
        if not reason:
            continue
        key = _normalize_reason(reason)
        if not key:
            continue
        ts = _parse_ts(entry.get("ts", ""))

        slot = agg.setdefault(
            key,
            {
                "reason": key,
                "count": 0,
                "gates": Counter(),
                "decisions": Counter(),
                "last_ts": None,
                "example": reason.strip(),
            },
        )
        slot["count"] += 1
        slot["gates"][gate] += 1
        slot["decisions"][decision] += 1
        if ts is not None and (slot["last_ts"] is None or ts > slot["last_ts"]):
            slot["last_ts"] = ts

    recurrence: list[dict[str, Any]] = []
    half_life: list[dict[str, Any]] = []
    for slot in agg.values():
        if slot["count"] < 2:
            continue  # recurrence requires >= 2 firings
        last_ts: datetime | None = slot["last_ts"]
        days_since = None
        verdict = "unknown"
        if last_ts is not None:
            days_since = (now - last_ts).total_seconds() / 86400.0
            if days_since <= 7:
                verdict = "active"
            elif days_since <= 30:
                verdict = "cooling"
            else:
                verdict = "dormant"
        row = {
            "reason": slot["reason"],
            "example": slot["example"],
            "count": slot["count"],
            "gates": dict(slot["gates"]),
            "decisions": dict(slot["decisions"]),
            "days_since_last": round(days_since, 1) if days_since is not None else None,
            "verdict": verdict,
        }
        recurrence.append(row)
        half_life.append(row)

    # Rank recurrence by count desc, then most-recent first.
    recurrence.sort(key=lambda r: (-r["count"], r["days_since_last"] or 1e9))
    # Half-life triage: surface active+recurring first (act now), then
    # cooling, then dormant (retire candidates).
    _verdict_order = {"active": 0, "cooling": 1, "dormant": 2, "unknown": 3}
    half_life.sort(key=lambda r: (_verdict_order.get(r["verdict"], 9), -r["count"]))

    return {
        "total_entries": len(entries),
        "frequency": dict(frequency),
        "recurrence": recurrence,
        "half_life": half_life,
    }


def _print_human(model: dict[str, Any], since_text: str, with_half_life: bool) -> None:
    print(f"Gate-signal recurrence report (window: last {since_text})")
    print(f"  total entries analyzed: {model['total_entries']}")
    print()

    print("  gate firing frequency:")
    freq = model["frequency"]
    if not freq:
        print("    (no firings in window)")
    else:
        for gate, count in sorted(freq.items(), key=lambda x: -x[1]):
            print(f"    {count:>5}  {gate}")
    print()

    print("  recurring reasons (normalized, count >= 2 — labeled corpus")
    print("  for Operating Rule 1 half-life review):")
    rec = model["recurrence"]
    if not rec:
        print("    (no recurring reasons yet)")
    else:
        for row in rec:
            gates = ",".join(sorted(row["gates"]))
            print(f"    x{row['count']:<4} [{gates}] {row['example']}")

    if with_half_life:
        print()
        print("  half-life triage (recurring reasons by recency):")
        print("    active=heuristic is live, update it · "
              "cooling=watch · dormant=retire candidate (possibly petrified)")
        hl = model["half_life"]
        if not hl:
            print("    (nothing to triage — no recurring reasons)")
        else:
            for row in hl:
                ds = row["days_since_last"]
                ds_text = f"{ds}d ago" if ds is not None else "unknown"
                gates = ",".join(sorted(row["gates"]))
                print(f"    [{row['verdict']:>7}] x{row['count']:<4} "
                      f"last {ds_text:>10}  [{gates}] {row['example']}")


def _run_self_test() -> int:
    """Behavioral controls: positive (recurrence is detected) + negative
    (a single non-recurring reason is NOT reported as recurring, and a
    missing file fails soft with exit 0). Run with --self-test.
    """
    failures: list[str] = []
    now = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)

    def iso(days_ago: float) -> str:
        return (now - timedelta(days=days_ago)).isoformat()

    # --- positive control: a reason that recurs 3x (with volatile
    # literals that must normalize to one key) and is recent -> must be
    # ranked as recurring AND verdict 'active'.
    entries = [
        {"ts": iso(0.5), "gate": "named_agents", "decision": "deny",
         "reason": "waiver reason 'tbd' is a placeholder"},
        {"ts": iso(1.0), "gate": "named_agents", "decision": "deny",
         "reason": "waiver reason 'wip' is a placeholder"},
        {"ts": iso(2.0), "gate": "named_agents", "decision": "deny",
         "reason": "waiver reason 'todo' is a placeholder"},
        # --- negative control 1: a one-off reason, must NOT appear in
        # recurrence (count 1).
        {"ts": iso(0.1), "gate": "spec_id", "decision": "deny",
         "reason": "anchor mismatch against a totally unique heading xyz"},
        # --- negative control 2: an 'allow' reason recurs but is NOT a
        # corpus decision, so it must NOT pollute the recurrence ranking.
        {"ts": iso(0.1), "gate": "named_agents", "decision": "allow",
         "reason": "named agent on team"},
        {"ts": iso(0.2), "gate": "named_agents", "decision": "allow",
         "reason": "named agent on team"},
        {"ts": iso(0.3), "gate": "named_agents", "decision": "allow",
         "reason": "named agent on team"},
        # --- dormant control: a corpus reason that recurs but last fired
        # long ago -> verdict 'dormant'.
        {"ts": iso(120), "gate": "old_gate", "decision": "deny",
         "reason": "legacy condition foo triggered"},
        {"ts": iso(118), "gate": "old_gate", "decision": "deny",
         "reason": "legacy condition foo triggered"},
    ]
    model = analyze(entries, now=now)

    # positive: the three placeholder denials collapse to ONE recurring
    # reason with count 3.
    placeholder_rows = [
        r for r in model["recurrence"] if "placeholder" in r["reason"]
    ]
    if len(placeholder_rows) != 1:
        failures.append(
            f"positive control: expected 1 collapsed placeholder reason, "
            f"got {len(placeholder_rows)}")
    elif placeholder_rows[0]["count"] != 3:
        failures.append(
            f"positive control: expected count 3 for placeholder reason, "
            f"got {placeholder_rows[0]['count']}")
    elif placeholder_rows[0]["verdict"] != "active":
        failures.append(
            f"positive control: recent recurring reason should be 'active', "
            f"got '{placeholder_rows[0]['verdict']}'")

    # negative 1: the unique one-off reason must NOT be reported as recurring.
    if any("unique heading" in r["reason"] for r in model["recurrence"]):
        failures.append(
            "negative control 1: a count-1 reason was wrongly reported as recurring")

    # negative 2: the recurring 'allow' boilerplate must NOT be in recurrence.
    if any("named agent on team" in r["reason"] for r in model["recurrence"]):
        failures.append(
            "negative control 2: a non-corpus 'allow' reason polluted the "
            "recurrence ranking")

    # dormant: legacy reason recurs but is old -> dormant verdict.
    dormant_rows = [r for r in model["half_life"] if "legacy condition" in r["reason"]]
    if len(dormant_rows) != 1:
        failures.append(
            f"dormant control: expected 1 legacy reason, got {len(dormant_rows)}")
    elif dormant_rows[0]["verdict"] != "dormant":
        failures.append(
            f"dormant control: stale recurring reason should be 'dormant', "
            f"got '{dormant_rows[0]['verdict']}'")

    # fail-soft: a missing file must yield zero entries and exit 0.
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope" / _SIGNAL_FILENAME
        soft = _read_entries(missing, timedelta(days=365))
        if soft != []:
            failures.append("fail-soft control: missing file should read as []")

    if failures:
        print("SELF-TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELF-TEST PASSED: recurrence detection, corpus filtering, "
          "half-life verdicts, and fail-soft behavior all verified.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="365d",
                        help="window: '1d', '7d', '30d', '24h' (default: all, 365d)")
    parser.add_argument("--half-life", action="store_true",
                        help="add the half-life triage section (Operating Rule 1)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of the human report")
    parser.add_argument("--self-test", action="store_true",
                        help="run behavioral positive/negative controls and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    try:
        since = _parse_since(args.since)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    sources = _signal_sources()

    # Fail soft: no signal file in either store yet. Emit the report headers
    # with zero rows so the learning loop is observably closed, and exit 0 —
    # a missing log is a normal early state.
    if not sources:
        primary = _resolve_signal_path()
        where = primary if primary is not None else "<no .beads/ found>"
        print(f"notice: no gate-signal log at {where} or in the user-level "
              "fallback — nothing to analyze yet (fail-soft).", file=sys.stderr)
        empty = {"total_entries": 0, "frequency": {}, "recurrence": [], "half_life": []}
        if args.json:
            print(json.dumps(empty, indent=2))
        else:
            _print_human(empty, args.since, args.half_life)
        return 0

    entries: list[dict[str, Any]] = []
    for source in sources:
        entries.extend(_read_entries(source, since))
    model = analyze(entries)

    if args.json:
        out = dict(model)
        if not args.half_life:
            out.pop("half_life", None)
        print(json.dumps(out, indent=2))
    else:
        _print_human(model, args.since, args.half_life)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
