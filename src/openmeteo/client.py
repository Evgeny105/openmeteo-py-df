"""Async client for the OpenMeteo weather API.

This module provides OpenMeteoClient for fetching weather data from the
OpenMeteo API with intelligent caching and error handling.

Key features:
    - Historical weather data from 1940 to present
    - 16-day weather forecast
    - Current weather conditions
    - Same variables for historical and forecast (ideal for ML)
    - Smart caching: JSON files for historical, memory for forecast
    - No API key required

Caching:
    Historical data is cached per-month in JSON files and accumulates
    indefinitely. Only missing months are fetched. Forecast data is
    cached in memory with TTL and freshness validation.

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
                    step=TimeStep.HOURLY
                )
                for i, t in enumerate(data.hourly.time):
                    print(f"{t}: {data.hourly.temperature_2m[i]}°C")

        asyncio.run(main())
"""

import logging
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Any, Optional, Union

import httpx

from .cache import ForecastCache, HistoricalCache, _parse_date
from .exceptions import (
    OpenMeteoAPIError,
    OpenMeteoConnectionError,
    OpenMeteoValidationError,
)
from .models import (
    CurrentData,
    CurrentResponse,
    DailyData,
    DailyResponse,
    DailyUnits,
    ErrorResponse,
    HourlyData,
    HourlyResponse,
    HourlyUnits,
)
from .types import (
    ARCHIVE_BASE_URL,
    DEFAULT_FORECAST_DAYS,
    DEFAULT_TTL_MINUTES,
    FORECAST_BASE_URL,
    MAX_FORECAST_DAYS,
    TimeStep,
)

logger = logging.getLogger(__name__)


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
    """Async client for OpenMeteo weather API.

    Provides methods to fetch historical, forecast, and current weather
    data from OpenMeteo with intelligent caching.

    Historical data is cached in JSON files per location per month,
    accumulating over time. Only missing months are fetched on subsequent
    requests.

    Forecast data is cached in memory with TTL and freshness validation.
    Cache is invalidated when approaching the end of the forecast period.

    Args:
        ttl_minutes: Cache TTL for forecast data in minutes. Defaults to 60.
        cache_dir: Directory for historical data cache. Defaults to
            ~/.cache/openmeteo/historical.
        timeout: HTTP request timeout in seconds. Defaults to 30.0.

    Attributes:
        _ttl: Forecast cache TTL in minutes.
        _timeout: HTTP timeout in seconds.
        _client: Lazy-initialized httpx.AsyncClient.
        _historical_cache: File-based cache for historical data.
        _forecast_cache: In-memory cache for forecast data.

    Example:
        Using as async context manager (recommended)::

            async with OpenMeteoClient() as client:
                forecast = await client.get_forecast(55.75, 37.62)

        Manual resource management::

            client = OpenMeteoClient()
            try:
                forecast = await client.get_forecast(55.75, 37.62)
            finally:
                await client.close()

        Custom configuration::

            async with OpenMeteoClient(
                ttl_minutes=30,
                cache_dir=Path("./my_cache"),
                timeout=60.0
            ) as client:
                forecast = await client.get_forecast(55.75, 37.62)
    """

    def __init__(
        self,
        *,
        ttl_minutes: int = DEFAULT_TTL_MINUTES,
        cache_dir: Optional[Path] = None,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the OpenMeteo client.

        Args:
            ttl_minutes: Cache TTL for forecast data in minutes. Defaults to 60.
            cache_dir: Directory for historical data cache. Defaults to
                ~/.cache/openmeteo/historical.
            timeout: HTTP request timeout in seconds. Defaults to 30.0.

        Example:
            >>> client = OpenMeteoClient()
            >>> client = OpenMeteoClient(
            ...     ttl_minutes=30,
            ...     cache_dir=Path("./cache"),
            ...     timeout=60.0
            ... )
        """
        self._ttl = ttl_minutes
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "openmeteo"
        self._historical_cache = HistoricalCache(cache_dir / "historical")
        self._forecast_cache = ForecastCache(ttl_minutes)

    async def __aenter__(self) -> "OpenMeteoClient":
        """Enter async context manager.

        Initializes the HTTP client and returns self for use in
        async with blocks.

        Returns:
            The OpenMeteoClient instance.

        Example:
            >>> async with OpenMeteoClient() as client:
            ...     data = await client.get_forecast(55.75, 37.62)
        """
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context manager.

        Closes the HTTP client and releases resources.

        Args:
            *args: Exception info if an exception was raised.
        """
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized.

        Lazily creates the httpx.AsyncClient if not already created.
        This allows the client to be created without immediately
        opening a connection.

        Returns:
            The initialized httpx.AsyncClient instance.

        Example:
            >>> # Internal method - called automatically by public methods
            >>> client = await self._ensure_client()
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and release resources.

        Should be called when done using the client if not using
        as a context manager. Safe to call multiple times.

        Example:
            >>> client = OpenMeteoClient()
            >>> try:
            ...     data = await client.get_forecast(55.75, 37.62)
            ... finally:
            ...     await client.close()
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _validate_coordinates(self, latitude: float, longitude: float) -> None:
        """Validate geographic coordinates.

        Args:
            latitude: Latitude in decimal degrees.
            longitude: Longitude in decimal degrees.

        Raises:
            OpenMeteoValidationError: If latitude not in [-90, 90] or
                longitude not in [-180, 180].

        Example:
            >>> self._validate_coordinates(55.75, 37.62)  # Valid
            >>> self._validate_coordinates(999.0, 37.62)  # Raises ValidationError
        """
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
        """Validate date range for requests.

        Args:
            start_date: Start date of the range.
            end_date: End date of the range.
            allow_future: Whether future dates are allowed. Defaults to False.

        Raises:
            OpenMeteoValidationError: If start_date > end_date, or if
                end_date is in the future and allow_future is False.

        Example:
            >>> self._validate_date_range(
            ...     date(2024, 1, 1), date(2024, 1, 31)
            ... )  # Valid
            >>> self._validate_date_range(
            ...     date(2024, 2, 1), date(2024, 1, 1)
            ... )  # Raises ValidationError (start > end)
        """
        if start_date > end_date:
            raise OpenMeteoValidationError(
                f"start_date ({start_date}) must be <= end_date ({end_date})"
            )
        if not allow_future and end_date > date.today():
            raise OpenMeteoValidationError(
                f"end_date ({end_date}) cannot be in the future for historical data"
            )

    def _validate_forecast_days(self, days: int) -> None:
        """Validate forecast days parameter.

        Args:
            days: Number of forecast days to fetch.

        Raises:
            OpenMeteoValidationError: If days not in [1, MAX_FORECAST_DAYS].

        Example:
            >>> self._validate_forecast_days(7)   # Valid
            >>> self._validate_forecast_days(20)  # Raises ValidationError (max 16)
        """
        if not 1 <= days <= MAX_FORECAST_DAYS:
            raise OpenMeteoValidationError(
                f"days must be in range [1, {MAX_FORECAST_DAYS}], got {days}"
            )

    async def _fetch(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch data from the API.

        Makes an HTTP GET request and handles errors.

        Args:
            url: API URL to fetch from.
            params: Query parameters.

        Returns:
            Parsed JSON response as dict.

        Raises:
            OpenMeteoConnectionError: If HTTP request fails.
            OpenMeteoAPIError: If API returns an error response.

        Example:
            >>> # Internal method - used by public methods
            >>> data = await self._fetch(
            ...     "https://api.open-meteo.com/v1/forecast",
            ...     {"latitude": 55.75, "longitude": 37.62}
            ... )
        """
        client = await self._ensure_client()

        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OpenMeteoConnectionError(f"HTTP error: {e}") from e
        except httpx.RequestError as e:
            raise OpenMeteoConnectionError(f"Request error: {e}") from e

        data = response.json()

        if data.get("error"):
            error = ErrorResponse(**data)
            raise OpenMeteoAPIError(error.reason)

        return data

    def _merge_data(
        self,
        existing: Optional[dict[str, Any]],
        new: dict[str, Any],
        step: TimeStep,
    ) -> dict[str, Any]:
        """Merge new data into existing cached data.

        Combines data from multiple API calls, avoiding duplicates
        based on timestamps. Used when fetching multiple months.

        Args:
            existing: Existing cached data, or None.
            new: New data to merge.
            step: Time step (HOURLY or DAILY).

        Returns:
            Merged data dict.

        Example:
            >>> merged = self._merge_data(
            ...     existing_cache,
            ...     new_api_response,
            ...     TimeStep.HOURLY
            ... )
        """
        if existing is None:
            return new

        data_key = "hourly" if step == TimeStep.HOURLY else "daily"
        times_key = "time"

        existing_times = set(existing.get(data_key, {}).get(times_key, []))
        new_data = new.get(data_key, {})
        new_times = new_data.get(times_key, [])

        merged = dict(existing)
        merged_data = dict(merged.get(data_key, {}))

        for key, values in new_data.items():
            if key not in merged_data:
                merged_data[key] = values
            else:
                existing_vals = merged_data[key] or []
                new_vals = values or []

                merged_vals = list(existing_vals)
                for i, t in enumerate(new_times):
                    if t not in existing_times:
                        if i < len(new_vals):
                            merged_vals.append(new_vals[i])

                merged_data[key] = merged_vals

        merged[data_key] = merged_data
        return merged

    def _trim_to_range(
        self,
        data: dict[str, Any],
        start_date: date,
        end_date: date,
        step: TimeStep,
    ) -> dict[str, Any]:
        """Trim data to the requested date range.

        Filters data to only include points within the specified range.
        Used after merging cached data from multiple months.

        Args:
            data: Data to trim.
            start_date: Start date of range.
            end_date: End date of range.
            step: Time step (HOURLY or DAILY).

        Returns:
            Trimmed data dict.

        Example:
            >>> trimmed = self._trim_to_range(
            ...     merged_data,
            ...     date(2024, 1, 15),
            ...     date(2024, 1, 20),
            ...     TimeStep.HOURLY
            ... )
        """
        data_key = "hourly" if step == TimeStep.HOURLY else "daily"
        time_list = data.get(data_key, {}).get("time", [])

        if not time_list:
            return data

        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        if step == TimeStep.HOURLY:
            start_str = f"{start_str}T00:00"
            end_str = f"{end_str}T23:59"

        indices = []
        for i, t in enumerate(time_list):
            t_date = t[:10] if step == TimeStep.DAILY else t[:16]
            if start_str <= t <= end_str:
                indices.append(i)

        if not indices:
            return data

        trimmed = dict(data)
        trimmed_data = dict(trimmed.get(data_key, {}))

        for key, values in trimmed_data.items():
            if isinstance(values, list):
                trimmed_data[key] = [values[i] for i in indices if i < len(values)]

        trimmed[data_key] = trimmed_data
        return trimmed

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

        Fetches historical weather data with intelligent caching. Data is
        cached per-month in JSON files. Only missing months are fetched.
        Recent months are re-fetched to capture OpenMeteo's corrections.

        Args:
            latitude: Latitude in decimal degrees (-90 to 90).
            longitude: Longitude in decimal degrees (-180 to 180).
            start_date: Start date of the historical period.
            end_date: End date of the historical period. Cannot be in the future.
            step: Time step granularity. Defaults to HOURLY.
            timezone: Timezone for data (e.g., "Europe/Moscow"). Defaults to "auto".
            variables: Custom list of variable names to fetch. Defaults to
                HOURLY_VARIABLES or DAILY_VARIABLES based on step.
            trim_to_range: Whether to trim results to exact date range.
                Defaults to True. Set False to get full months.

        Returns:
            HourlyResponse for HOURLY step, DailyResponse for DAILY step.

        Raises:
            OpenMeteoValidationError: If coordinates or dates are invalid.
            OpenMeteoConnectionError: If HTTP request fails.
            OpenMeteoAPIError: If API returns an error.

        Example:
            >>> from datetime import date
            >>> async with OpenMeteoClient() as client:
            ...     # Get hourly historical data
            ...     data = await client.get_historical(
            ...         latitude=55.75,
            ...         longitude=37.62,
            ...         start_date=date(2024, 1, 1),
            ...         end_date=date(2024, 1, 31),
            ...         step=TimeStep.HOURLY
            ...     )
            ...     for i, t in enumerate(data.hourly.time):
            ...         print(f"{t}: {data.hourly.temperature_2m[i]}°C")
            ...
            ...     # Get daily historical data with custom variables
            ...     daily = await client.get_historical(
            ...         latitude=55.75,
            ...         longitude=37.62,
            ...         start_date=date(2023, 1, 1),
            ...         end_date=date(2023, 12, 31),
            ...         step=TimeStep.DAILY,
            ...         variables=["temperature_2m_max", "temperature_2m_min"]
            ...     )
        """
        self._validate_coordinates(latitude, longitude)
        self._validate_date_range(start_date, end_date)

        if variables is None:
            variables = HOURLY_VARIABLES if step == TimeStep.HOURLY else DAILY_VARIABLES

        missing_months = self._historical_cache.get_missing_months(
            latitude, longitude, step, start_date, end_date
        )

        merged_data: Optional[dict[str, Any]] = None

        today_utc = datetime.now(tz=dt_timezone.utc).date()

        for month_key in missing_months:
            month_date = datetime.strptime(month_key, "%Y-%m")
            month_start = month_date.date()
            if month_start.month == 12:
                month_end = month_start.replace(
                    year=month_start.year + 1, day=1
                ) - timedelta(days=1)
            else:
                month_end = month_start.replace(
                    month=month_start.month + 1, day=1
                ) - timedelta(days=1)

            if month_end > today_utc:
                month_end = today_utc

            logger.debug(f"Fetching historical data for {month_key}")

            params = {
                "latitude": latitude,
                "longitude": longitude,
                "start_date": month_start.isoformat(),
                "end_date": month_end.isoformat(),
                "timezone": timezone,
                step.value: ",".join(variables),
            }

            data = await self._fetch(ARCHIVE_BASE_URL, params)

            self._historical_cache.save_month(
                latitude, longitude, step, month_key, data
            )

            merged_data = self._merge_data(merged_data, data, step)

        cached_months = self._historical_cache.get_cached_months(
            latitude, longitude, step
        )

        current = start_date.replace(day=1)
        end_month = end_date.replace(day=1)

        while current <= end_month:
            month_key = f"{current.year}-{current.month:02d}"
            if month_key not in missing_months and month_key in cached_months:
                cached = self._historical_cache.load_month(
                    latitude, longitude, step, month_key
                )
                if cached:
                    merged_data = self._merge_data(merged_data, cached, step)
            current = (current + timedelta(days=32)).replace(day=1)

        if merged_data is None:
            if step == TimeStep.HOURLY:
                return HourlyResponse(
                    latitude=latitude,
                    longitude=longitude,
                    elevation=0,
                    generationtime_ms=0,
                    utc_offset_seconds=0,
                    timezone=timezone,
                    timezone_abbreviation="",
                    hourly_units=HourlyUnits(),
                    hourly=HourlyData(time=[]),
                )
            else:
                return DailyResponse(
                    latitude=latitude,
                    longitude=longitude,
                    elevation=0,
                    generationtime_ms=0,
                    utc_offset_seconds=0,
                    timezone=timezone,
                    timezone_abbreviation="",
                    daily_units=DailyUnits(),
                    daily=DailyData(time=[]),
                )

        if trim_to_range and merged_data:
            merged_data = self._trim_to_range(merged_data, start_date, end_date, step)

        if step == TimeStep.HOURLY:
            return HourlyResponse(**merged_data)
        else:
            return DailyResponse(**merged_data)

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
        """Get weather forecast for a location.

        Fetches weather forecast with automatic caching. Returns cached
        data if valid, otherwise fetches fresh data from the API.

        Cache is invalidated based on:
        1. TTL: Cache expires after ttl_minutes
        2. Freshness: Cache expires when approaching forecast end

        Args:
            latitude: Latitude in decimal degrees (-90 to 90).
            longitude: Longitude in decimal degrees (-180 to 180).
            days: Number of forecast days (1-16). Defaults to 7.
            step: Time step granularity. Defaults to HOURLY.
            timezone: Timezone for data (e.g., "Europe/Moscow"). Defaults to "auto".
            variables: Custom list of variable names to fetch. Defaults to
                HOURLY_VARIABLES or DAILY_VARIABLES based on step.
            force_refresh: Skip cache and fetch fresh data. Defaults to False.

        Returns:
            HourlyResponse for HOURLY step, DailyResponse for DAILY step.

        Raises:
            OpenMeteoValidationError: If coordinates or days are invalid.
            OpenMeteoConnectionError: If HTTP request fails.
            OpenMeteoAPIError: If API returns an error.

        Example:
            >>> async with OpenMeteoClient() as client:
            ...     # Get hourly forecast
            ...     forecast = await client.get_forecast(
            ...         latitude=55.75,
            ...         longitude=37.62,
            ...         days=7,
            ...         step=TimeStep.HOURLY
            ...     )
            ...     for i, t in enumerate(forecast.hourly.time):
            ...         print(f"{t}: {forecast.hourly.temperature_2m[i]}°C")
            ...
            ...     # Get daily forecast
            ...     daily = await client.get_forecast(
            ...         latitude=55.75,
            ...         longitude=37.62,
            ...         days=10,
            ...         step=TimeStep.DAILY
            ...     )
            ...     for i, day in enumerate(daily.daily.time):
            ...         high = daily.daily.temperature_2m_max[i]
            ...         low = daily.daily.temperature_2m_min[i]
            ...         print(f"{day}: {low}°C - {high}°C")
        """
        self._validate_coordinates(latitude, longitude)
        self._validate_forecast_days(days)

        if variables is None:
            variables = HOURLY_VARIABLES if step == TimeStep.HOURLY else DAILY_VARIABLES

        if not force_refresh and self._forecast_cache.is_valid(
            latitude, longitude, step
        ):
            cached = self._forecast_cache.get(latitude, longitude, step)
            if cached:
                logger.debug(f"Using cached forecast for ({latitude}, {longitude})")
                if step == TimeStep.HOURLY:
                    return HourlyResponse(**cached)
                else:
                    return DailyResponse(**cached)

        logger.debug(f"Fetching fresh forecast for ({latitude}, {longitude})")

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "forecast_days": days,
            "timezone": timezone,
            step.value: ",".join(variables),
        }

        data = await self._fetch(FORECAST_BASE_URL, params)

        if step == TimeStep.HOURLY:
            response = HourlyResponse(**data)
        else:
            response = DailyResponse(**data)

        self._forecast_cache.set(latitude, longitude, step, response)

        return response

    async def get_current(
        self,
        latitude: float,
        longitude: float,
        *,
        timezone: str = "auto",
    ) -> CurrentResponse:
        """Get current weather conditions for a location.

        Fetches the current weather at the specified coordinates.
        This method does not cache results since current weather
        changes frequently.

        Args:
            latitude: Latitude in decimal degrees (-90 to 90).
            longitude: Longitude in decimal degrees (-180 to 180).
            timezone: Timezone for data (e.g., "Europe/Moscow"). Defaults to "auto".

        Returns:
            CurrentResponse with current weather conditions.

        Raises:
            OpenMeteoValidationError: If coordinates are invalid.
            OpenMeteoConnectionError: If HTTP request fails.
            OpenMeteoAPIError: If API returns an error.

        Example:
            >>> async with OpenMeteoClient() as client:
            ...     current = await client.get_current(55.75, 37.62)
            ...     c = current.current
            ...     print(f"Temperature: {c.temperature_2m}°C")
            ...     print(f"Humidity: {c.relative_humidity_2m}%")
            ...     print(f"Wind: {c.wind_speed_10m} km/h")
            ...     print(f"Pressure: {c.pressure_msl} hPa")
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

    def clear_forecast_cache(self) -> None:
        """Clear the in-memory forecast cache.

        Removes all cached forecast data. Useful for forcing fresh
        data fetches.

        Example:
            >>> async with OpenMeteoClient() as client:
            ...     forecast = await client.get_forecast(55.75, 37.62)
            ...     client.clear_forecast_cache()  # Next call fetches fresh
        """
        self._forecast_cache.clear()

    def clear_historical_cache(self) -> None:
        """Clear the file-based historical cache.

        Deletes all cached historical data files. The next historical
        data request will fetch all data from the API.

        Warning:
            This permanently deletes cached data. Use with caution
            if you have accumulated a large cache.

        Example:
            >>> async with OpenMeteoClient() as client:
            ...     client.clear_historical_cache()  # Delete all cached data
        """
        if self._historical_cache.cache_dir.exists():
            import shutil

            shutil.rmtree(self._historical_cache.cache_dir)
            self._historical_cache.cache_dir.mkdir(parents=True, exist_ok=True)

    def clear_all_cache(self) -> None:
        """Clear both forecast and historical caches.

        Removes all cached data, both in-memory and file-based.

        Example:
            >>> async with OpenMeteoClient() as client:
            ...     client.clear_all_cache()  # Clear everything
        """
        self.clear_forecast_cache()
        self.clear_historical_cache()
