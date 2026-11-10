"""Finding the Marstek entities and turning actions into service calls."""

from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.marstek_energy_trader.marstek import (
    MarstekController,
    desired_state,
    marstek_power,
    resolve_entities,
)
from homeassistant.core import HomeAssistant

from .common import add_marstek, marstek_entity_id


def test_marstek_power_steps() -> None:
    assert marstek_power(1944) == 1950
    assert marstek_power(10) == 50
    assert marstek_power(4000) == 2500


def test_desired_state_order() -> None:
    assert [key for key, _ in desired_state("charge", 1000)] == [
        "rs485_control_mode",
        "set_charge_power",
        "force_mode",
    ]
    assert desired_state("self_consumption", 0) == [
        ("user_work_mode", "anti_feed"),
        ("rs485_control_mode", False),
    ]
    assert desired_state("hold", 0) == [
        ("rs485_control_mode", True),
        ("force_mode", "standby"),
    ]


def test_hold_with_discharge_limit_stores_solar() -> None:
    assert desired_state("hold", 0, 2000) == [
        ("max_discharge_power", 0),
        ("user_work_mode", "anti_feed"),
        ("rs485_control_mode", False),
    ]
    assert desired_state("standby", 0, 2000) == [
        ("max_discharge_power", 2000),
        ("rs485_control_mode", True),
        ("force_mode", "standby"),
    ]
    assert desired_state("self_consumption", 0, 2000)[0] == (
        "max_discharge_power",
        2000,
    )


async def test_resolve_entities(hass: HomeAssistant) -> None:
    marstek = add_marstek(hass, disabled=("force_mode",), skip=("set_charge_power",))
    entities = resolve_entities(hass, marstek.entry_id)
    assert entities.disabled == [marstek_entity_id("force_mode")]
    assert entities.missing == ["number.set_charge_power"]
    assert not entities.usable
    assert entities.entity_ids["battery_soc"] == marstek_entity_id("battery_soc")


async def test_only_differences_are_written(hass: HomeAssistant) -> None:
    marstek = add_marstek(hass)
    controller = MarstekController(hass, resolve_entities(hass, marstek.entry_id))
    # Already in self-consumption with RS485 off.
    assert controller.pending_writes("self_consumption", 0, 2500) == []
    hass.states.async_set(marstek_entity_id("rs485_control_mode"), "on")
    hass.states.async_set(marstek_entity_id("set_charge_power"), "1000")
    writes = controller.pending_writes("charge", 1000, 2500)
    assert [w.key for w in writes] == ["force_mode"]

    numbers = async_mock_service(hass, "number", "set_value")
    selects = async_mock_service(hass, "select", "select_option")
    await controller.async_write(controller.pending_writes("discharge", 800, 2500))
    assert numbers[0].data == {
        "entity_id": marstek_entity_id("set_discharge_power"),
        "value": 800,
    }
    assert selects[0].data == {
        "entity_id": marstek_entity_id("force_mode"),
        "option": "discharge",
    }


async def test_discharge_limit_never_above_the_users_own(hass: HomeAssistant) -> None:
    marstek = add_marstek(hass, discharge_limit=True)
    limit_id = marstek_entity_id("max_discharge_power")
    # The user limited the Marstek to 800 W; the trader allows 2500 W.
    hass.states.async_set(limit_id, "800")
    controller = MarstekController(hass, resolve_entities(hass, marstek.entry_id))
    numbers = async_mock_service(hass, "number", "set_value")
    async_mock_service(hass, "select", "select_option")
    async_mock_service(hass, "switch", "turn_off")

    await controller.async_write(controller.pending_writes("hold", 0, 2500))
    assert numbers[-1].data == {"entity_id": limit_id, "value": 0}
    hass.states.async_set(limit_id, "0")
    await controller.async_write(controller.pending_writes("self_consumption", 0, 2500))
    assert numbers[-1].data == {"entity_id": limit_id, "value": 800}

    # A value the trader wrote itself is not taken as the user's limit.
    hass.states.async_set(limit_id, "800")
    assert controller.pending_writes("self_consumption", 0, 500)[0].value == 500
    # A lower value set by the user later is.
    hass.states.async_set(limit_id, "600")
    assert controller.pending_writes("self_consumption", 0, 2500) == []
    assert controller.user_discharge_limit_w == 600
