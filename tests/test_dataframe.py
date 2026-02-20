import pytest
from datetime import date
from unittest.mock import MagicMock

from openmeteo.models import (
    HourlyResponse,
    DailyResponse,
    CurrentResponse,
    HourlyData,
    DailyData,
    CurrentData,
    HourlyUnits,
    DailyUnits,
    CurrentUnits,
)


class TestToDataframeHourly:
    def test_to_dataframe_hourly(self):
        pytest.importorskip("pandas")
        from openmeteo.dataframe import to_dataframe

        response = HourlyResponse(
            latitude=55.75,
            longitude=37.62,
            elevation=130.0,
            generationtime_ms=0.5,
            utc_offset_seconds=10800,
            timezone="Europe/Moscow",
            timezone_abbreviation="MSK",
            hourly_units=HourlyUnits(),
            hourly=HourlyData(
                time=["2024-01-01T00:00", "2024-01-01T01:00"],
                temperature_2m=[-5.0, -4.5],
                relative_humidity_2m=[80, 82],
            ),
        )

        df = to_dataframe(response)

        assert df.shape[0] == 2
        assert "time" in df.columns
        assert "temperature_2m" in df.columns
        assert df["temperature_2m"].tolist() == [-5.0, -4.5]

    def test_to_dataframe_converts_time_to_datetime(self):
        pytest.importorskip("pandas")
        from openmeteo.dataframe import to_dataframe
        import pandas as pd

        response = HourlyResponse(
            latitude=55.75,
            longitude=37.62,
            elevation=130.0,
            generationtime_ms=0.5,
            utc_offset_seconds=10800,
            timezone="Europe/Moscow",
            timezone_abbreviation="MSK",
            hourly_units=HourlyUnits(),
            hourly=HourlyData(
                time=["2024-01-01T00:00", "2024-01-01T01:00"],
            ),
        )

        df = to_dataframe(response)

        assert pd.api.types.is_datetime64_any_dtype(df["time"])


class TestToDataframeDaily:
    def test_to_dataframe_daily(self):
        pytest.importorskip("pandas")
        from openmeteo.dataframe import to_dataframe

        response = DailyResponse(
            latitude=55.75,
            longitude=37.62,
            elevation=130.0,
            generationtime_ms=0.5,
            utc_offset_seconds=10800,
            timezone="Europe/Moscow",
            timezone_abbreviation="MSK",
            daily_units=DailyUnits(),
            daily=DailyData(
                time=["2024-01-01", "2024-01-02"],
                temperature_2m_max=[-2.0, -1.0],
                temperature_2m_min=[-8.0, -7.0],
            ),
        )

        df = to_dataframe(response)

        assert df.shape[0] == 2
        assert "temperature_2m_max" in df.columns
        assert df["temperature_2m_max"].tolist() == [-2.0, -1.0]

    def test_to_dataframe_daily_sunrise_sunset(self):
        pytest.importorskip("pandas")
        from openmeteo.dataframe import to_dataframe
        import pandas as pd

        response = DailyResponse(
            latitude=55.75,
            longitude=37.62,
            elevation=130.0,
            generationtime_ms=0.5,
            utc_offset_seconds=10800,
            timezone="Europe/Moscow",
            timezone_abbreviation="MSK",
            daily_units=DailyUnits(),
            daily=DailyData(
                time=["2024-01-01"],
                sunrise=["2024-01-01T07:30"],
                sunset=["2024-01-01T16:00"],
            ),
        )

        df = to_dataframe(response)

        assert pd.api.types.is_datetime64_any_dtype(df["sunrise"])
        assert pd.api.types.is_datetime64_any_dtype(df["sunset"])


class TestToDataframeCurrent:
    def test_to_dataframe_current(self):
        pytest.importorskip("pandas")
        from openmeteo.dataframe import to_dataframe

        response = CurrentResponse(
            latitude=55.75,
            longitude=37.62,
            elevation=130.0,
            generationtime_ms=0.5,
            utc_offset_seconds=10800,
            timezone="Europe/Moscow",
            timezone_abbreviation="MSK",
            current_units=CurrentUnits(),
            current=CurrentData(
                time="2024-01-01T12:00",
                interval=3600,
                temperature_2m=-5.0,
                relative_humidity_2m=80,
            ),
        )

        df = to_dataframe(response)

        assert df.shape[0] == 1
        assert "temperature_2m" in df.columns
        assert df["temperature_2m"].iloc[0] == -5.0


class TestToDataframeErrors:
    def test_raises_on_unsupported_type(self):
        pytest.importorskip("pandas")
        from openmeteo.dataframe import to_dataframe

        with pytest.raises(ValueError) as exc_info:
            to_dataframe("not a response")
        assert "Unsupported response type" in str(exc_info.value)

    def test_raises_without_pandas(self):
        import sys
        import importlib

        original_pandas = sys.modules.get("pandas")
        sys.modules["pandas"] = None

        try:
            from openmeteo.dataframe import to_dataframe

            response = HourlyResponse(
                latitude=55.75,
                longitude=37.62,
                elevation=130.0,
                generationtime_ms=0.5,
                utc_offset_seconds=10800,
                timezone="Europe/Moscow",
                timezone_abbreviation="MSK",
                hourly_units=HourlyUnits(),
                hourly=HourlyData(time=["2024-01-01T00:00"]),
            )

            with pytest.raises(ImportError) as exc_info:
                to_dataframe(response)
            assert "pandas is required" in str(exc_info.value)
        finally:
            if original_pandas is not None:
                sys.modules["pandas"] = original_pandas
            else:
                del sys.modules["pandas"]
