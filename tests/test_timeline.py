"""Reading price and solar forecasts."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.marstek_energy_trader.timeline import (
    Interval,
    TimelineError,
    estimate_solar_from_weather,
    parse_forecast_solar,
    parse_price_attributes,
    parse_solcast,
    total_between,
    value_at,
)

TZ = ZoneInfo("Europe/Amsterdam")


def test_anwb_and_nordpool_format() -> None:
    attributes = {
        "raw_today": [
            {
                "start": "2026-10-25T14:00:00+01:00",
                "end": "2026-10-25T15:00:00+01:00",
                "value": 0.13,
            }
        ],
        "raw_tomorrow": [
            {
                "start": datetime(2026, 10, 26, 0, tzinfo=TZ),
                "end": datetime(2026, 10, 26, 1, tzinfo=TZ),
                "value": 0.21,
            },
            {
                "start": "2026-10-26T01:00:00+01:00",
                "end": "2026-10-26T02:00:00+01:00",
                "value": None,
            },
        ],
    }
    intervals = parse_price_attributes(attributes, TZ)
    assert [i.value for i in intervals] == [0.13, 0.21]
    assert intervals[0].hours == 1


def test_entsoe_format_without_end() -> None:
    attributes = {
        "prices_today": [
            {"time": "2026-10-26 00:00:00+01:00", "price": 0.1},
            {"time": "2026-10-26 00:15:00+01:00", "price": 0.2},
        ],
        "prices_tomorrow": [],
    }
    intervals = parse_price_attributes(attributes, TZ)
    assert [i.end - i.start for i in intervals] == [timedelta(minutes=15)] * 2


def test_unknown_format() -> None:
    with pytest.raises(TimelineError):
        parse_price_attributes({"today": [0.1, 0.2]}, TZ)


def test_value_and_total_between() -> None:
    start = datetime(2026, 10, 26, 10, tzinfo=TZ)
    intervals = [Interval(start, start + timedelta(hours=1), 2.0)]
    assert value_at(intervals, start + timedelta(minutes=30)) == 2.0
    assert value_at(intervals, start + timedelta(hours=1)) is None
    half = total_between(
        intervals, start + timedelta(minutes=30), start + timedelta(hours=2)
    )
    assert half == pytest.approx(1.0)


def test_forecast_solar_response() -> None:
    response = {
        "watts": {"2026-10-26T12:00:00+01:00": 1500},
        "wh_period": {"2026-10-26T12:00:00+01:00": 1500},
    }
    intervals = parse_forecast_solar(response, TZ)
    assert intervals[0].value == 1.5
    assert intervals[0].hours == 1


def test_solcast_kw_to_kwh() -> None:
    forecasts = [
        {"period_start": "2026-10-26T12:00:00+01:00", "pv_estimate": 2.0},
        {"period_start": "2026-10-26T12:30:00+01:00", "pv_estimate": 1.0},
    ]
    intervals = parse_solcast(forecasts, TZ)
    assert [i.value for i in intervals] == [1.0, 0.5]


def test_weather_estimate_follows_sun_and_clouds() -> None:
    noon = datetime(2026, 6, 21, 13, tzinfo=TZ)
    forecast = [
        {"datetime": noon.isoformat(), "cloud_coverage": 0},
        {"datetime": (noon + timedelta(hours=1)).isoformat(), "cloud_coverage": 100},
        {"datetime": noon.replace(hour=1).isoformat(), "cloud_coverage": 0},
    ]
    intervals = estimate_solar_from_weather(forecast, 4.0, 52.1, 5.1, TZ)
    # The night hour has no sun and is left out.
    assert len(intervals) == 2
    assert intervals[0].value > 2.5
    assert intervals[1].value < intervals[0].value / 3
