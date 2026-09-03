"""Key-value cache backends.

A backend stores opaque bytes under string keys with an optional TTL. All
domain logic (months, coverage, freshness) lives in :mod:`openmeteo.cache.store`
and is shared by every backend, so a backend only has to implement this small
contract:

- :class:`MemoryBackend` - process-local dict; the fallback when no
  writable disk is available.
- :class:`FileBackend` - one file per key under a directory; atomic writes,
  all I/O off the event loop.
- :class:`RedisBackend` - standalone or cluster Redis via ``redis.asyncio``;
  requires the optional ``redis`` extra.

Backends are opened lazily with :meth:`CacheBackend.open` so constructing one
touches neither disk nor network.

Example:
    >>> backend = backend_from_url("redis://localhost:6379/0?cluster=true")
    >>> await backend.open()
    >>> await backend.set("k", b"v", timedelta(hours=1))
    >>> await backend.get("k")
    b'v'
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..exceptions import OpenMeteoCacheError

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=dt_timezone.utc)


def _expires_at(ttl: Optional[timedelta]) -> Optional[datetime]:
    return _now() + ttl if ttl is not None else None


@runtime_checkable
class CacheBackend(Protocol):
    """Contract every cache backend implements.

    All methods are coroutines. ``open()`` and ``close()`` are idempotent.
    Operations before ``open()`` raise :class:`OpenMeteoCacheError` for
    backends that need initialization.
    """

    async def open(self) -> None:
        """Initialize the backend (create directory, connect, ...)."""

    async def get(self, key: str) -> Optional[bytes]:
        """Return the value or ``None`` if absent or expired."""

    async def set(self, key: str, value: bytes, ttl: Optional[timedelta]) -> None:
        """Store ``value`` under ``key``; ``ttl=None`` means no expiry."""

    async def delete(self, key: str) -> None:
        """Remove ``key``; a missing key is not an error."""

    async def scan(self, prefix: str) -> list[str]:
        """Return all keys starting with ``prefix``."""

    async def clear(self, prefix: str) -> int:
        """Delete all keys starting with ``prefix``; return how many."""

    async def ping(self) -> None:
        """Raise if the backend is not reachable."""

    async def close(self) -> None:
        """Release resources; safe to call more than once."""


class MemoryBackend:
    """In-process cache backend.

    Entries are kept in a dict together with their expiry time and dropped
    lazily on access. A single event loop drives all operations, so no
    locking is required.

    Example:
        >>> backend = MemoryBackend()
        >>> await backend.set("k", b"v", None)
        >>> await backend.get("k")
        b'v'
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[bytes, Optional[datetime]]] = {}

    async def open(self) -> None:
        return None

    async def get(self, key: str) -> Optional[bytes]:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and _now() >= expires_at:
            self._data.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: bytes, ttl: Optional[timedelta]) -> None:
        self._data[key] = (bytes(value), _expires_at(ttl))

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def scan(self, prefix: str) -> list[str]:
        return [k for k in list(self._data) if k.startswith(prefix) and await self.get(k) is not None]

    async def clear(self, prefix: str) -> int:
        keys = [k for k in self._data if k.startswith(prefix)]
        for k in keys:
            del self._data[k]
        return len(keys)

    async def ping(self) -> None:
        return None

    async def close(self) -> None:
        return None


class FileBackend:
    """File-per-key cache backend.

    Each key becomes one JSON file ``{"key", "expires_at", "encoding", "value"}``
    in ``root``; text values are stored as-is, other bytes as base64. Writes go through a temporary file and ``os.replace`` so a
    reader never observes a partially written entry. Every filesystem call
    runs in a worker thread via :func:`asyncio.to_thread`.

    ``open()`` creates the directory and performs a write probe; failure
    raises :class:`OpenMeteoCacheError` so the caller can decide whether to
    fall back to :class:`MemoryBackend`.

    Args:
        root: Directory that holds the cache files.

    Example:
        >>> backend = FileBackend(Path.home() / ".cache" / "openmeteo")
        >>> await backend.open()
    """

    _SUFFIX = ".json"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._opened = False

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _safe_name(key: str) -> str:
        return key.replace("/", "_").replace(":", "_").replace("\\", "_")

    def _path(self, key: str) -> Path:
        return self.root / (self._safe_name(key) + self._SUFFIX)

    def _require_open(self) -> None:
        if not self._opened:
            raise OpenMeteoCacheError("FileBackend is not opened; call open() first")

    def _probe_sync(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".probe-", dir=self.root)
            os.close(fd)
            os.unlink(tmp)
        except OSError as e:
            raise OpenMeteoCacheError(f"Cache directory {self.root} is not writable: {e}") from e

    def _read_sync(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            raise OpenMeteoCacheError(f"Failed to read cache file {path}: {e}") from e
        try:
            envelope = json.loads(raw)
            expires_at = envelope.get("expires_at")
            text = envelope["value"]
            encoding = envelope.get("encoding", "utf-8")
            if not isinstance(text, str) or encoding not in ("utf-8", "base64"):
                raise TypeError("malformed envelope")
            value = text.encode("utf-8") if encoding == "utf-8" else base64.b64decode(text)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning("Corrupt cache file %s (%s); treating as missing", path, e)
            self._unlink_sync(path)
            return None
        if expires_at is not None and _now() >= datetime.fromisoformat(expires_at):
            self._unlink_sync(path)
            return None
        return value

    @staticmethod
    def _unlink_sync(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            raise OpenMeteoCacheError(f"Failed to delete cache file {path}: {e}") from e

    def _scan_sync(self, prefix: str) -> list[tuple[str, Path]]:
        """Return (key, path) pairs whose stored key starts with prefix."""
        safe_prefix = self._safe_name(prefix)
        found: list[tuple[str, Path]] = []
        try:
            entries = list(self.root.iterdir())
        except OSError as e:
            raise OpenMeteoCacheError(f"Failed to list cache directory {self.root}: {e}") from e
        for path in entries:
            if not path.name.startswith(safe_prefix) or not path.name.endswith(self._SUFFIX):
                continue
            if path.name.startswith("."):
                continue
            key = self._key_from_path(path)
            if key is not None and key.startswith(prefix):
                found.append((key, path))
        return found

    def _key_from_path(self, path: Path) -> Optional[str]:
        """Recover the original key stored inside the envelope.

        The filename is lossy (``:`` and ``/`` both map to ``_``), so the key is
        also stored inside the file.
        """
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            key = envelope.get("key")
            return key if isinstance(key, str) else None
        except (OSError, ValueError, AttributeError):
            return None

    # -- contract --------------------------------------------------------------

    async def open(self) -> None:
        if self._opened:
            return
        await asyncio.to_thread(self._probe_sync)
        self._opened = True

    async def get(self, key: str) -> Optional[bytes]:
        self._require_open()
        return await asyncio.to_thread(self._read_sync, key)

    async def set(self, key: str, value: bytes, ttl: Optional[timedelta]) -> None:
        self._require_open()
        await asyncio.to_thread(self._write_sync, key, value, ttl)

    def _write_sync(self, key: str, value: bytes, ttl: Optional[timedelta]) -> None:
        path = self._path(key)
        expires = _expires_at(ttl)
        try:
            text, encoding = value.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            text, encoding = base64.b64encode(value).decode("ascii"), "base64"
        envelope = {
            "key": key,
            "expires_at": expires.isoformat() if expires else None,
            "encoding": encoding,
            "value": text,
        }
        try:
            fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(envelope, f, ensure_ascii=False)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            raise OpenMeteoCacheError(f"Failed to write cache file {path}: {e}") from e

    async def delete(self, key: str) -> None:
        self._require_open()
        await asyncio.to_thread(self._unlink_sync, self._path(key))

    async def scan(self, prefix: str) -> list[str]:
        self._require_open()
        pairs = await asyncio.to_thread(self._scan_sync, prefix)
        live: list[str] = []
        for key, _ in pairs:
            if await asyncio.to_thread(self._read_sync, key) is not None:
                live.append(key)
        return live

    async def clear(self, prefix: str) -> int:
        self._require_open()

        def _clear() -> int:
            pairs = self._scan_sync(prefix)
            for _, path in pairs:
                self._unlink_sync(path)
            return len(pairs)

        return await asyncio.to_thread(_clear)

    async def ping(self) -> None:
        await asyncio.to_thread(self._probe_sync)

    async def close(self) -> None:
        self._opened = False


class RedisBackend:
    """Redis cache backend (standalone or cluster).

    The client is created lazily in ``open()`` with ``Redis.from_url`` or
    ``RedisCluster.from_url`` depending on ``cluster``; both share the API
    used here (``get``, ``set(ex=...)``, ``delete``, ``scan_iter``, ``ping``,
    ``aclose``). For ``rediss://`` URLs ``ca_bundle`` is passed as
    ``ssl_ca_certs``; ``None`` means the system trust store.

    A pre-built ``client`` may be supplied by the application; in that case
    ``close()`` leaves it open because the application owns it.

    Requires the optional dependency: ``pip install "openmeteo-py-df[redis]"``.

    Args:
        url: Redis URL (``redis://`` or ``rediss://``).
        cluster: Use ``RedisCluster`` instead of ``Redis``.
        ca_bundle: Path to a CA bundle for TLS connections.
        client: Existing ``redis.asyncio`` client to use instead of creating one.

    Example:
        >>> backend = RedisBackend("rediss://cache:6379/0", cluster=True)
        >>> await backend.open()
    """

    def __init__(
        self,
        url: str,
        *,
        cluster: bool = False,
        ca_bundle: Optional[str] = None,
        client: Any = None,
    ) -> None:
        self.url = url
        self.cluster = cluster
        self.ca_bundle = ca_bundle
        self._client: Any = client
        self._owns_client = client is None
        self._opened = False

    def _require_client(self) -> Any:
        if not self._opened or self._client is None:
            raise OpenMeteoCacheError("RedisBackend is not opened; call open() first")
        return self._client

    async def open(self) -> None:
        if self._opened:
            return
        if self._client is None:
            try:
                import redis.asyncio as redis_asyncio
            except ImportError as e:
                raise OpenMeteoCacheError(
                    "Redis cache backend requires the 'redis' package: "
                    'pip install "openmeteo-py-df[redis]"'
                ) from e
            kwargs: dict[str, Any] = {}
            if self.url.startswith("rediss://") and self.ca_bundle is not None:
                kwargs["ssl_ca_certs"] = self.ca_bundle
            factory = redis_asyncio.RedisCluster if self.cluster else redis_asyncio.Redis
            self._client = factory.from_url(self.url, **kwargs)
        self._opened = True

    async def get(self, key: str) -> Optional[bytes]:
        value = await self._require_client().get(key)
        if value is None:
            return None
        return value if isinstance(value, bytes) else str(value).encode("utf-8")

    async def set(self, key: str, value: bytes, ttl: Optional[timedelta]) -> None:
        client = self._require_client()
        if ttl is None:
            await client.set(key, value)
        else:
            ms = max(1, int(ttl.total_seconds() * 1000))
            await client.set(key, value, px=ms)

    async def delete(self, key: str) -> None:
        await self._require_client().delete(key)

    async def scan(self, prefix: str) -> list[str]:
        client = self._require_client()
        keys: list[str] = []
        async for raw in client.scan_iter(match=prefix + "*"):
            keys.append(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
        return keys

    async def clear(self, prefix: str) -> int:
        client = self._require_client()
        keys = await self.scan(prefix)
        if not keys:
            return 0
        # Cluster mode forbids multi-key DELETE across slots; delete one by one.
        for key in keys:
            await client.delete(key)
        return len(keys)

    async def ping(self) -> None:
        await self._require_client().ping()

    async def close(self) -> None:
        client = self._client
        self._opened = False
        if client is None:
            return
        if self._owns_client:
            self._client = None
            await client.aclose()


_TRUE = {"1", "true", "yes", "on"}


def backend_from_url(url: str) -> CacheBackend:
    """Build a backend from a URL.

    Supported schemes:

    - ``memory://`` - :class:`MemoryBackend`
    - ``file:///absolute/path`` - :class:`FileBackend`
    - ``redis://...`` / ``rediss://...`` - :class:`RedisBackend`; the query
      parameters ``cluster`` (``true``/``1``) and ``ca_bundle`` are consumed
      here and removed from the URL handed to ``redis``.

    Args:
        url: Backend URL.

    Returns:
        An unopened backend.

    Raises:
        OpenMeteoCacheError: Unknown scheme or malformed URL.

    Example:
        >>> backend_from_url("rediss://h:6379/0?cluster=true&ca_bundle=/etc/ca.pem")
        RedisBackend(...)
    """
    if not url:
        raise OpenMeteoCacheError("Cache URL is empty")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()

    if scheme == "memory":
        return MemoryBackend()

    if scheme == "file":
        if parts.netloc not in ("", "localhost"):
            raise OpenMeteoCacheError(
                f"file:// cache URL must use an absolute path (file:///path), got {url!r}"
            )
        if not parts.path or not parts.path.startswith("/"):
            raise OpenMeteoCacheError(f"file:// cache URL must be absolute, got {url!r}")
        return FileBackend(Path(parts.path))

    if scheme in ("redis", "rediss"):
        query = parse_qsl(parts.query, keep_blank_values=True)
        cluster = False
        ca_bundle: Optional[str] = None
        rest: list[tuple[str, str]] = []
        for k, v in query:
            if k == "cluster":
                cluster = v.strip().lower() in _TRUE
            elif k == "ca_bundle":
                ca_bundle = v or None
            else:
                rest.append((k, v))
        clean = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(rest), parts.fragment))
        return RedisBackend(clean, cluster=cluster, ca_bundle=ca_bundle)

    raise OpenMeteoCacheError(
        f"Unsupported cache URL scheme {scheme!r}; expected memory://, file://, redis:// or rediss://"
    )


__all__ = ["CacheBackend", "MemoryBackend", "FileBackend", "RedisBackend", "backend_from_url"]
