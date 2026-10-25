"""Collect prices, solar forecast, consumption and battery state for the planner."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_LOAD,
    CONF_CAPACITY,
    CONF_CONSUMPTION_SENSOR,
    CONF_EFFICIENCY,
    CONF_FEED_IN_COST,
    CONF_FEED_IN_FIXED,
    CONF_FEED_IN_MODE,
    CONF_FEED_IN_OFFSET,
    CONF_FEED_IN_SENSOR,
    CONF_FORECAST_SOLAR_ENTRY,
    CONF_PRICE_SENSOR,
    CONF_PV_POWER,
    CONF_SOLAR_SOURCE,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    CONF_WEATHER,
    CONSUMPTION_PROFILE_MAX_AGE,
    DEFAULT_BASE_LOAD,
    DEFAULT_EFFICIENCY,
    FEED_IN_FIXED,
    FEED_IN_OFFSET,
    FEED_IN_SENSOR,
    KEY_CAPACITY,
    KEY_SOC,
    MARSTEK_MAX_POWER,
    SOLAR_FORECAST_SOLAR,
    SOLAR_SOLCAST,
)
from .marstek import MarstekEntities
from .planner import Battery, Slot
from .timeline import (
    Interval,
    TimelineError,
    estimate_solar_from_weather,
    parse_forecast_solar,
    parse_price_attributes,
    parse_solcast,
    total_between,
    value_at,
)

_LOGGER = logging.getLogger(__name__)

SOLAR_MAX_AGE = timedelta(minutes=30)
# Fallback when the battery does not report its capacity (Venus E: 5.12 kWh).
DEFAULT_CAPACITY = 5.12
UNAVAILABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN)


class InputError(Exception):
    """Information needed for a plan is missing; the message explains what."""


@dataclass(frozen=True)
class Override:
    """A manual action for a time window."""

    start: datetime
    end: datetime
    action: str
    power_w: float | None = None


@dataclass(frozen=True)
class PlanSettings:
    """The user-adjustable settings the inputs depend on."""

    min_soc: float
    max_soc: float
    max_charge_power: float
    max_discharge_power: float
    forecast_trust: float


def read_price_sensor(hass: HomeAssistant, entity_id: str) -> list[Interval]:
    """Read the price forecast from the attributes of a price sensor."""
    state = hass.states.get(entity_id)
    if state is None or (state.state in UNAVAILABLE and not state.attributes):
        raise InputError(f"Price sensor {entity_id} is not available")
    try:
        intervals = parse_price_attributes(
            state.attributes, dt_util.get_default_time_zone()
        )
    except (TimelineError, KeyError, TypeError, ValueError) as err:
        raise InputError(f"Cannot read prices from {entity_id}: {err}") from err
    unit = str(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, "")).lower()
    divisor = 1
    if "ct" in unit or "cent" in unit:
        divisor = 100
    if "mwh" in unit:
        divisor *= 1000
    if divisor != 1:
        intervals = [Interval(i.start, i.end, i.value / divisor) for i in intervals]
    return intervals


class TraderInputs:
    """Builds the planner input from Home Assistant state."""

    def __init__(
        self, hass: HomeAssistant, options: dict[str, Any], marstek: MarstekEntities
    ) -> None:
        self.hass = hass
        self.options = options
        self.marstek = marstek
        self.prices: list[Interval] = []
        self.feed_in_prices: list[Interval] = []
        self._solar: list[Interval] = []
        self._solar_fetched: datetime | None = None
        self._profile: dict[int, float] | None = None
        self._profile_fetched: datetime | None = None
        self.solar_source_used: str | None = None

    @property
    def efficiency(self) -> float:
        return self.options.get(CONF_EFFICIENCY, DEFAULT_EFFICIENCY) / 100

    def sell_price(self, start: datetime, buy: float) -> float:
        """Feed-in price for a moment, minus the feed-in costs."""
        mode = self.options.get(CONF_FEED_IN_MODE)
        if mode == FEED_IN_FIXED:
            price = self.options.get(CONF_FEED_IN_FIXED, 0.0)
        elif mode == FEED_IN_OFFSET:
            price = buy + self.options.get(CONF_FEED_IN_OFFSET, 0.0)
        elif mode == FEED_IN_SENSOR:
            found = value_at(self.feed_in_prices, start)
            price = buy if found is None else found
        else:
            price = buy
        return price - self.options.get(CONF_FEED_IN_COST, 0.0)

    def current_prices(self, now: datetime) -> tuple[float, float] | None:
        """Buy and sell price right now, from the last read forecast."""
        buy = value_at(self.prices, now)
        if buy is None:
            return None
        return buy, self.sell_price(now, buy)

    def soc(self) -> float | None:
        """Battery level of the Marstek in %."""
        state = self.hass.states.get(self.marstek.entity_ids[KEY_SOC])
        try:
            return float(state.state) if state else None
        except ValueError:
            return None

    def battery(self, settings: PlanSettings) -> Battery:
        soc = self.soc()
        if soc is None:
            raise InputError("The Marstek battery level (SOC) is not available")
        capacity = self.options.get(CONF_CAPACITY) or self._capacity_from_sensor()
        return Battery(
            capacity_kwh=capacity,
            soc_kwh=capacity * soc / 100,
            min_soc_kwh=capacity * settings.min_soc / 100,
            max_soc_kwh=capacity * settings.max_soc / 100,
            max_charge_w=min(settings.max_charge_power, MARSTEK_MAX_POWER),
            max_discharge_w=min(settings.max_discharge_power, MARSTEK_MAX_POWER),
            efficiency=self.efficiency,
        )

    def _capacity_from_sensor(self) -> float:
        entity_id = self.marstek.entity_ids.get(KEY_CAPACITY)
        state = self.hass.states.get(entity_id) if entity_id else None
        try:
            capacity = float(state.state) if state else 0.0
        except ValueError:
            capacity = 0.0
        return capacity if capacity > 0 else DEFAULT_CAPACITY

    async def async_build_slots(
        self, now: datetime, settings: PlanSettings, overrides: list[Override]
    ) -> list[Slot]:
        """Time slots from now until the last known price."""
        self.prices = read_price_sensor(self.hass, self.options[CONF_PRICE_SENSOR])
        if self.options.get(CONF_FEED_IN_MODE) == FEED_IN_SENSOR:
            self.feed_in_prices = read_price_sensor(
                self.hass, self.options[CONF_FEED_IN_SENSOR]
            )
        upcoming = [interval for interval in self.prices if interval.end > now]
        if not upcoming or upcoming[0].start > now:
            raise InputError("The price sensor has no price for the current time")

        solar = await self._async_solar(now)
        trust = settings.forecast_trust / 100
        profile = await self._async_consumption_profile(now)

        slots: list[Slot] = []
        previous_end = None
        for interval in upcoming:
            if previous_end is not None and interval.start != previous_end:
                break  # Stop at a gap in the price data.
            start = max(interval.start, now)
            previous_end = interval.end
            if (interval.end - start).total_seconds() < 60:
                continue
            override = next(
                (o for o in overrides if o.start <= start < o.end),
                None,
            )
            slots.append(
                Slot(
                    start=start,
                    end=interval.end,
                    buy=interval.value,
                    sell=self.sell_price(start, interval.value),
                    solar_kwh=total_between(solar, start, interval.end) * trust,
                    load_kwh=self._load(profile, start, interval.end),
                    forced_action=override.action if override else None,
                    forced_power_w=override.power_w if override else None,
                )
            )
        return slots

    def _load(
        self, profile: dict[int, float] | None, start: datetime, end: datetime
    ) -> float:
        hours = (end - start).total_seconds() / 3600
        if profile:
            hour = dt_util.as_local(start).hour
            if hour in profile:
                return profile[hour] * hours
        return self.options.get(CONF_BASE_LOAD, DEFAULT_BASE_LOAD) / 1000 * hours

    async def _async_solar(self, now: datetime) -> list[Interval]:
        if self._solar_fetched and now - self._solar_fetched < SOLAR_MAX_AGE:
            return self._solar
        tz = dt_util.get_default_time_zone()
        source = self.options.get(CONF_SOLAR_SOURCE)
        solar: list[Interval] = []
        used = None
        try:
            if source == SOLAR_FORECAST_SOLAR:
                response = await self.hass.services.async_call(
                    "forecast_solar",
                    "get_forecast",
                    {
                        "config_entry": self.options[CONF_FORECAST_SOLAR_ENTRY],
                        "resolution": "hourly",
                    },
                    blocking=True,
                    return_response=True,
                )
                solar, used = parse_forecast_solar(response or {}, tz), source
            elif source == SOLAR_SOLCAST:
                forecasts: list[Any] = []
                for key in (CONF_SOLCAST_TODAY, CONF_SOLCAST_TOMORROW):
                    state = self.hass.states.get(self.options.get(key) or "")
                    if state:
                        forecasts += state.attributes.get("detailedForecast") or []
                solar, used = parse_solcast(forecasts, tz), source
        except (HomeAssistantError, KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Solar forecast not available: %s", err)
        if (
            not solar
            and self.options.get(CONF_WEATHER)
            and self.options.get(CONF_PV_POWER)
        ):
            solar = await self._async_weather_estimate(tz)
            used = "weather" if solar else None
        self._solar, self._solar_fetched, self.solar_source_used = solar, now, used
        return solar

    async def _async_weather_estimate(self, tz: Any) -> list[Interval]:
        entity_id = self.options[CONF_WEATHER]
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "hourly"},
                target={"entity_id": entity_id},
                blocking=True,
                return_response=True,
            )
            forecast = (response or {}).get(entity_id, {}).get("forecast", [])
            return estimate_solar_from_weather(
                forecast,
                self.options[CONF_PV_POWER],
                self.hass.config.latitude,
                self.hass.config.longitude,
                tz,
            )
        except (HomeAssistantError, KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Weather forecast not available: %s", err)
            return []

    async def _async_consumption_profile(
        self, now: datetime
    ) -> dict[int, float] | None:
        """Average consumption (kWh) per hour of the day over the last 7 days."""
        entity_id = self.options.get(CONF_CONSUMPTION_SENSOR)
        if not entity_id:
            return None
        if (
            self._profile_fetched
            and now - self._profile_fetched < CONSUMPTION_PROFILE_MAX_AGE
        ):
            return self._profile
        self._profile_fetched = now
        try:
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                now - timedelta(days=7),
                now,
                {entity_id},
                "hour",
                None,
                {"mean", "change"},
            )
        except (HomeAssistantError, KeyError) as err:
            _LOGGER.warning("Consumption history not available: %s", err)
            return self._profile
        state = self.hass.states.get(entity_id)
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None
        self._profile = build_profile(stats.get(entity_id, []), unit)
        return self._profile


def build_profile(rows: list[Any], unit: str | None) -> dict[int, float]:
    """kWh per local hour of the day from hourly statistics rows."""
    factors = {
        UnitOfPower.WATT: ("mean", 0.001),
        UnitOfPower.KILO_WATT: ("mean", 1.0),
        UnitOfEnergy.WATT_HOUR: ("change", 0.001),
        UnitOfEnergy.KILO_WATT_HOUR: ("change", 1.0),
    }
    if unit not in factors:
        return {}
    field, factor = factors[unit]
    per_hour: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        start = row["start"]
        moment = (
            dt_util.utc_from_timestamp(start) if isinstance(start, float) else start
        )
        per_hour[dt_util.as_local(moment).hour].append(max(value * factor, 0.0))
    return {hour: sum(values) / len(values) for hour, values in per_hour.items()}
