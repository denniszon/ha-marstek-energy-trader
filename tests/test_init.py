"""The trader inside Home Assistant, with a fake Marstek battery."""

from datetime import datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.marstek_energy_trader.const import DOMAIN, EVENT_ACTION
from custom_components.marstek_energy_trader.inputs import read_price_sensor
from custom_components.marstek_energy_trader.marstek import Write
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .common import (
    GRID_SENSOR,
    add_marstek,
    marstek_entity_id,
    set_prices,
    setup_trader,
)

NIGHT = "2026-10-26T03:10:00+01:00"
EVENING = "2026-10-26T18:10:00+01:00"
# Leaves room for the watchdog before the next 15-minute replan.
QUARTER_PAST = "2026-10-26T03:16:00+01:00"


def _record_writes(entry: ConfigEntry) -> list[tuple]:
    """Record what the trader writes to the Marstek, in order."""
    writes: list[tuple] = []

    async def record(batch: list[Write]) -> None:
        writes.extend((w.entity_id, w.value) for w in batch)

    entry.runtime_data.controller.async_write = record
    return writes


async def _start(hass: HomeAssistant, freezer: FrozenDateTimeFactory, moment: str):
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to(moment)
    marstek = add_marstek(hass)
    set_prices(hass, datetime.fromisoformat(moment))
    entry = await setup_trader(hass, marstek)
    return entry, _record_writes(entry)


async def _turn(hass: HomeAssistant, domain: str, service: str, entity: str, **data):
    await hass.services.async_call(
        domain, service, {"entity_id": entity, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def test_dry_run_plans_but_writes_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, calls = await _start(hass, freezer, NIGHT)
    assert entry.state is ConfigEntryState.LOADED

    status = hass.states.get(f"sensor.{DOMAIN}_status")
    assert status.state == "charge"
    assert status.attributes["dry_run"] is True
    assert status.attributes["reason"].startswith("Charging at €0.14")
    # It shows what it would write.
    assert (
        marstek_entity_id("rs485_control_mode") + " → on" in status.attributes["writes"]
    )
    assert calls == []

    plan = hass.states.get(f"sensor.{DOMAIN}_plan")
    assert float(plan.state) > 0
    assert plan.attributes["plan"][0]["action"] == "charge"
    assert plan.attributes["plan"][0]["power_w"] > 0
    assert len(plan.attributes["plan"]) == 21
    assert hass.states.get(f"switch.{DOMAIN}_dry_run").state == "on"
    assert hass.states.get(f"select.{DOMAIN}_mode").state == "solar_price"


async def test_live_mode_writes_in_order(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _, calls = await _start(hass, freezer, NIGHT)
    await _turn(hass, "switch", "turn_off", f"switch.{DOMAIN}_dry_run")

    power = hass.states.get(f"sensor.{DOMAIN}_status").attributes["power_w"]
    assert calls == [
        (marstek_entity_id("rs485_control_mode"), True),
        (marstek_entity_id("set_charge_power"), round(power / 50) * 50),
        (marstek_entity_id("force_mode"), "charge"),
    ]


async def test_discharge_without_export_uses_self_consumption(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _, calls = await _start(hass, freezer, EVENING)
    hass.states.async_set(marstek_entity_id("battery_soc"), "90")
    hass.states.async_set(marstek_entity_id("rs485_control_mode"), "on")
    await _turn(
        hass, "select", "select_option", f"select.{DOMAIN}_mode", option="price"
    )
    await _turn(hass, "switch", "turn_off", f"switch.{DOMAIN}_dry_run")

    status = hass.states.get(f"sensor.{DOMAIN}_status")
    assert status.state == "discharge"
    assert status.attributes["marstek_action"] == "self_consumption"
    assert calls == [(marstek_entity_id("rs485_control_mode"), False)]


async def test_watchdog_rewrites_drift(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _, calls = await _start(hass, freezer, QUARTER_PAST)
    await _turn(hass, "switch", "turn_off", f"switch.{DOMAIN}_dry_run")
    # The Marstek follows the writes ...
    hass.states.async_set(marstek_entity_id("rs485_control_mode"), "on")
    power = calls[1][1]
    hass.states.async_set(marstek_entity_id("set_charge_power"), str(power))
    hass.states.async_set(marstek_entity_id("force_mode"), "charge")
    calls.clear()
    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls == []

    # ... and then drops out of RS485 control by itself.
    hass.states.async_set(marstek_entity_id("rs485_control_mode"), "off")
    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [value for _, value in calls] == [True, power, "charge"]


async def test_override_and_mode_off(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _, calls = await _start(hass, freezer, NIGHT)
    events = async_capture_events(hass, EVENT_ACTION)
    await _turn(
        hass, "select", "select_option", f"select.{DOMAIN}_override", option="hold"
    )
    assert hass.states.get(f"sensor.{DOMAIN}_status").state == "hold"
    assert hass.states.get(f"select.{DOMAIN}_override").state == "hold"
    assert events[-1].data["status"] == "hold"

    # The override ends after the override duration (1 hour).
    freezer.tick(timedelta(minutes=65))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(f"select.{DOMAIN}_override").state == "auto"

    # Off hands control back to the Marstek once.
    hass.states.async_set(marstek_entity_id("user_work_mode"), "manual")
    await _turn(hass, "switch", "turn_off", f"switch.{DOMAIN}_dry_run")
    calls.clear()
    await _turn(hass, "select", "select_option", f"select.{DOMAIN}_mode", option="off")
    assert hass.states.get(f"sensor.{DOMAIN}_status").state == "off"
    assert calls[0] == (marstek_entity_id("user_work_mode"), "anti_feed")
    calls.clear()
    await _turn(hass, "button", "press", f"button.{DOMAIN}_replan")
    assert calls == []


async def test_set_override_service(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _start(hass, freezer, NIGHT)
    hass.states.async_set(marstek_entity_id("battery_soc"), "80")
    await hass.services.async_call(
        DOMAIN,
        "set_override",
        {"action": "discharge", "hours": 2, "power_w": 1000},
        blocking=True,
    )
    plan = hass.states.get(f"sensor.{DOMAIN}_plan").attributes["plan"]
    assert [slot["action"] for slot in plan[:2]] == ["discharge", "discharge"]
    assert plan[0]["override"] is True
    await hass.services.async_call(DOMAIN, "clear_override", blocking=True)
    plan = hass.states.get(f"sensor.{DOMAIN}_plan").attributes["plan"]
    assert plan[0]["override"] is False


async def test_strategy_sets_numbers(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _start(hass, freezer, NIGHT)
    await _turn(
        hass,
        "select",
        "select_option",
        f"select.{DOMAIN}_strategy",
        option="conservative",
    )
    assert float(hass.states.get(f"number.{DOMAIN}_min_profit").state) == 0.08
    assert float(hass.states.get(f"number.{DOMAIN}_min_soc").state) == 30
    await _turn(hass, "number", "set_value", f"number.{DOMAIN}_min_profit", value=0.05)
    assert hass.states.get(f"select.{DOMAIN}_strategy").state == "custom"


async def test_savings_are_measured(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _start(hass, freezer, NIGHT)
    # First sample sets the starting point.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # 1 kWh charged from the grid at €0.14.
    hass.states.async_set(marstek_entity_id("total_charging_energy"), "101.0")
    hass.states.async_set(marstek_entity_id("battery_power"), "-2000")
    hass.states.async_set(GRID_SENSOR, "2400", {"unit_of_measurement": "W"})
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert float(hass.states.get(f"sensor.{DOMAIN}_grid_to_battery").state) == 1.0
    assert float(hass.states.get(f"sensor.{DOMAIN}_savings_total").state) == -0.14
    assert float(hass.states.get(f"sensor.{DOMAIN}_savings_today").state) == -0.14
    assert float(hass.states.get(f"sensor.{DOMAIN}_average_stored_price").state) == 0.14


async def test_waiting_without_prices(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to(NIGHT)
    marstek = add_marstek(hass)
    hass.states.async_set("sensor.price", "0.2", {"raw_today": [], "raw_tomorrow": []})
    await setup_trader(hass, marstek)
    status = hass.states.get(f"sensor.{DOMAIN}_status")
    assert status.state == "waiting"
    assert "no price" in status.attributes["reason"]


async def test_setup_retries_when_entities_are_disabled(hass: HomeAssistant) -> None:
    marstek = add_marstek(hass, disabled=("force_mode",))
    entry = await setup_trader(hass, marstek)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_failed_write_is_retried_with_the_plan(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, calls = await _start(hass, freezer, QUARTER_PAST)
    attempts = []

    async def fail(batch: list[Write]) -> None:
        attempts.append([w.value for w in batch])
        raise HomeAssistantError("Modbus timeout")

    entry.runtime_data.controller.async_write = fail
    await _turn(hass, "switch", "turn_off", f"switch.{DOMAIN}_dry_run")
    status = hass.states.get(f"sensor.{DOMAIN}_status")
    assert status.state == "error"
    assert "Modbus timeout" in status.attributes["last_error"]

    # The watchdog retries the charge, not self-consumption, and recovers.
    entry.runtime_data.controller.async_write = lambda batch: _ok(calls, batch)
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert attempts[0][-1] == "charge"
    assert calls[-1] == (marstek_entity_id("force_mode"), "charge")
    assert hass.states.get(f"sensor.{DOMAIN}_status").state == "charge"


async def _ok(calls: list, batch: list[Write]) -> None:
    calls.extend((w.entity_id, w.value) for w in batch)


async def test_dry_run_on_and_unload_release_the_battery(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, calls = await _start(hass, freezer, NIGHT)
    await _turn(hass, "switch", "turn_off", f"switch.{DOMAIN}_dry_run")
    # The Marstek is now charging under RS485 control.
    hass.states.async_set(marstek_entity_id("rs485_control_mode"), "on")
    hass.states.async_set(marstek_entity_id("force_mode"), "charge")
    calls.clear()
    await _turn(hass, "switch", "turn_on", f"switch.{DOMAIN}_dry_run")
    assert calls == [(marstek_entity_id("rs485_control_mode"), False)]

    await _turn(hass, "switch", "turn_off", f"switch.{DOMAIN}_dry_run")
    calls.clear()
    await hass.config_entries.async_unload(entry.entry_id)
    assert calls == [(marstek_entity_id("rs485_control_mode"), False)]


async def test_reserve_stops_self_consumption(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _, calls = await _start(hass, freezer, EVENING)
    # Battery at the reserve (20%) while the house imports from the grid.
    hass.states.async_set(GRID_SENSOR, "500", {"unit_of_measurement": "W"})
    await _turn(hass, "switch", "turn_off", f"switch.{DOMAIN}_dry_run")
    status = hass.states.get(f"sensor.{DOMAIN}_status")
    assert status.attributes["marstek_action"] == "hold"
    assert calls[-1] == (marstek_entity_id("force_mode"), "standby")


async def test_price_units_are_converted(hass: HomeAssistant) -> None:
    for unit, value in (("ct/kWh", 14), ("€/MWh", 140), ("EUR/kWh", 0.14)):
        hass.states.async_set(
            "sensor.price",
            "0",
            {
                "unit_of_measurement": unit,
                "raw_today": [{"start": "2026-10-26T03:00:00+01:00", "value": value}],
            },
        )
        assert read_price_sensor(hass, "sensor.price")[0].value == 0.14
