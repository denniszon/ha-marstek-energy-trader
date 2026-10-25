"""Button to plan again right away."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TraderConfigEntry, TraderCoordinator
from .entity import TraderEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TraderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button."""
    async_add_entities([ReplanButton(entry.runtime_data)])


class ReplanButton(TraderEntity, ButtonEntity):
    """Make a new plan now, e.g. after changing something outside the trader."""

    def __init__(self, coordinator: TraderCoordinator) -> None:
        super().__init__(coordinator, Platform.BUTTON, "replan")

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
