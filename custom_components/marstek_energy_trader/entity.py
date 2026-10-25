"""Base entity for Marstek Energy Trader."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TraderCoordinator, TraderSettings


class TraderEntity(CoordinatorEntity[TraderCoordinator]):
    """An entity of the trader device; entity ids stay English and stable."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TraderCoordinator, platform: str, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self.entity_id = f"{platform}.{DOMAIN}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Marstek Energy Trader",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        # Settings and status stay usable when a planning round failed.
        return True

    @property
    def settings(self) -> TraderSettings:
        return self.coordinator.settings
