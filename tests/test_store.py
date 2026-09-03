"""Tests for HistoricalStore and ForecastStore (domain layer over a backend)."""

import json
import logging
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
import pytest

from openmeteo.cache.backends import MemoryBackend
from openmeteo.cache.keys import ForecastKey, HistoryKey
from openmeteo.cache.store import ForecastStore, HistoricalStore
from openmeteo.exceptions import OpenMeteoCacheError
from openmeteo.types import CACHE_FORMAT_VERSION, TimeStep

UTC = dt_timezone.utc
KEY = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "auto", "2025-12")
DAILY_KEY = HistoryKey(55.75, 37.62, TimeStep.DAILY, "auto", "2025-12")


def hourly_data(start: date, days: int, variables=("temperature_2m",), hours_per_day=24):
    times = []
    for d in range(days):
        day = start + timedelta(days=d)
        for h in range(hours_per_day):
            times.append(f"{day.isoformat()}T{h:02d}:00")
    block = {"time": times}
    for v in variables:
        block[v] = [1.0] * len(times)
    return {"latitude": 55.75, "longitude": 37.62, "hourly": block}


def daily_data(start: date, days: int, variables=("temperature_2m_max",)):
    times = [(start + timedelta(days=d)).isoformat() for d in range(days)]
    block = {"time": times}
    for v in variables:
        block[v] = [1.0] * len(times)
    return {"latitude": 55.75, "longitude": 37.62, "daily": block}


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock(datetime(2026, 9, 3, 12, 0, tzinfo=UTC))


@pytest.fixture
async def backend():
    b = MemoryBackend()
    await b.open()
    return b


@pytest.fixture
def store(backend, clock):
    return HistoricalStore(backend, recent_ttl=timedelta(hours=6), historical_ttl=None, now=clock)


class TestSaveFinalFlag:
    async def test_full_old_month_is_final(self, store):
        entry = await store.save(
            KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
        )
        assert entry["final"] is True
        assert entry["format"] == CACHE_FORMAT_VERSION
        assert entry["covered_from"] == "2025-12-01"
        assert entry["covered_to"] == "2025-12-31"
        assert entry["variables"] == ["temperature_2m"]
        assert entry["saved_at"] == "2026-09-03T12:00:00+00:00"
        assert entry["package_version"]

    async def test_full_but_fresh_month_not_final(self, backend):
        clock = Clock(datetime(2026, 1, 3, tzinfo=UTC))  # 3 days after month end, lag is 7
        store = HistoricalStore(backend, now=clock)
        entry = await store.save(
            KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
        )
        assert entry["final"] is False

    async def test_month_final_exactly_at_lag(self, backend):
        clock = Clock(datetime(2026, 1, 7, 0, 0, tzinfo=UTC))
        store = HistoricalStore(backend, now=clock)
        entry = await store.save(
            KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
        )
        assert entry["final"] is True

    async def test_current_month_not_final(self, store):
        key = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "auto", "2026-09")
        entry = await store.save(
            key, hourly_data(date(2026, 9, 1), 3), date(2026, 9, 1), date(2026, 9, 3), ["temperature_2m"]
        )
        assert entry["final"] is False

    async def test_truncated_data_not_final_with_warning(self, store, caplog):
        data = hourly_data(date(2025, 12, 1), 20)  # 480 hours instead of 744
        with caplog.at_level(logging.WARNING, logger="openmeteo.cache.store"):
            entry = await store.save(KEY, data, date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"])
        assert entry["final"] is False
        assert any("480" in r.getMessage() for r in caplog.records)

    async def test_dst_short_day_still_final(self, store):
        # 31 days but one of them has 23 hours (DST change): must not be flagged truncated
        data = hourly_data(date(2025, 12, 1), 31)
        data["hourly"]["time"].pop(30)
        data["hourly"]["temperature_2m"].pop(30)
        entry = await store.save(KEY, data, date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"])
        assert entry["final"] is True

    async def test_daily_full_month_final(self, store):
        entry = await store.save(
            DAILY_KEY, daily_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m_max"]
        )
        assert entry["final"] is True

    async def test_daily_truncated_not_final(self, store):
        entry = await store.save(
            DAILY_KEY, daily_data(date(2025, 12, 1), 20), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m_max"]
        )
        assert entry["final"] is False

    async def test_saved_entry_roundtrips(self, store):
        data = hourly_data(date(2025, 12, 1), 31)
        await store.save(KEY, data, date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"])
        entry = await store.load(KEY, ["temperature_2m"])
        assert entry is not None
        assert entry["data"] == data


class TestLoadRules:
    async def test_missing_returns_none(self, store):
        assert await store.load(KEY, ["temperature_2m"]) is None

    async def test_old_format_entry_dropped(self, store, backend):
        await backend.set(KEY.as_str(), json.dumps({"hourly": {"time": []}}).encode(), None)
        assert await store.load(KEY, []) is None
        assert await backend.get(KEY.as_str()) is None

    async def test_wrong_format_version_dropped(self, store, backend):
        await backend.set(KEY.as_str(), json.dumps({"format": 1, "data": {}}).encode(), None)
        assert await store.load(KEY, []) is None
        assert await backend.get(KEY.as_str()) is None

    async def test_unparseable_json_dropped_with_warning(self, store, backend, caplog):
        await backend.set(KEY.as_str(), b"\xff\xfe not json", None)
        with caplog.at_level(logging.WARNING, logger="openmeteo.cache.store"):
            assert await store.load(KEY, []) is None
        assert await backend.get(KEY.as_str()) is None
        assert any("corrupt" in r.getMessage().lower() for r in caplog.records)

    async def test_missing_variables_returns_none(self, store):
        await store.save(
            KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
        )
        assert await store.load(KEY, ["temperature_2m", "rain"]) is None
        # entry itself is kept; it is just not sufficient for this request
        assert await store.load(KEY, ["temperature_2m"]) is not None

    async def test_subset_of_variables_is_fine(self, store):
        await store.save(
            KEY,
            hourly_data(date(2025, 12, 1), 31, ("temperature_2m", "rain")),
            date(2025, 12, 1),
            date(2025, 12, 31),
            ["temperature_2m", "rain"],
        )
        assert await store.load(KEY, ["rain"]) is not None

    async def test_non_final_within_recent_ttl(self, store, clock):
        key = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "auto", "2026-09")
        await store.save(key, hourly_data(date(2026, 9, 1), 3), date(2026, 9, 1), date(2026, 9, 3), ["temperature_2m"])
        clock.now += timedelta(hours=2)
        assert await store.load(key, ["temperature_2m"]) is not None

    async def test_non_final_past_recent_ttl(self, store, clock):
        key = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "auto", "2026-09")
        await store.save(key, hourly_data(date(2026, 9, 1), 3), date(2026, 9, 1), date(2026, 9, 3), ["temperature_2m"])
        clock.now += timedelta(hours=7)
        assert await store.load(key, ["temperature_2m"]) is None

    async def test_final_without_historical_ttl_lives_forever(self, store, clock):
        await store.save(
            KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
        )
        clock.now += timedelta(days=3 * 365)
        assert await store.load(KEY, ["temperature_2m"]) is not None

    async def test_final_past_historical_ttl(self, backend, clock):
        store = HistoricalStore(backend, historical_ttl=timedelta(days=365), now=clock)
        await store.save(
            KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
        )
        clock.now += timedelta(days=2 * 365)
        assert await store.load(KEY, ["temperature_2m"]) is None

    async def test_final_within_historical_ttl(self, backend, clock):
        store = HistoricalStore(backend, historical_ttl=timedelta(days=365), now=clock)
        await store.save(
            KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
        )
        clock.now += timedelta(days=100)
        assert await store.load(KEY, ["temperature_2m"]) is not None

    async def test_corrupt_series_length_dropped(self, store, backend):
        data = hourly_data(date(2025, 12, 1), 31)
        data["hourly"]["temperature_2m"] = data["hourly"]["temperature_2m"][:10]
        await store.save(KEY, data, date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"])
        assert await store.load(KEY, ["temperature_2m"]) is None
        assert await backend.get(KEY.as_str()) is None

    async def test_missing_data_block_dropped(self, store, backend):
        bad = {
            "format": CACHE_FORMAT_VERSION,
            "saved_at": "2026-09-03T12:00:00+00:00",
            "covered_from": "2025-12-01",
            "covered_to": "2025-12-31",
            "final": True,
            "variables": [],
            "data": {"latitude": 1.0},
        }
        await backend.set(KEY.as_str(), json.dumps(bad).encode(), None)
        assert await store.load(KEY, []) is None

    async def test_clear_removes_only_history(self, store, backend):
        await store.save(
            KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
        )
        await backend.set(ForecastKey(1, 2, TimeStep.DAILY, 7, "auto").as_str(), b"{}", None)
        assert await store.clear() == 1
        assert await store.load(KEY, ["temperature_2m"]) is None
        assert await backend.get(ForecastKey(1, 2, TimeStep.DAILY, 7, "auto").as_str()) == b"{}"


class FailingBackend(MemoryBackend):
    def __init__(self, fail_on=("get", "set", "delete", "scan", "clear")):
        super().__init__()
        self.fail_on = fail_on

    async def get(self, key):
        if "get" in self.fail_on:
            raise ConnectionError("redis down")
        return await super().get(key)

    async def set(self, key, value, ttl):
        if "set" in self.fail_on:
            raise ConnectionError("redis down")
        return await super().set(key, value, ttl)

    async def delete(self, key):
        if "delete" in self.fail_on:
            raise ConnectionError("redis down")
        return await super().delete(key)

    async def clear(self, prefix):
        if "clear" in self.fail_on:
            raise ConnectionError("redis down")
        return await super().clear(prefix)

    async def ping(self):
        raise ConnectionError("redis down")


class TestErrorPolicy:
    async def test_lenient_load_returns_none_and_logs_error(self, caplog):
        store = HistoricalStore(FailingBackend(), strict=False)
        with caplog.at_level(logging.ERROR, logger="openmeteo.cache.store"):
            assert await store.load(KEY, []) is None
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    async def test_lenient_save_swallows_and_logs(self, caplog):
        store = HistoricalStore(FailingBackend(), strict=False)
        with caplog.at_level(logging.ERROR, logger="openmeteo.cache.store"):
            entry = await store.save(
                KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
            )
        assert entry["final"] is True  # entry is still computed and returned
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    async def test_strict_load_raises(self):
        store = HistoricalStore(FailingBackend(), strict=True)
        with pytest.raises(OpenMeteoCacheError):
            await store.load(KEY, [])

    async def test_strict_save_raises(self):
        store = HistoricalStore(FailingBackend(), strict=True)
        with pytest.raises(OpenMeteoCacheError):
            await store.save(
                KEY, hourly_data(date(2025, 12, 1), 31), date(2025, 12, 1), date(2025, 12, 31), ["temperature_2m"]
            )

    async def test_ping_never_swallowed(self):
        store = HistoricalStore(FailingBackend(), strict=False)
        with pytest.raises(ConnectionError):
            await store.ping()

    async def test_cache_error_from_backend_passes_through_in_strict(self):
        class B(MemoryBackend):
            async def get(self, key):
                raise OpenMeteoCacheError("not opened")

        store = HistoricalStore(B(), strict=True)
        with pytest.raises(OpenMeteoCacheError):
            await store.load(KEY, [])


FKEY = ForecastKey(55.75, 37.62, TimeStep.HOURLY, 7, "auto")


def forecast_payload(last: str):
    return {"latitude": 55.75, "hourly": {"time": ["2026-09-03T00:00", last], "temperature_2m": [1.0, 2.0]}}


class TestForecastStore:
    async def test_roundtrip(self, backend, clock):
        store = ForecastStore(backend, ttl=timedelta(minutes=60), now=clock)
        data = forecast_payload("2026-09-10T00:00")
        await store.save(FKEY, data, datetime(2026, 9, 10, tzinfo=UTC))
        assert await store.load(FKEY) == data

    async def test_missing(self, backend, clock):
        store = ForecastStore(backend, ttl=timedelta(minutes=60), now=clock)
        assert await store.load(FKEY) is None

    async def test_ttl_passed_to_backend(self, clock):
        seen = {}

        class B(MemoryBackend):
            async def set(self, key, value, ttl):
                seen["ttl"] = ttl
                await super().set(key, value, ttl)

        store = ForecastStore(B(), ttl=timedelta(minutes=30), now=clock)
        await store.save(FKEY, forecast_payload("2026-09-10T00:00"), datetime(2026, 9, 10, tzinfo=UTC))
        assert seen["ttl"] == timedelta(minutes=30)

    async def test_safety_margin_invalidates(self, backend, clock):
        store = ForecastStore(backend, ttl=timedelta(minutes=60), now=clock)
        last = clock.now + timedelta(hours=2)  # margin is 3 hours
        await store.save(FKEY, forecast_payload(last.strftime("%Y-%m-%dT%H:%M")), last)
        assert await store.load(FKEY) is None

    async def test_safety_margin_ok(self, backend, clock):
        store = ForecastStore(backend, ttl=timedelta(minutes=60), now=clock)
        last = clock.now + timedelta(hours=5)
        await store.save(FKEY, forecast_payload(last.strftime("%Y-%m-%dT%H:%M")), last)
        assert await store.load(FKEY) is not None

    async def test_shared_between_stores(self, backend, clock):
        a = ForecastStore(backend, ttl=timedelta(minutes=60), now=clock)
        b = ForecastStore(backend, ttl=timedelta(minutes=60), now=clock)
        await a.save(FKEY, forecast_payload("2026-09-10T00:00"), datetime(2026, 9, 10, tzinfo=UTC))
        assert await b.load(FKEY) is not None

    async def test_old_format_dropped(self, backend, clock):
        store = ForecastStore(backend, ttl=timedelta(minutes=60), now=clock)
        await backend.set(FKEY.as_str(), b'{"hourly": {}}', None)
        assert await store.load(FKEY) is None
        assert await backend.get(FKEY.as_str()) is None

    async def test_clear_removes_only_forecasts(self, backend, clock):
        store = ForecastStore(backend, ttl=timedelta(minutes=60), now=clock)
        await store.save(FKEY, forecast_payload("2026-09-10T00:00"), datetime(2026, 9, 10, tzinfo=UTC))
        await backend.set(KEY.as_str(), b"{}", None)
        assert await store.clear() == 1
        assert await backend.get(KEY.as_str()) == b"{}"

    async def test_lenient_errors(self, caplog):
        store = ForecastStore(FailingBackend(), ttl=timedelta(minutes=60), strict=False)
        with caplog.at_level(logging.ERROR, logger="openmeteo.cache.store"):
            assert await store.load(FKEY) is None
            await store.save(FKEY, forecast_payload("x"), datetime(2026, 9, 10, tzinfo=UTC))
        assert sum(r.levelno == logging.ERROR for r in caplog.records) == 2

    async def test_strict_errors(self):
        store = ForecastStore(FailingBackend(), ttl=timedelta(minutes=60), strict=True)
        with pytest.raises(OpenMeteoCacheError):
            await store.load(FKEY)
