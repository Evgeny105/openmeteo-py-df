"""Tests for _fetch: retries, backoff, error classification."""

from unittest.mock import AsyncMock

import httpx
import pytest

from openmeteo import OpenMeteoAPIError, OpenMeteoClient, OpenMeteoConnectionError
from openmeteo.cache import MemoryBackend
from openmeteo.types import MAX_RETRY_DELAY

URL = "https://example.test/v1/forecast"
OK_BODY = {"latitude": 1.0, "hourly": {"time": ["2024-01-01T00:00"], "temperature_2m": [5.0]}}


def make_client(handler, **kwargs):
    client = OpenMeteoClient(cache=MemoryBackend(), **kwargs)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._sleep = AsyncMock()
    return client


class TestFetch:
    async def test_success(self):
        client = make_client(lambda req: httpx.Response(200, json=OK_BODY))
        assert await client._fetch(URL, {"a": 1}) == OK_BODY
        client._sleep.assert_not_awaited()
        await client.close()

    async def test_transport_errors_then_success(self, caplog):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, json=OK_BODY)

        client = make_client(handler, retries=3)
        with caplog.at_level("WARNING", logger="openmeteo.client"):
            assert await client._fetch(URL, {}) == OK_BODY
        assert calls["n"] == 3
        assert client._sleep.await_count == 2
        messages = [r.getMessage() for r in caplog.records]
        assert any("retry 1/3" in m for m in messages) and any("retry 2/3" in m for m in messages)
        await client.close()

    async def test_timeout_is_retried(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, json=OK_BODY)

        client = make_client(handler)
        assert await client._fetch(URL, {}) == OK_BODY
        await client.close()

    async def test_exhausted_raises_connection_error_with_attempts(self):
        client = make_client(lambda req: httpx.Response(502, text="Bad Gateway"), retries=3)
        with pytest.raises(OpenMeteoConnectionError, match="after 4 attempt"):
            await client._fetch(URL, {})
        assert client._sleep.await_count == 3
        await client.close()

    async def test_zero_retries(self):
        client = make_client(lambda req: httpx.Response(503), retries=0)
        with pytest.raises(OpenMeteoConnectionError, match="after 1 attempt"):
            await client._fetch(URL, {})
        client._sleep.assert_not_awaited()
        await client.close()

    async def test_api_error_body_not_retried(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(400, json={"error": True, "reason": "Cannot resolve historical data"})

        client = make_client(handler)
        with pytest.raises(OpenMeteoAPIError) as exc:
            await client._fetch(URL, {})
        assert exc.value.reason == "Cannot resolve historical data"
        assert calls["n"] == 1
        client._sleep.assert_not_awaited()
        await client.close()

    async def test_api_error_in_200_body(self):
        client = make_client(lambda req: httpx.Response(200, json={"error": True, "reason": "nope"}))
        with pytest.raises(OpenMeteoAPIError, match="nope"):
            await client._fetch(URL, {})
        await client.close()

    async def test_other_4xx_not_retried(self):
        client = make_client(lambda req: httpx.Response(404, text="not found"))
        with pytest.raises(OpenMeteoConnectionError, match="404"):
            await client._fetch(URL, {})
        client._sleep.assert_not_awaited()
        await client.close()

    async def test_429_retried(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "7"})
            return httpx.Response(200, json=OK_BODY)

        client = make_client(handler)
        assert await client._fetch(URL, {}) == OK_BODY
        client._sleep.assert_awaited_once_with(7.0)
        await client.close()

    async def test_invalid_json_on_200(self):
        client = make_client(lambda req: httpx.Response(200, text="<html>"))
        with pytest.raises(OpenMeteoConnectionError, match="Invalid JSON"):
            await client._fetch(URL, {})
        await client.close()

    async def test_non_transport_http_error_not_retried(self):
        def handler(request):
            raise httpx.TooManyRedirects("loop", request=request)

        client = make_client(handler)
        with pytest.raises(OpenMeteoConnectionError):
            await client._fetch(URL, {})
        client._sleep.assert_not_awaited()
        await client.close()

    async def test_params_are_sent(self):
        seen = {}

        def handler(request):
            seen["q"] = dict(request.url.params)
            return httpx.Response(200, json=OK_BODY)

        client = make_client(handler)
        await client._fetch(URL, {"latitude": 55.75, "hourly": "a,b"})
        assert seen["q"] == {"latitude": "55.75", "hourly": "a,b"}
        await client.close()


class TestRetryDelay:
    def test_exponential_with_jitter(self):
        client = OpenMeteoClient(cache=MemoryBackend(), retry_backoff=1.0)
        for attempt, (lo, hi) in enumerate([(0.75, 1.25), (1.5, 2.5), (3.0, 5.0)]):
            for _ in range(50):
                assert lo <= client._retry_delay(attempt, None) <= hi

    def test_capped(self):
        client = OpenMeteoClient(cache=MemoryBackend(), retry_backoff=10.0)
        assert client._retry_delay(10, None) <= MAX_RETRY_DELAY

    def test_retry_after_seconds_wins(self):
        client = OpenMeteoClient(cache=MemoryBackend())
        assert client._retry_delay(0, "12") == 12.0

    def test_retry_after_capped(self):
        client = OpenMeteoClient(cache=MemoryBackend())
        assert client._retry_delay(0, "3600") == MAX_RETRY_DELAY

    def test_retry_after_http_date_ignored(self):
        client = OpenMeteoClient(cache=MemoryBackend(), retry_backoff=1.0)
        assert 0.75 <= client._retry_delay(0, "Wed, 21 Oct 2015 07:28:00 GMT") <= 1.25
