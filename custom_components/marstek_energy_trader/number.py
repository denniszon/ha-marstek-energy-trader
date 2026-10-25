"""Adjustable limits for the planner."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import (
    CURRENCY_EURO,
    PERCENTAGE,
    EntityCategory,
    Platform,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MARSTEK_MAX_POWER, MARSTEK_POWER_STEP
from .coordinator import TraderConfigEntry, TraderCoordinator
from .entity import TraderEntity


@dataclass(frozen=True, kw_only=True)
class TraderNumberDescription(NumberEntityDescription):
    """A number backed by a TraderSettings field."""

    # Part of a strategy preset: changing it switches the strategy to custom.
    in_strategy: bool = False
    # Changing it changes the plan.
    replan: bool = True


NUMBERS = (
    TraderNumberDescription(
        key="min_profit",
        native_min_value=0,
        native_max_value=0.5,
        native_step=0.01,
        native_unit_of_measurement=f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}",
        mode=NumberMode.BOX,
        in_strategy=True,
    ),
    TraderNumberDescription(
        key="min_soc",
        native_min_value=10,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        in_strategy=True,
    ),
    TraderNumberDescription(
        key="max_soc",
        native_min_value=20,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    TraderNumberDescription(
        key="max_charge_power",
        native_min_value=100,
        native_max_value=MARSTEK_MAX_POWER,
        native_step=MARSTEK_POWER_STEP,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        mode=NumberMode.SLIDER,
    ),
    TraderNumberDescription(
        key="max_discharge_power",
        native_min_value=100,
        native_max_value=MARSTEK_MAX_POWER,
        native_step=MARSTEK_POWER_STEP,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        mode=NumberMode.SLIDER,
    ),
    TraderNumberDescription(
        key="forecast_trust",
        native_min_value=0,
        native_max_value=150,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    TraderNumberDescription(
        key="override_hours",
        native_min_value=0.25,
        native_max_value=24,
        native_step=0.25,
        native_unit_of_measurement=UnitOfTime.HOURS,
        mode=NumberMode.BOX,
        replan=False,
    ),
    TraderNumberDescription(
        key="watchdog_minutes",
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        replan=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TraderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the numbers."""
    coordinator = entry.runtime_data
    async_add_entities(
        TraderNumber(coordinator, description) for description in NUMBERS
    )


class TraderNumber(TraderEntity, RestoreNumber):
    """A planner setting; the value survives restarts."""

    entity_description: TraderNumberDescription

    def __init__(
        self, coordinator: TraderCoordinator, description: TraderNumberDescription
    ) -> None:
        super().__init__(coordinator, Platform.NUMBER, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            setattr(self.settings, self.entity_description.key, last.native_value)

    @property
    def native_value(self) -> float:
        return getattr(self.settings, self.entity_description.key)

    async def async_set_native_value(self, value: float) -> None:
        setattr(self.settings, self.entity_description.key, value)
        if self.entity_description.in_strategy:
            self.settings.update_strategy()
            self.coordinator.async_update_listeners()
        else:
            self.async_write_ha_state()
        if self.entity_description.replan:
            await self.coordinator.async_refresh()
