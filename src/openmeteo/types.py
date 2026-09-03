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

from datetime import timedelta
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
"""int: Deprecated since 1.1.0 and no longer used.

Kept for import compatibility. Freshness of cached historical months is
now governed by the ``final`` flag of a cache entry together with
``recent_ttl`` / ``historical_ttl`` (see :class:`openmeteo.OpenMeteoClient`).
"""

ARCHIVE_LAG_DAYS = 7
"""int: Days after a month ends before its archive data is considered final.

The Open-Meteo archive serves the most recent days from preliminary model
runs and replaces them later. A month cached earlier than this many days
after its last day is stored with ``final=False`` and re-fetched once
``recent_ttl`` expires.
"""

DEFAULT_RECENT_TTL = timedelta(hours=6)
"""timedelta: How long a non-final cached month is served before re-fetch."""

DEFAULT_MAX_CONCURRENCY = 4
"""int: Maximum number of concurrent archive requests when filling cache gaps."""

DEFAULT_RETRIES = 3
"""int: Default number of retries for transient API failures."""

DEFAULT_RETRY_BACKOFF = 1.0
"""float: Base delay in seconds for exponential retry backoff."""

MAX_RETRY_DELAY = 30.0
"""float: Upper bound in seconds for a single retry delay."""

CACHE_KEY_PREFIX = "openmeteo:v2"
"""str: Common prefix of all cache keys.

The ``v2`` component is the cache entry format version. It changes together
with the entry format so that entries written by an incompatible version
become invisible instead of being misread.
"""

CACHE_FORMAT_VERSION = 2
"""int: Value of the ``format`` field in cache entries."""

DEFAULT_CACHE_URL_ENV = "OPENMETEO_CACHE_URL"
"""str: Environment variable consulted when no cache backend is configured."""

