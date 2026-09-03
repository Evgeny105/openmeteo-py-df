"""Tests for month arithmetic and multi-month assembly."""

from datetime import date

import pytest

from openmeteo.assembly import (
    align_variables,
    assemble,
    check_invariant,
    iter_months,
    month_end,
    parse_date,
    trim_to_range,
)
from openmeteo.exceptions import OpenMeteoDataError
from openmeteo.types import TimeStep

TODAY = date(2026, 9, 3)


class TestMonthEnd:
    @pytest.mark.parametrize(
        "month,last",
        [(1, 31), (2, 29), (3, 31), (4, 30), (5, 31), (6, 30), (7, 31), (8, 31), (9, 30), (10, 31), (11, 30), (12, 31)],
    )
    def test_every_month_of_leap_year(self, month, last):
        assert month_end(date(2024, month, 1)) == date(2024, month, last)

    def test_february_non_leap(self):
        assert month_end(date(2025, 2, 10)) == date(2025, 2, 28)

    def test_december_stays_in_same_year(self):
        assert month_end(date(2025, 12, 1)) == date(2025, 12, 31)


class TestIterMonths:
    def test_december_request_is_one_month(self):
        months = list(iter_months(date(2025, 12, 1), date(2025, 12, 31), TODAY))
        assert months == [("2025-12", date(2025, 12, 1), date(2025, 12, 31))]

    def test_range_spanning_year_boundary(self):
        months = list(iter_months(date(2025, 11, 15), date(2026, 1, 10), TODAY))
        assert [m.key for m in months] == ["2025-11", "2025-12", "2026-01"]
        assert months[1].start == date(2025, 12, 1)
        assert months[1].end == date(2025, 12, 31)

    def test_every_month_end_in_same_month(self):
        for m in iter_months(date(2024, 1, 1), date(2025, 12, 31), TODAY):
            assert m.end.month == m.start.month and m.end.year == m.start.year

    def test_current_month_clipped_to_today(self):
        months = list(iter_months(date(2026, 8, 20), date(2026, 9, 3), TODAY))
        assert months[-1] == ("2026-09", date(2026, 9, 1), TODAY)

    def test_full_months_even_for_partial_request(self):
        months = list(iter_months(date(2024, 1, 15), date(2024, 1, 20), TODAY))
        assert months == [("2024-01", date(2024, 1, 1), date(2024, 1, 31))]

    def test_start_after_end_raises(self):
        with pytest.raises(OpenMeteoDataError):
            list(iter_months(date(2024, 2, 1), date(2024, 1, 1), TODAY))

    def test_chronological_order(self):
        keys = [m.key for m in iter_months(date(2023, 1, 1), date(2024, 12, 31), TODAY)]
        assert keys == sorted(keys) and len(keys) == 24


class TestParseDate:
    def test_date_only(self):
        assert parse_date("2024-01-15") == date(2024, 1, 15)

    def test_datetime(self):
        assert parse_date("2024-01-15T12:00") == date(2024, 1, 15)


def month(times, **series):
    block = {"time": list(times)}
    block.update(series)
    return {
        "latitude": 55.75,
        "longitude": 37.62,
        "elevation": 130.0,
        "generationtime_ms": 0.1,
        "utc_offset_seconds": 0,
        "timezone": "GMT",
        "timezone_abbreviation": "GMT",
        "hourly_units": {"time": "iso8601", **{k: "unit" for k in series}},
        "hourly": block,
    }


class TestAlignVariables:
    def test_missing_variable_padded(self):
        block = {"time": ["a", "b"], "t": [1, 2]}
        out = align_variables(block, ["t", "v"])
        assert out["v"] == [None, None]
        assert out["t"] == [1, 2]

    def test_wrong_length_replaced(self):
        out = align_variables({"time": ["a", "b"], "t": [1]}, ["t"])
        assert out["t"] == [None, None]

    def test_unrequested_left_alone(self):
        out = align_variables({"time": ["a"], "x": [9]}, ["t"])
        assert out["x"] == [9]

    def test_input_not_mutated(self):
        block = {"time": ["a"]}
        align_variables(block, ["t"])
        assert "t" not in block


class TestCheckInvariant:
    def test_ok(self):
        check_invariant({"time": [1, 2], "t": [1, 2]})

    def test_mismatch_raises_with_details(self):
        with pytest.raises(OpenMeteoDataError, match="t has 1"):
            check_invariant({"time": [1, 2], "t": [1]})

    def test_non_list_series_raises(self):
        with pytest.raises(OpenMeteoDataError):
            check_invariant({"time": [1], "t": None})

    def test_no_time_raises(self):
        with pytest.raises(OpenMeteoDataError):
            check_invariant({"t": [1]})


class TestAssemble:
    def test_chronological_concatenation(self):
        a = month(["2024-01-01T00:00", "2024-01-01T01:00"], temperature_2m=[1.0, 2.0])
        b = month(["2024-02-01T00:00"], temperature_2m=[3.0])
        out = assemble([a, b], TimeStep.HOURLY, ["temperature_2m"])
        assert out["hourly"]["time"] == ["2024-01-01T00:00", "2024-01-01T01:00", "2024-02-01T00:00"]
        assert out["hourly"]["temperature_2m"] == [1.0, 2.0, 3.0]

    def test_different_variable_sets_are_padded(self):
        full = month([f"2024-01-01T{h:02d}:00" for h in range(3)], temperature_2m=[1, 2, 3], rain=[0, 0, 1])
        partial = month(["2024-02-01T00:00", "2024-02-01T01:00"], temperature_2m=[4, 5])
        out = assemble([full, partial], TimeStep.HOURLY, ["temperature_2m", "rain"])
        assert len(out["hourly"]["time"]) == 5
        assert out["hourly"]["temperature_2m"] == [1, 2, 3, 4, 5]
        assert out["hourly"]["rain"] == [0, 0, 1, None, None]

    def test_partial_month_first(self):
        partial = month(["2024-01-01T00:00"], temperature_2m=[1])
        full = month(["2024-02-01T00:00"], temperature_2m=[2], rain=[0])
        out = assemble([partial, full], TimeStep.HOURLY, ["temperature_2m", "rain"])
        assert out["hourly"]["rain"] == [None, 0]

    def test_duplicate_timestamps_first_wins(self):
        a = month(["2024-01-31T23:00"], temperature_2m=[1])
        b = month(["2024-01-31T23:00", "2024-02-01T00:00"], temperature_2m=[9, 2])
        out = assemble([a, b], TimeStep.HOURLY, ["temperature_2m"])
        assert out["hourly"]["time"] == ["2024-01-31T23:00", "2024-02-01T00:00"]
        assert out["hourly"]["temperature_2m"] == [1, 2]

    def test_unrequested_variables_dropped(self):
        a = month(["2024-01-01T00:00"], temperature_2m=[1], rain=[0])
        out = assemble([a], TimeStep.HOURLY, ["temperature_2m"])
        assert "rain" not in out["hourly"]

    def test_metadata_from_first_month(self):
        a = month(["2024-01-01T00:00"], temperature_2m=[1])
        b = month(["2024-02-01T00:00"], temperature_2m=[2])
        b["elevation"] = 999.0
        out = assemble([a, b], TimeStep.HOURLY, ["temperature_2m"])
        assert out["elevation"] == 130.0

    def test_units_merged_first_wins(self):
        a = month(["2024-01-01T00:00"], temperature_2m=[1])
        b = month(["2024-02-01T00:00"], temperature_2m=[2], rain=[0])
        b["hourly_units"]["temperature_2m"] = "other"
        out = assemble([a, b], TimeStep.HOURLY, ["temperature_2m", "rain"])
        assert out["hourly_units"]["temperature_2m"] == "unit"
        assert out["hourly_units"]["rain"] == "unit"

    def test_daily_step(self):
        a = {"latitude": 1, "daily_units": {"time": "iso8601"}, "daily": {"time": ["2024-01-01"], "temperature_2m_max": [5.0]}}
        out = assemble([a], TimeStep.DAILY, ["temperature_2m_max"])
        assert out["daily"]["temperature_2m_max"] == [5.0]

    def test_empty_months_raises(self):
        with pytest.raises(OpenMeteoDataError):
            assemble([], TimeStep.HOURLY, ["temperature_2m"])

    def test_wrong_length_series_in_month_padded_not_propagated(self):
        a = month(["2024-01-01T00:00", "2024-01-01T01:00"], temperature_2m=[1])
        out = assemble([a], TimeStep.HOURLY, ["temperature_2m"])
        assert out["hourly"]["temperature_2m"] == [None, None]

    def test_invariant_checked_on_output(self, monkeypatch):
        import openmeteo.assembly as mod

        calls = []

        def spy(block, context=""):
            calls.append((dict(block), context))
            raise OpenMeteoDataError("boom")

        monkeypatch.setattr(mod, "check_invariant", spy)
        a = month(["2024-01-01T00:00", "2024-01-01T01:00"], temperature_2m=[1, 2])
        with pytest.raises(OpenMeteoDataError, match="boom"):
            assemble([a], TimeStep.HOURLY, ["temperature_2m"])
        assert calls and calls[0][0]["time"] == ["2024-01-01T00:00", "2024-01-01T01:00"]


class TestTrimToRange:
    def test_daily(self):
        data = {"daily": {"time": ["2024-01-01", "2024-01-02", "2024-01-03"], "t": [1, 2, 3]}}
        out = trim_to_range(data, date(2024, 1, 2), date(2024, 1, 3), TimeStep.DAILY)
        assert out["daily"]["time"] == ["2024-01-02", "2024-01-03"]
        assert out["daily"]["t"] == [2, 3]

    def test_hourly(self):
        data = {"hourly": {"time": ["2024-01-01T00:00", "2024-01-05T00:00"], "t": [1, 2]}}
        out = trim_to_range(data, date(2024, 1, 5), date(2024, 1, 10), TimeStep.HOURLY)
        assert out["hourly"]["time"] == ["2024-01-05T00:00"]

    def test_empty(self):
        out = trim_to_range({"hourly": {"time": []}}, date(2024, 1, 1), date(2024, 1, 2), TimeStep.HOURLY)
        assert out["hourly"]["time"] == []

    def test_non_list_fields_preserved(self):
        data = {"hourly": {"time": ["2024-01-01T00:00"], "t": [1], "meta": "x"}, "latitude": 1}
        out = trim_to_range(data, date(2024, 1, 1), date(2024, 1, 1), TimeStep.HOURLY)
        assert out["hourly"]["meta"] == "x" and out["latitude"] == 1
