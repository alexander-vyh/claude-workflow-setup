#!/usr/bin/env python3
"""Aggregate gate-signal logs across all repos under ~/GitHub.

Per claude/rules/gate-design.md Rule 2: every gate produces persistent
signal. This script reads `.beads/.gate-signal.jsonl` from every repo it
finds and reports aggregated patterns:

  - which gates fired most, in which repos
  - which gates have NEVER fired (candidates for petrification review)
  - which gates show mock-bureaucracy risk (high waiver:deny ratio,
    Wiesche et al. 2013) — symbolic compliance without behavior change
  - per-category counts for `validate_no_shirking` (the 8 anti-pattern
    classes — half-life query per Operating Rule 1)

Intended to run weekly via a Claude Code scheduled agent or launchd
job. Output is human-readable by default; pass --json for machine output.

## Usage

    python3 ~/.claude/bin/gate_signal_monitor.py [--since 7d]
                                                 [--root ~/GitHub]
                                                 [--json]
                                                 [--known-gates <gate>,...]

The --known-gates argument is used to detect "silent" gates — gates
that exist as hooks but never appear in the signal log. Defaults to
the 14 gates migrated as of 2026-05-27.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# Beads-issue title prefix for monitor-filed issues. Used for dedup —
# the monitor checks `bd list` for open issues whose title starts with
# this prefix + the specific pattern, and skips re-filing.
_BEAD_TITLE_PREFIX = "[gate-monitor]"

# Repo where monitor-filed beads land. The monitor IS the workflow
# tooling; concerning patterns surface in this repo's bd queue rather
# than wherever the signal happened to be captured.
_BEAD_TARGET_REPO = Path(os.path.expanduser("~/GitHub/escapement"))


_DEFAULT_KNOWN_GATES = [
    "spec_id_enforcement",
    "discovery_gate",
    "discovery_input_gate",
    "tdd_gate",
    "no_direct_send_guard",
    "serena_preference_gate",
    "outcome_assertion_gate",
    "context_burn_detector",
    "review_gate",
    "discovery_close_gate",
    "enforce_named_agents",
    "test_oracle_brief_gate",
]


def _parse_since(token: str) -> timedelta:
    suffix_map = {"d": "days", "h": "hours", "m": "minutes"}
    suffix = token[-1].lower()
    if suffix not in suffix_map:
        raise ValueError(f"unrecognized suffix in --since '{token}'")
    return timedelta(**{suffix_map[suffix]: int(token[:-1])})


def find_signal_logs(root: Path) -> list[Path]:
    """Return every gate-signal log this monitor should read.

    Per-repo `.beads/.gate-signal.jsonl` files under root, plus the single
    user-level fallback sink. Gates firing in a context without a locatable
    `.beads/` — a linked worktree, a scratch directory — write there instead,
    so a per-repo scan alone misses whole sessions rather than a few rows.
    """
    out: list[Path] = []
    if root.is_dir():
        for entry in root.iterdir():
            if not entry.is_dir() or entry.is_symlink():
                continue
            signal_log = entry / ".beads" / ".gate-signal.jsonl"
            if signal_log.is_file():
                out.append(signal_log)
    fallback = _fallback_signal_log()
    if fallback is not None and fallback not in out:
        out.append(fallback)
    return sorted(out)


def _fallback_signal_log() -> Path | None:
    """The user-level fallback sink, if it exists.

    Mirrors the writer's resolution in claude/hooks/_gate_signal.py without
    importing it: this script runs standalone against arbitrary roots and
    must not depend on a sibling checkout being present.
    """
    env_dir = os.environ.get("GATE_SIGNAL_FALLBACK_DIR")
    base = Path(env_dir) if env_dir else Path("~/.claude/harness")
    candidate = base.expanduser() / "gate-signal-fallback.jsonl"
    return candidate if candidate.is_file() else None


def read_entries(
    signal_path: Path, cutoff: datetime,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        with signal_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = entry.get("ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts < cutoff:
                    continue
                entries.append(entry)
    except OSError:
        pass
    return entries


def analyze(
    entries_by_repo: dict[str, list[dict[str, Any]]],
    known_gates: list[str],
) -> dict[str, Any]:
    """Roll up per-gate, per-decision, per-category counts."""
    by_gate: Counter[str] = Counter()
    by_repo: Counter[str] = Counter()
    by_gate_decision: dict[str, Counter[str]] = defaultdict(Counter)
    by_gate_repo: dict[str, Counter[str]] = defaultdict(Counter)
    shirking_categories: Counter[str] = Counter()
    shirking_waivers_by_category: Counter[str] = Counter()
    reasons_by_gate: dict[str, list[str]] = defaultdict(list)

    total = 0
    for repo, entries in entries_by_repo.items():
        for e in entries:
            total += 1
            gate = e.get("gate", "?")
            decision = e.get("decision", "?")
            by_gate[gate] += 1
            by_repo[repo] += 1
            by_gate_decision[gate][decision] += 1
            by_gate_repo[gate][repo] += 1
            reason = e.get("reason", "")
            if reason and len(reasons_by_gate[gate]) < 30:
                reasons_by_gate[gate].append(reason)

            # validate_no_shirking has categories — track separately
            if gate == "validate_no_shirking":
                cat = (e.get("extras") or {}).get("category", "uncategorized")
                if decision == "deny":
                    shirking_categories[cat] += 1
                elif decision == "waiver-accepted":
                    shirking_waivers_by_category[cat] += 1

    # Mock-bureaucracy risk: gates where waivers/asks outnumber denies+allows
    # (i.e. the user is overriding more than enforcing) — gate-design.md Rule 3.
    mock_risk: list[dict[str, Any]] = []
    for gate, decisions in by_gate_decision.items():
        deny_count = decisions.get("deny", 0)
        waiver_count = (
            decisions.get("waiver-accepted", 0) + decisions.get("override-applied", 0)
        )
        if deny_count > 0 and waiver_count > deny_count:
            mock_risk.append({
                "gate": gate,
                "denies": deny_count,
                "waivers": waiver_count,
                "ratio": round(waiver_count / max(deny_count, 1), 2),
            })

    # Silent gates: in known list but absent from signal — petrification
    # candidates per Operating Rule 1.
    fired = set(by_gate.keys())
    silent = sorted(g for g in known_gates if g not in fired)

    # Shirking categories with high waiver:deny ratio → false-positive heavy
    shirking_fp_heavy: list[dict[str, Any]] = []
    for cat in set(shirking_categories) | set(shirking_waivers_by_category):
        denies = shirking_categories[cat]
        waivers = shirking_waivers_by_category[cat]
        if denies + waivers >= 3 and waivers > denies:
            shirking_fp_heavy.append({
                "category": cat,
                "denies": denies,
                "waivers": waivers,
                "ratio": round(waivers / max(denies, 1), 2),
            })

    return {
        "total_events": total,
        "by_gate": dict(by_gate),
        "by_repo": dict(by_repo),
        "by_gate_decision": {g: dict(d) for g, d in by_gate_decision.items()},
        "by_gate_repo": {g: dict(r) for g, r in by_gate_repo.items()},
        "silent_known_gates": silent,
        "mock_bureaucracy_risk": sorted(mock_risk, key=lambda x: -x["ratio"]),
        "shirking_categories": dict(shirking_categories),
        "shirking_fp_heavy": sorted(shirking_fp_heavy, key=lambda x: -x["ratio"]),
        "reasons_by_gate_sample": {
            g: list(dict.fromkeys(rs))[:5]  # dedup, keep first 5
            for g, rs in reasons_by_gate.items()
        },
    }


def _list_open_monitor_beads(repo: Path) -> list[dict[str, Any]]:
    """Return open bd issues in repo whose title starts with the monitor prefix.

    Used for dedup — we only file a new bead if no existing open one
    covers the same pattern. Best-effort: returns [] on any error.
    """
    try:
        result = subprocess.run(
            ["bd", "list", "--status=open", "--json"],
            capture_output=True,
            text=True,
            cwd=str(repo),
            timeout=10,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        if not isinstance(data, list):
            return []
        return [
            entry for entry in data
            if isinstance(entry, dict)
            and isinstance(entry.get("title"), str)
            and entry["title"].startswith(_BEAD_TITLE_PREFIX)
        ]
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return []


def _file_bead(
    repo: Path, title: str, description: str, priority: int = 2,
) -> str | None:
    """Create a bd issue in repo. Returns the new bead id on success."""
    try:
        result = subprocess.run(
            [
                "bd", "create",
                "--type=task",
                f"--priority={priority}",
                f"--title={title}",
                f"--description={description}",
            ],
            capture_output=True,
            text=True,
            cwd=str(repo),
            timeout=15,
        )
        if result.returncode != 0:
            return None
        # Parse "Created issue: <id> — <title>" from stdout
        for line in result.stdout.splitlines():
            if "Created issue:" in line:
                return line.split("Created issue:")[1].split("—")[0].strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def file_concerning_patterns(
    summary: dict[str, Any], repo: Path, window_text: str,
) -> list[dict[str, Any]]:
    """Open bd issues for each concerning pattern in summary.

    Returns a list of {title, action: 'filed' | 'deduped', id?} records.
    """
    existing = _list_open_monitor_beads(repo)
    existing_titles = {b.get("title", "") for b in existing}

    actions: list[dict[str, Any]] = []

    # Mock-bureaucracy risk per gate
    for m in summary.get("mock_bureaucracy_risk", []):
        title = f"{_BEAD_TITLE_PREFIX} mock-bureaucracy risk: {m['gate']}"
        if title in existing_titles:
            actions.append({"title": title, "action": "deduped"})
            continue
        description = (
            f"Gate `{m['gate']}` shows mock-bureaucracy risk in the last "
            f"{window_text}: {m['waivers']} waivers vs {m['denies']} denies "
            f"(ratio {m['ratio']}).\n\n"
            f"Per `claude/rules/gate-design.md` Rule 3 (validate the value, "
            f"not just the presence) and the Wiesche et al. (2013) finding "
            f"that both coercive AND enabling designs can produce mock "
            f"bureaucracy: this ratio suggests symbolic compliance is "
            f"taking over from real enforcement.\n\n"
            f"## What to check\n\n"
            f"1. Read the captured waiver reasons:\n"
            f"   `python3 ~/.claude/bin/gate_signal_query.py "
            f"--gate {m['gate']} --decision waiver-accepted --since 30d`\n"
            f"2. If the reasons cluster on a specific pattern, the gate's "
            f"heuristic is too aggressive — prune or refine.\n"
            f"3. If the reasons are diverse / ad-hoc, the gate may be "
            f"firing correctly and the user is overriding for context "
            f"reasons; consider whether the gate's denial message could "
            f"better explain *why* the rule applies in this case.\n\n"
            f"This bead was filed automatically by the weekly gate-signal "
            f"monitor. Filed once; subsequent weeks dedup against this "
            f"open issue. Close after acting on the finding."
        )
        new_id = _file_bead(repo, title, description, priority=2)
        actions.append({"title": title, "action": "filed", "id": new_id})

    # validate_no_shirking FP-heavy categories
    for c in summary.get("shirking_fp_heavy", []):
        title = (
            f"{_BEAD_TITLE_PREFIX} validate_no_shirking FP-heavy: "
            f"{c['category']}"
        )
        if title in existing_titles:
            actions.append({"title": title, "action": "deduped"})
            continue
        description = (
            f"`validate_no_shirking` category `{c['category']}` is "
            f"false-positive-heavy in the last {window_text}: "
            f"{c['waivers']} waivers vs {c['denies']} denies "
            f"(ratio {c['ratio']}).\n\n"
            f"Per `claude/rules/gate-design.md` Operating Rule 1 (every "
            f"rule has a half-life), patterns in this category are "
            f"firing on legitimate prose the user keeps overriding. "
            f"Candidates for pruning in the next quarterly review — "
            f"or sooner if the ratio stays elevated.\n\n"
            f"## What to check\n\n"
            f"1. Read the matched phrases:\n"
            f"   `python3 ~/.claude/bin/gate_signal_query.py "
            f"--gate validate_no_shirking --decision waiver-accepted "
            f"--since 30d`\n"
            f"2. Look for false-positive phrasings: legitimate uses of "
            f"language that incidentally match a shirking pattern. The "
            f"patterns are listed by category in "
            f"`claude/hooks/validate_no_shirking.py` `_CATEGORIZED_PATTERNS`.\n"
            f"3. Prune or tighten the offending pattern(s); commit the "
            f"revision; close this bead.\n\n"
            f"Filed by the weekly gate-signal monitor."
        )
        new_id = _file_bead(repo, title, description, priority=2)
        actions.append({"title": title, "action": "filed", "id": new_id})

    return actions


def render_human(summary: dict[str, Any], since_text: str) -> str:
    lines: list[str] = []
    lines.append(f"Gate-signal monitor — last {since_text}")
    lines.append(f"  total events: {summary['total_events']}")
    lines.append(f"  repos active: {len(summary['by_repo'])}")

    if summary["total_events"] == 0:
        lines.append("")
        lines.append("  No gate activity. Either no real work happened, or hooks aren't")
        lines.append("  deployed correctly. Run: ls -la ~/.claude/hooks/_gate_signal.py")
        return "\n".join(lines)

    lines.append("")
    lines.append("By gate (top 10):")
    by_gate = summary["by_gate"]
    for gate, count in sorted(by_gate.items(), key=lambda x: -x[1])[:10]:
        decisions = summary["by_gate_decision"][gate]
        dstr = ", ".join(
            f"{d}={n}" for d, n in sorted(decisions.items(), key=lambda x: -x[1])
        )
        lines.append(f"  {gate}: {count}  ({dstr})")

    lines.append("")
    lines.append("By repo:")
    for repo, count in sorted(summary["by_repo"].items(), key=lambda x: -x[1]):
        lines.append(f"  {repo}: {count}")

    if summary["mock_bureaucracy_risk"]:
        lines.append("")
        lines.append("⚠️  MOCK-BUREAUCRACY RISK (waivers > denies — gate may be too aggressive):")
        for m in summary["mock_bureaucracy_risk"]:
            lines.append(f"  {m['gate']}: {m['waivers']} waivers vs {m['denies']} denies (ratio {m['ratio']})")

    if summary["silent_known_gates"]:
        lines.append("")
        lines.append("Silent gates (known but never fired in window — candidates for half-life review):")
        for g in summary["silent_known_gates"]:
            lines.append(f"  {g}")

    if summary["shirking_fp_heavy"]:
        lines.append("")
        lines.append("⚠️  validate_no_shirking — categories with high waiver ratio (false-positive-heavy):")
        for c in summary["shirking_fp_heavy"]:
            lines.append(f"  {c['category']}: {c['waivers']} waivers vs {c['denies']} denies (ratio {c['ratio']})")
    elif summary["shirking_categories"]:
        lines.append("")
        lines.append("validate_no_shirking — counts by anti-pattern category:")
        for cat, n in sorted(summary["shirking_categories"].items(), key=lambda x: -x[1]):
            lines.append(f"  {cat}: {n}")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--since", default="7d", help="window: '1d', '7d', '30d' (default 7d)")
    p.add_argument(
        "--root",
        type=Path,
        default=Path(os.path.expanduser("~/GitHub")),
        help="directory to scan for repos (default: ~/GitHub)",
    )
    p.add_argument("--json", action="store_true", help="emit JSON instead of human text")
    p.add_argument(
        "--known-gates",
        default=",".join(_DEFAULT_KNOWN_GATES),
        help="comma-separated gate names used to detect 'silent' gates",
    )
    p.add_argument(
        "--file-beads",
        action="store_true",
        help=(
            "Open bd issues in escapement for concerning patterns "
            "(mock-bureaucracy risk, FP-heavy shirking categories). Deduped "
            "against existing open `[gate-monitor]`-tagged issues."
        ),
    )
    p.add_argument(
        "--beads-repo",
        type=Path,
        default=_BEAD_TARGET_REPO,
        help=f"Repo to file monitor beads in (default: {_BEAD_TARGET_REPO})",
    )
    args = p.parse_args()

    try:
        since = _parse_since(args.since)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    cutoff = datetime.now(timezone.utc) - since
    known_gates = [g.strip() for g in args.known_gates.split(",") if g.strip()]

    logs = find_signal_logs(args.root)
    entries_by_repo: dict[str, list[dict[str, Any]]] = {}
    for log_path in logs:
        # Repo name = parent of the .beads/ directory
        repo_name = log_path.parent.parent.name
        entries = read_entries(log_path, cutoff)
        if entries:
            entries_by_repo[repo_name] = entries

    summary = analyze(entries_by_repo, known_gates)
    summary["window"] = args.since
    summary["root"] = str(args.root)
    summary["logs_scanned"] = len(logs)
    summary["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    bead_actions: list[dict[str, Any]] = []
    if args.file_beads:
        bead_actions = file_concerning_patterns(summary, args.beads_repo, args.since)
        summary["bead_actions"] = bead_actions

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(render_human(summary, args.since))
        if bead_actions:
            print()
            print("Bead actions (--file-beads):")
            for a in bead_actions:
                tag = a["action"].upper()
                bid = f" → {a['id']}" if a.get("id") else ""
                print(f"  [{tag}] {a['title']}{bid}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
