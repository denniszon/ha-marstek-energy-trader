"""Keep the measured savings up to date and stored across restarts."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_POWER_SIGN,
    CONF_GRID_SENSOR,
    CONF_GRID_SIGN,
    DOMAIN,
    KEY_BATTERY_POWER,
    KEY_TOTAL_CHARGED,
    KEY_TOTAL_DISCHARGED,
    SIGN_IMPORT_NEGATIVE,
    SIGN_POSITIVE_CHARGE,
)
from .inputs import TraderInputs
from .savings import SavingsTotals, account

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 60
# Larger jumps of the energy counters (kWh) are treated as a counter reset.
MAX_COUNTER_STEP = 2.0


def _float_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    state = hass.states.get(entity_id) if entity_id else None
    try:
        return float(state.state) if state else None
    except ValueError:
        return None


class SavingsTracker:
    """Measures charged and discharged energy and what it was worth."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        options: dict[str, Any],
        inputs: TraderInputs,
    ) -> None:
        self.hass = hass
        self.options = options
        self.inputs = inputs
        self.store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.totals = SavingsTotals()
        self.today = SavingsTotals()
        self.today_date: str | None = None
        # Sum of the savings the plan expected for the time that has passed.
        self.planned_savings = 0.0
        self._last_counters: tuple[float, float] | None = None

    @property
    def available(self) -> bool:
        ids = self.inputs.marstek.entity_ids
        return KEY_TOTAL_CHARGED in ids and KEY_TOTAL_DISCHARGED in ids

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        self.totals = SavingsTotals.from_dict(data.get("totals"))
        self.today = SavingsTotals.from_dict(data.get("today"))
        self.today_date = data.get("today_date")
        self.planned_savings = float(data.get("planned_savings", 0.0))

    def _data(self) -> dict[str, Any]:
        return {
            "totals": self.totals.as_dict(),
            "today": self.today.as_dict(),
            "today_date": self.today_date,
            "planned_savings": self.planned_savings,
        }

    def _save(self) -> None:
        self.store.async_delay_save(self._data, SAVE_DELAY)

    async def async_save_now(self) -> None:
        await self.store.async_save(self._data())

    def _roll_day(self, now: datetime) -> None:
        today = dt_util.as_local(now).date().isoformat()
        if self.today_date != today:
            self.today = SavingsTotals()
            self.today_date = today

    def today_totals(self) -> SavingsTotals:
        """Today's totals; empty after midnight until the first measurement."""
        if self.today_date == dt_util.now().date().isoformat():
            return self.today
        return SavingsTotals()

    def add_planned(self, amount: float) -> None:
        self.planned_savings += amount
        self._save()

    def battery_power(self) -> float | None:
        """Battery power in W, positive while charging."""
        value = _float_state(
            self.hass, self.inputs.marstek.entity_ids.get(KEY_BATTERY_POWER)
        )
        if value is None:
            return None
        if self.options.get(CONF_BATTERY_POWER_SIGN) == SIGN_POSITIVE_CHARGE:
            return value
        return -value

    def grid_power(self) -> float | None:
        """Grid power in W, positive while importing."""
        entity_id = self.options.get(CONF_GRID_SENSOR)
        value = _float_state(self.hass, entity_id)
        if value is None:
            return None
        state = self.hass.states.get(entity_id)
        if state and state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == (
            UnitOfPower.KILO_WATT
        ):
            value *= 1000
        if self.options.get(CONF_GRID_SIGN) == SIGN_IMPORT_NEGATIVE:
            return -value
        return value

    def sample(self, now: datetime) -> bool:
        """Account for the energy since the previous sample; True if changed."""
        if not self.available:
            return False
        ids = self.inputs.marstek.entity_ids
        charged = _float_state(self.hass, ids[KEY_TOTAL_CHARGED])
        discharged = _float_state(self.hass, ids[KEY_TOTAL_DISCHARGED])
        if charged is None or discharged is None:
            return False
        previous, self._last_counters = self._last_counters, (charged, discharged)
        if previous is None:
            return False
        delta_charged = charged - previous[0]
        delta_discharged = discharged - previous[1]
        if not 0 <= delta_charged <= MAX_COUNTER_STEP or not (
            0 <= delta_discharged <= MAX_COUNTER_STEP
        ):
            _LOGGER.debug("Energy counter jump ignored")
            return False
        if not delta_charged and not delta_discharged:
            return False
        prices = self.inputs.current_prices(now)
        if prices is None:
            _LOGGER.debug("No price for %s, energy not valued", now)
            return False

        self._roll_day(now)
        measurement = {
            "charged_kwh": delta_charged,
            "discharged_kwh": delta_discharged,
            "battery_w": self.battery_power(),
            "grid_w": self.grid_power(),
            "buy": prices[0],
            "sell": prices[1],
        }
        account(self.totals, **measurement)
        account(self.today, **measurement)
        self._save()
        return True
