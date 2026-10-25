"""Dry run and grid export switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import TraderConfigEntry, TraderCoordinator
from .entity import TraderEntity

SWITCHES = ("dry_run", "allow_grid_export")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TraderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    coordinator = entry.runtime_data
    async_add_entities(TraderSwitch(coordinator, key) for key in SWITCHES)


class TraderSwitch(TraderEntity, SwitchEntity, RestoreEntity):
    """A setting that is on or off; survives restarts."""

    def __init__(self, coordinator: TraderCoordinator, key: str) -> None:
        super().__init__(coordinator, Platform.SWITCH, key)
        self._key = key

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in (STATE_ON, STATE_OFF):
            setattr(self.settings, self._key, last.state == STATE_ON)

    @property
    def is_on(self) -> bool:
        return getattr(self.settings, self._key)

    async def _async_set(self, value: bool) -> None:
        if self._key == "dry_run" and value and not self.settings.dry_run:
            # Don't leave the battery in a forced mode nobody controls anymore.
            await self.coordinator.async_release()
        setattr(self.settings, self._key, value)
        self.async_write_ha_state()
        await self.coordinator.async_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)
