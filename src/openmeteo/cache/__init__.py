"""Caching layer: key-value backends and the domain stores built on them.

Backends (:mod:`openmeteo.cache.backends`) store bytes under string keys with
an optional TTL. Stores (:mod:`openmeteo.cache.store`) implement the domain
rules for cached historical months and forecasts on top of any backend.

Example:
    >>> from openmeteo.cache import RedisBackend
    >>> async with OpenMeteoClient(cache=RedisBackend("redis://cache:6379/0")) as client:
    ...     data = await client.get_historical(55.75, 37.62, start, end)
"""

from .backends import (
    CacheBackend,
    FileBackend,
    MemoryBackend,
    RedisBackend,
    backend_from_url,
)
from .keys import ForecastKey, HistoryKey
from .store import ForecastStore, HistoricalStore

__all__ = [
    "CacheBackend",
    "MemoryBackend",
    "FileBackend",
    "RedisBackend",
    "backend_from_url",
    "HistoryKey",
    "ForecastKey",
    "HistoricalStore",
    "ForecastStore",
]
