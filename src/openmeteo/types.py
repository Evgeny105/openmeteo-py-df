"""Types and constants for the OpenMeteo API client.

This module defines enumerations and configuration constants used
throughout the OpenMeteo client.

Example:
    Using TimeStep enum::

        from openmeteo import OpenMeteoClient, TimeStep

        async with OpenMeteoClient() as client:
            # Get hourly historical data
            historical = await client.get_historical(
                latitude=55.75,
                longitude=37.62,
                start_date=start,
                end_date=end,
                step=TimeStep.HOURLY
            )

            # Get daily forecast
            forecast = await client.get_forecast(
                latitude=55.75,
                longitude=37.62,
                step=TimeStep.DAILY
            )
"""

from enum import Enum


class TimeStep(str, Enum):
    """Time step granularity for weather data.

    Determines the temporal resolution of requested data. OpenMeteo
    provides two main granularities for both historical and forecast data.

    Attributes:
        HOURLY: Hourly data with measurements every hour.
            Best for detailed analysis and ML training.
            Returns HourlyResponse with HourlyData.
        DAILY: Daily aggregates with min/max/sum values.
            Best for overview and long-term trends.
            Returns DailyResponse with DailyData.

    Example:
        >>> from openmeteo.types import TimeStep
        >>> TimeStep.HOURLY.value
        'hourly'
        >>> TimeStep.DAILY.value
        'daily'
    """

    HOURLY = "hourly"
    DAILY = "daily"


ARCHIVE_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
"""str: Base URL for the OpenMeteo Archive API.

Used for fetching historical weather data. The archive contains
data from 1940 to approximately 5 days ago (recent days may use
different data sources).
"""

FORECAST_BASE_URL = "https://api.open-meteo.com/v1/forecast"
"""str: Base URL for the OpenMeteo Forecast API.

Used for fetching weather forecasts (up to 16 days) and current
weather conditions.
"""

DEFAULT_FORECAST_DAYS = 7
"""int: Default number of forecast days to fetch.

Balances between having useful forecast range and API response size.
Can be overridden in get_forecast() calls.
"""

MAX_FORECAST_DAYS = 16
"""int: Maximum number of forecast days supported by the API.

Requests exceeding this will raise OpenMeteoValidationError.
OpenMeteo's free tier supports up to 16 days of forecast.
"""

DEFAULT_TTL_MINUTES = 60
"""int: Default cache time-to-live in minutes for forecast data.

Cached forecasts are considered fresh for this duration.
Default is 60 minutes, balancing data freshness with API call reduction.
"""

CACHE_SAFETY_MARGIN_HOURS = 3
"""int: Safety margin for forecast cache invalidation in hours.

Even if within TTL, forecast cache is invalidated if the current time
is within this many hours of the last forecast timestamp. This ensures
we don't return stale data for periods that should have fresh forecasts.
"""

HISTORY_RECENT_DAYS = 5
"""int: Number of recent days considered "fresh" for historical data.

Historical data for months within this range from today will be
re-fetched even if cached, since recent historical data may be
updated/corrected by OpenMeteo.

For example, with a value of 5, data from the current month and
the previous 5 months will always be re-fetched to get the latest
corrections.
"""
