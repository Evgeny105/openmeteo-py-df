"""Async client for the Open-Meteo weather API.

``OpenMeteoClient`` fetches historical data (archive API), forecasts and
current conditions with caching and retries.

Key features:
    - Historical weather data from 1940 to present, cached per month
    - 16-day forecast, cached with TTL
    - Same variable names for history and forecast (ideal for ML)
    - Pluggable cache backends: memory, files, Redis (see :mod:`openmeteo.cache`)
    - Retries with exponential backoff for transient API failures
    - No API key required

Caching:
    Historical months are stored with coverage metadata. A month is
    re-fetched when it is missing, lacks requested variables, is not yet
    final (the archive still serves preliminary data) and older than
    ``recent_ttl``, or is final and older than ``historical_ttl``.
    Forecasts are cached for ``ttl_minutes`` and dropped when the forecast
    horizon is closer than :data:`CACHE_SAFETY_MARGIN_HOURS`.

    Backend selection when ``cache`` is not given: ``cache_url`` argument,
    then the ``OPENMETEO_CACHE_URL`` environment variable, then ``cache_dir``,
    then ``$XDG_CACHE_HOME/openmeteo`` (or ``~/.cache/openmeteo``). If the
    default directory is not writable (read-only container filesystem) the
    client falls back to an in-memory cache and logs a warning.

Example:
    Fetch historical data::

        import asyncio
        from datetime import date
        from openmeteo import OpenMeteoClient, TimeStep

        async def main():
            async with OpenMeteoClient() as client:
                data = await client.get_historical(
                    latitude=55.75,
                    longitude=37.62,
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 31),
                    step=TimeStep.HOURLY,
                )
                for i, t in enumerate(data.hourly.time):
                    print(f"{t}: {data.hourly.temperature_2m[i]}°C")

        asyncio.run(main())

    Share the cache between replicas through Redis::

        async with OpenMeteoClient(cache_url="redis://cache:6379/0") as client:
            ...
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Any, Optional, Union

import httpx

from .assembly import align_variables, assemble, iter_months
from .assembly import trim_to_range as trim_to_range_fn
from .cache.backends import CacheBackend, FileBackend, MemoryBackend, backend_from_url
from .cache.keys import ForecastKey, HistoryKey
from .cache.store import ForecastStore, HistoricalStore
from .exceptions import (
    OpenMeteoAPIError,
    OpenMeteoCacheError,
    OpenMeteoConnectionError,
    OpenMeteoValidationError,
)
from .models import (
    CurrentResponse,
    DailyResponse,
    ErrorResponse,
    HourlyResponse,
)
from .types import (
    ARCHIVE_BASE_URL,
    DEFAULT_CACHE_URL_ENV,
    DEFAULT_FORECAST_DAYS,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_RECENT_TTL,
    DEFAULT_RETRIES,
    DEFAULT_RETRY_BACKOFF,
    DEFAULT_TTL_MINUTES,
    FORECAST_BASE_URL,
    MAX_FORECAST_DAYS,
    MAX_RETRY_DELAY,
    TimeStep,
)

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({429})  # plus every 5xx


def _utc_now() -> datetime:
    return datetime.now(tz=dt_timezone.utc)


def _utc_today() -> date:
    return _utc_now().date()


def _default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "openmeteo"


def _last_forecast_time(data: dict[str, Any], step: TimeStep) -> datetime:
    """Timestamp of the last forecast point, UTC-aware; now if unavailable."""
    block = data.get(step.value) or {}
    times = block.get("time") or []
    if not times:
        return _utc_now()
    last = str(times[-1])
    if "T" in last:
        dt = datetime.fromisoformat(last)
    else:
        dt = datetime.combine(date.fromisoformat(last), datetime.min.time())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    "precipitation",
    "rain",
    "snowfall",
    "snow_depth",
    "weather_code",
    "pressure_msl",
    "surface_pressure",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "et0_fao_evapotranspiration",
    "vapour_pressure_deficit",
    "visibility",
    "is_day",
]
"""list[str]: Default hourly weather variables to fetch.

These variables are requested when calling get_historical() or
get_forecast() with step=TimeStep.HOURLY and no custom variables.

Includes:
    - Temperature: air, apparent, dew point
    - Humidity: relative humidity
    - Precipitation: total, rain, snowfall, snow depth
    - Pressure: mean sea level, surface
    - Clouds: total, low, mid, high
    - Wind: speed, direction, gusts
    - Radiation: shortwave, direct, diffuse
    - Evapotranspiration: ET0 FAO reference
    - Other: vapour pressure deficit, visibility, day/night
    - Weather code: WMO classification
"""

DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "apparent_temperature_mean",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "precipitation_hours",
    "weather_code",
    "sunrise",
    "sunset",
    "daylight_duration",
    "sunshine_duration",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "shortwave_radiation_sum",
    "et0_fao_evapotranspiration",
    "uv_index_max",
]
"""list[str]: Default daily weather variables to fetch.

These variables are requested when calling get_historical() or
get_forecast() with step=TimeStep.DAILY and no custom variables.

Includes:
    - Temperature: max, min, mean (air and apparent)
    - Precipitation: sum, rain sum, snowfall sum, hours
    - Wind: max speed, max gusts, dominant direction
    - Solar: sunrise, sunset, daylight, sunshine duration, radiation sum
    - UV: maximum UV index
    - Evapotranspiration: ET0 FAO reference
    - Weather code: WMO classification
"""

CURRENT_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    "precipitation",
    "rain",
    "snowfall",
    "weather_code",
    "pressure_msl",
    "surface_pressure",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
]
"""list[str]: Default current weather variables to fetch.

These variables are requested when calling get_current().

Includes:
    - Temperature: air, apparent, dew point
    - Humidity: relative humidity
    - Precipitation: total, rain, snowfall
    - Pressure: mean sea level, surface
    - Clouds: total cover
    - Wind: speed, direction, gusts
    - Weather code: WMO classification
"""




class OpenMeteoClient:
    """Async client for the Open-Meteo API with caching and retries.

    Construction is cheap and side-effect free: the HTTP client and the
    cache backend are created on first use. Use the client as an async
    context manager or call :meth:`close` when done.

    Args:
        cache: Ready cache backend. The caller owns it and closes it.
        cache_url: Backend URL (``memory://``, ``file:///path``,
            ``redis://…``, ``rediss://…?cluster=true&ca_bundle=/path``).
        cache_dir: Deprecated since 1.1.0; same as ``cache_url="file://<dir>"``.
        ttl_minutes: Forecast cache TTL. Defaults to 60.
        recent_ttl: How long a non-final historical month is served from
            cache before re-fetch. Defaults to 6 hours.
        historical_ttl: How long a final historical month is served from
            cache. ``None`` (default) means forever.
        cache_strict: Raise :class:`OpenMeteoCacheError` on backend failures
            instead of logging them and falling through to the API.
        timeout: HTTP request timeout in seconds. Defaults to 30.
        retries: Retries for transient API failures. Defaults to 3.
        retry_backoff: Base delay in seconds for exponential backoff.
        max_concurrency: Concurrent archive requests when filling cache gaps.

    Example:
        >>> async with OpenMeteoClient(cache_url="redis://cache:6379/0") as client:
        ...     forecast = await client.get_forecast(55.75, 37.62, days=7)
    """

    _sleep = staticmethod(asyncio.sleep)

    def __init__(
        self,
        *,
        cache: Optional[CacheBackend] = None,
        cache_url: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        ttl_minutes: int = DEFAULT_TTL_MINUTES,
        recent_ttl: timedelta = DEFAULT_RECENT_TTL,
        historical_ttl: Optional[timedelta] = None,
        cache_strict: bool = False,
        timeout: float = 30.0,
        retries: int = DEFAULT_RETRIES,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        if retries < 0:
            raise OpenMeteoValidationError(f"retries must be >= 0, got {retries}")
        if max_concurrency < 1:
            raise OpenMeteoValidationError(f"max_concurrency must be >= 1, got {max_concurrency}")
        if ttl_minutes <= 0:
            raise OpenMeteoValidationError(f"ttl_minutes must be > 0, got {ttl_minutes}")

        self._timeout = timeout
        self._retries = retries
        self._retry_backoff = retry_backoff
        self._max_concurrency = max_concurrency
        self._forecast_ttl = timedelta(minutes=ttl_minutes)
        self._recent_ttl = recent_ttl
        self._historical_ttl = historical_ttl
        self._cache_strict = cache_strict

        self._client: Optional[httpx.AsyncClient] = None

        self._cache_backend: Optional[CacheBackend] = cache
        self._cache_url = cache_url
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._owns_cache = cache is None
        self._cache_is_default = False
        self._cache_open = False
        self._cache_lock = asyncio.Lock()
        self._historical_store: Optional[HistoricalStore] = None
        self._forecast_store: Optional[ForecastStore] = None

    # -- lifecycle -------------------------------------------------------------

    async def __aenter__(self) -> "OpenMeteoClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and the cache backend this client created.

        Safe to call more than once. A backend passed in via ``cache`` is
        left open because the caller owns it.
        """
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()
        if self._cache_open and self._owns_cache and self._cache_backend is not None:
            await self._cache_backend.close()
        self._cache_open = False
        self._historical_store = None
        self._forecast_store = None

    # -- cache -----------------------------------------------------------------

    def _resolve_backend(self) -> tuple[CacheBackend, bool]:
        """Pick the backend from the configuration chain.

        Returns:
            ``(backend, is_default)`` where ``is_default`` is True only when the
            backend comes from the built-in default directory, i.e. nobody asked
            for it explicitly and a fallback to memory is acceptable.
        """
        if self._cache_backend is not None:
            return self._cache_backend, False
        if self._cache_url:
            return backend_from_url(self._cache_url), False
        env_url = os.environ.get(DEFAULT_CACHE_URL_ENV)
        if env_url:
            return backend_from_url(env_url), False
        if self._cache_dir is not None:
            return FileBackend(self._cache_dir), False
        return FileBackend(_default_cache_dir()), True

    @property
    def cache(self) -> CacheBackend:
        """The cache backend in use (resolved lazily, may not be opened yet)."""
        if self._cache_backend is None:
            self._cache_backend, self._cache_is_default = self._resolve_backend()
        return self._cache_backend

    async def _ensure_cache(self) -> None:
        if self._cache_open:
            return
        async with self._cache_lock:
            if self._cache_open:
                return
            backend = self.cache
            try:
                await backend.open()
            except OpenMeteoCacheError as e:
                if not self._cache_is_default:
                    raise
                logger.warning(
                    "Default cache directory is not usable (%s); falling back to an "
                    "in-memory cache that does not survive process restart. Set "
                    "%s to choose a backend explicitly.",
                    e,
                    DEFAULT_CACHE_URL_ENV,
                )
                backend = MemoryBackend()
                await backend.open()
                self._cache_backend = backend
                self._cache_is_default = False
            self._historical_store = HistoricalStore(
                backend,
                recent_ttl=self._recent_ttl,
                historical_ttl=self._historical_ttl,
                strict=self._cache_strict,
            )
            self._forecast_store = ForecastStore(
                backend, ttl=self._forecast_ttl, strict=self._cache_strict
            )
            self._cache_open = True

    async def cache_health(self) -> None:
        """Check that the cache backend is reachable.

        Intended for dependency health endpoints. Opens the backend if needed
        and propagates any error from its ``ping()``.
        """
        await self._ensure_cache()
        await self.cache.ping()

    async def clear_forecast_cache(self) -> int:
        """Delete all cached forecasts. Returns the number of removed entries."""
        await self._ensure_cache()
        assert self._forecast_store is not None
        return await self._forecast_store.clear()

    async def clear_historical_cache(self) -> int:
        """Delete all cached historical months. Returns the number of removed entries."""
        await self._ensure_cache()
        assert self._historical_store is not None
        return await self._historical_store.clear()

    async def clear_all_cache(self) -> int:
        """Delete all cached data. Returns the number of removed entries."""
        return await self.clear_forecast_cache() + await self.clear_historical_cache()

    # -- validation ------------------------------------------------------------

    def _validate_coordinates(self, latitude: float, longitude: float) -> None:
        if not -90.0 <= latitude <= 90.0:
            raise OpenMeteoValidationError(
                f"Latitude must be in range [-90.0, 90.0], got {latitude}"
            )
        if not -180.0 <= longitude <= 180.0:
            raise OpenMeteoValidationError(
                f"Longitude must be in range [-180.0, 180.0], got {longitude}"
            )

    def _validate_date_range(
        self, start_date: date, end_date: date, allow_future: bool = False
    ) -> None:
        if start_date > end_date:
            raise OpenMeteoValidationError(
                f"start_date ({start_date}) must be <= end_date ({end_date})"
            )
        if not allow_future and end_date > _utc_today():
            raise OpenMeteoValidationError(
                f"end_date ({end_date}) cannot be in the future for historical data"
            )

    def _validate_forecast_days(self, days: int) -> None:
        if not 1 <= days <= MAX_FORECAST_DAYS:
            raise OpenMeteoValidationError(
                f"days must be in range [1, {MAX_FORECAST_DAYS}], got {days}"
            )

    # -- HTTP ------------------------------------------------------------------

    def _retry_delay(self, attempt: int, retry_after: Optional[str]) -> float:
        """Delay before retry number ``attempt`` (0-based)."""
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), MAX_RETRY_DELAY)
            except ValueError:
                pass  # HTTP-date form; fall back to backoff
        base = min(self._retry_backoff * (2**attempt), MAX_RETRY_DELAY)
        return min(base * random.uniform(0.75, 1.25), MAX_RETRY_DELAY)

    @staticmethod
    def _api_error_from_body(response: httpx.Response) -> Optional[OpenMeteoAPIError]:
        try:
            body = response.json()
        except ValueError:
            return None
        if isinstance(body, dict) and body.get("error"):
            reason = body.get("reason") or f"HTTP {response.status_code}"
            return OpenMeteoAPIError(str(reason))
        return None

    async def _fetch(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET ``url`` and return the parsed JSON body.

        Retries on transport errors, HTTP 429 and 5xx with exponential
        backoff (``Retry-After`` is honoured). Other 4xx responses are not
        retried: a body of the form ``{"error": true, "reason": ...}`` raises
        :class:`OpenMeteoAPIError`, anything else :class:`OpenMeteoConnectionError`.

        Raises:
            OpenMeteoAPIError: The API rejected the request.
            OpenMeteoConnectionError: Network failure or server error after
                all retries.
        """
        client = await self._ensure_client()
        attempts = self._retries + 1
        last_error: Optional[BaseException] = None
        reason = ""
        for attempt in range(attempts):
            retry_after: Optional[str] = None
            try:
                response = await client.get(url, params=params)
            except httpx.TransportError as e:
                last_error, reason = e, f"request error: {e}"
            except httpx.HTTPError as e:
                raise OpenMeteoConnectionError(f"Request error: {e}") from e
            else:
                status = response.status_code
                if status < 400:
                    try:
                        data = response.json()
                    except ValueError as e:
                        raise OpenMeteoConnectionError(
                            f"Invalid JSON in response from {url}: {e}"
                        ) from e
                    if isinstance(data, dict) and data.get("error"):
                        raise OpenMeteoAPIError(ErrorResponse(**data).reason)
                    return data
                if status in _RETRYABLE_STATUS or status >= 500:
                    last_error, reason = None, f"HTTP {status}"
                    retry_after = response.headers.get("Retry-After")
                else:
                    api_error = self._api_error_from_body(response)
                    if api_error is not None:
                        raise api_error
                    raise OpenMeteoConnectionError(f"HTTP error: {status} for {url}")

            if attempt + 1 >= attempts:
                break
            delay = self._retry_delay(attempt, retry_after)
            logger.warning(
                "Open-Meteo request failed (%s); retry %d/%d in %.1fs",
                reason,
                attempt + 1,
                self._retries,
                delay,
            )
            await self._sleep(delay)

        raise OpenMeteoConnectionError(
            f"Open-Meteo request failed after {attempts} attempt(s): {reason}"
        ) from last_error

    # -- public API ------------------------------------------------------------

    async def get_historical(
        self,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
        step: TimeStep = TimeStep.HOURLY,
        *,
        timezone: str = "auto",
        variables: Optional[list[str]] = None,
        trim_to_range: bool = True,
    ) -> Union[HourlyResponse, DailyResponse]:
        """Get historical weather data for a location and date range.

        Data is cached per calendar month. Months that are missing from the
        cache, lack requested variables or are stale are fetched from the
        archive API concurrently (bounded by ``max_concurrency``) and stored.
        The result is assembled chronologically; every requested variable is
        a list of exactly ``len(time)`` values.

        Args:
            latitude: Latitude in decimal degrees (-90 to 90).
            longitude: Longitude in decimal degrees (-180 to 180).
            start_date: First day of the period.
            end_date: Last day of the period; cannot be in the future.
            step: Time step. Defaults to HOURLY.
            timezone: Timezone for timestamps (e.g. ``"Europe/Moscow"``).
                Defaults to ``"auto"``. Part of the cache key.
            variables: Variables to fetch. Defaults to ``HOURLY_VARIABLES`` or
                ``DAILY_VARIABLES`` depending on ``step``.
            trim_to_range: Cut the result to the exact date range. Defaults
                to True; False returns whole months.

        Returns:
            HourlyResponse for HOURLY step, DailyResponse for DAILY step.

        Raises:
            OpenMeteoValidationError: Invalid coordinates or dates.
            OpenMeteoConnectionError: Network failure after retries.
            OpenMeteoAPIError: The API rejected a request.
            OpenMeteoDataError: Assembled data violated an invariant.
            OpenMeteoCacheError: Cache failure in strict mode or an explicitly
                configured backend that cannot be opened.

        Example:
            >>> data = await client.get_historical(
            ...     55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31),
            ...     step=TimeStep.DAILY, variables=["temperature_2m_max"],
            ... )
        """
        self._validate_coordinates(latitude, longitude)
        self._validate_date_range(start_date, end_date)
        if variables is None:
            variables = HOURLY_VARIABLES if step == TimeStep.HOURLY else DAILY_VARIABLES
        variables = list(variables)

        await self._ensure_cache()
        store = self._historical_store
        assert store is not None

        months = list(iter_months(start_date, end_date, _utc_today()))
        keys = [
            HistoryKey(latitude, longitude, step, timezone, m.key) for m in months
        ]
        cached = await asyncio.gather(*(store.load(k, variables) for k in keys))

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def fetch_month(index: int) -> dict[str, Any]:
            month = months[index]
            async with semaphore:
                logger.debug("Fetching historical %s for (%s, %s)", month.key, latitude, longitude)
                params = {
                    "latitude": latitude,
                    "longitude": longitude,
                    "start_date": month.start.isoformat(),
                    "end_date": month.end.isoformat(),
                    "timezone": timezone,
                    step.value: ",".join(variables),
                }
                data = await self._fetch(ARCHIVE_BASE_URL, params)
                await store.save(keys[index], data, month.start, month.end, variables)
                return data

        missing = [i for i, entry in enumerate(cached) if entry is None]
        results = await asyncio.gather(
            *(fetch_month(i) for i in missing), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

        month_data: list[dict[str, Any]] = []
        fetched = dict(zip(missing, results))
        for i, entry in enumerate(cached):
            month_data.append(entry["data"] if entry is not None else fetched[i])  # type: ignore[index]

        merged = assemble(month_data, step, variables)
        if trim_to_range:
            merged = trim_to_range_fn(merged, start_date, end_date, step)

        if step == TimeStep.HOURLY:
            return HourlyResponse(**merged)
        return DailyResponse(**merged)

    async def get_forecast(
        self,
        latitude: float,
        longitude: float,
        days: int = DEFAULT_FORECAST_DAYS,
        step: TimeStep = TimeStep.HOURLY,
        *,
        timezone: str = "auto",
        variables: Optional[list[str]] = None,
        force_refresh: bool = False,
    ) -> Union[HourlyResponse, DailyResponse]:
        """Get the weather forecast for a location.

        Cached for ``ttl_minutes``; the cache entry is also dropped when the
        forecast horizon is less than :data:`CACHE_SAFETY_MARGIN_HOURS` away.
        The cache key includes ``days`` and ``timezone``.

        Args:
            latitude: Latitude in decimal degrees (-90 to 90).
            longitude: Longitude in decimal degrees (-180 to 180).
            days: Forecast days (1-16). Defaults to 7.
            step: Time step. Defaults to HOURLY.
            timezone: Timezone for timestamps. Defaults to ``"auto"``.
            variables: Variables to fetch. Defaults to the step's default list.
            force_refresh: Bypass the cache. Defaults to False.

        Returns:
            HourlyResponse for HOURLY step, DailyResponse for DAILY step.

        Raises:
            OpenMeteoValidationError: Invalid coordinates or days.
            OpenMeteoConnectionError: Network failure after retries.
            OpenMeteoAPIError: The API rejected the request.
        """
        self._validate_coordinates(latitude, longitude)
        self._validate_forecast_days(days)
        if variables is None:
            variables = HOURLY_VARIABLES if step == TimeStep.HOURLY else DAILY_VARIABLES
        variables = list(variables)

        await self._ensure_cache()
        store = self._forecast_store
        assert store is not None
        key = ForecastKey(latitude, longitude, step, days, timezone)

        if not force_refresh:
            cached = await store.load(key)
            if cached is not None:
                logger.debug("Using cached forecast for (%s, %s)", latitude, longitude)
                return self._forecast_response(cached, step, variables)

        logger.debug("Fetching forecast for (%s, %s)", latitude, longitude)
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "forecast_days": days,
            "timezone": timezone,
            step.value: ",".join(variables),
        }
        data = await self._fetch(FORECAST_BASE_URL, params)
        await store.save(key, data, _last_forecast_time(data, step))
        return self._forecast_response(data, step, variables)

    @staticmethod
    def _forecast_response(
        data: dict[str, Any], step: TimeStep, variables: Sequence[str]
    ) -> Union[HourlyResponse, DailyResponse]:
        payload = dict(data)
        payload[step.value] = align_variables(data.get(step.value) or {"time": []}, variables)
        if step == TimeStep.HOURLY:
            return HourlyResponse(**payload)
        return DailyResponse(**payload)

    async def get_current(
        self,
        latitude: float,
        longitude: float,
        *,
        timezone: str = "auto",
    ) -> CurrentResponse:
        """Get current weather conditions for a location (not cached).

        Args:
            latitude: Latitude in decimal degrees (-90 to 90).
            longitude: Longitude in decimal degrees (-180 to 180).
            timezone: Timezone for the timestamp. Defaults to ``"auto"``.

        Returns:
            CurrentResponse with current conditions.

        Raises:
            OpenMeteoValidationError: Invalid coordinates.
            OpenMeteoConnectionError: Network failure after retries.
            OpenMeteoAPIError: The API rejected the request.
        """
        self._validate_coordinates(latitude, longitude)
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "current": ",".join(CURRENT_VARIABLES),
        }
        data = await self._fetch(FORECAST_BASE_URL, params)
        return CurrentResponse(**data)


__all__ = [
    "OpenMeteoClient",
    "HOURLY_VARIABLES",
    "DAILY_VARIABLES",
    "CURRENT_VARIABLES",
]
