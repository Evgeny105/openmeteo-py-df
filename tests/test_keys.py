"""Tests for cache key construction."""

from openmeteo.cache.keys import ForecastKey, HistoryKey
from openmeteo.types import TimeStep


class TestHistoryKey:
    def test_as_str_format(self):
        key = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "Europe/Moscow", "2025-12")
        assert key.as_str() == "openmeteo:v2:hist:55.7500:37.6200:hourly:Europe/Moscow:2025-12"

    def test_coordinates_rounded_to_four_digits(self):
        a = HistoryKey(55.782298, 37.327136, TimeStep.DAILY, "auto", "2026-01")
        b = HistoryKey(55.7823, 37.3271, TimeStep.DAILY, "auto", "2026-01")
        assert a.as_str() == b.as_str()

    def test_negative_coordinates(self):
        key = HistoryKey(-33.865, 151.21, TimeStep.HOURLY, "auto", "2024-01")
        assert ":-33.8650:151.2100:" in key.as_str()

    def test_timezone_distinguishes_keys(self):
        a = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "Europe/Moscow", "2025-12")
        b = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "auto", "2025-12")
        assert a.as_str() != b.as_str()

    def test_step_distinguishes_keys(self):
        a = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "auto", "2025-12")
        b = HistoryKey(55.75, 37.62, TimeStep.DAILY, "auto", "2025-12")
        assert a.as_str() != b.as_str()

    def test_prefix_covers_all_history_keys(self):
        key = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "auto", "2025-12")
        assert key.as_str().startswith(HistoryKey.prefix())
        assert HistoryKey.prefix() == "openmeteo:v2:hist:"

    def test_hashable_and_immutable(self):
        key = HistoryKey(55.75, 37.62, TimeStep.HOURLY, "auto", "2025-12")
        assert {key: 1}[key] == 1
        try:
            key.lat = 1.0  # type: ignore[misc]
        except AttributeError:
            pass
        else:  # pragma: no cover
            raise AssertionError("NamedTuple must be immutable")


class TestForecastKey:
    def test_as_str_format(self):
        key = ForecastKey(55.75, 37.62, TimeStep.DAILY, 7, "auto")
        assert key.as_str() == "openmeteo:v2:fc:55.7500:37.6200:daily:7:auto"

    def test_days_distinguish_keys(self):
        a = ForecastKey(55.75, 37.62, TimeStep.DAILY, 3, "auto")
        b = ForecastKey(55.75, 37.62, TimeStep.DAILY, 7, "auto")
        assert a.as_str() != b.as_str()

    def test_timezone_distinguishes_keys(self):
        a = ForecastKey(55.75, 37.62, TimeStep.DAILY, 7, "auto")
        b = ForecastKey(55.75, 37.62, TimeStep.DAILY, 7, "Europe/Moscow")
        assert a.as_str() != b.as_str()

    def test_prefix(self):
        assert ForecastKey.prefix() == "openmeteo:v2:fc:"
        assert ForecastKey(1, 2, TimeStep.HOURLY, 1, "auto").as_str().startswith(
            ForecastKey.prefix()
        )

    def test_history_and_forecast_prefixes_disjoint(self):
        assert not HistoryKey.prefix().startswith(ForecastKey.prefix())
        assert not ForecastKey.prefix().startswith(HistoryKey.prefix())
