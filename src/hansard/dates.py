"""Defensive date normalisation for reference data.

Reference feeds use full dates, year-only dates ("1983"), year-month dates,
empty strings, nulls, or omit the field entirely. Every interval is normalised
to a closed [start, end] range so temporal joins never silently drop rows:

* missing / empty / null start -> 0001-01-01
* missing / empty / null end   -> 9999-12-31 (currently serving)
* year-only start "YYYY"       -> YYYY-01-01; year-only end -> YYYY-12-31
* "YYYY-MM" start              -> first of month; end -> last of month
"""

from __future__ import annotations

import calendar
from datetime import date

MIN_START = date(1, 1, 1)
SENTINEL_END = date(9999, 12, 31)


def _parts(value: object) -> tuple[int, ...] | None:
    if value is None:
        return None
    if isinstance(value, date):
        return (value.year, value.month, value.day)
    text = str(value).strip()
    if not text or text.lower() in {"null", "none"}:
        return None
    pieces = text[:10].split("-")
    try:
        return tuple(int(p) for p in pieces if p)
    except ValueError as exc:
        raise ValueError(f"Unparseable date: {value!r}") from exc


def parse_start(value: object) -> date:
    parts = _parts(value)
    if not parts:
        return MIN_START
    if len(parts) == 1:
        return date(parts[0], 1, 1)
    if len(parts) == 2:
        return date(parts[0], parts[1], 1)
    return date(*parts[:3])


def parse_end(value: object) -> date:
    parts = _parts(value)
    if not parts:
        return SENTINEL_END
    if len(parts) == 1:
        return date(parts[0], 12, 31)
    if len(parts) == 2:
        return date(parts[0], parts[1], calendar.monthrange(parts[0], parts[1])[1])
    return date(*parts[:3])


def overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end
