"""Status, plan and savings sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CURRENCY_EURO,
    PERCENTAGE,
    Platform,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTIONS,
    STATUS_ERROR,
    STATUS_OFF,
    STATUS_WAITING,
)
from .coordinator import TraderConfigEntry, TraderCoordinator
from .entity import TraderEntity
from .planner import PlannedSlot
from .savings import SavingsTotals

STATUSES = [*ACTIONS, STATUS_OFF, STATUS_WAITING, STATUS_ERROR]
PRICE_UNIT = f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}"


def signed_power(slot: PlannedSlot) -> int:
    """Battery power of a slot: positive while charging, negative discharging."""
    return slot.power_w if slot.battery_kwh >= 0 else -slot.power_w


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TraderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        StatusSensor(coordinator),
        PlanSensor(coordinator),
        NextChangeSensor(coordinator),
        TargetPowerSensor(coordinator),
        PlannedSocSensor(coordinator),
    ]
    entities += [SavingsSensor(coordinator, d) for d in SAVINGS_SENSORS]
    async_add_entities(entities)


class StatusSensor(TraderEntity, SensorEntity):
    """What the trader is doing now, and why."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES
    _unrecorded_attributes = frozenset({"decisions", "writes"})

    def __init__(self, coordinator: TraderCoordinator) -> None:
        super().__init__(coordinator, Platform.SENSOR, "status")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.status if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        settings = self.settings
        override = self.coordinator.active_override()
        return {
            "reason": (data.reason or data.message) if data else None,
            "power_w": data.power_w if data else 0,
            "marstek_action": data.marstek_action if data else None,
            "mode": settings.mode,
            "dry_run": settings.dry_run,
            "override_until": override.end.isoformat() if override else None,
            "solar_forecast": self.coordinator.inputs.solar_source_used,
            "last_error": self.coordinator.last_error,
            "writes": data.writes if data else [],
            "decisions": [d.as_dict() for d in self.coordinator.decisions],
        }


class PlanSensor(TraderEntity, SensorEntity):
    """Expected savings of the current plan; the plan itself is an attribute."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(self, coordinator: TraderCoordinator) -> None:
        super().__init__(coordinator, Platform.SENSOR, "plan")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return data.plan.expected_savings if data and data.plan else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data or not data.plan:
            return {"plan": [], "horizon_end": None}
        coordinator = self.coordinator
        return {
            "horizon_end": data.plan.slots[-1].end.isoformat(),
            "terminal_price": round(data.plan.terminal_price, 4),
            "plan": [
                {
                    "start": slot.start.isoformat(),
                    "end": slot.end.isoformat(),
                    "action": slot.action,
                    "power_w": signed_power(slot),
                    "soc_start": slot.soc_start,
                    "soc_end": slot.soc_end,
                    "buy": round(slot.buy, 4),
                    "sell": round(slot.sell, 4),
                    "solar_kwh": round(slot.solar_kwh, 3),
                    "load_kwh": round(slot.load_kwh, 3),
                    "grid_kwh": round(slot.grid_kwh, 3),
                    "savings": round(slot.savings, 4),
                    "override": slot.forced,
                    "reason": coordinator.text(slot),
                }
                for slot in data.plan.slots
            ],
        }


def _next_change(slots: list[PlannedSlot]) -> PlannedSlot | None:
    if not slots:
        return None
    first = slots[0]
    return next(
        (
            slot
            for slot in slots[1:]
            if (slot.action, signed_power(slot) > 0)
            != (first.action, signed_power(first) > 0)
        ),
        None,
    )


class NextChangeSensor(TraderEntity, SensorEntity):
    """When the planned action changes next, and to what."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: TraderCoordinator) -> None:
        super().__init__(coordinator, Platform.SENSOR, "next_change")

    def _slot(self) -> PlannedSlot | None:
        data = self.coordinator.data
        return _next_change(data.plan.slots) if data and data.plan else None

    @property
    def native_value(self) -> datetime | None:
        slot = self._slot()
        return slot.start if slot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slot = self._slot()
        return {
            "action": slot.action if slot else None,
            "power_w": signed_power(slot) if slot else None,
            "reason": self.coordinator.text(slot) if slot else None,
        }


class TargetPowerSensor(TraderEntity, SensorEntity):
    """Planned battery power now: positive charging, negative discharging."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: TraderCoordinator) -> None:
        super().__init__(coordinator, Platform.SENSOR, "target_power")

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        current = data.current if data else None
        if current is None or current.action not in (ACTION_CHARGE, ACTION_DISCHARGE):
            return 0
        return signed_power(current)


class PlannedSocSensor(TraderEntity, SensorEntity):
    """Battery level the plan expects at the end of the current slot."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: TraderCoordinator) -> None:
        super().__init__(coordinator, Platform.SENSOR, "planned_soc")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return data.current.soc_end if data and data.current else None


@dataclass(frozen=True, kw_only=True)
class SavingsSensorDescription(SensorEntityDescription):
    """A value from the savings tracker."""

    value_fn: Callable[[TraderCoordinator], float | None]


def _total(field: str) -> Callable[[TraderCoordinator], float]:
    return lambda coordinator: getattr(coordinator.tracker.totals, field)


def _money(
    key: str,
    value_fn: Callable[[TraderCoordinator], float | None],
    state_class: SensorStateClass | None = SensorStateClass.TOTAL,
) -> SavingsSensorDescription:
    return SavingsSensorDescription(
        key=key,
        device_class=SensorDeviceClass.MONETARY,
        state_class=state_class,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        value_fn=value_fn,
    )


def _energy(key: str, field: str) -> SavingsSensorDescription:
    return SavingsSensorDescription(
        key=key,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_total(field),
    )


def _today(coordinator: TraderCoordinator) -> float:
    totals: SavingsTotals = coordinator.tracker.today_totals()
    return totals.savings


SAVINGS_SENSORS = (
    _money("savings_total", _total("savings")),
    # Resets at midnight; use savings_total for long-term statistics.
    _money("savings_today", _today, state_class=None),
    _money("charge_cost_total", _total("charge_cost")),
    _money("discharge_value_total", _total("discharge_value")),
    _money("planned_savings_total", lambda c: c.tracker.planned_savings),
    _energy("grid_to_battery", "grid_to_battery"),
    _energy("solar_to_battery", "solar_to_battery"),
    _energy("battery_to_home", "battery_to_home"),
    _energy("battery_to_grid", "battery_to_grid"),
    SavingsSensorDescription(
        key="average_stored_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=3,
        value_fn=lambda c: c.tracker.totals.average_stored_price,
    ),
)


class SavingsSensor(TraderEntity, SensorEntity):
    """Measured savings and energy flows of the battery."""

    entity_description: SavingsSensorDescription

    def __init__(
        self, coordinator: TraderCoordinator, description: SavingsSensorDescription
    ) -> None:
        super().__init__(coordinator, Platform.SENSOR, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        value = self.entity_description.value_fn(self.coordinator)
        return None if value is None else round(value, 4)
