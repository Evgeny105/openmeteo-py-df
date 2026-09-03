"""Month arithmetic and assembly of multi-month historical responses.

Pure functions, no I/O: they are the part of ``get_historical`` that used to
corrupt data (wrong December end, misaligned series, non-chronological
merge) and are therefore kept small and exhaustively tested.

Example:
    >>> months = list(iter_months(date(2025, 11, 15), date(2026, 1, 10), date(2026, 9, 3)))
    >>> [(m.key, m.start, m.end) for m in months]
    [('2025-11', date(2025, 11, 1), date(2025, 11, 30)),
     ('2025-12', date(2025, 12, 1), date(2025, 12, 31)),
     ('2026-01', date(2026, 1, 1), date(2026, 1, 31))]
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Iterator, Sequence
from datetime import date, datetime
from typing import Any, NamedTuple

from .exceptions import OpenMeteoDataError
from .types import TimeStep


class MonthRange(NamedTuple):
    """One calendar month clipped to the requested period and to today.

    Attributes:
        key: Month in ``YYYY-MM`` format.
        start: First day of the month.
        end: Last day of the month, or ``today`` if the month is not over.
    """

    key: str
    start: date
    end: date


def month_end(month_start: date) -> date:
    """Last calendar day of the month containing ``month_start``."""
    last_day = calendar.monthrange(month_start.year, month_start.month)[1]
    return month_start.replace(day=last_day)


def iter_months(start_date: date, end_date: date, today: date) -> Iterator[MonthRange]:
    """Yield the months covering ``start_date..end_date`` in chronological order.

    Each month spans its full calendar range (the request is cached per month)
    except that the end is clipped to ``today`` so the archive is never asked
    for the future.

    Args:
        start_date: First day of the requested period.
        end_date: Last day of the requested period.
        today: Current date (UTC); months are clipped to it.

    Raises:
        OpenMeteoDataError: If ``start_date`` is after ``end_date``.
    """
    if start_date > end_date:
        raise OpenMeteoDataError(f"start_date {start_date} is after end_date {end_date}")
    current = start_date.replace(day=1)
    last_month = end_date.replace(day=1)
    while current <= last_month:
        end = month_end(current)
        assert end.month == current.month and end.year == current.year
        if end > today:
            end = today
        if end >= current:
            yield MonthRange(current.strftime("%Y-%m"), current, end)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)


def parse_date(value: str) -> date:
    """Parse ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM`` into a date."""
    if "T" in value:
        return datetime.fromisoformat(value).date()
    return date.fromisoformat(value)


def align_variables(block: dict[str, Any], variables: Iterable[str]) -> dict[str, Any]:
    """Return a copy of a data block where every requested variable is present.

    A variable the API did not return (or returned with a wrong length) becomes a
    list of ``None`` matching ``time``. Variables that were not requested are left
    untouched.
    """
    times = block.get("time") or []
    n = len(times)
    out = dict(block)
    for var in variables:
        series = out.get(var)
        if not isinstance(series, list) or len(series) != n:
            out[var] = [None] * n
    return out


def check_invariant(block: dict[str, Any], context: str = "") -> None:
    """Raise :class:`OpenMeteoDataError` if any series differs in length from ``time``."""
    times = block.get("time")
    if not isinstance(times, list):
        raise OpenMeteoDataError(f"Data block has no time axis {context}".strip())
    n = len(times)
    bad = {
        k: len(v) if isinstance(v, list) else None
        for k, v in block.items()
        if k != "time" and (not isinstance(v, list) or len(v) != n)
    }
    if bad:
        raise OpenMeteoDataError(
            f"Series length mismatch {context}: time has {n} points, "
            + ", ".join(f"{k} has {v}" for k, v in sorted(bad.items()))
        )


def assemble(
    months: Sequence[dict[str, Any]], step: TimeStep, variables: Sequence[str]
) -> dict[str, Any]:
    """Merge per-month API responses into one response dict.

    ``months`` must be in chronological order. Timestamps are concatenated
    with duplicates dropped (the first occurrence wins). Every requested
    variable becomes a series of exactly ``len(time)`` values; a month that
    lacks a variable contributes ``None`` for its timestamps. Metadata and
    units come from the first month; units missing there are taken from
    later months.

    Raises:
        OpenMeteoDataError: If ``months`` is empty or the result violates the
            length invariant.
    """
    if not months:
        raise OpenMeteoDataError("No months to assemble")
    data_key = step.value
    units_key = f"{data_key}_units"

    times: list[str] = []
    seen: set[str] = set()
    columns: dict[str, list[Any]] = {v: [] for v in variables}

    for month in months:
        block = month.get(data_key) or {}
        month_times = block.get("time") or []
        keep = [i for i, t in enumerate(month_times) if t not in seen]
        seen.update(month_times)
        aligned = align_variables(block, variables)
        for var in variables:
            series = aligned[var]
            columns[var].extend(series[i] for i in keep)
        times.extend(month_times[i] for i in keep)

    result = {k: v for k, v in months[0].items() if k not in (data_key, units_key)}
    units: dict[str, Any] = {}
    for month in months:
        for k, v in (month.get(units_key) or {}).items():
            units.setdefault(k, v)
    result[units_key] = units
    result[data_key] = {"time": times, **columns}
    check_invariant(result[data_key], f"after assembling {len(months)} month(s)")
    return result


def trim_to_range(
    data: dict[str, Any], start_date: date, end_date: date, step: TimeStep
) -> dict[str, Any]:
    """Keep only the points whose date lies within ``start_date..end_date``."""
    data_key = step.value
    block = data.get(data_key) or {}
    times = block.get("time") or []
    keep = [i for i, t in enumerate(times) if start_date <= parse_date(t) <= end_date]
    trimmed = {
        k: [v[i] for i in keep] if isinstance(v, list) else v for k, v in block.items()
    }
    out = dict(data)
    out[data_key] = trimmed
    return out


__all__ = [
    "MonthRange",
    "month_end",
    "iter_months",
    "parse_date",
    "align_variables",
    "check_invariant",
    "assemble",
    "trim_to_range",
]
