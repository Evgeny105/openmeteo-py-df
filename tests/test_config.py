"""Tests for cache backend resolution, fallback, ownership and error policy."""

import logging
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

import openmeteo.client as client_mod
from openmeteo import OpenMeteoCacheError, OpenMeteoClient
from openmeteo.cache import FileBackend, MemoryBackend
from openmeteo.cache.backends import RedisBackend

ENV = "OPENMETEO_CACHE_URL"
PAYLOAD = {
    "latitude": 55.75,
    "longitude": 37.62,
    "elevation": 1.0,
    "generationtime_ms": 0.1,
    "utc_offset_seconds": 0,
    "timezone": "GMT",
    "timezone_abbreviation": "GMT",
    "hourly_units": {"time": "iso8601"},
    "hourly": {"time": ["2030-01-01T00:00"], "temperature_2m": [1.0]},
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)


class TestResolution:
    def test_explicit_backend_wins(self, monkeypatch):
        monkeypatch.setenv(ENV, "memory://")
        b = MemoryBackend()
        assert OpenMeteoClient(cache=b, cache_url="file:///tmp/x").cache is b

    def test_cache_url_over_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV, "memory://")
        c = OpenMeteoClient(cache_url=f"file://{tmp_path}")
        assert isinstance(c.cache, FileBackend)

    def test_env_over_cache_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV, "memory://")
        assert isinstance(OpenMeteoClient(cache_dir=tmp_path).cache, MemoryBackend)

    def test_env_redis(self, monkeypatch):
        monkeypatch.setenv(ENV, "rediss://h:6379/0?cluster=true")
        c = OpenMeteoClient()
        assert isinstance(c.cache, RedisBackend) and c.cache.cluster is True

    def test_cache_dir_used(self, tmp_path):
        c = OpenMeteoClient(cache_dir=tmp_path)
        assert isinstance(c.cache, FileBackend) and c.cache.root == tmp_path

    def test_default_directory(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        c = OpenMeteoClient()
        assert isinstance(c.cache, FileBackend)
        assert c.cache.root == tmp_path / "openmeteo"

    def test_default_directory_home(self, monkeypatch, tmp_path):
        monkeypatch.setattr(client_mod.Path, "home", staticmethod(lambda: tmp_path))
        assert OpenMeteoClient().cache.root == tmp_path / ".cache" / "openmeteo"

    def test_empty_env_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV, "")
        assert isinstance(OpenMeteoClient(cache_dir=tmp_path).cache, FileBackend)


class TestFallback:
    async def test_default_dir_unwritable_falls_back_to_memory(self, monkeypatch, tmp_path, caplog):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setattr(client_mod, "_default_cache_dir", lambda: blocker / "openmeteo")
        c = OpenMeteoClient()
        with patch.object(c, "_fetch", AsyncMock(return_value=PAYLOAD)) as fetch:
            with caplog.at_level(logging.WARNING, logger="openmeteo.client"):
                await c.get_forecast(55.75, 37.62)
                await c.get_forecast(55.75, 37.62)
        assert isinstance(c.cache, MemoryBackend)
        assert fetch.await_count == 1  # memory cache works
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and ENV in warnings[0].getMessage()
        await c.close()

    async def test_explicit_unwritable_dir_raises(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        c = OpenMeteoClient(cache_url=f"file://{blocker}/openmeteo")
        with pytest.raises(OpenMeteoCacheError):
            await c.get_forecast(55.75, 37.62)

    async def test_env_unwritable_dir_raises(self, monkeypatch, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setenv(ENV, f"file://{blocker}/openmeteo")
        with pytest.raises(OpenMeteoCacheError):
            await OpenMeteoClient().get_forecast(55.75, 37.62)

    async def test_cache_dir_unwritable_raises(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        with pytest.raises(OpenMeteoCacheError):
            await OpenMeteoClient(cache_dir=blocker / "openmeteo").get_forecast(55.75, 37.62)

    async def test_default_dir_works_when_writable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        c = OpenMeteoClient()
        with patch.object(c, "_fetch", AsyncMock(return_value=PAYLOAD)):
            await c.get_forecast(55.75, 37.62)
        assert isinstance(c.cache, FileBackend)
        assert any((tmp_path / "openmeteo").iterdir())
        await c.close()


class FailingBackend(MemoryBackend):
    def __init__(self, fail=True):
        super().__init__()
        self.fail = fail
        self.closed = 0

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return await super().get(key)

    async def set(self, key, value, ttl):
        if self.fail:
            raise ConnectionError("redis down")
        await super().set(key, value, ttl)

    async def ping(self):
        raise ConnectionError("redis down")

    async def close(self):
        self.closed += 1


class TestErrorPolicy:
    async def test_lenient_serves_from_api(self, caplog):
        c = OpenMeteoClient(cache=FailingBackend())
        with patch.object(c, "_fetch", AsyncMock(return_value=PAYLOAD)) as fetch:
            with caplog.at_level(logging.ERROR, logger="openmeteo.cache.store"):
                r1 = await c.get_forecast(55.75, 37.62)
                r2 = await c.get_forecast(55.75, 37.62)
        assert r1.hourly.temperature_2m == r2.hourly.temperature_2m == [1.0]
        assert fetch.await_count == 2
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    async def test_strict_raises(self):
        c = OpenMeteoClient(cache=FailingBackend(), cache_strict=True)
        with patch.object(c, "_fetch", AsyncMock(return_value=PAYLOAD)):
            with pytest.raises(OpenMeteoCacheError):
                await c.get_forecast(55.75, 37.62)
            with pytest.raises(OpenMeteoCacheError):
                await c.get_historical(55.75, 37.62, date(2024, 1, 1), date(2024, 1, 1), variables=["temperature_2m"])

    async def test_cache_health_propagates(self):
        c = OpenMeteoClient(cache=FailingBackend())
        with pytest.raises(ConnectionError):
            await c.cache_health()

    async def test_cache_health_ok(self):
        c = OpenMeteoClient(cache=MemoryBackend())
        await c.cache_health()


class TestOwnership:
    async def test_passed_backend_not_closed(self):
        b = FailingBackend(fail=False)
        c = OpenMeteoClient(cache=b)
        with patch.object(c, "_fetch", AsyncMock(return_value=PAYLOAD)):
            await c.get_forecast(55.75, 37.62)
        await c.close()
        assert b.closed == 0

    async def test_own_backend_closed(self, tmp_path):
        c = OpenMeteoClient(cache_url=f"file://{tmp_path}")
        with patch.object(c, "_fetch", AsyncMock(return_value=PAYLOAD)):
            await c.get_forecast(55.75, 37.62)
        backend = c.cache
        assert backend._opened is True
        await c.close()
        assert backend._opened is False

    async def test_close_without_use_is_fine(self):
        await OpenMeteoClient().close()

    async def test_reuse_after_close_reopens(self, tmp_path):
        c = OpenMeteoClient(cache_url=f"file://{tmp_path}")
        with patch.object(c, "_fetch", AsyncMock(return_value=PAYLOAD)) as fetch:
            await c.get_forecast(55.75, 37.62)
            await c.close()
            await c.get_forecast(55.75, 37.62)
        assert fetch.await_count == 1  # file cache survived close
        await c.close()

    async def test_concurrent_first_use_opens_once(self):
        opened = {"n": 0}

        class B(MemoryBackend):
            async def open(self):
                opened["n"] += 1

        c = OpenMeteoClient(cache=B())
        with patch.object(c, "_fetch", AsyncMock(return_value=PAYLOAD)):
            import asyncio

            await asyncio.gather(*(c.get_forecast(55.75, 37.62) for _ in range(5)))
        assert opened["n"] == 1
