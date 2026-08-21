#!/usr/bin/env python3
"""
Capture baseline metrics from Claude Code session transcripts.

Output: harness/baseline-{YYYY-MM-DD}.json containing:
  - window_days
  - sessions_scanned
  - total_user_messages
  - short_prod_count           (well?, now?, continue, etc.)
  - short_prod_rate            (short_prods / total_user_messages)
  - sessions_terminating_on_tool_call
  - sessions_terminating_on_plain_text
  - terminating_tool_call_rate
  - shirking_blocks_in_transcripts (legacy: counts regex hook block messages; the
    gate that produced them is retired, so this reads 0 on new transcripts)

Re-run at the 7-day post-deploy mark and compare.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re
import sys

import os

WINDOW_DAYS = 14
HOME = pathlib.Path.home()
PROJECTS_ROOT = HOME / ".claude" / "projects"
# Standard per-user state location (env-overridable); not the repo tree.
OUT_DIR = pathlib.Path(
    os.environ.get(
        "HARNESS_ROOT",
        os.environ.get("CONTINUATION_HARNESS_HOME", HOME / ".claude" / "harness"),
    )
)

# Short-prod pattern derived from session-miner. Lowercased, stripped of
# punctuation before match.
SHORT_PROD_PATTERN = re.compile(
    r"^(well|now|and|so|go|continue|finish|keep going|why|did you|you stopped|"
    r"run it|push|proceed|next|more|\?|\.{2,})\??$"
)
SHORT_PROD_MAX_LEN = 30

# Validate_no_shirking block message signatures from current hook output.
SHIRKING_SIGNATURES = (
    "OUTCOME OWNERSHIP VIOLATION",
    "VERIFICATION REQUIRED",
)


def _iter_recent_transcripts(window_days: int):
    if not PROJECTS_ROOT.exists():
        return
    cutoff = _dt.datetime.now().timestamp() - window_days * 86400
    for project_dir in PROJECTS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                if jsonl.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            yield jsonl


def _is_short_prod(text: str) -> bool:
    if not text:
        return False
    if len(text) > SHORT_PROD_MAX_LEN:
        return False
    normalized = text.strip().lower()
    return bool(SHORT_PROD_PATTERN.match(normalized))


def _extract_text(record: dict) -> str:
    """Pull text content from a transcript record. Handles both string and list-of-blocks shapes."""
    msg = record.get("message", record)
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "text" in block:
                    parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return ""


def _has_tool_use(record: dict) -> bool:
    msg = record.get("message", record)
    if not isinstance(msg, dict):
        return False
    content = msg.get("content", "")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return True
    return False


def _role(record: dict) -> str:
    msg = record.get("message", record)
    if isinstance(msg, dict):
        return msg.get("role", "") or record.get("type", "")
    return record.get("type", "")


def scan(window_days: int = WINDOW_DAYS) -> dict:
    sessions_scanned = 0
    total_user_messages = 0
    short_prod_count = 0
    sessions_terminating_tool_call = 0
    sessions_terminating_plain_text = 0
    shirking_blocks = 0

    for transcript in _iter_recent_transcripts(window_days):
        sessions_scanned += 1
        last_assistant_record = None

        try:
            with transcript.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    role = _role(rec)
                    text = _extract_text(rec)
                    if role == "user":
                        total_user_messages += 1
                        if _is_short_prod(text):
                            short_prod_count += 1
                        # Shirking block messages show up as user-role
                        # (hook feedback) or system-injected text. Match
                        # signatures in any text.
                    if any(sig in text for sig in SHIRKING_SIGNATURES):
                        shirking_blocks += 1
                    if role == "assistant":
                        last_assistant_record = rec
        except OSError:
            continue

        if last_assistant_record is not None:
            if _has_tool_use(last_assistant_record):
                sessions_terminating_tool_call += 1
            else:
                sessions_terminating_plain_text += 1

    sessions_with_terminator = (
        sessions_terminating_tool_call + sessions_terminating_plain_text
    )
    short_prod_rate = (
        short_prod_count / total_user_messages if total_user_messages > 0 else 0.0
    )
    terminating_tool_call_rate = (
        sessions_terminating_tool_call / sessions_with_terminator
        if sessions_with_terminator > 0
        else 0.0
    )

    return {
        "captured_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "window_days": window_days,
        "sessions_scanned": sessions_scanned,
        "total_user_messages": total_user_messages,
        "short_prod_count": short_prod_count,
        "short_prod_rate": round(short_prod_rate, 6),
        "sessions_terminating_on_tool_call": sessions_terminating_tool_call,
        "sessions_terminating_on_plain_text": sessions_terminating_plain_text,
        "terminating_tool_call_rate": round(terminating_tool_call_rate, 6),
        "validate_no_shirking_blocks_in_transcripts": shirking_blocks,
    }


def main(argv: list[str]) -> int:
    window = WINDOW_DAYS
    if len(argv) > 1:
        try:
            window = int(argv[1])
        except ValueError:
            print(f"baseline: invalid window arg {argv[1]!r}", file=sys.stderr)
            return 2

    metrics = scan(window)
    date_str = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    out = OUT_DIR / f"baseline-{date_str}.json"
    with out.open("w") as f:
        json.dump(metrics, f, indent=2)

    print(json.dumps(metrics, indent=2))
    print(f"\nbaseline written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
