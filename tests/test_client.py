import pytest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import tempfile
import os

from openmeteo import (
    OpenMeteoClient,
    TimeStep,
    OpenMeteoAPIError,
    OpenMeteoConnectionError,
    OpenMeteoValidationError,
)
from openmeteo.models import (
    HourlyResponse,
    DailyResponse,
    CurrentResponse,
    HourlyData,
    DailyData,
    HourlyUnits,
    DailyUnits,
)
from openmeteo.cache import (
    HistoricalCache,
    ForecastCache,
    _coord_key,
    _month_key,
    _parse_date,
)


class TestValidation:
    def test_valid_coordinates(self):
        client = OpenMeteoClient()
        client._validate_coordinates(0.0, 0.0)
        client._validate_coordinates(55.782298, 37.327136)
        client._validate_coordinates(-90.0, -180.0)
        client._validate_coordinates(90.0, 180.0)
        client._validate_coordinates(45.5, -122.5)

    def test_invalid_latitude_too_high(self):
        client = OpenMeteoClient()
        with pytest.raises(OpenMeteoValidationError) as exc_info:
            client._validate_coordinates(91.0, 0.0)
        assert "Latitude must be in range [-90.0, 90.0]" in str(exc_info.value)

    def test_invalid_latitude_too_low(self):
        client = OpenMeteoClient()
        with pytest.raises(OpenMeteoValidationError) as exc_info:
            client._validate_coordinates(-91.0, 0.0)
        assert "Latitude must be in range [-90.0, 90.0]" in str(exc_info.value)

    def test_invalid_longitude_out_of_range(self):
        client = OpenMeteoClient()
        with pytest.raises(OpenMeteoValidationError) as exc_info:
            client._validate_coordinates(0.0, 181.0)
        assert "Longitude must be in range [-180.0, 180.0]" in str(exc_info.value)

    def test_valid_date_range(self):
        client = OpenMeteoClient()
        today = date.today()
        client._validate_date_range(today - timedelta(days=10), today)

    def test_invalid_date_range_start_after_end(self):
        client = OpenMeteoClient()
        today = date.today()
        with pytest.raises(OpenMeteoValidationError) as exc_info:
            client._validate_date_range(today, today - timedelta(days=1))
        assert "must be <=" in str(exc_info.value)

    def test_invalid_date_range_future(self):
        client = OpenMeteoClient()
        with pytest.raises(OpenMeteoValidationError) as exc_info:
            client._validate_date_range(date.today(), date.today() + timedelta(days=10))
        assert "cannot be in the future" in str(exc_info.value)

    def test_date_range_with_allow_future(self):
        client = OpenMeteoClient()
        today = date.today()
        client._validate_date_range(
            today, today + timedelta(days=10), allow_future=True
        )

    def test_valid_forecast_days(self):
        client = OpenMeteoClient()
        for days in [1, 5, 16]:
            client._validate_forecast_days(days)

    def test_invalid_forecast_days_over_max(self):
        client = OpenMeteoClient()
        with pytest.raises(OpenMeteoValidationError) as exc_info:
            client._validate_forecast_days(17)
        assert "days must be in range [1, 16]" in str(exc_info.value)

    def test_invalid_forecast_days_zero(self):
        client = OpenMeteoClient()
        with pytest.raises(OpenMeteoValidationError):
            client._validate_forecast_days(0)


class TestTrimToRange:
    def test_trim_daily_data(self):
        client = OpenMeteoClient()
        data = {
            "daily": {
                "time": [
                    "2024-01-01",
                    "2024-01-02",
                    "2024-01-03",
                    "2024-01-04",
                    "2024-01-05",
                ],
                "temperature_2m_max": [1.0, 2.0, 3.0, 4.0, 5.0],
                "temperature_2m_min": [0.0, 1.0, 2.0, 3.0, 4.0],
            }
        }

        trimmed = client._trim_to_range(
            data, date(2024, 1, 2), date(2024, 1, 4), TimeStep.DAILY
        )

        assert len(trimmed["daily"]["time"]) == 3
        assert trimmed["daily"]["time"] == ["2024-01-02", "2024-01-03", "2024-01-04"]
        assert trimmed["daily"]["temperature_2m_max"] == [2.0, 3.0, 4.0]

    def test_trim_hourly_data(self):
        client = OpenMeteoClient()
        data = {
            "hourly": {
                "time": [
                    "2024-01-01T00:00",
                    "2024-01-01T01:00",
                    "2024-01-01T02:00",
                    "2024-01-01T03:00",
                ],
                "temperature_2m": [1.0, 2.0, 3.0, 4.0],
            }
        }

        trimmed = client._trim_to_range(
            data, date(2024, 1, 1), date(2024, 1, 1), TimeStep.HOURLY
        )

        assert len(trimmed["hourly"]["time"]) == 4

    def test_trim_empty_data(self):
        client = OpenMeteoClient()
        data = {"daily": {"time": []}}
        trimmed = client._trim_to_range(
            data, date(2024, 1, 1), date(2024, 1, 5), TimeStep.DAILY
        )
        assert trimmed == data

    def test_trim_no_matching_data(self):
        client = OpenMeteoClient()
        data = {
            "hourly": {
                "time": ["2024-01-01T00:00"],
                "temperature_2m": [1.0],
            }
        }
        trimmed = client._trim_to_range(
            data, date(2024, 1, 5), date(2024, 1, 10), TimeStep.HOURLY
        )
        assert len(trimmed["hourly"]["time"]) == 1


class TestMergeData:
    def test_merge_empty_existing(self):
        client = OpenMeteoClient()
        new = {"daily": {"time": ["2024-01-01"], "temperature_2m_max": [5.0]}}

        merged = client._merge_data(None, new, TimeStep.DAILY)

        assert merged == new

    def test_merge_non_overlapping_data(self):
        client = OpenMeteoClient()
        existing = {"daily": {"time": ["2024-01-01"], "temperature_2m_max": [5.0]}}
        new = {"daily": {"time": ["2024-01-02"], "temperature_2m_max": [6.0]}}

        merged = client._merge_data(existing, new, TimeStep.DAILY)

        assert len(merged["daily"]["time"]) == 2
        assert merged["daily"]["time"] == ["2024-01-01", "2024-01-02"]
        assert merged["daily"]["temperature_2m_max"] == [5.0, 6.0]

    def test_merge_adds_new_variable(self):
        client = OpenMeteoClient()
        existing = {"hourly": {"time": ["2024-01-01T00:00"], "temperature_2m": [5.0]}}
        new = {
            "hourly": {
                "time": ["2024-01-01T01:00"],
                "temperature_2m": [6.0],
                "humidity": [80.0],
            }
        }

        merged = client._merge_data(existing, new, TimeStep.HOURLY)

        assert len(merged["hourly"]["time"]) == 2
        assert merged["hourly"]["humidity"] == [80.0]


class TestCacheKey:
    def test_coord_key_format(self):
        key = _coord_key(55.782298, 37.327136)
        assert "55" in key
        assert "37" in key

    def test_coord_key_negative(self):
        key = _coord_key(-33.865, 151.21)
        assert "m33" in key

    def test_month_key_format(self):
        key = _month_key(date(2024, 1, 15))
        assert key == "2024-01"

    def test_parse_date_date_only(self):
        result = _parse_date("2024-01-15")
        assert result == date(2024, 1, 15)

    def test_parse_date_datetime(self):
        result = _parse_date("2024-01-15T12:00")
        assert result == date(2024, 1, 15)


class TestHistoricalCache:
    def test_init_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            cache = HistoricalCache(cache_dir)
            assert cache_dir.exists()

    def test_save_and_load_month(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HistoricalCache(Path(tmpdir))
            data = {"hourly": {"time": ["2024-01-01T00:00"], "temperature_2m": [5.0]}}

            cache.save_month(55.75, 37.62, TimeStep.HOURLY, "2024-01", data)
            loaded = cache.load_month(55.75, 37.62, TimeStep.HOURLY, "2024-01")

            assert loaded == data

    def test_load_nonexistent_month(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HistoricalCache(Path(tmpdir))
            result = cache.load_month(55.75, 37.62, TimeStep.HOURLY, "2024-01")
            assert result is None

    def test_get_cached_months(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HistoricalCache(Path(tmpdir))
            cache.save_month(55.75, 37.62, TimeStep.HOURLY, "2024-01", {"test": 1})
            cache.save_month(55.75, 37.62, TimeStep.HOURLY, "2024-02", {"test": 2})

            months = cache.get_cached_months(55.75, 37.62, TimeStep.HOURLY)

            assert "2024-01" in months
            assert "2024-02" in months

    def test_is_month_recent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HistoricalCache(Path(tmpdir))

            today = date.today()
            current_month = _month_key(today)
            old_month = f"{today.year - 2}-01"

            assert cache.is_month_recent(current_month) is True
            assert cache.is_month_recent(old_month) is False

    def test_get_missing_months(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HistoricalCache(Path(tmpdir))

            start = date(2024, 1, 1)
            end = date(2024, 3, 31)
            missing = cache.get_missing_months(
                55.75, 37.62, TimeStep.HOURLY, start, end
            )

            assert "2024-01" in missing
            assert "2024-02" in missing
            assert "2024-03" in missing

    def test_get_missing_months_with_cached(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HistoricalCache(Path(tmpdir))
            cache.save_month(55.75, 37.62, TimeStep.HOURLY, "2024-01", {"test": 1})

            start = date(2024, 1, 1)
            end = date(2024, 2, 29)
            missing = cache.get_missing_months(
                55.75, 37.62, TimeStep.HOURLY, start, end
            )

            assert "2024-01" not in missing
            assert "2024-02" in missing


class TestForecastCache:
    def test_set_and_get(self):
        cache = ForecastCache()

        response = MagicMock()
        response.hourly.time = ["2024-01-01T00:00", "2024-01-01T01:00"]
        response.model_dump.return_value = {"hourly": {"time": ["2024-01-01T00:00"]}}

        cache.set(55.75, 37.62, TimeStep.HOURLY, response)
        result = cache.get(55.75, 37.62, TimeStep.HOURLY)

        assert result is not None
        assert "hourly" in result

    def test_is_valid_nonexistent(self):
        cache = ForecastCache()
        assert cache.is_valid(55.75, 37.62, TimeStep.HOURLY) is False

    def test_is_valid_returns_true(self):
        cache = ForecastCache(ttl_minutes=60)

        response = MagicMock()
        response.hourly.time = ["2030-01-01T00:00"]
        response.model_dump.return_value = {"hourly": {"time": ["2030-01-01T00:00"]}}

        cache.set(55.75, 37.62, TimeStep.HOURLY, response)
        assert cache.get(55.75, 37.62, TimeStep.HOURLY) is not None

    def test_clear(self):
        cache = ForecastCache()

        response = MagicMock()
        response.hourly.time = ["2030-01-01T00:00"]
        response.model_dump.return_value = {"hourly": {"time": ["2030-01-01T00:00"]}}

        cache.set(55.75, 37.62, TimeStep.HOURLY, response)
        cache.clear()

        assert cache.get(55.75, 37.62, TimeStep.HOURLY) is None

    def test_get_last_time_hourly(self):
        cache = ForecastCache()

        response = HourlyResponse(
            latitude=55.75,
            longitude=37.62,
            elevation=130.0,
            generationtime_ms=0.5,
            utc_offset_seconds=10800,
            timezone="Europe/Moscow",
            timezone_abbreviation="MSK",
            hourly_units=HourlyUnits(),
            hourly=HourlyData(time=["2024-01-01T12:00"]),
        )

        result = cache._get_last_time(response)
        assert result is not None

    def test_get_last_time_daily(self):
        cache = ForecastCache()

        response = DailyResponse(
            latitude=55.75,
            longitude=37.62,
            elevation=130.0,
            generationtime_ms=0.5,
            utc_offset_seconds=10800,
            timezone="Europe/Moscow",
            timezone_abbreviation="MSK",
            daily_units=DailyUnits(),
            daily=DailyData(time=["2024-01-01"]),
        )

        result = cache._get_last_time(response)
        assert result is not None

    def test_get_last_time_empty(self):
        cache = ForecastCache()

        response = HourlyResponse(
            latitude=55.75,
            longitude=37.62,
            elevation=130.0,
            generationtime_ms=0.5,
            utc_offset_seconds=10800,
            timezone="Europe/Moscow",
            timezone_abbreviation="MSK",
            hourly_units=HourlyUnits(),
            hourly=HourlyData(time=[]),
        )

        result = cache._get_last_time(response)
        assert result is not None

    def test_get_last_time_no_hourly_or_daily(self):
        cache = ForecastCache()

        response = MagicMock()
        del response.hourly
        del response.daily

        result = cache._get_last_time(response)
        assert result is not None


class TestExceptions:
    def test_api_error(self):
        error = OpenMeteoAPIError("Invalid parameter")
        assert error.reason == "Invalid parameter"
        assert "Invalid parameter" in str(error)

    def test_validation_error(self):
        error = OpenMeteoValidationError("Invalid value")
        assert "Invalid value" in str(error)

    def test_connection_error(self):
        error = OpenMeteoConnectionError("Network error")
        assert "Network error" in str(error)


class TestClientLifecycle:
    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with OpenMeteoClient() as client:
            assert client._client is not None

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        client = OpenMeteoClient()
        await client.close()
        await client.close()

    @pytest.mark.asyncio
    async def test_ensure_client_creates_once(self):
        client = OpenMeteoClient()
        c1 = await client._ensure_client()
        c2 = await client._ensure_client()
        assert c1 is c2
        await client.close()


class TestCacheManagement:
    def test_clear_forecast_cache(self):
        client = OpenMeteoClient()
        client._forecast_cache.set(
            55.75,
            37.62,
            TimeStep.HOURLY,
            MagicMock(
                hourly=MagicMock(time=["2030-01-01T00:00"]), model_dump=lambda: {}
            ),
        )
        client.clear_forecast_cache()
        assert client._forecast_cache.get(55.75, 37.62, TimeStep.HOURLY) is None

    def test_clear_historical_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenMeteoClient(cache_dir=Path(tmpdir))
            client._historical_cache.save_month(
                55.75, 37.62, TimeStep.HOURLY, "2024-01", {"test": 1}
            )

            client.clear_historical_cache()

            result = client._historical_cache.load_month(
                55.75, 37.62, TimeStep.HOURLY, "2024-01"
            )
            assert result is None

    def test_clear_all_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenMeteoClient(cache_dir=Path(tmpdir))
            client._historical_cache.save_month(
                55.75, 37.62, TimeStep.HOURLY, "2024-01", {"test": 1}
            )

            client.clear_all_cache()

            assert client._forecast_cache.get(55.75, 37.62, TimeStep.HOURLY) is None


class TestFetchMethod:
    @pytest.mark.asyncio
    async def test_fetch_success(self):
        client = OpenMeteoClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "latitude": 55.75,
            "longitude": 37.62,
            "hourly": {"time": ["2024-01-01T00:00"], "temperature_2m": [5.0]},
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_ensure_client") as mock_ensure:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_ensure.return_value = mock_http_client

            result = await client._fetch("https://example.com", {"param": "value"})

            assert "hourly" in result

        await client.close()

    @pytest.mark.asyncio
    async def test_fetch_api_error(self):
        client = OpenMeteoClient()

        mock_response = MagicMock()
        mock_response.json.return_value = {"error": True, "reason": "Invalid parameter"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_ensure_client") as mock_ensure:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_ensure.return_value = mock_http_client

            with pytest.raises(OpenMeteoAPIError) as exc_info:
                await client._fetch("https://example.com", {})
            assert "Invalid parameter" in str(exc_info.value)

        await client.close()


class TestGetForecast:
    @pytest.mark.asyncio
    async def test_get_forecast_returns_hourly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenMeteoClient(cache_dir=Path(tmpdir))

            mock_response = {
                "latitude": 55.75,
                "longitude": 37.62,
                "elevation": 130.0,
                "generationtime_ms": 0.5,
                "utc_offset_seconds": 10800,
                "timezone": "Europe/Moscow",
                "timezone_abbreviation": "MSK",
                "hourly_units": {"time": "iso8601"},
                "hourly": {"time": ["2024-01-01T00:00"], "temperature_2m": [5.0]},
            }

            with patch.object(client, "_fetch", AsyncMock(return_value=mock_response)):
                result = await client.get_forecast(
                    55.75, 37.62, days=7, step=TimeStep.HOURLY
                )

                assert isinstance(result, HourlyResponse)

            await client.close()

    @pytest.mark.asyncio
    async def test_get_forecast_returns_daily(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenMeteoClient(cache_dir=Path(tmpdir))

            mock_response = {
                "latitude": 55.75,
                "longitude": 37.62,
                "elevation": 130.0,
                "generationtime_ms": 0.5,
                "utc_offset_seconds": 10800,
                "timezone": "Europe/Moscow",
                "timezone_abbreviation": "MSK",
                "daily_units": {"time": "iso8601"},
                "daily": {"time": ["2024-01-01"], "temperature_2m_max": [5.0]},
            }

            with patch.object(client, "_fetch", AsyncMock(return_value=mock_response)):
                result = await client.get_forecast(
                    55.75, 37.62, days=7, step=TimeStep.DAILY
                )

                assert isinstance(result, DailyResponse)

            await client.close()

    @pytest.mark.asyncio
    async def test_get_forecast_uses_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenMeteoClient(cache_dir=Path(tmpdir))

            mock_response = {
                "latitude": 55.75,
                "longitude": 37.62,
                "elevation": 130.0,
                "generationtime_ms": 0.5,
                "utc_offset_seconds": 10800,
                "timezone": "Europe/Moscow",
                "timezone_abbreviation": "MSK",
                "hourly_units": {"time": "iso8601"},
                "hourly": {"time": ["2030-01-01T00:00"], "temperature_2m": [5.0]},
            }

            mock_fetch = AsyncMock(return_value=mock_response)
            with patch.object(client, "_fetch", mock_fetch):
                await client.get_forecast(55.75, 37.62, days=7, step=TimeStep.HOURLY)
                mock_fetch.assert_called_once()

                mock_fetch.reset_mock()
                await client.get_forecast(55.75, 37.62, days=7, step=TimeStep.HOURLY)
                mock_fetch.assert_not_called()

            await client.close()


class TestGetCurrent:
    @pytest.mark.asyncio
    async def test_get_current_returns_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenMeteoClient(cache_dir=Path(tmpdir))

            mock_response = {
                "latitude": 55.75,
                "longitude": 37.62,
                "elevation": 130.0,
                "generationtime_ms": 0.5,
                "utc_offset_seconds": 10800,
                "timezone": "Europe/Moscow",
                "timezone_abbreviation": "MSK",
                "current_units": {"time": "iso8601", "interval": "seconds"},
                "current": {
                    "time": "2024-01-01T12:00",
                    "interval": 3600,
                    "temperature_2m": -5.0,
                },
            }

            with patch.object(client, "_fetch", AsyncMock(return_value=mock_response)):
                result = await client.get_current(55.75, 37.62)

                assert isinstance(result, CurrentResponse)
                assert result.current.temperature_2m == -5.0

            await client.close()


class TestGetHistorical:
    @pytest.mark.asyncio
    async def test_get_historical_returns_hourly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenMeteoClient(cache_dir=Path(tmpdir))

            mock_response = {
                "latitude": 55.75,
                "longitude": 37.62,
                "elevation": 130.0,
                "generationtime_ms": 0.5,
                "utc_offset_seconds": 10800,
                "timezone": "Europe/Moscow",
                "timezone_abbreviation": "MSK",
                "hourly_units": {"time": "iso8601"},
                "hourly": {"time": ["2024-01-01T00:00"], "temperature_2m": [5.0]},
            }

            with patch.object(client, "_fetch", AsyncMock(return_value=mock_response)):
                result = await client.get_historical(
                    55.75,
                    37.62,
                    date(2024, 1, 1),
                    date(2024, 1, 1),
                    step=TimeStep.HOURLY,
                )

                assert isinstance(result, HourlyResponse)

            await client.close()

    @pytest.mark.asyncio
    async def test_get_historical_returns_daily(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenMeteoClient(cache_dir=Path(tmpdir))

            mock_response = {
                "latitude": 55.75,
                "longitude": 37.62,
                "elevation": 130.0,
                "generationtime_ms": 0.5,
                "utc_offset_seconds": 10800,
                "timezone": "Europe/Moscow",
                "timezone_abbreviation": "MSK",
                "daily_units": {"time": "iso8601"},
                "daily": {"time": ["2024-01-01"], "temperature_2m_max": [5.0]},
            }

            with patch.object(client, "_fetch", AsyncMock(return_value=mock_response)):
                result = await client.get_historical(
                    55.75,
                    37.62,
                    date(2024, 1, 1),
                    date(2024, 1, 1),
                    step=TimeStep.DAILY,
                )

                assert isinstance(result, DailyResponse)

            await client.close()

    @pytest.mark.asyncio
    async def test_get_historical_empty_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = OpenMeteoClient(cache_dir=Path(tmpdir))

            with patch.object(
                client, "_fetch", AsyncMock(side_effect=Exception("No data"))
            ):
                with patch.object(
                    client._historical_cache,
                    "get_missing_months",
                    return_value=[],
                ):
                    with patch.object(
                        client._historical_cache,
                        "get_cached_months",
                        return_value=set(),
                    ):
                        result = await client.get_historical(
                            55.75,
                            37.62,
                            date(2024, 1, 1),
                            date(2024, 1, 1),
                            step=TimeStep.HOURLY,
                        )

                        assert isinstance(result, HourlyResponse)
                        assert len(result.hourly.time) == 0

            await client.close()
