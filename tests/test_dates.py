from datetime import date

import pytest

from hansard.dates import MIN_START, SENTINEL_END, parse_end, parse_start


@pytest.mark.parametrize("value", [None, "", "  ", "null"])
def test_open_ended_end_dates_become_sentinel(value):
    assert parse_end(value) == SENTINEL_END == date(9999, 12, 31)


@pytest.mark.parametrize("value", [None, ""])
def test_missing_start_dates_become_min(value):
    assert parse_start(value) == MIN_START


def test_year_only_dates_span_the_whole_year():
    assert parse_start("1983") == date(1983, 1, 1)
    assert parse_end("1983") == date(1983, 12, 31)


def test_year_month_dates():
    assert parse_start("2024-02") == date(2024, 2, 1)
    assert parse_end("2024-02") == date(2024, 2, 29)


def test_full_dates_and_datetimes():
    assert parse_start("2016-07-13") == date(2016, 7, 13)
    assert parse_end("2016-07-13T10:00:00") == date(2016, 7, 13)
