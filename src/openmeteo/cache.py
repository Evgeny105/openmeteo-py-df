"""Caching utilities for OpenMeteo client.

This module provides two caching strategies:

1. **HistoricalCache**: Persistent file-based cache for historical data.
   - Stores data in JSON files per location per month
   - Accumulates data indefinitely
   - Only fetches missing months
   - Re-fetches recent months (may be updated by OpenMeteo)

2. **ForecastCache**: In-memory cache for forecast data.
   - TTL-based expiration
   - Data freshness validation (expires near forecast end)
   - Fast access for repeated requests

Cache file naming:
    Files are named: `{coord_key}_{step}_{YYYY-MM}.json`
    Where coord_key is derived from lat/lon (e.g., "55p7500_37p6200").

Example:
    Cache files are managed automatically by OpenMeteoClient::

        async with OpenMeteoClient() as client:
            # First call: fetches and caches
            data1 = await client.get_historical(55.75, 37.62, start, end)

            # Second call: uses cache
            data2 = await client.get_historical(55.75, 37.62, start, end)
"""

import json
import logging
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Any, Optional, Union

from .models import DailyData, DailyResponse, HourlyData, HourlyResponse
from .types import CACHE_SAFETY_MARGIN_HOURS, HISTORY_RECENT_DAYS, TimeStep

logger = logging.getLogger(__name__)


def _coord_key(lat: float, lon: float) -> str:
    """Generate a filesystem-safe key from coordinates.

    Converts coordinates to a string suitable for use in filenames,
    replacing special characters that could cause issues.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.

    Returns:
        Filesystem-safe string (e.g., "55p7500_37p6200" for Moscow).

    Example:
        >>> _coord_key(55.75, 37.62)
        '55p7500_37p6200'
        >>> _coord_key(-33.865, 151.21)
        'm33p8650_151p2100'
    """
    return f"{lat:.4f}_{lon:.4f}".replace("-", "m").replace(".", "p")


def _month_key(d: date) -> str:
    """Generate a month key from a date.

    Args:
        d: Date to convert.

    Returns:
        Month key in YYYY-MM format.

    Example:
        >>> from datetime import date
        >>> _month_key(date(2024, 1, 15))
        '2024-01'
    """
    return d.strftime("%Y-%m")


def _parse_date(s: str) -> date:
    """Parse a date string from API response.

    Handles both date-only ("2024-01-15") and datetime
    ("2024-01-15T12:00") formats.

    Args:
        s: Date or datetime string.

    Returns:
        Parsed date object.

    Example:
        >>> _parse_date("2024-01-15")
        datetime.date(2024, 1, 15)
        >>> _parse_date("2024-01-15T12:00")
        datetime.date(2024, 1, 15)
    """
    if "T" in s:
        return datetime.fromisoformat(s).date()
    return date.fromisoformat(s)


class HistoricalCache:
    """File-based cache for historical weather data.

    Stores historical data in JSON files, organized by location and month.
    Data accumulates over time - only missing months are fetched.
    Recent months are re-fetched to capture OpenMeteo's corrections.

    Cache structure::

        cache_dir/
        ├── 55p7500_37p6200_hourly_2024-01.json
        ├── 55p7500_37p6200_hourly_2024-02.json
        ├── 55p7500_37p6200_daily_2024-01.json
        └── ...

    Args:
        cache_dir: Directory to store cache files. Created if not exists.

    Attributes:
        cache_dir: Path to the cache directory.

    Example:
        >>> from pathlib import Path
        >>> cache = HistoricalCache(Path.home() / ".cache" / "openmeteo" / "historical")
        >>> cache.save_month(55.75, 37.62, TimeStep.HOURLY, "2024-01", data)
        >>> loaded = cache.load_month(55.75, 37.62, TimeStep.HOURLY, "2024-01")
    """

    def __init__(self, cache_dir: Path) -> None:
        """Initialize the historical cache.

        Args:
            cache_dir: Directory to store cache files. Created if not exists.

        Example:
            >>> cache = HistoricalCache(Path("/tmp/openmeteo_cache"))
        """
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_file(
        self, lat: float, lon: float, step: TimeStep, month_key: str
    ) -> Path:
        """Get the cache file path for a location, step, and month.

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            step: Time step (HOURLY or DAILY).
            month_key: Month key in YYYY-MM format.

        Returns:
            Path to the cache file.

        Example:
            >>> cache._get_cache_file(55.75, 37.62, TimeStep.HOURLY, "2024-01")
            PosixPath('/cache/55p7500_37p6200_hourly_2024-01.json')
        """
        coord = _coord_key(lat, lon)
        return self.cache_dir / f"{coord}_{step.value}_{month_key}.json"

    def load_month(
        self, lat: float, lon: float, step: TimeStep, month_key: str
    ) -> Optional[dict[str, Any]]:
        """Load cached data for a specific month.

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            step: Time step (HOURLY or DAILY).
            month_key: Month key in YYYY-MM format.

        Returns:
            Cached data as dict, or None if not found or on error.

        Example:
            >>> data = cache.load_month(55.75, 37.62, TimeStep.HOURLY, "2024-01")
            >>> if data:
            ...     print(f"Loaded {len(data['hourly']['time'])} data points")
        """
        cache_file = self._get_cache_file(lat, lon, step, month_key)
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load cache {cache_file}: {e}")
            return None

    def save_month(
        self,
        lat: float,
        lon: float,
        step: TimeStep,
        month_key: str,
        data: dict[str, Any],
    ) -> None:
        """Save data for a specific month to cache.

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            step: Time step (HOURLY or DAILY).
            month_key: Month key in YYYY-MM format.
            data: API response data to cache.

        Example:
            >>> cache.save_month(
            ...     55.75, 37.62, TimeStep.HOURLY, "2024-01",
            ...     {"hourly": {"time": [...], "temperature_2m": [...]}}
            ... )
        """
        cache_file = self._get_cache_file(lat, lon, step, month_key)
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            logger.debug(f"Saved cache to {cache_file}")
        except Exception as e:
            logger.error(f"Failed to save cache {cache_file}: {e}")

    def get_cached_months(self, lat: float, lon: float, step: TimeStep) -> set[str]:
        """Get set of cached month keys for a location and step.

        Scans the cache directory for files matching the location
        and step pattern.

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            step: Time step (HOURLY or DAILY).

        Returns:
            Set of month keys (e.g., {"2024-01", "2024-02", ...}).

        Example:
            >>> months = cache.get_cached_months(55.75, 37.62, TimeStep.HOURLY)
            >>> print(f"Have data for {len(months)} months")
        """
        coord = _coord_key(lat, lon)
        pattern = f"{coord}_{step.value}_*.json"
        months = set()
        for f in self.cache_dir.glob(pattern):
            parts = f.stem.split("_")
            if len(parts) >= 3:
                months.add(parts[-1])
        return months

    def is_month_recent(self, month_key: str) -> bool:
        """Check if a month is considered "recent" and should be re-fetched.

        Recent months (within HISTORY_RECENT_DAYS from current month) are
        re-fetched to capture OpenMeteo's data corrections.

        Args:
            month_key: Month key in YYYY-MM format.

        Returns:
            True if the month should be considered for re-fetching.

        Example:
            >>> # Current date: 2024-03-15
            >>> cache.is_month_recent("2024-03")  # Current month
            True
            >>> cache.is_month_recent("2024-02")  # Last month
            True
            >>> cache.is_month_recent("2023-01")  # Old data
            False
        """
        today = datetime.now(tz=dt_timezone.utc).date()
        month_date = datetime.strptime(month_key, "%Y-%m").date()
        cutoff = today.replace(day=1) - timedelta(days=HISTORY_RECENT_DAYS * 31)
        return month_date >= cutoff

    def get_missing_months(
        self,
        lat: float,
        lon: float,
        step: TimeStep,
        start_date: date,
        end_date: date,
    ) -> list[str]:
        """Get list of months that need to be fetched.

        Determines which months in the requested range are either:
        - Not cached at all
        - Cached but recent (should be re-fetched for corrections)

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            step: Time step (HOURLY or DAILY).
            start_date: Start of requested date range.
            end_date: End of requested date range.

        Returns:
            Sorted list of month keys to fetch.

        Example:
            >>> from datetime import date
            >>> missing = cache.get_missing_months(
            ...     55.75, 37.62, TimeStep.HOURLY,
            ...     date(2024, 1, 1), date(2024, 3, 31)
            ... )
            >>> print(f"Need to fetch {len(missing)} months")
        """
        cached = self.get_cached_months(lat, lon, step)
        needed = set()

        current = start_date.replace(day=1)
        end_month = end_date.replace(day=1)

        while current <= end_month:
            month_key = _month_key(current)
            if month_key not in cached or self.is_month_recent(month_key):
                needed.add(month_key)
            current = (current + timedelta(days=32)).replace(day=1)

        return sorted(needed)


class ForecastCache:
    """In-memory cache for forecast data.

    Stores forecast data with TTL and data freshness validation.
    Cache is invalidated when:
    1. TTL has expired (too much time since fetch)
    2. Current time is within CACHE_SAFETY_MARGIN_HOURS of the last
       forecast point (data is becoming stale)

    Args:
        ttl_minutes: Cache time-to-live in minutes. Defaults to 60.

    Example:
        >>> cache = ForecastCache(ttl_minutes=30)
        >>> cache.set(55.75, 37.62, TimeStep.HOURLY, response)
        >>> if cache.is_valid(55.75, 37.62, TimeStep.HOURLY):
        ...     data = cache.get(55.75, 37.62, TimeStep.HOURLY)
    """

    def __init__(self, ttl_minutes: int = 60) -> None:
        """Initialize the forecast cache.

        Args:
            ttl_minutes: Cache time-to-live in minutes. Defaults to 60.

        Example:
            >>> cache = ForecastCache(ttl_minutes=30)
        """
        self._ttl = timedelta(minutes=ttl_minutes)
        self._cache: dict[tuple[float, float, TimeStep], dict[str, Any]] = {}
        self._fetched_at: dict[tuple[float, float, TimeStep], datetime] = {}
        self._last_forecast_time: dict[tuple[float, float, TimeStep], datetime] = {}

    def _get_last_time(self, data: Any) -> datetime:
        """Extract the timestamp of the last forecast point.

        Args:
            data: HourlyResponse or DailyResponse object.

        Returns:
            Datetime of the last forecast point, or now if unavailable.
            Always returns timezone-aware datetime in UTC.

        Example:
            >>> last_time = cache._get_last_time(response)
            >>> print(f"Forecast ends at {last_time}")
        """
        if hasattr(data, "hourly"):
            hourly = data.hourly
            if hasattr(hourly, "time") and hourly.time:
                last = hourly.time[-1]
                if "T" in last:
                    dt = datetime.fromisoformat(last)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=dt_timezone.utc)
                    return dt
                return datetime.combine(
                    date.fromisoformat(last),
                    datetime.min.time(),
                    tzinfo=dt_timezone.utc,
                )

        if hasattr(data, "daily"):
            daily = data.daily
            if hasattr(daily, "time") and daily.time:
                last = daily.time[-1]
                return datetime.combine(
                    date.fromisoformat(last),
                    datetime.min.time(),
                    tzinfo=dt_timezone.utc,
                )

        return datetime.now(tz=dt_timezone.utc)

    def get(self, lat: float, lon: float, step: TimeStep) -> Optional[dict[str, Any]]:
        """Get cached forecast data.

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            step: Time step (HOURLY or DAILY).

        Returns:
            Cached data as dict, or None if not cached.

        Example:
            >>> data = cache.get(55.75, 37.62, TimeStep.HOURLY)
            >>> if data:
            ...     print(f"Have cached data with {len(data['hourly']['time'])} points")
        """
        key = (lat, lon, step)
        return self._cache.get(key)

    def set(
        self,
        lat: float,
        lon: float,
        step: TimeStep,
        data: Union[HourlyResponse, DailyResponse],
    ) -> None:
        """Store forecast data in cache.

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            step: Time step (HOURLY or DAILY).
            data: Response object to cache.

        Example:
            >>> cache.set(55.75, 37.62, TimeStep.HOURLY, response)
        """
        key = (lat, lon, step)
        now = datetime.now(tz=dt_timezone.utc)
        self._cache[key] = data.model_dump()
        self._fetched_at[key] = now
        self._last_forecast_time[key] = self._get_last_time(data)

    def is_valid(self, lat: float, lon: float, step: TimeStep) -> bool:
        """Check if cached data is still valid.

        Uses two-level validation:
        1. TTL check: Has enough time passed since fetch?
        2. Freshness check: Are we too close to the forecast's end?

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            step: Time step (HOURLY or DAILY).

        Returns:
            True if cache is valid and can be used, False otherwise.

        Example:
            >>> if cache.is_valid(55.75, 37.62, TimeStep.HOURLY):
            ...     data = cache.get(55.75, 37.62, TimeStep.HOURLY)
            ... else:
            ...     # Need to fetch fresh data
            ...     pass
        """
        key = (lat, lon, step)
        if key not in self._cache:
            return False

        now = datetime.now(tz=dt_timezone.utc)
        fetched = self._fetched_at.get(key)
        last_time = self._last_forecast_time.get(key)

        if not fetched or not last_time:
            return False

        if (now - fetched) > self._ttl:
            return False

        if now > (last_time - timedelta(hours=CACHE_SAFETY_MARGIN_HOURS)):
            return False

        return True

    def clear(self) -> None:
        """Clear all cached forecast data.

        Removes all entries from the in-memory cache.

        Example:
            >>> cache.clear()  # Force fresh fetches on next requests
        """
        self._cache.clear()
        self._fetched_at.clear()
        self._last_forecast_time.clear()
