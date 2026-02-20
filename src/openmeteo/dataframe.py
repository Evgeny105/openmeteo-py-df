"""DataFrame conversion utilities for OpenMeteo responses.

This module provides functions to convert OpenMeteo response objects to
pandas DataFrames for easier data analysis and manipulation.

Requirements:
    pandas >= 2.0 must be installed. Install with:
        pip install pandas
    Or install openmeteo with pandas extra:
        pip install openmeteo[pandas]

Functions:
    to_dataframe: Convert any response to a pandas DataFrame

Example:
    Basic usage::

        import asyncio
        from openmeteo import OpenMeteoClient, TimeStep
        from openmeteo.dataframe import to_dataframe

        async def main():
            async with OpenMeteoClient() as client:
                # Get hourly data
                response = await client.get_historical(
                    latitude=55.75,
                    longitude=37.62,
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 31),
                    step=TimeStep.HOURLY,
                )

                # Convert to DataFrame
                df = to_dataframe(response)
                print(df.head())

                # Get daily data
                daily = await client.get_forecast(
                    latitude=55.75,
                    longitude=37.62,
                    step=TimeStep.DAILY,
                )
                df_daily = to_dataframe(daily)
                print(df_daily.head())

        asyncio.run(main())

    With current weather::

        from openmeteo import OpenMeteoClient
        from openmeteo.dataframe import to_dataframe

        async with OpenMeteoClient() as client:
            current = await client.get_current(55.75, 37.62)
            df = to_dataframe(current)
            print(df)
"""

from datetime import date, datetime
from typing import Union

from .models import CurrentResponse, DailyResponse, HourlyResponse


def _check_pandas() -> None:
    """Check if pandas is installed and raise informative error if not."""
    try:
        import pandas  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "pandas is required for DataFrame conversion. "
            "Install it with: pip install pandas"
        ) from e


def to_dataframe(
    response: Union[HourlyResponse, DailyResponse, CurrentResponse],
) -> "pd.DataFrame":
    """Convert an OpenMeteo response to a pandas DataFrame.

    Automatically detects the response type and extracts the appropriate
    data (hourly, daily, or current). Converts time columns to datetime.

    Args:
        response: OpenMeteo response object. Can be:
            - HourlyResponse: from get_historical() or get_forecast() with
              step=TimeStep.HOURLY
            - DailyResponse: from get_historical() or get_forecast() with
              step=TimeStep.DAILY
            - CurrentResponse: from get_current()

    Returns:
        pandas DataFrame with all weather variables as columns.
        The 'time' column is converted to datetime64[ns] dtype.

    Raises:
        ImportError: If pandas is not installed.
        ValueError: If response type is not recognized.

    Example:
        >>> from openmeteo import OpenMeteoClient, TimeStep
        >>> from openmeteo.dataframe import to_dataframe
        >>>
        >>> async with OpenMeteoClient() as client:
        ...     response = await client.get_historical(
        ...         55.75, 37.62,
        ...         date(2024, 1, 1), date(2024, 1, 31),
        ...         step=TimeStep.HOURLY
        ...     )
        ...     df = to_dataframe(response)
        ...     print(df.columns)
        Index(['time', 'temperature_2m', 'relative_humidity_2m', ...], dtype='object')

    Note:
        For HourlyResponse and DailyResponse, the DataFrame contains one row
        per time point. For CurrentResponse, the DataFrame contains a single
        row with current conditions.
    """
    _check_pandas()
    import pandas as pd

    if isinstance(response, HourlyResponse):
        data = response.hourly.model_dump()
        df = pd.DataFrame(data)
        df["time"] = pd.to_datetime(df["time"])
        return df

    if isinstance(response, DailyResponse):
        data = response.daily.model_dump()
        df = pd.DataFrame(data)
        df["time"] = pd.to_datetime(df["time"])
        if "sunrise" in df.columns:
            df["sunrise"] = pd.to_datetime(df["sunrise"])
        if "sunset" in df.columns:
            df["sunset"] = pd.to_datetime(df["sunset"])
        return df

    if isinstance(response, CurrentResponse):
        data = response.current.model_dump()
        df = pd.DataFrame([data])
        df["time"] = pd.to_datetime(df["time"])
        return df

    raise ValueError(
        f"Unsupported response type: {type(response).__name__}. "
        "Expected HourlyResponse, DailyResponse, or CurrentResponse."
    )


__all__ = ["to_dataframe"]
