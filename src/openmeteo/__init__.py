"""OpenMeteo API async client for historical and forecast weather data.

This module provides an async client for fetching weather data from the
OpenMeteo API. Supports historical data (from 1940), forecasts (up to 16
days), and current conditions with intelligent caching.

Key features:
    - Historical weather data from 1940 to present
    - 16-day weather forecast
    - Current weather conditions
    - Same variables for historical and forecast (ideal for ML)
    - Smart caching: JSON files for historical (accumulates), memory for forecast
    - Global coverage, no API key required
    - Optional DataFrame conversion via openmeteo.dataframe module

Caching strategy:
    - **Historical data**: Persisted to JSON files per location per month.
      Only missing months are fetched. Data accumulates indefinitely.
    - **Forecast data**: In-memory cache with TTL and data freshness checks.
      Automatically refreshes when approaching forecast end.

DataFrame conversion:
    For pandas DataFrame output, use the dataframe submodule::

        from openmeteo import OpenMeteoClient, TimeStep
        from openmeteo.dataframe import to_dataframe

        async with OpenMeteoClient() as client:
            response = await client.get_historical(
                latitude=55.75, longitude=37.62,
                start_date=date(2024, 1, 1), end_date=date(2024, 1, 31),
                step=TimeStep.HOURLY
            )
            df = to_dataframe(response)  # Returns pandas DataFrame

Example:
    Fetch historical data::

        import asyncio
        from datetime import date
        from openmeteo import OpenMeteoClient, TimeStep

        async def main():
            async with OpenMeteoClient() as client:
                # Get historical hourly data
                historical = await client.get_historical(
                    latitude=55.75,
                    longitude=37.62,
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 31),
                    step=TimeStep.HOURLY
                )
                for i, time in enumerate(historical.hourly.time):
                    temp = historical.hourly.temperature_2m[i]
                    print(f"{time}: {temp}°C")

        asyncio.run(main())

    Fetch forecast::

        from openmeteo import OpenMeteoClient, TimeStep

        async with OpenMeteoClient() as client:
            forecast = await client.get_forecast(
                latitude=55.75,
                longitude=37.62,
                days=7,
                step=TimeStep.DAILY
            )
            for i, day in enumerate(forecast.daily.time):
                high = forecast.daily.temperature_2m_max[i]
                low = forecast.daily.temperature_2m_min[i]
                print(f"{day}: {low}°C - {high}°C")

    Get current weather::

        async with OpenMeteoClient() as client:
            current = await client.get_current(55.75, 37.62)
            print(f"Temperature: {current.current.temperature_2m}°C")
            print(f"Humidity: {current.current.relative_humidity_2m}%")

See Also:
    - OpenMeteo API docs: https://open-meteo.com/en/docs
    - GisMeteo module for Russian forecasts with water temperature
"""

from .client import OpenMeteoClient
from .exceptions import (
    OpenMeteoAPIError,
    OpenMeteoCacheError,
    OpenMeteoConnectionError,
    OpenMeteoError,
    OpenMeteoValidationError,
)
from .models import (
    CurrentData,
    CurrentResponse,
    DailyData,
    DailyResponse,
    HourlyData,
    HourlyResponse,
)
from .types import (
    ARCHIVE_BASE_URL,
    CACHE_SAFETY_MARGIN_HOURS,
    DEFAULT_FORECAST_DAYS,
    DEFAULT_TTL_MINUTES,
    FORECAST_BASE_URL,
    HISTORY_RECENT_DAYS,
    MAX_FORECAST_DAYS,
    TimeStep,
)

__all__ = [
    "OpenMeteoClient",
    "TimeStep",
    "HourlyResponse",
    "HourlyData",
    "DailyResponse",
    "DailyData",
    "CurrentResponse",
    "CurrentData",
    "OpenMeteoError",
    "OpenMeteoAPIError",
    "OpenMeteoConnectionError",
    "OpenMeteoValidationError",
    "OpenMeteoCacheError",
    "ARCHIVE_BASE_URL",
    "FORECAST_BASE_URL",
    "DEFAULT_FORECAST_DAYS",
    "DEFAULT_TTL_MINUTES",
    "MAX_FORECAST_DAYS",
    "CACHE_SAFETY_MARGIN_HOURS",
    "HISTORY_RECENT_DAYS",
]
