"""Helpers: a fake Marstek battery and a price sensor."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek_energy_trader.const import (
    CONF_BASE_LOAD,
    CONF_EFFICIENCY,
    CONF_FEED_IN_MODE,
    CONF_GRID_SENSOR,
    CONF_MARSTEK_ENTRY,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_SOURCE,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

TZ = ZoneInfo("Europe/Amsterdam")
PRICE_SENSOR = "sensor.price"
GRID_SENSOR = "sensor.p1_power"
# Cheap night, expensive evening.
DAY = [0.20, 0.18, 0.15, 0.14, 0.14, 0.16, 0.22, 0.30, 0.32, 0.28, 0.24, 0.20]
DAY += [0.18, 0.19, 0.21, 0.25, 0.30, 0.38, 0.42, 0.40, 0.33, 0.28, 0.24, 0.22]

MARSTEK_ENTITIES = {
    # key: (platform, state)
    "user_work_mode": ("select", "anti_feed"),
    "force_mode": ("select", "standby"),
    "set_charge_power": ("number", "0"),
    "set_discharge_power": ("number", "0"),
    "rs485_control_mode": ("switch", "off"),
    "battery_soc": ("sensor", "20"),
    "battery_power": ("sensor", "0"),
    "battery_total_energy": ("sensor", "5.12"),
    "total_charging_energy": ("sensor", "100.0"),
    "total_discharging_energy": ("sensor", "80.0"),
}


def marstek_entity_id(key: str) -> str:
    return f"{MARSTEK_ENTITIES[key][0]}.marstek_{key}"


def add_marstek(
    hass: HomeAssistant, disabled: tuple[str, ...] = (), skip: tuple[str, ...] = ()
) -> MockConfigEntry:
    """Register the Marstek entities like the marstek_modbus integration does."""
    entry = MockConfigEntry(domain="marstek_modbus", title="Marstek")
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for key, (platform, state) in MARSTEK_ENTITIES.items():
        if key in skip:
            continue
        registry.async_get_or_create(
            platform,
            "marstek_modbus",
            f"{entry.entry_id}_{key}",
            suggested_object_id=f"marstek_{key}",
            config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.USER if key in disabled else None,
        )
        hass.states.async_set(marstek_entity_id(key), state)
    return entry


def set_prices(hass: HomeAssistant, day: datetime, prices: list[float] = DAY) -> None:
    """A price sensor with ANWB/Nord Pool style attributes for one day."""
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    raw = [
        {
            "start": (start + timedelta(hours=h)).isoformat(),
            "end": (start + timedelta(hours=h + 1)).isoformat(),
            "value": price,
        }
        for h, price in enumerate(prices)
    ]
    hass.states.async_set(
        PRICE_SENSOR,
        "0.2",
        {"unit_of_measurement": "€/kWh", "raw_today": raw, "raw_tomorrow": []},
    )


def trader_options(marstek: MockConfigEntry) -> dict:
    return {
        CONF_MARSTEK_ENTRY: marstek.entry_id,
        CONF_EFFICIENCY: 85,
        CONF_PRICE_SENSOR: PRICE_SENSOR,
        CONF_FEED_IN_MODE: "same",
        CONF_GRID_SENSOR: GRID_SENSOR,
        CONF_SOLAR_SOURCE: "none",
        CONF_BASE_LOAD: 400,
    }


async def setup_trader(
    hass: HomeAssistant, marstek: MockConfigEntry
) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options=trader_options(marstek))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
