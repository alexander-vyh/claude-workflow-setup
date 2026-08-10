"""Independent time ordering fixtures for Task 5 producer/consumer tests."""

from __future__ import annotations

import datetime as dt


def qualifying_health_after_registration(entry: dict, generation: int, factory):
    """Build fresh health whose successful pass is provably after registration."""
    registered = dt.datetime.fromisoformat(entry["registered_at"])
    observed = dt.datetime.now(dt.timezone.utc)
    step = (observed - registered) / 3
    if step.total_seconds() <= 0:
        raise AssertionError("managed registration must precede consumer observation")
    started = registered + step
    completed = registered + (step * 2)
    return factory(
        completed_generation=generation,
        reconcile_started_at=started.isoformat(),
        last_successful_reconcile_started_at=started.isoformat(),
        last_successful_reconcile_at=completed.isoformat(),
    )
