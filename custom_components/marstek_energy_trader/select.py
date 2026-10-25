"""Mode, strategy and manual override selects."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import ACTIONS, MODES, OVERRIDE_AUTO, STRATEGIES
from .coordinator import TraderConfigEntry, TraderCoordinator
from .entity import TraderEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TraderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            ModeSelect(coordinator),
            StrategySelect(coordinator),
            OverrideSelect(coordinator),
        ]
    )


class ModeSelect(TraderEntity, SelectEntity, RestoreEntity):
    """Operating mode: off, solar only, price only, or solar and price."""

    _attr_options = MODES

    def __init__(self, coordinator: TraderCoordinator) -> None:
        super().__init__(coordinator, Platform.SELECT, "mode")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in MODES:
            self.settings.mode = last.state

    @property
    def current_option(self) -> str:
        return self.settings.mode

    async def async_select_option(self, option: str) -> None:
        self.settings.mode = option
        self.async_write_ha_state()
        await self.coordinator.async_refresh()


class StrategySelect(TraderEntity, SelectEntity, RestoreEntity):
    """Preset for how eager the trader is; numbers can fine-tune it."""

    _attr_options = STRATEGIES
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: TraderCoordinator) -> None:
        super().__init__(coordinator, Platform.SELECT, "strategy")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in STRATEGIES:
            # The numbers restore their own values, so only the name is restored.
            self.settings.strategy = last.state

    @property
    def current_option(self) -> str:
        return self.settings.strategy

    async def async_select_option(self, option: str) -> None:
        self.settings.apply_strategy(option)
        self.coordinator.async_update_listeners()
        await self.coordinator.async_refresh()


class OverrideSelect(TraderEntity, SelectEntity):
    """Manual action for the next hours (see the override duration number)."""

    _attr_options = [OVERRIDE_AUTO, *ACTIONS]

    def __init__(self, coordinator: TraderCoordinator) -> None:
        super().__init__(coordinator, Platform.SELECT, "override")

    @property
    def current_option(self) -> str:
        override = self.coordinator.active_override()
        return override.action if override else OVERRIDE_AUTO

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        override = self.coordinator.active_override()
        return {"until": override.end.isoformat() if override else None}

    async def async_select_option(self, option: str) -> None:
        if option == OVERRIDE_AUTO:
            self.coordinator.clear_overrides()
        else:
            now = dt_util.now()
            hours = self.settings.override_hours
            self.coordinator.set_override(option, now, now + timedelta(hours=hours))
        self.async_write_ha_state()
        await self.coordinator.async_refresh()
