# OpenMeteo Python Client

[![CI](https://github.com/Evgeny105/openmeteo-py-df/actions/workflows/ci.yml/badge.svg)](https://github.com/Evgeny105/openmeteo-py-df/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/openmeteo.svg)](https://pypi.org/project/openmeteo/)
[![Python versions](https://img.shields.io/pypi/pyversions/openmeteo.svg)](https://pypi.org/project/openmeteo/)
[![Coverage](https://img.shields.io/badge/coverage-93%25-brightgreen)](https://github.com/Evgeny105/openmeteo-py-df)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Async Python client for OpenMeteo API with historical data caching and DataFrame support.

## Features

- **Historical weather data** from 1940 to present
- **16-day weather forecast**
- **Current weather conditions**
- **Same variables for historical and forecast** (ideal for ML)
- **Smart caching**:
  - Historical: JSON files per location per month, accumulates indefinitely
  - Forecast: in-memory with TTL and data freshness validation
- **DataFrame conversion** (optional, via pandas)
- **Global coverage**, no API key required
- **Full type hints** with Pydantic models

## Installation

```bash
pip install openmeteo

# With DataFrame support
pip install "openmeteo[dataframe]"
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
    
    df = to_dataframe(response)
    print(df.head())
    print(df.describe())
```

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

- Cached in JSON files per location per month
- Only missing months are fetched
- Data accumulates indefinitely
- Cache directory: `~/.cache/openmeteo/historical/`

### Forecast Data

- In-memory cache with TTL (default 60 minutes)
- Invalidated when approaching forecast end
- Ensures data freshness

### Cache Management

```python
client = OpenMeteoClient()

# Clear forecast cache
client.clear_forecast_cache()

# Clear historical cache
client.clear_historical_cache()

# Clear all
client.clear_all_cache()
```

## Error Handling

```python
from openmeteo import (
    OpenMeteoError,
    OpenMeteoAPIError,
    OpenMeteoConnectionError,
    OpenMeteoValidationError,
)

try:
    data = await client.get_historical(91.0, 0.0, start, end)
except OpenMeteoValidationError as e:
    print(f"Invalid parameters: {e}")
except OpenMeteoAPIError as e:
    print(f"API error: {e.reason}")
except OpenMeteoConnectionError as e:
    print(f"Connection error: {e}")
```

## Development

### Setup

```bash
git clone https://github.com/Evgeny105/openmeteo-py-df.git
cd openmeteo-py-df
pip install -e ".[dev,dataframe]"
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

This project requires **minimum 90% test coverage**. Current coverage: **96%**.

## Requirements

- Python >= 3.10
- httpx >= 0.24
- pydantic >= 2.0

**Optional:**
- pandas >= 2.0 (for DataFrame conversion)

## License

MIT License - see [LICENSE](LICENSE)

## Links

- [OpenMeteo API Documentation](https://open-meteo.com/en/docs)
- [GitHub Repository](https://github.com/Evgeny105/openmeteo-py-df)
- [PyPI Package](https://pypi.org/project/openmeteo/)
