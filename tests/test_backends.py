"""Contract tests for cache backends and the URL factory."""

import asyncio
import builtins
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openmeteo.cache.backends import (
    CacheBackend,
    FileBackend,
    MemoryBackend,
    RedisBackend,
    backend_from_url,
)
from openmeteo.exceptions import OpenMeteoCacheError


@pytest.fixture(params=["memory", "file", "redis"])
async def backend(request, tmp_path):
    """Open a backend of each kind; the contract below runs against all."""
    if request.param == "memory":
        b: CacheBackend = MemoryBackend()
    elif request.param == "file":
        b = FileBackend(tmp_path / "cache")
    else:
        import fakeredis.aioredis

        b = RedisBackend("redis://localhost:6379/0", client=fakeredis.aioredis.FakeRedis())
    await b.open()
    try:
        yield b
    finally:
        await b.close()


class TestBackendContract:
    async def test_set_then_get(self, backend):
        await backend.set("k", b"v", None)
        assert await backend.get("k") == b"v"

    async def test_get_missing_returns_none(self, backend):
        assert await backend.get("missing") is None

    async def test_delete(self, backend):
        await backend.set("k", b"v", None)
        await backend.delete("k")
        assert await backend.get("k") is None

    async def test_delete_missing_is_noop(self, backend):
        await backend.delete("nothing")

    async def test_overwrite(self, backend):
        await backend.set("k", b"1", None)
        await backend.set("k", b"2", None)
        assert await backend.get("k") == b"2"

    async def test_ttl_expires(self, backend):
        await backend.set("k", b"v", timedelta(milliseconds=50))
        assert await backend.get("k") == b"v"
        await asyncio.sleep(0.2)
        assert await backend.get("k") is None

    async def test_scan_and_clear_by_prefix(self, backend):
        await backend.set("a:1", b"1", None)
        await backend.set("a:2", b"2", None)
        await backend.set("b:1", b"3", None)
        assert sorted(await backend.scan("a:")) == ["a:1", "a:2"]
        assert await backend.clear("a:") == 2
        assert await backend.get("a:1") is None
        assert await backend.get("a:2") is None
        assert await backend.get("b:1") == b"3"

    async def test_clear_empty_prefix_returns_zero(self, backend):
        assert await backend.clear("nope:") == 0

    async def test_ping(self, backend):
        await backend.ping()

    async def test_open_idempotent(self, backend):
        await backend.open()
        await backend.set("k", b"v", None)
        assert await backend.get("k") == b"v"

    async def test_close_idempotent(self, backend):
        await backend.close()
        await backend.close()

    async def test_binary_value_roundtrip(self, backend):
        payload = bytes(range(256))
        await backend.set("bin", payload, None)
        assert await backend.get("bin") == payload

    async def test_key_with_special_characters(self, backend):
        key = "openmeteo:v2:hist:55.7500:37.6200:hourly:Europe/Moscow:2025-12"
        await backend.set(key, b"v", None)
        assert await backend.get(key) == b"v"
        assert await backend.scan("openmeteo:v2:hist:") == [key]


class TestMemoryBackend:
    async def test_instances_are_isolated(self):
        a, b = MemoryBackend(), MemoryBackend()
        await a.open()
        await b.open()
        await a.set("k", b"v", None)
        assert await b.get("k") is None


class TestFileBackend:
    async def test_open_creates_directory(self, tmp_path):
        root = tmp_path / "nested" / "cache"
        b = FileBackend(root)
        await b.open()
        assert root.is_dir()

    async def test_open_unwritable_raises(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        b = FileBackend(blocker / "cache")  # parent is a file, mkdir fails
        with pytest.raises(OpenMeteoCacheError):
            await b.open()

    async def test_open_readonly_dir_raises(self, tmp_path):
        if os.geteuid() == 0:  # pragma: no cover
            pytest.skip("root ignores permission bits")
        root = tmp_path / "ro"
        root.mkdir()
        root.chmod(0o500)
        try:
            with pytest.raises(OpenMeteoCacheError):
                await FileBackend(root).open()
        finally:
            root.chmod(0o700)

    async def test_data_survives_reopen(self, tmp_path):
        root = tmp_path / "cache"
        first = FileBackend(root)
        await first.open()
        await first.set("k", b"v", None)
        await first.close()
        second = FileBackend(root)
        await second.open()
        assert await second.get("k") == b"v"

    async def test_no_nested_directories_for_slash_keys(self, tmp_path):
        root = tmp_path / "cache"
        b = FileBackend(root)
        await b.open()
        await b.set("a/b:c", b"v", None)
        assert all(p.is_file() for p in root.iterdir())
        assert await b.get("a/b:c") == b"v"

    async def test_corrupt_file_is_treated_as_missing(self, tmp_path):
        root = tmp_path / "cache"
        b = FileBackend(root)
        await b.open()
        await b.set("k", b"v", None)
        for p in root.iterdir():
            p.write_text("not json")
        assert await b.get("k") is None

    async def test_write_is_atomic_no_temp_files_left(self, tmp_path):
        root = tmp_path / "cache"
        b = FileBackend(root)
        await b.open()
        await b.set("k", b"v", None)
        names = [p.name for p in root.iterdir()]
        assert len(names) == 1
        assert not any(n.startswith(".") or n.endswith(".tmp") for n in names)

    async def test_operations_before_open_raise(self, tmp_path):
        b = FileBackend(tmp_path / "cache")
        with pytest.raises(OpenMeteoCacheError):
            await b.get("k")


class TestRedisBackend:
    async def test_missing_redis_package(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "redis" or name.startswith("redis."):
                raise ImportError("No module named 'redis'")
            return real_import(name, *args, **kwargs)

        b = RedisBackend("redis://localhost/0")
        with patch.object(builtins, "__import__", side_effect=fake_import):
            with pytest.raises(OpenMeteoCacheError, match=r"openmeteo-py-df\[redis\]"):
                await b.open()

    async def test_selects_cluster_client(self):
        with patch("redis.asyncio.RedisCluster") as cluster, patch("redis.asyncio.Redis") as single:
            cluster.from_url.return_value = MagicMock(aclose=AsyncMock())
            b = RedisBackend("redis://h:6379/0", cluster=True)
            await b.open()
            cluster.from_url.assert_called_once_with("redis://h:6379/0")
            single.from_url.assert_not_called()

    async def test_selects_single_client(self):
        with patch("redis.asyncio.RedisCluster") as cluster, patch("redis.asyncio.Redis") as single:
            single.from_url.return_value = MagicMock(aclose=AsyncMock())
            b = RedisBackend("redis://h:6379/0")
            await b.open()
            single.from_url.assert_called_once_with("redis://h:6379/0")
            cluster.from_url.assert_not_called()

    async def test_tls_passes_ca_bundle(self):
        with patch("redis.asyncio.Redis") as single:
            single.from_url.return_value = MagicMock(aclose=AsyncMock())
            b = RedisBackend("rediss://h:6379/0", ca_bundle="/etc/ca.pem")
            await b.open()
            single.from_url.assert_called_once_with("rediss://h:6379/0", ssl_ca_certs="/etc/ca.pem")

    async def test_tls_without_ca_bundle_uses_system_store(self):
        with patch("redis.asyncio.Redis") as single:
            single.from_url.return_value = MagicMock(aclose=AsyncMock())
            b = RedisBackend("rediss://h:6379/0")
            await b.open()
            single.from_url.assert_called_once_with("rediss://h:6379/0")

    async def test_external_client_not_closed(self):
        import fakeredis.aioredis

        client = fakeredis.aioredis.FakeRedis()
        client.aclose = AsyncMock()  # type: ignore[method-assign]
        b = RedisBackend("redis://ignored", client=client)
        await b.open()
        await b.close()
        client.aclose.assert_not_called()

    async def test_own_client_closed(self):
        fake = MagicMock(aclose=AsyncMock())
        with patch("redis.asyncio.Redis") as single:
            single.from_url.return_value = fake
            b = RedisBackend("redis://h/0")
            await b.open()
            await b.close()
        fake.aclose.assert_awaited_once()

    async def test_operations_before_open_raise(self):
        b = RedisBackend("redis://h/0")
        with pytest.raises(OpenMeteoCacheError):
            await b.get("k")


class TestBackendFromUrl:
    def test_memory(self):
        assert isinstance(backend_from_url("memory://"), MemoryBackend)

    def test_file(self):
        b = backend_from_url("file:///tmp/openmeteo")
        assert isinstance(b, FileBackend)
        assert b.root == Path("/tmp/openmeteo")

    def test_file_relative_path_rejected(self):
        with pytest.raises(OpenMeteoCacheError):
            backend_from_url("file://relative/path")

    def test_redis_plain(self):
        b = backend_from_url("redis://:pw@h:6379/0")
        assert isinstance(b, RedisBackend)
        assert b.url == "redis://:pw@h:6379/0"
        assert b.cluster is False
        assert b.ca_bundle is None

    def test_redis_with_params_stripped(self):
        b = backend_from_url("rediss://h:6379/0?cluster=true&ca_bundle=/etc/ca.pem")
        assert isinstance(b, RedisBackend)
        assert b.cluster is True
        assert b.ca_bundle == "/etc/ca.pem"
        assert b.url == "rediss://h:6379/0"

    def test_redis_cluster_numeric_flag_and_other_params_kept(self):
        b = backend_from_url("redis://h:6379/0?cluster=1&socket_timeout=5")
        assert b.cluster is True
        assert b.url == "redis://h:6379/0?socket_timeout=5"

    def test_unknown_scheme(self):
        with pytest.raises(OpenMeteoCacheError):
            backend_from_url("s3://bucket")

    def test_empty_url(self):
        with pytest.raises(OpenMeteoCacheError):
            backend_from_url("")
