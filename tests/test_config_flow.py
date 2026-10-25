"""Setting up and changing the trader."""

from datetime import datetime

from custom_components.marstek_energy_trader.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .common import PRICE_SENSOR, add_marstek, set_prices, setup_trader


async def test_full_flow(hass: HomeAssistant) -> None:
    marstek = add_marstek(hass)
    set_prices(hass, datetime(2026, 10, 26))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    # The frontend cannot show a required config entry selector without a value.
    marker = next(k for k in result["data_schema"].schema if k == "marstek_entry")
    assert marker.description == {"suggested_value": marstek.entry_id}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"marstek_entry": marstek.entry_id}
    )
    assert result["step_id"] == "prices"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"price_sensor": PRICE_SENSOR, "feed_in_mode": "same"}
    )
    assert result["step_id"] == "sources"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"solar_source": "none"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["marstek_entry"] == marstek.entry_id
    assert result["options"]["price_sensor"] == PRICE_SENSOR
    assert result["options"]["round_trip_efficiency"] == 85
    assert result["options"]["base_load"] == 400


async def test_no_marstek(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_marstek"


async def test_disabled_marstek_entities(hass: HomeAssistant) -> None:
    marstek = add_marstek(hass, disabled=("set_charge_power",))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"marstek_entry": marstek.entry_id}
    )
    assert result["errors"] == {"base": "marstek_disabled"}
    assert "set_charge_power" in result["description_placeholders"]["entities"]


async def test_price_sensor_and_solar_validation(hass: HomeAssistant) -> None:
    marstek = add_marstek(hass)
    hass.states.async_set("sensor.flat_price", "0.25")
    set_prices(hass, datetime(2026, 10, 26))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"marstek_entry": marstek.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"price_sensor": "sensor.flat_price", "feed_in_mode": "sensor"},
    )
    assert result["errors"] == {
        "price_sensor": "invalid_price_sensor",
        "feed_in_sensor": "required",
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"price_sensor": PRICE_SENSOR, "feed_in_mode": "same"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"solar_source": "solcast"}
    )
    assert result["errors"] == {"solcast_today": "required"}


async def test_options_flow(hass: HomeAssistant) -> None:
    marstek = add_marstek(hass)
    set_prices(hass, datetime.now())
    entry = await setup_trader(hass, marstek)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"marstek_entry": marstek.entry_id, "round_trip_efficiency": 90},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"price_sensor": PRICE_SENSOR, "feed_in_mode": "fixed", "feed_in_fixed": 0.05},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"solar_source": "none", "base_load": 300}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The options flow reloads the entry in a task; let it finish.
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options["round_trip_efficiency"] == 90
    assert entry.options["feed_in_fixed"] == 0.05
    assert entry.options["base_load"] == 300
    # The grid sensor was not filled in again, so it is removed.
    assert "grid_power_sensor" not in entry.options
