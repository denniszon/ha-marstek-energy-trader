"""Find and drive the Marstek Venus entities of the marstek_modbus integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_HOLD,
    FORCE_CHARGE,
    FORCE_DISCHARGE,
    FORCE_STANDBY,
    KEY_BATTERY_POWER,
    KEY_CAPACITY,
    KEY_CHARGE_POWER,
    KEY_DISCHARGE_POWER,
    KEY_FORCE_MODE,
    KEY_RS485,
    KEY_SOC,
    KEY_TOTAL_CHARGED,
    KEY_TOTAL_DISCHARGED,
    KEY_WORK_MODE,
    MARSTEK_DOMAIN,
    MARSTEK_MAX_POWER,
    MARSTEK_POWER_STEP,
    WORK_MODE_SELF_CONSUMPTION,
)

_LOGGER = logging.getLogger(__name__)

REQUIRED_ENTITIES = {
    KEY_WORK_MODE: Platform.SELECT,
    KEY_FORCE_MODE: Platform.SELECT,
    KEY_CHARGE_POWER: Platform.NUMBER,
    KEY_DISCHARGE_POWER: Platform.NUMBER,
    KEY_RS485: Platform.SWITCH,
    KEY_SOC: Platform.SENSOR,
}
OPTIONAL_ENTITIES = {
    KEY_BATTERY_POWER: Platform.SENSOR,
    KEY_CAPACITY: Platform.SENSOR,
    KEY_TOTAL_CHARGED: Platform.SENSOR,
    KEY_TOTAL_DISCHARGED: Platform.SENSOR,
}


@dataclass(frozen=True)
class MarstekEntities:
    """Entity ids per Marstek key, plus what is missing or disabled."""

    entity_ids: dict[str, str]
    missing: list[str]
    disabled: list[str]

    @property
    def usable(self) -> bool:
        return not self.missing and not self.disabled


def resolve_entities(hass: HomeAssistant, marstek_entry_id: str) -> MarstekEntities:
    """Look up the Marstek entities by their unique id "<entry id>_<key>"."""
    registry = er.async_get(hass)
    entity_ids: dict[str, str] = {}
    missing: list[str] = []
    disabled: list[str] = []
    for key, platform in (REQUIRED_ENTITIES | OPTIONAL_ENTITIES).items():
        entity_id = registry.async_get_entity_id(
            platform, MARSTEK_DOMAIN, f"{marstek_entry_id}_{key}"
        )
        entry = registry.async_get(entity_id) if entity_id else None
        if entry is None or entry.disabled:
            if key in REQUIRED_ENTITIES:
                (disabled if entry else missing).append(
                    entity_id or f"{platform}.{key}"
                )
            continue
        entity_ids[key] = entity_id
    return MarstekEntities(entity_ids, missing, disabled)


@dataclass(frozen=True)
class Write:
    """One service call to the Marstek."""

    key: str
    entity_id: str
    value: Any

    @property
    def description(self) -> str:
        if isinstance(self.value, bool):
            return f"{self.entity_id} → {'on' if self.value else 'off'}"
        return f"{self.entity_id} → {self.value}"


def marstek_power(power_w: float) -> int:
    """Round to the 50 W steps the Marstek accepts."""
    steps = round(power_w / MARSTEK_POWER_STEP)
    return max(MARSTEK_POWER_STEP, min(MARSTEK_MAX_POWER, steps * MARSTEK_POWER_STEP))


def desired_state(action: str, power_w: float) -> list[tuple[str, Any]]:
    """Marstek settings for an action, in the order they must be written.

    Forced modes need RS485 control mode first, then the power, then the mode.
    Self-consumption is restored by writing the work mode (which also ends
    RS485 control) and switching RS485 off.
    """
    if action == ACTION_CHARGE:
        return [
            (KEY_RS485, True),
            (KEY_CHARGE_POWER, marstek_power(power_w)),
            (KEY_FORCE_MODE, FORCE_CHARGE),
        ]
    if action == ACTION_DISCHARGE:
        return [
            (KEY_RS485, True),
            (KEY_DISCHARGE_POWER, marstek_power(power_w)),
            (KEY_FORCE_MODE, FORCE_DISCHARGE),
        ]
    if action == ACTION_HOLD:
        return [(KEY_RS485, True), (KEY_FORCE_MODE, FORCE_STANDBY)]
    return [(KEY_WORK_MODE, WORK_MODE_SELF_CONSUMPTION), (KEY_RS485, False)]


class MarstekController:
    """Writes the wanted state to the Marstek, only where it differs."""

    def __init__(self, hass: HomeAssistant, entities: MarstekEntities) -> None:
        self.hass = hass
        self.entities = entities

    def _matches(self, key: str, value: Any) -> bool:
        state = self.hass.states.get(self.entities.entity_ids[key])
        if state is None:
            return False
        if isinstance(value, bool):
            return (state.state == STATE_ON) is value
        if isinstance(value, int):
            try:
                return abs(float(state.state) - value) < 1
            except ValueError:
                return False
        return state.state == value

    def pending_writes(self, action: str, power_w: float) -> list[Write]:
        """Writes needed to reach the action; once one differs, rewrite the rest.

        Later settings only take effect after the earlier ones, so everything
        after the first difference is written again.
        """
        wanted = desired_state(action, power_w)
        for index, (key, value) in enumerate(wanted):
            if not self._matches(key, value):
                return [
                    Write(key, self.entities.entity_ids[key], value)
                    for key, value in wanted[index:]
                ]
        return []

    async def async_write(self, writes: list[Write]) -> None:
        """Perform the writes in order; raises HomeAssistantError on failure."""
        for write in writes:
            _LOGGER.debug("Marstek write: %s", write.description)
            target = {"entity_id": write.entity_id}
            if isinstance(write.value, bool):
                await self.hass.services.async_call(
                    Platform.SWITCH,
                    "turn_on" if write.value else "turn_off",
                    target=target,
                    blocking=True,
                )
            elif isinstance(write.value, int):
                await self.hass.services.async_call(
                    Platform.NUMBER,
                    "set_value",
                    {"value": write.value},
                    target=target,
                    blocking=True,
                )
            else:
                await self.hass.services.async_call(
                    Platform.SELECT,
                    "select_option",
                    {"option": write.value},
                    target=target,
                    blocking=True,
                )
