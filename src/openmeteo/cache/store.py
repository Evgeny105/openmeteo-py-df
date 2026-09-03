"""Domain stores on top of a key-value backend.

- :class:`HistoricalStore` keeps one entry per cached month of historical data
  with coverage metadata and decides whether a cached month is still usable.
- :class:`ForecastStore` keeps forecast responses with a TTL and a freshness
  margin before the forecast horizon.

Both stores apply the same error policy toward the backend: with
``strict=False`` (the default) a failing backend call is logged at ERROR level
and treated as a cache miss so data still flows from the API; with
``strict=True`` it surfaces as :class:`OpenMeteoCacheError`. ``ping()`` is never
swallowed so health checks stay truthful.

Entry format for a historical month (``format`` = 2)::

    {
      "format": 2,
      "package_version": "1.1.0",
      "saved_at": "2026-09-03T12:00:00+00:00",
      "covered_from": "2025-12-01",
      "covered_to": "2025-12-31",
      "final": true,
      "variables": ["temperature_2m", "..."],
      "data": { ...API response as returned... }
    }

Example:
    >>> store = HistoricalStore(backend, recent_ttl=timedelta(hours=6))
    >>> entry = await store.load(key, ["temperature_2m"])
    >>> if entry is None:
    ...     data = await fetch_month(...)
    ...     await store.save(key, data, month_start, month_end, ["temperature_2m"])
"""

from __future__ import annotations

import calendar
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any, Optional, TypeVar

from ..exceptions import OpenMeteoCacheError
from ..types import (
    ARCHIVE_LAG_DAYS,
    CACHE_FORMAT_VERSION,
    CACHE_SAFETY_MARGIN_HOURS,
    DEFAULT_RECENT_TTL,
    TimeStep,
)
from .backends import CacheBackend
from .keys import ForecastKey, HistoryKey

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Minimum hours per day accepted as a complete day (DST switch days have 23).
_MIN_HOURS_PER_DAY = 23


def _utc_now() -> datetime:
    return datetime.now(tz=dt_timezone.utc)


def _package_version() -> str:
    from .. import __version__

    return __version__


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


class _BackendGuard:
    """Shared error policy for backend calls."""

    def __init__(self, backend: CacheBackend, strict: bool) -> None:
        self._backend = backend
        self._strict = strict

    @property
    def backend(self) -> CacheBackend:
        return self._backend

    async def _call(self, op: str, key: str, coro: Awaitable[T], default: T) -> T:
        try:
            return await coro
        except OpenMeteoCacheError:
            if self._strict:
                raise
            logger.error("Cache %s failed for %s; treating as miss", op, key, exc_info=True)
            return default
        except Exception as e:  # backend-specific errors: policy decides, never silent
            if self._strict:
                raise OpenMeteoCacheError(f"Cache {op} failed for {key}: {e}") from e
            logger.error("Cache %s failed for %s; treating as miss", op, key, exc_info=True)
            return default

    async def get(self, key: str) -> Optional[bytes]:
        return await self._call("get", key, self._backend.get(key), None)

    async def set(self, key: str, value: bytes, ttl: Optional[timedelta]) -> None:
        await self._call("set", key, self._backend.set(key, value, ttl), None)

    async def delete(self, key: str) -> None:
        await self._call("delete", key, self._backend.delete(key), None)

    async def clear(self, prefix: str) -> int:
        return await self._call("clear", prefix, self._backend.clear(prefix), 0)

    async def ping(self) -> None:
        await self._backend.ping()


def _decode_entry(raw: bytes, key: str) -> Optional[dict[str, Any]]:
    """Decode an entry; return None (caller deletes) when unusable."""
    try:
        entry = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        logger.warning("Corrupt cache entry %s (%s); dropping", key, e)
        return None
    if not isinstance(entry, dict) or entry.get("format") != CACHE_FORMAT_VERSION:
        logger.debug("Cache entry %s has unsupported format; dropping", key)
        return None
    return entry


class HistoricalStore:
    """Cached months of historical data with coverage-aware freshness.

    Args:
        backend: Opened cache backend.
        recent_ttl: How long a non-final month is served before re-fetch.
        historical_ttl: How long a final month is served; ``None`` = forever.
        strict: Raise :class:`OpenMeteoCacheError` on backend failures
            instead of treating them as misses.
        now: Clock, injectable for tests.
    """

    def __init__(
        self,
        backend: CacheBackend,
        *,
        recent_ttl: timedelta = DEFAULT_RECENT_TTL,
        historical_ttl: Optional[timedelta] = None,
        strict: bool = False,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._guard = _BackendGuard(backend, strict)
        self._recent_ttl = recent_ttl
        self._historical_ttl = historical_ttl
        self._now = now

    # -- entry construction ----------------------------------------------------

    @staticmethod
    def _series_lengths_consistent(data: dict[str, Any], step: TimeStep) -> bool:
        block = data.get(step.value)
        if not isinstance(block, dict) or not isinstance(block.get("time"), list):
            return False
        n = len(block["time"])
        return all(
            isinstance(v, list) and len(v) == n for k, v in block.items() if k != "time"
        )

    @staticmethod
    def _looks_complete(
        data: dict[str, Any], step: TimeStep, covered_from: date, covered_to: date
    ) -> tuple[bool, int, int]:
        """Return (complete, actual_points, minimum_expected_points)."""
        block = data.get(step.value) or {}
        times = block.get("time") or []
        days = (covered_to - covered_from).days + 1
        minimum = days * _MIN_HOURS_PER_DAY if step == TimeStep.HOURLY else days
        actual = len(times)
        if actual < minimum:
            return False, actual, minimum
        last = str(times[-1])[:10] if times else ""
        return last == covered_to.isoformat(), actual, minimum

    def _build_entry(
        self,
        key: HistoryKey,
        data: dict[str, Any],
        covered_from: date,
        covered_to: date,
        variables: Iterable[str],
    ) -> dict[str, Any]:
        now = self._now()
        last_day = calendar.monthrange(covered_to.year, covered_to.month)[1]
        month_complete = covered_to.day == last_day
        lag_passed = now.date() >= covered_to + timedelta(days=ARCHIVE_LAG_DAYS)
        complete, actual, expected = self._looks_complete(data, key.step, covered_from, covered_to)
        if not complete:
            logger.warning(
                "Cached month %s covers %s..%s but has %d points (expected at least %d); "
                "marking as non-final",
                key.as_str(),
                covered_from,
                covered_to,
                actual,
                expected,
            )
        return {
            "format": CACHE_FORMAT_VERSION,
            "package_version": _package_version(),
            "saved_at": now.isoformat(),
            "covered_from": covered_from.isoformat(),
            "covered_to": covered_to.isoformat(),
            "final": bool(month_complete and lag_passed and complete),
            "variables": sorted(set(variables)),
            "data": data,
        }

    # -- public API ------------------------------------------------------------

    async def save(
        self,
        key: HistoryKey,
        data: dict[str, Any],
        covered_from: date,
        covered_to: date,
        variables: Iterable[str],
    ) -> dict[str, Any]:
        """Store a month and return the entry that was written.

        The entry is returned even if the backend write failed in lenient
        mode, so the caller can keep serving the freshly fetched data.
        """
        entry = self._build_entry(key, data, covered_from, covered_to, variables)
        payload = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        ttl = self._historical_ttl if entry["final"] else self._recent_ttl
        await self._guard.set(key.as_str(), payload, ttl)
        return entry

    async def load(self, key: HistoryKey, requested_vars: Iterable[str]) -> Optional[dict[str, Any]]:
        """Return a usable entry or ``None`` when the month must be re-fetched.

        Unusable-by-construction entries (bad format, corrupt JSON, inconsistent
        series lengths) are deleted from the backend. Entries that are merely
        insufficient for this request (missing variables, expired) are kept.
        """
        skey = key.as_str()
        raw = await self._guard.get(skey)
        if raw is None:
            return None
        entry = _decode_entry(raw, skey)
        if entry is None:
            await self._guard.delete(skey)
            return None

        data = entry.get("data")
        if not isinstance(data, dict) or not self._series_lengths_consistent(data, key.step):
            logger.warning("Cache entry %s has inconsistent series; dropping", skey)
            await self._guard.delete(skey)
            return None

        try:
            saved_at = _parse_dt(entry["saved_at"])
            final = bool(entry["final"])
            variables = set(entry["variables"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Cache entry %s lacks required metadata; dropping", skey)
            await self._guard.delete(skey)
            return None

        if not variables.issuperset(requested_vars):
            logger.debug("Cache entry %s lacks requested variables; re-fetching", skey)
            return None

        age = self._now() - saved_at
        if not final and age > self._recent_ttl:
            logger.debug("Cache entry %s is non-final and older than recent_ttl; re-fetching", skey)
            return None
        if final and self._historical_ttl is not None and age > self._historical_ttl:
            logger.debug("Cache entry %s is older than historical_ttl; re-fetching", skey)
            return None
        return entry

    async def clear(self) -> int:
        """Delete all cached historical months."""
        return await self._guard.clear(HistoryKey.prefix())

    async def ping(self) -> None:
        """Health check; propagates backend errors."""
        await self._guard.ping()


class ForecastStore:
    """Cached forecast responses with TTL and a freshness margin.

    Args:
        backend: Opened cache backend.
        ttl: Time to live of a cached forecast.
        strict: Raise on backend failures instead of treating them as misses.
        now: Clock, injectable for tests.
    """

    def __init__(
        self,
        backend: CacheBackend,
        *,
        ttl: timedelta,
        strict: bool = False,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._guard = _BackendGuard(backend, strict)
        self._ttl = ttl
        self._now = now

    async def save(self, key: ForecastKey, data: dict[str, Any], last_forecast_time: datetime) -> None:
        """Store a forecast response."""
        entry = {
            "format": CACHE_FORMAT_VERSION,
            "saved_at": self._now().isoformat(),
            "last_forecast_time": last_forecast_time.isoformat(),
            "data": data,
        }
        payload = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        await self._guard.set(key.as_str(), payload, self._ttl)

    async def load(self, key: ForecastKey) -> Optional[dict[str, Any]]:
        """Return cached response data or ``None`` if absent or too close to the horizon."""
        skey = key.as_str()
        raw = await self._guard.get(skey)
        if raw is None:
            return None
        entry = _decode_entry(raw, skey)
        if entry is None:
            await self._guard.delete(skey)
            return None
        try:
            last = _parse_dt(entry["last_forecast_time"])
            data = entry["data"]
        except (KeyError, TypeError, ValueError):
            await self._guard.delete(skey)
            return None
        if self._now() > last - timedelta(hours=CACHE_SAFETY_MARGIN_HOURS):
            return None
        return data if isinstance(data, dict) else None

    async def clear(self) -> int:
        """Delete all cached forecasts."""
        return await self._guard.clear(ForecastKey.prefix())

    async def ping(self) -> None:
        """Health check; propagates backend errors."""
        await self._guard.ping()


__all__ = ["HistoricalStore", "ForecastStore"]
