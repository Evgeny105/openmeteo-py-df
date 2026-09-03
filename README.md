# OpenMeteo Python Client

[![CI](https://github.com/Evgeny105/openmeteo-py-df/actions/workflows/ci.yml/badge.svg)](https://github.com/Evgeny105/openmeteo-py-df/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/openmeteo-py-df.svg)](https://pypi.org/project/openmeteo-py-df/)
[![Python versions](https://img.shields.io/pypi/pyversions/openmeteo-py-df.svg)](https://pypi.org/project/openmeteo-py-df/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Async Python client for OpenMeteo API with historical data caching and DataFrame support.

## Features

- **Historical weather data** from 1940 to present
- **16-day weather forecast**
- **Current weather conditions**
- **Same variables for historical and forecast** (ideal for ML)
- **Smart caching** with pluggable backends (files, memory, Redis):
  - Historical: one entry per location per month with coverage metadata;
    only missing, incomplete or stale months are fetched
  - Forecast: TTL plus freshness validation near the forecast horizon
- **Kubernetes-friendly**: works on a read-only filesystem, shares the cache
  between replicas through Redis, configured by one environment variable
- **Retries** with exponential backoff for transient API failures
- **DataFrame conversion** (optional, via pandas)
- **Global coverage**, no API key required
- **Full type hints** with Pydantic models

## Installation

```bash
pip install openmeteo-py-df

# With DataFrame support
pip install "openmeteo-py-df[dataframe]"

# With Redis cache backend
pip install "openmeteo-py-df[redis]"
```

## Quick Start

### Historical Data

```python
import asyncio
from datetime import date
from openmeteo import OpenMeteoClient, TimeStep

async def main():
    async with OpenMeteoClient() as client:
        # Get hourly historical data
        data = await client.get_historical(
            latitude=55.75,
            longitude=37.62,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            step=TimeStep.HOURLY,
            timezone="Europe/Moscow",
        )
        
        for i, time in enumerate(data.hourly.time):
            temp = data.hourly.temperature_2m[i]
            print(f"{time}: {temp}°C")

asyncio.run(main())
```

### Forecast

```python
async with OpenMeteoClient() as client:
    forecast = await client.get_forecast(
        latitude=55.75,
        longitude=37.62,
        days=7,
        step=TimeStep.DAILY,
    )
    
    for i, day in enumerate(forecast.daily.time):
        high = forecast.daily.temperature_2m_max[i]
        low = forecast.daily.temperature_2m_min[i]
        print(f"{day}: {low}°C - {high}°C")
```

### Current Weather

```python
async with OpenMeteoClient() as client:
    current = await client.get_current(55.75, 37.62)
    print(f"Temperature: {current.current.temperature_2m}°C")
    print(f"Humidity: {current.current.relative_humidity_2m}%")
    print(f"Wind: {current.current.wind_speed_10m} km/h")
```

### DataFrame Conversion

```python
from openmeteo import OpenMeteoClient, TimeStep
from openmeteo.dataframe import to_dataframe

async with OpenMeteoClient() as client:
    response = await client.get_historical(
        latitude=55.75,
        longitude=37.62,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        step=TimeStep.HOURLY,
    )
    
    df = to_dataframe(response)   # only requested variables become columns
    print(df.head())
    print(df.describe())
```

Prefer `to_dataframe` over zipping the model fields by hand: a variable that
was not requested is `None` in the model, while a requested one is always a
list of `len(time)` values.

## Available Variables

### Hourly (26 variables)

| Variable | Description | Unit |
|----------|-------------|------|
| `temperature_2m` | Air temperature | °C |
| `relative_humidity_2m` | Relative humidity | % |
| `dew_point_2m` | Dew point | °C |
| `apparent_temperature` | Feels like temperature | °C |
| `precipitation` | Total precipitation | mm |
| `rain` | Rain amount | mm |
| `snowfall` | Snowfall | cm |
| `snow_depth` | Snow depth | m |
| `weather_code` | WMO weather code | code |
| `pressure_msl` | Pressure (sea level) | hPa |
| `surface_pressure` | Surface pressure | hPa |
| `cloud_cover` | Total cloud cover | % |
| `cloud_cover_low/mid/high` | Cloud layers | % |
| `wind_speed_10m` | Wind speed | km/h |
| `wind_direction_10m` | Wind direction | ° |
| `wind_gusts_10m` | Wind gusts | km/h |
| `shortwave_radiation` | Shortwave radiation | W/m² |
| `direct_radiation` | Direct solar radiation | W/m² |
| `diffuse_radiation` | Diffuse radiation | W/m² |
| `et0_fao_evapotranspiration` | ET0 evapotranspiration | mm |
| `vapour_pressure_deficit` | VPD | kPa |
| `visibility` | Visibility* | m |
| `is_day` | Day/night | 0/1 |

*Note: `visibility` only available in Forecast API, not Archive API.

### Daily (21 variables)

| Variable | Description |
|----------|-------------|
| `temperature_2m_max/min/mean` | Daily temperature |
| `apparent_temperature_max/min/mean` | Feels like temperature |
| `precipitation_sum` | Total precipitation |
| `rain_sum`, `snowfall_sum` | Rain and snow totals |
| `weather_code` | WMO weather code |
| `sunrise`, `sunset` | Sun times |
| `daylight_duration`, `sunshine_duration` | Duration in seconds |
| `wind_speed_10m_max` | Max wind speed |
| `wind_gusts_10m_max` | Max gusts |
| `wind_direction_10m_dominant` | Dominant direction |
| `shortwave_radiation_sum` | Solar radiation |
| `et0_fao_evapotranspiration` | Evapotranspiration |
| `uv_index_max` | Maximum UV index |

## Caching

### Historical Data

- One cache entry per location, time step, timezone and calendar month.
- Only missing months are fetched. Months are fetched concurrently
  (`max_concurrency`, default 4).
- Each entry records the covered period and whether the month is **final**:
  covered to its last day and saved at least `ARCHIVE_LAG_DAYS` (7) after it
  ended, because the archive serves the most recent days from preliminary
  model runs.
- A non-final month (the current one, or one saved right after it ended) is
  re-fetched once it is older than `recent_ttl` (default 6 hours). A final
  month is kept forever unless `historical_ttl` is set.
- A month is also re-fetched when it lacks a requested variable or when the
  entry is corrupt. Every requested variable in the response is a list of
  exactly `len(time)` values.

### Forecast Data

- Cached for `ttl_minutes` (default 60).
- Dropped when the forecast horizon is closer than 3 hours.
- The cache key includes coordinates, step, number of days and timezone.

### Backends

| URL | Backend | Notes |
|-----|---------|-------|
| `file:///path` | `FileBackend` | Default: `$XDG_CACHE_HOME/openmeteo` or `~/.cache/openmeteo`. Atomic writes, I/O off the event loop. |
| `memory://` | `MemoryBackend` | Process-local, lost on restart. |
| `redis://host:6379/0` | `RedisBackend` | Requires `openmeteo-py-df[redis]`. |
| `rediss://host:6379/0?cluster=true&ca_bundle=/etc/ca.pem` | `RedisBackend` | TLS, Redis Cluster, custom CA. |

The backend is chosen from, in order: the `cache` argument, the `cache_url`
argument, the `OPENMETEO_CACHE_URL` environment variable, the (deprecated)
`cache_dir` argument, the default directory.

```python
from openmeteo import OpenMeteoClient
from openmeteo.cache import RedisBackend

# Explicit backend (you own it and close it)
backend = RedisBackend("redis://cache:6379/0", cluster=True)
async with OpenMeteoClient(cache=backend) as client:
    ...
await backend.close()

# URL (the client opens and closes the backend)
async with OpenMeteoClient(cache_url="redis://cache:6379/0") as client:
    ...
```

### Cache Management

```python
async with OpenMeteoClient() as client:
    removed = await client.clear_forecast_cache()
    removed = await client.clear_historical_cache()
    removed = await client.clear_all_cache()
    await client.cache_health()   # raises if the backend is unreachable
```

## Deployment on Kubernetes

Pods usually run with a read-only root filesystem and several replicas.
Without configuration the client tries the default cache directory, and if
it is not writable falls back to an in-memory cache and logs one warning:
requests keep working, but every pod fetches its own data and loses it on
restart.

To share the cache between replicas, point the client at Redis through the
environment, no code change required:

```yaml
env:
  - name: OPENMETEO_CACHE_URL
    value: "rediss://redis.cache.svc:6379/0?cluster=true"
```

and add the `redis` extra to your dependencies. Behaviour to know about:

- Backend failures are logged at ERROR level and treated as cache misses,
  so a Redis outage degrades to direct API calls. Pass `cache_strict=True`
  to fail instead.
- An explicitly configured backend that cannot be opened (missing `redis`
  package, unwritable `file://` path) raises `OpenMeteoCacheError` on first
  use; only the built-in default falls back to memory.
- Use `await client.cache_health()` in a dependency health endpoint.
- Transient API failures (network errors, HTTP 429, 5xx) are retried
  (`retries`, `retry_backoff`).

## Error Handling

```python
from openmeteo import (
    OpenMeteoError,
    OpenMeteoAPIError,
    OpenMeteoCacheError,
    OpenMeteoConnectionError,
    OpenMeteoDataError,
    OpenMeteoValidationError,
)

try:
    data = await client.get_historical(91.0, 0.0, start, end)
except OpenMeteoValidationError as e:
    print(f"Invalid parameters: {e}")
except OpenMeteoAPIError as e:
    print(f"API error: {e.reason}")
except OpenMeteoConnectionError as e:
    print(f"Connection error after retries: {e}")
except OpenMeteoDataError as e:
    print(f"Inconsistent data, not returned: {e}")
except OpenMeteoCacheError as e:
    print(f"Cache backend problem: {e}")
```

## Development

### Setup

```bash
git clone https://github.com/Evgeny105/openmeteo-py-df.git
cd openmeteo-py-df
pip install -e ".[dev,dataframe,redis]"
```

### Run Tests

```bash
# Run tests
pytest tests/

# Run with coverage
pytest tests/ --cov=openmeteo --cov-report=term-missing

# HTML coverage report
pytest tests/ --cov=openmeteo --cov-report=html
```

### Minimum Coverage

This project requires **minimum 90% test coverage**.

## Requirements

- Python >= 3.10
- httpx >= 0.24
- pydantic >= 2.0

**Optional:**
- pandas >= 2.0 (for DataFrame conversion)
- redis >= 5.0 (for the Redis cache backend)

## License

MIT License - see [LICENSE](LICENSE)

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Links

- [OpenMeteo API Documentation](https://open-meteo.com/en/docs)
- [GitHub Repository](https://github.com/Evgeny105/openmeteo-py-df)
- [PyPI Package](https://pypi.org/project/openmeteo-py-df/)
