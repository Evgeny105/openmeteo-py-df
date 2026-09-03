"""Cache key types.

Keys are immutable ``NamedTuple`` values so they are hashable, self-describing
and cannot be mutated by accident. ``as_str()`` renders the string used by
cache backends; ``prefix()`` gives the common prefix of all keys of that kind
for ``scan`` / ``clear``.

All keys share :data:`openmeteo.types.CACHE_KEY_PREFIX`, which carries the
cache entry format version: entries written by an incompatible version live
under a different prefix and are simply never seen.

Example:
    >>> HistoryKey(55.75, 37.62, TimeStep.HOURLY, "Europe/Moscow", "2025-12").as_str()
    'openmeteo:v2:hist:55.7500:37.6200:hourly:Europe/Moscow:2025-12'
    >>> ForecastKey(55.75, 37.62, TimeStep.DAILY, 7, "auto").as_str()
    'openmeteo:v2:fc:55.7500:37.6200:daily:7:auto'
"""

from typing import NamedTuple

from ..types import CACHE_KEY_PREFIX, TimeStep

_HIST = "hist"
_FC = "fc"


def _coord(value: float) -> str:
    """Render a coordinate with 4 decimals (about 11 m of precision)."""
    return f"{value:.4f}"


class HistoryKey(NamedTuple):
    """Key of one cached month of historical data.

    Attributes:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        step: Time step of the data.
        timezone: Timezone parameter of the request (e.g. ``"auto"``). It is
            part of the key because timestamps in the data are rendered in it.
        month: Month in ``YYYY-MM`` format.
    """

    lat: float
    lon: float
    step: TimeStep
    timezone: str
    month: str

    @staticmethod
    def prefix() -> str:
        """Common prefix of all history keys."""
        return f"{CACHE_KEY_PREFIX}:{_HIST}:"

    def as_str(self) -> str:
        """Render the backend key string."""
        return (
            f"{self.prefix()}{_coord(self.lat)}:{_coord(self.lon)}:"
            f"{self.step.value}:{self.timezone}:{self.month}"
        )


class ForecastKey(NamedTuple):
    """Key of one cached forecast response.

    Attributes:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        step: Time step of the data.
        days: Number of forecast days requested.
        timezone: Timezone parameter of the request.
    """

    lat: float
    lon: float
    step: TimeStep
    days: int
    timezone: str

    @staticmethod
    def prefix() -> str:
        """Common prefix of all forecast keys."""
        return f"{CACHE_KEY_PREFIX}:{_FC}:"

    def as_str(self) -> str:
        """Render the backend key string."""
        return (
            f"{self.prefix()}{_coord(self.lat)}:{_coord(self.lon)}:"
            f"{self.step.value}:{self.days}:{self.timezone}"
        )


__all__ = ["HistoryKey", "ForecastKey"]
