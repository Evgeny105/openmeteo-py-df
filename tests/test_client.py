"""Tests for OpenMeteoClient: validation, lifecycle, forecast, current, historical."""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from openmeteo import (
    DailyResponse,
    HourlyResponse,
    OpenMeteoAPIError,
    OpenMeteoClient,
    OpenMeteoConnectionError,
    OpenMeteoDataError,
    OpenMeteoValidationError,
    TimeStep,
)
from openmeteo.cache import HistoryKey, MemoryBackend
from openmeteo.models import CurrentResponse

META = {
    "latitude": 55.75,
    "longitude": 37.62,
    "elevation": 130.0,
    "generationtime_ms": 0.5,
    "utc_offset_seconds": 10800,
    "timezone": "Europe/Moscow",
    "timezone_abbreviation": "MSK",
}


def hourly_response(times, **series):
    return {**META, "hourly_units": {"time": "iso8601"}, "hourly": {"time": list(times), **series}}


def daily_response(times, **series):
    return {**META, "daily_units": {"time": "iso8601"}, "daily": {"time": list(times), **series}}


def month_hours(start: date, end: date):
    out = []
    d = start
    while d <= end:
        out.extend(f"{d.isoformat()}T{h:02d}:00" for h in range(24))
        d += timedelta(days=1)
    return out


def archive_stub(variables_per_month=None, record=None):
    """Build a _fetch replacement answering archive requests month by month."""

    async def fetch(url, params):
        if record is not None:
            record.append(params)
        start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
        requested = params["hourly"].split(",")
        month = start.strftime("%Y-%m")
        available = (variables_per_month or {}).get(month, requested)
        times = month_hours(start, end)
        series = {v: [float(i) for i in range(len(times))] for v in requested if v in available}
        return hourly_response(times, **series)

    return fetch


@pytest.fixture
def client():
    return OpenMeteoClient(cache=MemoryBackend())


class TestValidation:
    def test_valid_coordinates(self, client):
        client._validate_coordinates(55.75, 37.62)

    @pytest.mark.parametrize("lat,lon", [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)])
    def test_invalid_coordinates(self, client, lat, lon):
        with pytest.raises(OpenMeteoValidationError):
            client._validate_coordinates(lat, lon)

    def test_valid_date_range(self, client):
        client._validate_date_range(date(2024, 1, 1), date(2024, 1, 31))

    def test_start_after_end(self, client):
        with pytest.raises(OpenMeteoValidationError):
            client._validate_date_range(date(2024, 2, 1), date(2024, 1, 1))

    def test_future_end_rejected(self, client):
        with pytest.raises(OpenMeteoValidationError):
            client._validate_date_range(date(2024, 1, 1), date(2100, 1, 1))

    def test_future_allowed(self, client):
        client._validate_date_range(date(2024, 1, 1), date(2100, 1, 1), allow_future=True)

    @pytest.mark.parametrize("days", [1, 7, 16])
    def test_valid_forecast_days(self, client, days):
        client._validate_forecast_days(days)

    @pytest.mark.parametrize("days", [0, 17, -1])
    def test_invalid_forecast_days(self, client, days):
        with pytest.raises(OpenMeteoValidationError):
            client._validate_forecast_days(days)

    def test_constructor_argument_validation(self):
        with pytest.raises(OpenMeteoValidationError):
            OpenMeteoClient(retries=-1)
        with pytest.raises(OpenMeteoValidationError):
            OpenMeteoClient(max_concurrency=0)
        with pytest.raises(OpenMeteoValidationError):
            OpenMeteoClient(ttl_minutes=0)

    def test_legacy_constructor_arguments_accepted(self, tmp_path):
        OpenMeteoClient(ttl_minutes=30, cache_dir=tmp_path, timeout=60.0)
        OpenMeteoClient()


class TestLifecycle:
    async def test_context_manager(self):
        async with OpenMeteoClient(cache=MemoryBackend()) as c:
            assert c._client is not None
        assert c._client is None

    async def test_close_idempotent(self, client):
        await client._ensure_client()
        await client.close()
        await client.close()

    async def test_ensure_client_creates_once(self, client):
        a = await client._ensure_client()
        b = await client._ensure_client()
        assert a is b
        await client.close()

    def test_constructor_has_no_side_effects(self, tmp_path):
        target = tmp_path / "never-created"
        OpenMeteoClient(cache_dir=target)
        assert not target.exists()


class TestGetForecast:
    async def test_returns_hourly(self, client):
        with patch.object(client, "_fetch", AsyncMock(return_value=hourly_response(["2030-01-01T00:00"], temperature_2m=[5.0]))):
            result = await client.get_forecast(55.75, 37.62, days=7, step=TimeStep.HOURLY)
        assert isinstance(result, HourlyResponse)
        assert result.hourly.temperature_2m == [5.0]
        await client.close()

    async def test_returns_daily(self, client):
        with patch.object(client, "_fetch", AsyncMock(return_value=daily_response(["2030-01-01"], temperature_2m_max=[5.0]))):
            result = await client.get_forecast(55.75, 37.62, days=7, step=TimeStep.DAILY)
        assert isinstance(result, DailyResponse)
        await client.close()

    async def test_uses_cache(self, client):
        fetch = AsyncMock(return_value=hourly_response(["2030-01-01T00:00"], temperature_2m=[5.0]))
        with patch.object(client, "_fetch", fetch):
            await client.get_forecast(55.75, 37.62, days=7)
            await client.get_forecast(55.75, 37.62, days=7)
        assert fetch.await_count == 1
        await client.close()

    async def test_force_refresh_bypasses_cache(self, client):
        fetch = AsyncMock(return_value=hourly_response(["2030-01-01T00:00"], temperature_2m=[5.0]))
        with patch.object(client, "_fetch", fetch):
            await client.get_forecast(55.75, 37.62, days=7)
            await client.get_forecast(55.75, 37.62, days=7, force_refresh=True)
        assert fetch.await_count == 2
        await client.close()

    async def test_days_and_timezone_part_of_key(self, client):
        fetch = AsyncMock(return_value=hourly_response(["2030-01-01T00:00"], temperature_2m=[5.0]))
        with patch.object(client, "_fetch", fetch):
            await client.get_forecast(55.75, 37.62, days=3)
            await client.get_forecast(55.75, 37.62, days=7)
            await client.get_forecast(55.75, 37.62, days=7, timezone="Europe/Moscow")
        assert fetch.await_count == 3
        await client.close()

    async def test_requested_variables_aligned(self, client):
        payload = hourly_response(["2030-01-01T00:00", "2030-01-01T01:00"], temperature_2m=[1.0, 2.0])
        with patch.object(client, "_fetch", AsyncMock(return_value=payload)):
            result = await client.get_forecast(55.75, 37.62, variables=["temperature_2m", "visibility"])
        assert result.hourly.visibility == [None, None]
        assert result.hourly.rain is None
        await client.close()

    async def test_cached_response_also_aligned(self, client):
        payload = hourly_response(["2030-01-01T00:00"], temperature_2m=[1.0])
        with patch.object(client, "_fetch", AsyncMock(return_value=payload)):
            await client.get_forecast(55.75, 37.62, variables=["temperature_2m", "visibility"])
            result = await client.get_forecast(55.75, 37.62, variables=["temperature_2m", "visibility"])
        assert result.hourly.visibility == [None]
        await client.close()

    async def test_shared_backend_between_clients(self):
        backend = MemoryBackend()
        a, b = OpenMeteoClient(cache=backend), OpenMeteoClient(cache=backend)
        payload = hourly_response(["2030-01-01T00:00"], temperature_2m=[5.0])
        with patch.object(a, "_fetch", AsyncMock(return_value=payload)):
            await a.get_forecast(55.75, 37.62)
        with patch.object(b, "_fetch", AsyncMock(side_effect=AssertionError("should not fetch"))):
            result = await b.get_forecast(55.75, 37.62)
        assert result.hourly.temperature_2m == [5.0]


class TestGetCurrent:
    async def test_returns_response(self, client):
        payload = {
            **META,
            "current_units": {"time": "iso8601", "interval": "seconds"},
            "current": {"time": "2024-01-01T12:00", "interval": 3600, "temperature_2m": -5.0},
        }
        with patch.object(client, "_fetch", AsyncMock(return_value=payload)) as fetch:
            result = await client.get_current(55.75, 37.62)
            await client.get_current(55.75, 37.62)
        assert isinstance(result, CurrentResponse)
        assert result.current.temperature_2m == -5.0
        assert fetch.await_count == 2  # never cached
        await client.close()


class TestGetHistorical:
    async def test_single_month_hourly(self, client):
        with patch.object(client, "_fetch", archive_stub()):
            result = await client.get_historical(
                55.75, 37.62, date(2024, 1, 1), date(2024, 1, 2), step=TimeStep.HOURLY, variables=["temperature_2m"]
            )
        assert isinstance(result, HourlyResponse)
        assert len(result.hourly.time) == 48  # trimmed to the two requested days
        assert len(result.hourly.temperature_2m) == 48

    async def test_daily(self, client):
        async def fetch(url, params):
            start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
            times = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
            return daily_response(times, temperature_2m_max=[1.0] * len(times))

        with patch.object(client, "_fetch", fetch):
            result = await client.get_historical(
                55.75, 37.62, date(2024, 1, 10), date(2024, 1, 12), step=TimeStep.DAILY, variables=["temperature_2m_max"]
            )
        assert isinstance(result, DailyResponse)
        assert result.daily.time == ["2024-01-10", "2024-01-11", "2024-01-12"]

    async def test_no_trim_returns_whole_months(self, client):
        with patch.object(client, "_fetch", archive_stub()):
            result = await client.get_historical(
                55.75, 37.62, date(2024, 1, 10), date(2024, 1, 12), variables=["temperature_2m"], trim_to_range=False
            )
        assert len(result.hourly.time) == 31 * 24

    async def test_december_request_asks_for_december_only(self, client):
        calls = []
        with patch.object(client, "_fetch", archive_stub(record=calls)):
            await client.get_historical(55.75, 37.62, date(2025, 12, 1), date(2025, 12, 31), variables=["temperature_2m"])
        assert calls == [
            {
                "latitude": 55.75,
                "longitude": 37.62,
                "start_date": "2025-12-01",
                "end_date": "2025-12-31",
                "timezone": "auto",
                "hourly": "temperature_2m",
            }
        ]

    async def test_cached_and_fetched_months_in_chronological_order(self, client):
        calls = []
        stub = archive_stub(record=calls)
        with patch.object(client, "_fetch", stub):
            await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 2, 29), variables=["temperature_2m"])
            calls.clear()
            result = await client.get_historical(
                55.75, 37.62, date(2024, 1, 1), date(2024, 3, 31), variables=["temperature_2m"]
            )
        assert [c["start_date"] for c in calls] == ["2024-03-01"]  # only March fetched
        times = result.hourly.time
        assert times[0] == "2024-01-01T00:00" and times[-1] == "2024-03-31T23:00"
        assert times == sorted(times)
        assert len(times) == (31 + 29 + 31) * 24

    async def test_second_call_served_from_cache(self, client):
        fetch = AsyncMock(side_effect=archive_stub())
        with patch.object(client, "_fetch", fetch):
            await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m"])
            await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m"])
        assert fetch.await_count == 1

    async def test_missing_variables_trigger_refetch(self, client):
        fetch = AsyncMock(side_effect=archive_stub())
        with patch.object(client, "_fetch", fetch):
            await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m"])
            await client.get_historical(
                55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m", "rain"]
            )
        assert fetch.await_count == 2

    async def test_timezone_is_part_of_cache_key(self, client):
        fetch = AsyncMock(side_effect=archive_stub())
        with patch.object(client, "_fetch", fetch):
            await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m"])
            await client.get_historical(
                55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m"], timezone="Europe/Moscow"
            )
        assert fetch.await_count == 2

    async def test_months_with_different_variable_sets_are_aligned(self):
        backend = MemoryBackend()
        client = OpenMeteoClient(cache=backend)
        # January is cached by a "narrow" request first: only temperature.
        with patch.object(client, "_fetch", archive_stub()):
            await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m"])
        # Then a wide request: January lacks rain -> re-fetched; simulate the API
        # not returning rain for January at all (variable unavailable that month).
        with patch.object(client, "_fetch", archive_stub(variables_per_month={"2024-01": ["temperature_2m"]})):
            result = await client.get_historical(
                55.75, 37.62, date(2024, 1, 1), date(2024, 2, 29), variables=["temperature_2m", "rain"]
            )
        n = (31 + 29) * 24
        assert len(result.hourly.time) == n
        assert len(result.hourly.temperature_2m) == n
        assert len(result.hourly.rain) == n
        assert result.hourly.rain[: 31 * 24] == [None] * (31 * 24)
        assert result.hourly.rain[31 * 24] == 0.0

    async def test_concurrency_bounded(self):
        client = OpenMeteoClient(cache=MemoryBackend(), max_concurrency=2)
        active, peak = 0, 0

        async def fetch(url, params):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return await archive_stub()(url, params)

        with patch.object(client, "_fetch", fetch):
            await client.get_historical(55.75, 37.62, date(2023, 1, 1), date(2023, 12, 31), variables=["temperature_2m"])
        assert peak == 2

    async def test_error_in_one_month_fails_whole_request(self, client):
        async def fetch(url, params):
            if params["start_date"] == "2024-02-01":
                raise OpenMeteoConnectionError("boom")
            return await archive_stub()(url, params)

        with patch.object(client, "_fetch", fetch):
            with pytest.raises(OpenMeteoConnectionError):
                await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 3, 31), variables=["temperature_2m"])

    async def test_api_error_propagates(self, client):
        with patch.object(client, "_fetch", AsyncMock(side_effect=OpenMeteoAPIError("bad"))):
            with pytest.raises(OpenMeteoAPIError):
                await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 2), variables=["temperature_2m"])

    async def test_corrupt_cache_entry_is_refetched(self):
        backend = MemoryBackend()
        client = OpenMeteoClient(cache=backend)
        fetch = AsyncMock(side_effect=archive_stub())
        with patch.object(client, "_fetch", fetch):
            await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m"])
            key = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "auto", "2024-01").as_str()
            await backend.set(key, b"garbage", None)
            result = await client.get_historical(
                55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m"]
            )
        assert fetch.await_count == 2
        assert len(result.hourly.time) == 31 * 24

    async def test_requested_variable_absent_everywhere_is_none_list(self, client):
        with patch.object(client, "_fetch", archive_stub(variables_per_month={"2024-01": []})):
            result = await client.get_historical(
                55.75, 37.62, date(2024, 1, 1), date(2024, 1, 1), variables=["temperature_2m"]
            )
        assert result.hourly.temperature_2m == [None] * 24
        assert result.hourly.rain is None

    async def test_data_error_surfaces(self, client):
        with patch.object(client, "_fetch", archive_stub()), patch(
            "openmeteo.client.assemble", side_effect=OpenMeteoDataError("mismatch")
        ):
            with pytest.raises(OpenMeteoDataError):
                await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 1), variables=["temperature_2m"])


class TestCacheManagement:
    async def test_clear_forecast_cache(self, client):
        with patch.object(client, "_fetch", AsyncMock(return_value=hourly_response(["2030-01-01T00:00"], temperature_2m=[5.0]))) as fetch:
            await client.get_forecast(55.75, 37.62)
            assert await client.clear_forecast_cache() == 1
            await client.get_forecast(55.75, 37.62)
        assert fetch.await_count == 2

    async def test_clear_historical_cache_keeps_forecasts(self, client):
        with patch.object(client, "_fetch", archive_stub()):
            await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 2, 29), variables=["temperature_2m"])
        with patch.object(client, "_fetch", AsyncMock(return_value=hourly_response(["2030-01-01T00:00"], temperature_2m=[5.0]))) as fetch:
            await client.get_forecast(55.75, 37.62)
            assert await client.clear_historical_cache() == 2
            await client.get_forecast(55.75, 37.62)
        assert fetch.await_count == 1

    async def test_clear_all_cache(self, client):
        with patch.object(client, "_fetch", archive_stub()):
            await client.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 31), variables=["temperature_2m"])
        with patch.object(client, "_fetch", AsyncMock(return_value=hourly_response(["2030-01-01T00:00"], temperature_2m=[5.0]))):
            await client.get_forecast(55.75, 37.62)
        assert await client.clear_all_cache() == 2
