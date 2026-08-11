"""Lean verification for the oracle-independence pilot and human handoff."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "openspec/changes/oracle-independence/skeleton-probe"
CONDITION_A = PROBE / "condition-a-results.md"
CONDITION_B = PROBE / "condition-b-results.md"
RESULT = PROBE / "result.md"
PACKET = PROBE / "human-scoring-packet.md"
CASE_IDS = tuple(f"diff-{number:02d}" for number in range(1, 7))


def _condition_verdicts(path: Path) -> dict[str, str]:
    verdicts: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\| \d+ \| `diff-\d{2}` \|", line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        case_id = cells[1].strip("`")
        verdicts[case_id] = cells[3].strip("*")
    return verdicts


def _result_rows() -> dict[str, tuple[str, str, str]]:
    rows: dict[str, tuple[str, str, str]] = {}
    for line in RESULT.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `diff-"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        case_id = cells[0].strip("`")
        truth = re.search(r"CLEAN|PLANTED", cells[1])
        assert truth is not None
        rows[case_id] = (truth.group(), cells[2].strip("*"), cells[4].strip("*"))
    return rows


def test_ai_pilot_is_labeled_honestly_and_arithmetic_is_consistent() -> None:
    a_text = CONDITION_A.read_text(encoding="utf-8")
    b_text = CONDITION_B.read_text(encoding="utf-8")
    result_text = RESULT.read_text(encoding="utf-8")
    for text in (a_text, b_text, result_text):
        assert "AI" in text
        assert "human reviewer" not in text.lower()

    rows = _result_rows()
    assert tuple(rows) == CASE_IDS
    assert _condition_verdicts(CONDITION_A) == {
        case_id: row[1] for case_id, row in rows.items()
    }
    assert _condition_verdicts(CONDITION_B) == {
        case_id: row[2] for case_id, row in rows.items()
    }

    a_correct = sum(truth == a for truth, a, _ in rows.values())
    b_correct = sum(truth == b for truth, _, b in rows.values())
    planted = [row for row in rows.values() if row[0] == "PLANTED"]
    assert (a_correct, b_correct) == (4, 6)
    assert sum(truth == a for truth, a, _ in planted) == 1
    assert sum(truth == b for truth, _, b in planted) == 2
    assert "AI PILOT ONLY" in result_text
    assert "not human evidence" in result_text.lower()
    assert "2026-06-12T01:27:01-07:00" in result_text
    assert "2026-06-12T01:27:01Z" not in result_text


def test_human_scoring_packet_is_ready_but_does_not_claim_a_result() -> None:
    packet = PACKET.read_text(encoding="utf-8")
    assert "BLOCKED" in packet
    assert "awaiting a human response" in packet.lower()
    assert "Condition A" in packet and "Condition B" in packet
    assert "condition-a-results.md" in packet and "condition-b-results.md" in packet
    assert "do not open" in packet.lower()

    payloads = [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)```", packet, re.DOTALL)
    ]
    assert [payload["condition"] for payload in payloads] == ["A", "B"]
    for payload in payloads:
        assert [row["case_id"] for row in payload["verdicts"]] == list(CASE_IDS)
        assert {row["verdict"] for row in payload["verdicts"]} == {"CLEAN or PLANTED"}
        assert all(
            row["rationale"] == "replace with rationale" for row in payload["verdicts"]
        )

    assert "double-underscore" not in packet
    assert "startswith" not in packet
    assert not (PROBE.parent / "skeleton-probe-human-v2").exists()
