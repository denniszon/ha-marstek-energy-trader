"""Read price and solar forecasts into time intervals (no Home Assistant imports)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
import math
from statistics import median
from typing import Any

# Attribute pairs that hold a price forecast, in order of preference:
# ANWB Energie and Nord Pool use raw_today/raw_tomorrow, ENTSO-e uses
# prices_today/prices_tomorrow and also a combined "prices" list.
PRICE_ATTRIBUTE_GROUPS = (
    ("raw_today", "raw_tomorrow"),
    ("prices_today", "prices_tomorrow"),
    ("prices",),
    ("forecast",),
)
START_KEYS = ("start", "from", "time", "datetime", "start_time", "period_start")
END_KEYS = ("end", "till", "to", "end_time", "period_end")
VALUE_KEYS = ("value", "price", "total", "all_in", "electricity_price")


@dataclass(frozen=True)
class Interval:
    """A value that applies from start (inclusive) to end (exclusive)."""

    start: datetime
    end: datetime
    value: float

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


class TimelineError(ValueError):
    """The data could not be read as a timeline."""


def _as_datetime(value: Any, tz: tzinfo) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value)
    else:
        raise TimelineError(f"Not a date/time: {value!r}")
    return result if result.tzinfo else result.replace(tzinfo=tz)


def _first(item: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _fill_missing_ends(
    rows: list[tuple[datetime, datetime | None, float]],
) -> list[Interval]:
    """Use the next start (or the usual spacing) when a source gives no end."""
    rows.sort(key=lambda row: row[0])
    steps = [b[0] - a[0] for a, b in zip(rows, rows[1:], strict=False)]
    usual = median(steps) if steps else timedelta(hours=1)
    intervals: list[Interval] = []
    for index, (start, end, value) in enumerate(rows):
        if end is None:
            following = rows[index + 1][0] if index + 1 < len(rows) else None
            end = following if following and following - start <= usual else None
            end = end or start + usual
        if end > start:
            intervals.append(Interval(start, end, value))
    return intervals


def parse_price_list(items: Iterable[Any], tz: tzinfo) -> list[Interval]:
    """Parse a list of {start, end, value}-like dicts; unknown hours are skipped."""
    rows: list[tuple[datetime, datetime | None, float]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise TimelineError("Price list items must be objects")
        start = _first(item, START_KEYS)
        value = _first(item, VALUE_KEYS)
        if start is None:
            raise TimelineError("Price list item without a start time")
        if value is None:
            continue
        end = _first(item, END_KEYS)
        rows.append(
            (
                _as_datetime(start, tz),
                _as_datetime(end, tz) if end is not None else None,
                float(value),
            )
        )
    return _fill_missing_ends(rows)


def parse_price_attributes(attributes: Mapping[str, Any], tz: tzinfo) -> list[Interval]:
    """Find and parse the price forecast in the attributes of a price sensor."""
    for group in PRICE_ATTRIBUTE_GROUPS:
        lists = [attributes[name] for name in group if name in attributes]
        if not lists or not all(isinstance(items, list) for items in lists):
            continue
        by_start: dict[datetime, Interval] = {}
        for items in lists:
            for interval in parse_price_list(items, tz):
                by_start[interval.start] = interval
        return sorted(by_start.values(), key=lambda interval: interval.start)
    raise TimelineError(
        "No price forecast found; expected one of the attributes "
        + ", ".join("/".join(group) for group in PRICE_ATTRIBUTE_GROUPS)
    )


def value_at(intervals: list[Interval], moment: datetime) -> float | None:
    """Value of the interval that contains the moment."""
    for interval in intervals:
        if interval.start <= moment < interval.end:
            return interval.value
    return None


def total_between(intervals: list[Interval], start: datetime, end: datetime) -> float:
    """Sum of interval values (amounts, e.g. kWh) overlapping start-end, pro rata."""
    total = 0.0
    for interval in intervals:
        overlap = (min(end, interval.end) - max(start, interval.start)).total_seconds()
        if overlap > 0:
            total += (
                interval.value
                * overlap
                / (interval.end - interval.start).total_seconds()
            )
    return total


def parse_forecast_solar(response: Mapping[str, Any], tz: tzinfo) -> list[Interval]:
    """Parse the forecast_solar.get_forecast response (hourly resolution) into kWh.

    `wh_period` holds the energy (Wh) produced in the hour starting at each key.
    """
    wh_period = response.get("wh_period") or {}
    return [
        Interval(start, start + timedelta(hours=1), float(wh) / 1000)
        for start, wh in sorted(
            (_as_datetime(key, tz), wh) for key, wh in wh_period.items()
        )
    ]


def parse_solcast(forecasts: Iterable[Any], tz: tzinfo) -> list[Interval]:
    """Parse Solcast detailedForecast/detailedHourly items into kWh per interval.

    `pv_estimate` is the average power in kW over the period.
    """
    rows = [
        (_as_datetime(item["period_start"], tz), None, float(item["pv_estimate"]))
        for item in forecasts
        if isinstance(item, Mapping) and item.get("pv_estimate") is not None
    ]
    return [
        Interval(interval.start, interval.end, interval.value * interval.hours)
        for interval in _fill_missing_ends(rows)
    ]


def _sun_elevation(moment: datetime, latitude: float, longitude: float) -> float:
    """Approximate solar elevation in degrees (accurate to about a degree)."""
    utc = moment.astimezone(UTC).timetuple()
    day = utc.tm_yday
    hour = utc.tm_hour + utc.tm_min / 60
    declination = math.radians(23.44) * math.sin(math.radians(360 / 365 * (day - 81)))
    solar_time = hour + longitude / 15
    hour_angle = math.radians(15 * (solar_time - 12))
    lat = math.radians(latitude)
    sin_elevation = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(
        declination
    ) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_elevation))))


def estimate_solar_from_weather(
    forecast: Iterable[Mapping[str, Any]],
    peak_kw: float,
    latitude: float,
    longitude: float,
    tz: tzinfo,
) -> list[Interval]:
    """Rough PV estimate per hour from cloud coverage, for when no forecast exists.

    Clear-sky output follows the sun's elevation; clouds reduce it with the
    Kasten-Czeplak relation: 1 - 0.75 * (cloud fraction) ^ 3.4.
    """
    intervals = []
    for item in forecast:
        start = _as_datetime(item["datetime"], tz)
        middle = start + timedelta(minutes=30)
        elevation = _sun_elevation(middle, latitude, longitude)
        if elevation <= 0:
            continue
        clouds = (item.get("cloud_coverage") or 0) / 100
        clear_sky = peak_kw * 0.85 * math.sin(math.radians(elevation))
        intervals.append(
            Interval(
                start,
                start + timedelta(hours=1),
                clear_sky * (1 - 0.75 * clouds**3.4),
            )
        )
    return intervals
