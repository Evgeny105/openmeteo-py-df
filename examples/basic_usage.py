"""Basic usage examples for OpenMeteo client."""

import asyncio
from datetime import date

from openmeteo import OpenMeteoClient, TimeStep


async def historical_example() -> None:
    """Get historical weather data."""
    async with OpenMeteoClient() as client:
        data = await client.get_historical(
            latitude=55.75,
            longitude=37.62,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 7),
            step=TimeStep.HOURLY,
            timezone="Europe/Moscow",
        )

        print("=== Historical Data ===")
        print(f"Location: {data.latitude}, {data.longitude}")
        print(f"Timezone: {data.timezone}")
        print(f"Data points: {len(data.hourly.time)}")
        print()

        for i in range(min(5, len(data.hourly.time))):
            time = data.hourly.time[i]
            temp = data.hourly.temperature_2m[i]
            humidity = data.hourly.relative_humidity_2m[i]
            print(f"{time}: {temp}°C, {humidity}% humidity")


async def forecast_example() -> None:
    """Get weather forecast."""
    async with OpenMeteoClient() as client:
        forecast = await client.get_forecast(
            latitude=55.75,
            longitude=37.62,
            days=7,
            step=TimeStep.DAILY,
            timezone="Europe/Moscow",
        )

        print("\n=== 7-Day Forecast ===")
        for i, day in enumerate(forecast.daily.time):
            high = forecast.daily.temperature_2m_max[i]
            low = forecast.daily.temperature_2m_min[i]
            precip = forecast.daily.precipitation_sum[i]
            print(f"{day}: {low}°C - {high}°C, precipitation: {precip}mm")


async def current_example() -> None:
    """Get current weather."""
    async with OpenMeteoClient() as client:
        current = await client.get_current(55.75, 37.62)

        print("\n=== Current Weather ===")
        c = current.current
        print(f"Temperature: {c.temperature_2m}°C")
        print(f"Feels like: {c.apparent_temperature}°C")
        print(f"Humidity: {c.relative_humidity_2m}%")
        print(f"Wind: {c.wind_speed_10m} km/h")
        print(f"Pressure: {c.pressure_msl} hPa")


async def dataframe_example() -> None:
    """Convert to pandas DataFrame."""
    try:
        from openmeteo.dataframe import to_dataframe
    except ImportError:
        print("\n=== DataFrame Example ===")
        print("Install pandas: pip install openmeteo[dataframe]")
        return

    async with OpenMeteoClient() as client:
        data = await client.get_historical(
            latitude=55.75,
            longitude=37.62,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 7),
            step=TimeStep.HOURLY,
        )

        df = to_dataframe(data)

        print("\n=== DataFrame Example ===")
        print(f"Shape: {df.shape}")
        print(f"Columns: {list(df.columns)}")
        print()
        print(df.head())


async def main() -> None:
    """Run all examples."""
    await historical_example()
    await forecast_example()
    await current_example()
    await dataframe_example()


if __name__ == "__main__":
    asyncio.run(main())
