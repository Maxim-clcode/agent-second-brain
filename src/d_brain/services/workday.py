"""Utilities for determining next working day (skips weekends + Russian holidays)."""

from datetime import date, timedelta

import holidays

_RU_HOLIDAYS = holidays.Russia()


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in _RU_HOLIDAYS


def next_workday(from_date: date, include_today: bool = False) -> date:
    """Return the next working day after from_date.

    If include_today is True and from_date itself is a workday, return it.
    """
    d = from_date if include_today else from_date + timedelta(days=1)
    while not is_workday(d):
        d += timedelta(days=1)
    return d
