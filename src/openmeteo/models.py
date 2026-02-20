"""Pydantic models for OpenMeteo API responses.

This module defines data models for parsing and validating responses
from the OpenMeteo API. Models support both historical and forecast
data with consistent variable naming.

Key model groups:
    1. **Units**: Unit information for each data type
    2. **Data containers**: HourlyData, DailyData, CurrentData
    3. **Responses**: HourlyResponse, DailyResponse, CurrentResponse
    4. **Error handling**: ErrorResponse

Note:
    OpenMeteo uses the same variable names for historical and forecast
    data, making it ideal for ML pipelines where training data (historical)
    and inference data (forecast) need consistent schemas.

Example:
    Accessing hourly data::

        response = await client.get_historical(55.75, 37.62, start, end)
        for i, time in enumerate(response.hourly.time):
            temp = response.hourly.temperature_2m[i]
            humidity = response.hourly.relative_humidity_2m[i]
            print(f"{time}: {temp}°C, {humidity}%")

    Accessing daily data::

        response = await client.get_forecast(55.75, 37.62, step=TimeStep.DAILY)
        for i, day in enumerate(response.daily.time):
            high = response.daily.temperature_2m_max[i]
            low = response.daily.temperature_2m_min[i]
            print(f"{day}: {low}°C - {high}°C")
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class MetaInfo(BaseModel):
    """Base metadata shared across response types.

    Provides location and response generation information.

    Attributes:
        latitude: Actual latitude of the nearest weather station/model point.
        longitude: Actual longitude of the nearest weather station/model point.
        elevation: Elevation above sea level in meters.
        generationtime_ms: Time taken to generate the response in milliseconds.
        utc_offset_seconds: Timezone offset from UTC in seconds.
        timezone: Timezone name (e.g., "Europe/Moscow").
        timezone_abbreviation: Short timezone code (e.g., "MSK").
    """

    latitude: float
    longitude: float
    elevation: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str


class HourlyUnits(BaseModel):
    """Unit information for hourly variables.

    All fields default to standard units. Additional units are added
    dynamically based on requested variables.

    Attributes:
        time: Time format, typically "iso8601".
    """

    time: str = "iso8601"


class DailyUnits(BaseModel):
    """Unit information for daily variables.

    All fields default to standard units. Additional units are added
    dynamically based on requested variables.

    Attributes:
        time: Time format, typically "iso8601".
    """

    time: str = "iso8601"


class CurrentUnits(BaseModel):
    """Unit information for current weather variables.

    Attributes:
        time: Time format, typically "iso8601".
        interval: Measurement interval unit, typically "seconds".
    """

    time: str = "iso8601"
    interval: str = "seconds"


class HourlyData(BaseModel):
    """Container for hourly weather measurements.

    Each field is a list where indices correspond to the time list.
    All fields except time are optional and depend on requested variables.

    Attributes:
        time: List of ISO8601 datetime strings (e.g., "2024-01-15T00:00").
        temperature_2m: Temperature at 2m height in °C.
        relative_humidity_2m: Relative humidity at 2m in %.
        dew_point_2m: Dew point temperature at 2m in °C.
        apparent_temperature: "Feels like" temperature in °C.
        precipitation: Total precipitation in mm.
        rain: Rain amount in mm.
        snowfall: Snowfall amount in cm.
        snow_depth: Snow depth in m.
        weather_code: WMO weather code (0-99).
        pressure_msl: Atmospheric pressure at mean sea level in hPa.
        surface_pressure: Surface atmospheric pressure in hPa.
        cloud_cover: Total cloud cover in %.
        cloud_cover_low: Low-level cloud cover in %.
        cloud_cover_mid: Mid-level cloud cover in %.
        cloud_cover_high: High-level cloud cover in %.
        wind_speed_10m: Wind speed at 10m in km/h.
        wind_direction_10m: Wind direction at 10m in degrees.
        wind_gusts_10m: Wind gusts at 10m in km/h.
        shortwave_radiation: Shortwave solar radiation in W/m².
        direct_radiation: Direct solar radiation in W/m².
        diffuse_radiation: Diffuse solar radiation in W/m².
        et0_fao_evapotranspiration: ET0 reference evapotranspiration in mm.
        vapour_pressure_deficit: Vapour pressure deficit in kPa.
        visibility: Visibility in m.
        is_day: Day/night indicator (1=day, 0=night).

    Example:
        >>> for i, t in enumerate(data.time):
        ...     temp = data.temperature_2m[i]
        ...     humidity = data.relative_humidity_2m[i]
        ...     print(f"{t}: {temp}°C, {humidity}%")
    """

    time: list[str]
    temperature_2m: Optional[list[Optional[float]]] = None
    relative_humidity_2m: Optional[list[Optional[int]]] = None
    dew_point_2m: Optional[list[Optional[float]]] = None
    apparent_temperature: Optional[list[Optional[float]]] = None
    precipitation: Optional[list[Optional[float]]] = None
    rain: Optional[list[Optional[float]]] = None
    snowfall: Optional[list[Optional[float]]] = None
    snow_depth: Optional[list[Optional[float]]] = None
    weather_code: Optional[list[Optional[int]]] = None
    pressure_msl: Optional[list[Optional[float]]] = None
    surface_pressure: Optional[list[Optional[float]]] = None
    cloud_cover: Optional[list[Optional[int]]] = None
    cloud_cover_low: Optional[list[Optional[int]]] = None
    cloud_cover_mid: Optional[list[Optional[int]]] = None
    cloud_cover_high: Optional[list[Optional[int]]] = None
    wind_speed_10m: Optional[list[Optional[float]]] = None
    wind_direction_10m: Optional[list[Optional[int]]] = None
    wind_gusts_10m: Optional[list[Optional[float]]] = None
    shortwave_radiation: Optional[list[Optional[float]]] = None
    direct_radiation: Optional[list[Optional[float]]] = None
    diffuse_radiation: Optional[list[Optional[float]]] = None
    et0_fao_evapotranspiration: Optional[list[Optional[float]]] = None
    vapour_pressure_deficit: Optional[list[Optional[float]]] = None
    visibility: Optional[list[Optional[float]]] = None
    is_day: Optional[list[Optional[int]]] = None


class DailyData(BaseModel):
    """Container for daily weather aggregates.

    Each field is a list where indices correspond to the time list.
    Daily values are aggregates (max, min, sum, mean) computed from
    hourly data by OpenMeteo.

    Attributes:
        time: List of ISO8601 date strings (e.g., "2024-01-15").
        temperature_2m_max: Maximum daily temperature at 2m in °C.
        temperature_2m_min: Minimum daily temperature at 2m in °C.
        temperature_2m_mean: Mean daily temperature at 2m in °C.
        apparent_temperature_max: Maximum "feels like" temperature in °C.
        apparent_temperature_min: Minimum "feels like" temperature in °C.
        apparent_temperature_mean: Mean "feels like" temperature in °C.
        precipitation_sum: Total daily precipitation in mm.
        rain_sum: Total daily rain in mm.
        snowfall_sum: Total daily snowfall in cm.
        precipitation_hours: Hours with precipitation.
        weather_code: Dominant WMO weather code (0-99).
        sunrise: Sunrise time as ISO8601 string.
        sunset: Sunset time as ISO8601 string.
        daylight_duration: Daylight duration in seconds.
        sunshine_duration: Sunshine duration in seconds.
        wind_speed_10m_max: Maximum wind speed at 10m in km/h.
        wind_gusts_10m_max: Maximum wind gusts at 10m in km/h.
        wind_direction_10m_dominant: Dominant wind direction in degrees.
        shortwave_radiation_sum: Total daily shortwave radiation in MJ/m².
        et0_fao_evapotranspiration: Daily ET0 evapotranspiration in mm.
        uv_index_max: Maximum UV index.

    Example:
        >>> for i, day in enumerate(data.time):
        ...     high = data.temperature_2m_max[i]
        ...     low = data.temperature_2m_min[i]
        ...     rain = data.precipitation_sum[i]
        ...     print(f"{day}: {low}°C - {high}°C, rain: {rain}mm")
    """

    time: list[str]
    temperature_2m_max: Optional[list[Optional[float]]] = None
    temperature_2m_min: Optional[list[Optional[float]]] = None
    temperature_2m_mean: Optional[list[Optional[float]]] = None
    apparent_temperature_max: Optional[list[Optional[float]]] = None
    apparent_temperature_min: Optional[list[Optional[float]]] = None
    apparent_temperature_mean: Optional[list[Optional[float]]] = None
    precipitation_sum: Optional[list[Optional[float]]] = None
    rain_sum: Optional[list[Optional[float]]] = None
    snowfall_sum: Optional[list[Optional[float]]] = None
    precipitation_hours: Optional[list[Optional[float]]] = None
    weather_code: Optional[list[Optional[int]]] = None
    sunrise: Optional[list[str]] = None
    sunset: Optional[list[str]] = None
    daylight_duration: Optional[list[Optional[float]]] = None
    sunshine_duration: Optional[list[Optional[float]]] = None
    wind_speed_10m_max: Optional[list[Optional[float]]] = None
    wind_gusts_10m_max: Optional[list[Optional[float]]] = None
    wind_direction_10m_dominant: Optional[list[Optional[int]]] = None
    shortwave_radiation_sum: Optional[list[Optional[float]]] = None
    et0_fao_evapotranspiration: Optional[list[Optional[float]]] = None
    uv_index_max: Optional[list[Optional[float]]] = None


class CurrentData(BaseModel):
    """Container for current weather conditions.

    Represents weather at a single point in time (now).

    Attributes:
        time: ISO8601 datetime string of the measurement.
        interval: Measurement interval in seconds.
        temperature_2m: Current temperature at 2m in °C.
        relative_humidity_2m: Current relative humidity at 2m in %.
        dew_point_2m: Current dew point at 2m in °C.
        apparent_temperature: Current "feels like" temperature in °C.
        precipitation: Current precipitation in mm.
        rain: Current rain in mm.
        snowfall: Current snowfall in cm.
        weather_code: Current WMO weather code (0-99).
        pressure_msl: Current pressure at mean sea level in hPa.
        surface_pressure: Current surface pressure in hPa.
        cloud_cover: Current cloud cover in %.
        wind_speed_10m: Current wind speed at 10m in km/h.
        wind_direction_10m: Current wind direction at 10m in degrees.
        wind_gusts_10m: Current wind gusts at 10m in km/h.

    Example:
        >>> current = response.current
        >>> print(f"Temperature: {current.temperature_2m}°C")
        >>> print(f"Humidity: {current.relative_humidity_2m}%")
        >>> print(f"Wind: {current.wind_speed_10m} km/h")
    """

    time: str
    interval: int
    temperature_2m: Optional[float] = None
    relative_humidity_2m: Optional[int] = None
    dew_point_2m: Optional[float] = None
    apparent_temperature: Optional[float] = None
    precipitation: Optional[float] = None
    rain: Optional[float] = None
    snowfall: Optional[float] = None
    weather_code: Optional[int] = None
    pressure_msl: Optional[float] = None
    surface_pressure: Optional[float] = None
    cloud_cover: Optional[int] = None
    wind_speed_10m: Optional[float] = None
    wind_direction_10m: Optional[int] = None
    wind_gusts_10m: Optional[float] = None


class HourlyResponse(BaseModel):
    """Response container for hourly weather data.

    Contains metadata, units, and hourly measurements for a location.
    Works for both historical and forecast data.

    Attributes:
        latitude: Actual latitude of the data point.
        longitude: Actual longitude of the data point.
        elevation: Elevation in meters.
        generationtime_ms: Response generation time in milliseconds.
        utc_offset_seconds: Timezone offset in seconds.
        timezone: Timezone name (e.g., "Europe/Moscow").
        timezone_abbreviation: Timezone abbreviation (e.g., "MSK").
        hourly_units: Unit information for hourly variables.
        hourly: Hourly weather data.

    Example:
        >>> response = await client.get_historical(
        ...     55.75, 37.62, start, end, step=TimeStep.HOURLY
        ... )
        >>> print(f"Location: {response.latitude}, {response.longitude}")
        >>> for i, t in enumerate(response.hourly.time):
        ...     print(f"{t}: {response.hourly.temperature_2m[i]}°C")
    """

    model_config = ConfigDict(extra="allow")

    latitude: float
    longitude: float
    elevation: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    hourly_units: HourlyUnits
    hourly: HourlyData


class DailyResponse(BaseModel):
    """Response container for daily weather data.

    Contains metadata, units, and daily aggregates for a location.
    Works for both historical and forecast data.

    Attributes:
        latitude: Actual latitude of the data point.
        longitude: Actual longitude of the data point.
        elevation: Elevation in meters.
        generationtime_ms: Response generation time in milliseconds.
        utc_offset_seconds: Timezone offset in seconds.
        timezone: Timezone name (e.g., "Europe/Moscow").
        timezone_abbreviation: Timezone abbreviation (e.g., "MSK").
        daily_units: Unit information for daily variables.
        daily: Daily weather data.

    Example:
        >>> response = await client.get_forecast(
        ...     55.75, 37.62, days=7, step=TimeStep.DAILY
        ... )
        >>> for i, day in enumerate(response.daily.time):
        ...     high = response.daily.temperature_2m_max[i]
        ...     low = response.daily.temperature_2m_min[i]
        ...     print(f"{day}: {low}°C - {high}°C")
    """

    model_config = ConfigDict(extra="allow")

    latitude: float
    longitude: float
    elevation: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    daily_units: DailyUnits
    daily: DailyData


class CurrentResponse(BaseModel):
    """Response container for current weather data.

    Contains metadata, units, and current conditions for a location.

    Attributes:
        latitude: Actual latitude of the data point.
        longitude: Actual longitude of the data point.
        elevation: Elevation in meters.
        generationtime_ms: Response generation time in milliseconds.
        utc_offset_seconds: Timezone offset in seconds.
        timezone: Timezone name (e.g., "Europe/Moscow").
        timezone_abbreviation: Timezone abbreviation (e.g., "MSK").
        current_units: Unit information for current variables.
        current: Current weather data.

    Example:
        >>> response = await client.get_current(55.75, 37.62)
        >>> c = response.current
        >>> print(f"Temperature: {c.temperature_2m}°C")
        >>> print(f"Humidity: {c.relative_humidity_2m}%")
        >>> print(f"Wind: {c.wind_speed_10m} km/h")
    """

    model_config = ConfigDict(extra="allow")

    latitude: float
    longitude: float
    elevation: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    current_units: CurrentUnits
    current: CurrentData


class ErrorResponse(BaseModel):
    """Error response from the OpenMeteo API.

    Returned when the API encounters an error processing the request.

    Attributes:
        error: Always True for error responses.
        reason: Human-readable error description.

    Example:
        >>> # This is what an error response looks like
        >>> {"error": True, "reason": "Cannot resolve historical data"}
    """

    error: bool
    reason: str
