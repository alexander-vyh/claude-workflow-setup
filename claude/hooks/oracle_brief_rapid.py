"""Fail-closed validation for the compact Test Oracle Brief form."""

from __future__ import annotations

import re
import shlex
import shutil
from collections import Counter


REQUIRED_SECTIONS = (
    "Business invariant",
    "Independent source of truth",
    "Solution constraints",
    "Invalid solution classes",
    "Fragile implementation to reject",
    "Negative control",
    "Positive control",
    "Missing/unresolved handling",
    "Final outcome verification",
)
RAPID_SECTION_FIELDS = {
    "Business invariant": (
        "Outcome",
        "Independent source of truth",
        "Binding constraints",
        "Authorization/security",
        "Money or sensitive data",
        "Production mutation",
        "Schema/migration",
        "Public contracts",
        "Irreversible external effects",
        "Shared infrastructure",
        "Root cause",
    ),
    "Negative control": (
        "Named fragile implementation",
        "Negative control",
        "Positive control",
        "Missing/unresolved handling",
    ),
    "Final outcome verification": ("Exact user-facing verification",),
}
RAPID_REVIEW_FIELDS = (
    "Focused proof result",
    "Objective blockers",
    "Known limitations",
    "Remaining landing proof",
)
RAPID_OBSERVED_FIELD = "Observed result"
RAPID_PROTECTED_FIELDS = (
    "Authorization/security",
    "Money or sensitive data",
    "Production mutation",
    "Schema/migration",
    "Public contracts",
    "Irreversible external effects",
    "Shared infrastructure",
)
PROOF_STAGES = frozenset({"edit", "durable", "review", "final"})
PLACEHOLDER_VALUES = frozenset({"tbd", "todo", "n/a", "na", "???", "coming soon"})

_OBSERVATION_CONTRADICTIONS = re.compile(
    r"\b(?:not checked|not observed|not run|never ran|unknown|unresolved|tbd|todo)\b",
    re.I,
)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _section_heading(value: str) -> str | None:
    normalized = _normalize(value.strip("*_ \t:-"))
    return next(
        (section for section in REQUIRED_SECTIONS if _normalize(section) == normalized),
        None,
    )


def _field_owner() -> dict[str, str]:
    owners = {
        field: section
        for section, fields in RAPID_SECTION_FIELDS.items()
        for field in fields
    }
    owners.update(
        {field: "Final outcome verification" for field in RAPID_REVIEW_FIELDS}
    )
    owners[RAPID_OBSERVED_FIELD] = "Final outcome verification"
    return owners


def _sections_and_fields(
    text: str,
) -> tuple[bool, dict[str, list[str]], list[str]]:
    rapid_headings = frozenset(RAPID_SECTION_FIELDS)
    owners = _field_owner()
    normalized_fields = {_normalize(field): field for field in owners}
    recognized_headings: list[str] = []
    active_section: str | None = None
    values: dict[str, list[str]] = {}
    misplaced: list[str] = []

    for line in text.splitlines():
        heading_match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if heading_match:
            heading = _section_heading(heading_match.group(1))
            active_section = heading if heading in rapid_headings else None
            if heading is not None:
                recognized_headings.append(heading)
            continue
        field_match = re.match(r"^\s*(?:[-*+]\s+)?([^:]+):\s*(.*)$", line)
        if not field_match:
            continue
        field = normalized_fields.get(_normalize(field_match.group(1)))
        if field is None:
            continue
        values.setdefault(field, []).append(field_match.group(2).strip())
        if active_section != owners[field]:
            misplaced.append(field)

    counts = Counter(recognized_headings)
    candidate = set(counts) == rapid_headings and all(
        counts[heading] == 1 for heading in rapid_headings
    )
    return candidate, values, misplaced


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _substantive(value: str, *, minimum: int = 4) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip("`*_ \t.:;"))
    if not normalized or normalized.casefold() in PLACEHOLDER_VALUES:
        return False
    tokens = _tokens(normalized)
    return len(tokens) >= minimum and len(set(tokens)) >= min(3, minimum)


def _evidence_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _command_action(value: str) -> bool:
    try:
        parts = shlex.split(value)
    except ValueError:
        return False
    if len(parts) < 2 or not re.fullmatch(
        r"(?:[./][^\s]+|[a-z0-9][a-z0-9_.+-]*)", parts[0], re.I
    ):
        return False
    if shutil.which(parts[0]) is None:
        return False
    return any(
        part.startswith("-") or any(marker in part for marker in ("/", ".", "="))
        for part in parts[1:]
    )


def _query_action(value: str) -> bool:
    identifier = r"[a-z_][a-z0-9_.]*"
    columns = rf"(?:\*|{identifier}(?:\s*,\s*{identifier})*)"
    literal = r"(?:'(?:[^']|'')*'|\d+(?:\.\d+)?|true|false|null)"
    condition = rf"{identifier}\s*(?:=|!=|<>|<=|>=|<|>)\s*{literal}"
    query = (
        rf"select\s+{columns}\s+from\s+{identifier}"
        rf"(?:\s+where\s+{condition}(?:\s+(?:and|or)\s+{condition})*)?"
    )
    return re.fullmatch(query, value.strip(), re.I) is not None


def _action_is_executable(kind: str, action: str) -> bool:
    lowered = action.casefold()
    if kind == "command":
        return _command_action(action)
    if kind == "query":
        return _query_action(action)
    if kind == "api":
        return bool(re.match(r"(?:get|post|put|patch|delete)\s+/\S+", lowered))
    if kind == "ui":
        return bool(re.search(r"\b(?:open|navigate|click|submit|choose)\b", lowered))
    if kind == "report":
        match = re.fullmatch(r"(?:run|generate)\s+(.+)", action, re.I)
        return bool(match and _command_action(match.group(1)))
    return False


def _proof_parts(value: str) -> tuple[str, str, str] | None:
    match = re.fullmatch(
        r"(Command|Query|API|Report|UI):\s*(.+?);\s*Expected:\s*(.+)",
        value.strip(),
        re.I,
    )
    if not match:
        return None
    kind, action, expected = match.groups()
    if not _substantive(action, minimum=2) or not _substantive(expected, minimum=3):
        return None
    kind = kind.casefold()
    if _OBSERVATION_CONTRADICTIONS.search(expected) or not _action_is_executable(
        kind, action
    ):
        return None
    return kind, action, expected


def _structured_proof(value: str) -> bool:
    return _proof_parts(value) is not None


def _structured_observation(value: str, *, planned_expected: str | None = None) -> bool:
    match = re.fullmatch(
        r"Expected:\s*(.+?);\s*Actual:\s*(.+?);\s*Match:\s*yes",
        value.strip(),
        re.I,
    )
    if not match:
        return False
    expected, actual = match.groups()
    return (
        _substantive(expected, minimum=3)
        and _substantive(actual, minimum=3)
        and _OBSERVATION_CONTRADICTIONS.search(expected) is None
        and _OBSERVATION_CONTRADICTIONS.search(actual) is None
        and _evidence_text(expected) == _evidence_text(actual)
        and (
            planned_expected is None
            or _evidence_text(expected) == _evidence_text(planned_expected)
        )
    )


def _structured_executed_proof(value: str) -> bool:
    match = re.fullmatch(
        r"(Command|Query|API|Report|UI):\s*(.+?);\s*Expected:\s*(.+?);\s*Actual:\s*(.+?);\s*Match:\s*yes",
        value.strip(),
        re.I,
    )
    if not match:
        return False
    kind, action, expected, actual = match.groups()
    planned = f"{kind}: {action}; Expected: {expected}"
    observed = f"Expected: {expected}; Actual: {actual}; Match: yes"
    return _structured_proof(planned) and _structured_observation(observed)


def _positive_control_valid(value: str) -> bool:
    proof = _proof_parts(value)
    if proof is None:
        return False
    expected = proof[2]
    positive = re.search(
        r"\b(?:allow|complete|nonempty|preserv|result|success|valid)\w*\b",
        expected,
        re.I,
    )
    negated = re.search(
        r"\b(?:absent|discard|drop|empty|never|no|not|suppress)\w*\b",
        expected,
        re.I,
    )
    return bool(positive and negated is None)


def _missing_handling_valid(value: str) -> bool:
    state = re.search(r"\b(?:absent|lookup|missing|optional|source|unknown|unresolved)\b", value, re.I)
    action = re.search(r"\b(?:allow|block|explicit|fail|full|open|reject)\w*\b", value, re.I)
    return _substantive(value) and bool(state and action)


def rapid_brief_errors(text: str, stage: str) -> tuple[bool, list[str]]:
    candidate, values, misplaced = _sections_and_fields(text)
    if not candidate:
        return False, []

    required = [field for fields in RAPID_SECTION_FIELDS.values() for field in fields]
    if stage in {"review", "final"}:
        required.extend(RAPID_REVIEW_FIELDS)
    if stage == "final":
        required.append(RAPID_OBSERVED_FIELD)

    errors = [f"misplaced {field}" for field in sorted(set(misplaced))]
    for field in required:
        field_values = values.get(field, [])
        if len(field_values) != 1:
            errors.append(f"{field} must appear exactly once")
        elif (
            field not in RAPID_PROTECTED_FIELDS
            and field not in {"Objective blockers", "Known limitations"}
            and not _substantive(field_values[0])
        ):
            errors.append(f"{field} needs substantive evidence")
    for field, field_values in values.items():
        if len(field_values) > 1 and field not in required:
            errors.append(f"{field} must not be duplicated")

    for field in RAPID_PROTECTED_FIELDS:
        field_values = values.get(field, [])
        if len(field_values) == 1 and field_values[0].strip().casefold() != "no":
            errors.append(f"{field} is protected or unknown")

    root = values.get("Root cause", [""])
    if len(root) == 1 and not _structured_executed_proof(root[0]):
        errors.append("Root cause lacks observed executable proof")

    positive = values.get("Positive control", [""])
    if len(positive) == 1 and not _positive_control_valid(positive[0]):
        errors.append("Positive control lacks valid-output evidence")
    missing = values.get("Missing/unresolved handling", [""])
    if len(missing) == 1 and not _missing_handling_valid(missing[0]):
        errors.append("Missing/unresolved handling lacks a disposition")

    exact = values.get("Exact user-facing verification", [""])
    if len(exact) == 1 and not _structured_proof(exact[0]):
        errors.append("Exact user-facing verification is not executable")

    if stage in {"review", "final"}:
        focused = values.get("Focused proof result", [""])
        planned = _proof_parts(exact[0]) if len(exact) == 1 else None
        planned_expected = planned[2] if planned else None
        if len(focused) == 1 and not _structured_observation(
            focused[0], planned_expected=planned_expected
        ):
            errors.append("Focused proof result is not an observation")
        blockers = values.get("Objective blockers", [""])
        if len(blockers) == 1 and blockers[0].strip().casefold() != "none":
            errors.append("Objective blockers remain")
        limitations = values.get("Known limitations", [""])
        if len(limitations) == 1 and limitations[0].strip().casefold() != "none":
            errors.append("Known limitations require the full lane")
        remaining = values.get("Remaining landing proof", [""])
        if len(remaining) == 1 and not _structured_proof(remaining[0]):
            errors.append("Remaining landing proof is not executable")

    if stage == "final":
        observed = values.get(RAPID_OBSERVED_FIELD, [""])
        planned = _proof_parts(exact[0]) if len(exact) == 1 else None
        planned_expected = planned[2] if planned else None
        if len(observed) == 1 and not _structured_observation(
            observed[0], planned_expected=planned_expected
        ):
            errors.append("Observed result is not substantive user-facing proof")

    evidence_fields = [
        field
        for field in required
        if field not in RAPID_PROTECTED_FIELDS
        and field
        not in {
            "Root cause",
            "Focused proof result",
            "Objective blockers",
            RAPID_OBSERVED_FIELD,
        }
    ]
    normalized_evidence = [
        re.sub(r"\s+", " ", values[field][0].strip()).casefold()
        for field in evidence_fields
        if len(values.get(field, [])) == 1 and values[field][0].strip()
    ]
    if any(count > 1 for count in Counter(normalized_evidence).values()):
        errors.append("Rapid evidence fields repeat the same boilerplate")
    return True, errors
