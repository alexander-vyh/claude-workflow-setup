#!/usr/bin/env python3
"""Canonical classification of a Beads record's optional parent relationship."""

from __future__ import annotations


def classify_canonical_parent(record: object) -> tuple[str, str | None]:
    """Return standalone, parented, or unresolved without guessing a parent."""
    if not isinstance(record, dict):
        return "unresolved", None

    values = [record[key] for key in ("parent", "parent_id") if key in record]
    if not values or all(value is None for value in values):
        return "standalone", None
    if any(
        value is not None and (not isinstance(value, str) or not value)
        for value in values
    ):
        return "unresolved", None

    parent_ids = {value for value in values if value is not None}
    if len(parent_ids) != 1:
        return "unresolved", None
    return "parented", parent_ids.pop()
