"""Plans the battery schedule and applies the current action to the Marstek."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfPower,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import PowerConverter

from .const import (
    ACTION_DISCHARGE,
    ACTION_HOLD,
    ACTION_SELF,
    BLOCK_RELEASE_DELAY,
    CONF_BLOCK_ENTITIES,
    CONF_BLOCK_THRESHOLD,
    CONF_FEED_IN_SENSOR,
    CONF_PRICE_SENSOR,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    DECISION_LOG_SIZE,
    DEFAULT_BLOCK_THRESHOLD,
    DOMAIN,
    EVENT_ACTION,
    KEY_SOC,
    MARSTEK_MAX_POWER,
    MARSTEK_STANDBY,
    MODE_OFF,
    MODE_SOLAR_PRICE,
    SAVINGS_INTERVAL,
    STATUS_ERROR,
    STATUS_OFF,
    STATUS_WAITING,
    STRATEGY_BALANCED,
    STRATEGY_CUSTOM,
    STRATEGY_PRESETS,
)
from .inputs import InputError, Override, PlanSettings, TraderInputs
from .marstek import MarstekController, MarstekEntities, marstek_power
from .planner import Plan, PlannedSlot, Strategy, make_plan
from .reasons import describe, describe_block
from .tracker import SavingsTracker

_LOGGER = logging.getLogger(__name__)

type TraderConfigEntry = ConfigEntry[TraderCoordinator]


def _is_number(state: State | None) -> bool:
    if state is None:
        return False
    try:
        float(state.state)
    except ValueError:
        return False
    return True


@dataclass
class TraderSettings:
    """Settings changed from the dashboard (select, number and switch entities)."""

    mode: str = MODE_SOLAR_PRICE
    dry_run: bool = True
    strategy: str = STRATEGY_BALANCED
    min_profit: float = STRATEGY_PRESETS[STRATEGY_BALANCED]["min_profit"]
    min_soc: float = STRATEGY_PRESETS[STRATEGY_BALANCED]["min_soc"]
    max_soc: float = 100
    max_charge_power: float = 2500
    max_discharge_power: float = 2500
    forecast_trust: float = 80
    allow_grid_export: bool = False
    watchdog_minutes: float = 5
    override_hours: float = 1

    def plan_settings(self) -> PlanSettings:
        return PlanSettings(
            min_soc=self.min_soc,
            max_soc=max(self.max_soc, self.min_soc),
            max_charge_power=self.max_charge_power,
            max_discharge_power=self.max_discharge_power,
            forecast_trust=self.forecast_trust,
        )

    def apply_strategy(self, strategy: str) -> None:
        self.strategy = strategy
        for key, value in STRATEGY_PRESETS.get(strategy, {}).items():
            setattr(self, key, value)

    def update_strategy(self) -> None:
        """Switch to "custom" when the numbers no longer match the preset."""
        preset = STRATEGY_PRESETS.get(self.strategy)
        if preset and any(getattr(self, k) != v for k, v in preset.items()):
            self.strategy = STRATEGY_CUSTOM


@dataclass(frozen=True)
class Decision:
    """A change of action, kept for the status sensor and the logbook."""

    time: datetime
    status: str
    power_w: int
    reason: str
    dry_run: bool
    writes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "time": self.time.isoformat(),
            "status": self.status,
            "power_w": self.power_w,
            "reason": self.reason,
            "dry_run": self.dry_run,
            "writes": self.writes,
        }


@dataclass(frozen=True)
class TraderData:
    """Result of a planning round."""

    # The planned action, or off/waiting; error after a failed write.
    status: str
    plan: Plan | None = None
    power_w: int = 0
    reason: str = ""
    message: str | None = None
    # What the Marstek was told to do; differs from status for discharging
    # without grid export, which runs as Marstek self-consumption.
    marstek_action: str | None = None
    writes: list[str] = field(default_factory=list)

    @property
    def current(self) -> PlannedSlot | None:
        return self.plan.slots[0] if self.plan and self.plan.slots else None


class TraderCoordinator(DataUpdateCoordinator[TraderData]):
    """Replans every slot, on price/forecast changes, when settings change and when
    the battery level becomes available again."""

    config_entry: TraderConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TraderConfigEntry,
        marstek: MarstekEntities,
    ) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None
        )
        self.options = {**entry.data, **entry.options}
        self.settings = TraderSettings()
        self.inputs = TraderInputs(hass, self.options, marstek)
        self.controller = MarstekController(
            hass, marstek, Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.marstek")
        )
        self.tracker = SavingsTracker(hass, entry.entry_id, self.options, self.inputs)
        self.overrides: list[Override] = []
        self.decisions: deque[Decision] = deque(maxlen=DECISION_LOG_SIZE)
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        self._last_decision: tuple[str, int, bool] | None = None
        self._released = False
        self._planned_at: datetime | None = None
        self._last_apply: datetime | None = None
        self._override_timers: list[CALLBACK_TYPE] = []
        # A load (wallbox, dryer, ...) is on: hold whatever the plan says.
        self.blocked = False
        self._block_reason = ""
        self._block_names: list[str] = []
        self._block_release: CALLBACK_TYPE | None = None

    @property
    def language(self) -> str:
        return self.hass.config.language

    def text(self, slot: PlannedSlot) -> str:
        return describe(slot.reason, self.language, dt_util.get_default_time_zone())

    async def async_start(self) -> None:
        """Start the timers and listeners."""
        entry = self.config_entry
        entry.async_on_unload(
            async_track_time_change(
                self.hass, self._async_on_time, minute=[0, 15, 30, 45], second=2
            )
        )
        entry.async_on_unload(
            async_track_time_interval(
                self.hass, self._async_watchdog, timedelta(minutes=1)
            )
        )
        entry.async_on_unload(
            async_track_time_interval(self.hass, self._async_sample, SAVINGS_INTERVAL)
        )
        watched = [
            self.options.get(key)
            for key in (
                CONF_PRICE_SENSOR,
                CONF_FEED_IN_SENSOR,
                CONF_SOLCAST_TODAY,
                CONF_SOLCAST_TOMORROW,
            )
        ]
        entry.async_on_unload(
            async_track_state_change_event(
                self.hass, [e for e in watched if e], self._async_on_input_change
            )
        )
        entry.async_on_unload(
            async_track_state_change_event(
                self.hass,
                self.inputs.marstek.entity_ids[KEY_SOC],
                self._async_on_soc_change,
            )
        )
        if blockers := self.options.get(CONF_BLOCK_ENTITIES):
            entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, blockers, self._async_on_block_change
                )
            )
            entry.async_on_unload(self._cancel_block_release)
            self.blocked = bool(self._active_loads())
        entry.async_on_unload(self.tracker.async_save_now)
        entry.async_on_unload(self.controller.async_save_now)
        entry.async_on_unload(self.cancel_override_timers)

    async def _async_on_time(self, _now: datetime) -> None:
        await self.async_request_refresh()

    @callback
    def _async_on_input_change(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _async_on_soc_change(self, event: Event) -> None:
        """Replan when the trader waits and the battery level comes back."""
        if self.data is None or self.data.status != STATUS_WAITING:
            return
        if _is_number(event.data["new_state"]) and not _is_number(
            event.data["old_state"]
        ):
            self.hass.async_create_task(self.async_request_refresh())

    def _active_loads(self) -> list[tuple[str, float | None]]:
        """Blocking loads in use: name and power in W (None for a switch)."""
        threshold = self.options.get(CONF_BLOCK_THRESHOLD, DEFAULT_BLOCK_THRESHOLD)
        loads: list[tuple[str, float | None]] = []
        for entity_id in self.options.get(CONF_BLOCK_ENTITIES, []):
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            if state.domain != "sensor":
                if state.state == STATE_ON:
                    loads.append((state.name, None))
                continue
            try:
                power = float(state.state)
            except ValueError:
                continue
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            if unit in PowerConverter.VALID_UNITS:
                power = PowerConverter.convert(power, unit, UnitOfPower.WATT)
            if power > threshold:
                loads.append((state.name, power))
        return loads

    def _loads_unknown(self) -> bool:
        """A blocking entity is unavailable: it may still be in use."""
        for entity_id in self.options.get(CONF_BLOCK_ENTITIES, []):
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                return True
        return False

    @callback
    def _async_on_block_change(self, _event: Event) -> None:
        if loads := self._active_loads():
            self._cancel_block_release()
            names = [name for name, _ in loads]
            if not self.blocked or names != self._block_names:
                # Also when another load joins in, so the reason names it.
                self.blocked = True
                self._block_names = names
                self.config_entry.async_create_background_task(
                    self.hass, self._async_reapply(), f"{DOMAIN} block"
                )
        elif self.blocked and self._block_release is None and not self._loads_unknown():
            # Wait a while: a car or dryer pauses now and then.
            self._block_release = async_call_later(
                self.hass, BLOCK_RELEASE_DELAY, self._async_end_block
            )

    async def _async_end_block(self, _now: datetime) -> None:
        self._block_release = None
        if not self._active_loads() and not self._loads_unknown():
            self.blocked = False
            await self.async_request_refresh()

    @callback
    def _cancel_block_release(self) -> None:
        if self._block_release:
            self._block_release()
            self._block_release = None

    async def _async_reapply(self) -> None:
        """Apply the current plan again now, without waiting for a replan."""
        if self.data is None:
            return
        async with self._lock:
            data = await self._async_execute_locked(self.data)
        self.async_set_updated_data(data)

    async def _async_watchdog(self, now: datetime) -> None:
        """Re-apply the current action when the Marstek drifted away from it."""
        if self._lock.locked() or self.data is None or self._last_apply is None:
            return
        if now - self._last_apply < timedelta(minutes=self.settings.watchdog_minutes):
            return
        current = self.data.current
        if current is not None and current.end <= now:
            # The plan is out of date (a replan failed); make a new one.
            await self.async_request_refresh()
            return
        async with self._lock:
            data = await self._async_execute_locked(self.data)
        if data != self.data:
            self.async_set_updated_data(data)

    async def _async_sample(self, now: datetime) -> None:
        if self.tracker.sample(now):
            self.async_update_listeners()

    def set_override(
        self,
        action: str,
        start: datetime,
        end: datetime,
        power_w: float | None = None,
    ) -> None:
        self.overrides = [
            o for o in self.overrides if o.end <= start or o.start >= end
        ] + [Override(start, end, action, power_w)]
        self.overrides.sort(key=lambda o: o.start)
        # Replan exactly when the override starts and ends.
        for moment in (start, end):
            if moment > dt_util.now():
                self._override_timers.append(
                    async_track_point_in_time(self.hass, self._async_on_time, moment)
                )

    def clear_overrides(self) -> None:
        self.overrides = []
        self.cancel_override_timers()

    def cancel_override_timers(self) -> None:
        for cancel in self._override_timers:
            cancel()
        self._override_timers = []

    def active_override(self, now: datetime | None = None) -> Override | None:
        now = now or dt_util.now()
        return next((o for o in self.overrides if o.start <= now < o.end), None)

    def _count_planned_savings(self, now: datetime) -> None:
        """Add the expected savings of the time since the previous plan."""
        current = self.data.current if self.data else None
        if current and self._planned_at and not self.blocked:
            start = max(self._planned_at, current.start)
            end = min(now, current.end)
            duration = (current.end - current.start).total_seconds()
            if end > start and duration > 0:
                part = (end - start).total_seconds() / duration
                self.tracker.add_planned(current.savings * part)
        self._planned_at = now

    async def _async_update_data(self) -> TraderData:
        now = dt_util.now()
        self._count_planned_savings(now)
        self.overrides = [o for o in self.overrides if o.end > now]
        settings = self.settings
        if settings.mode == MODE_OFF:
            return await self._async_execute(TraderData(status=STATUS_OFF))

        try:
            plan_settings = settings.plan_settings()
            slots = await self.inputs.async_build_slots(
                now, plan_settings, self.overrides
            )
            battery = self.inputs.battery(plan_settings)
        except InputError as err:
            _LOGGER.info("Cannot plan yet: %s", err)
            return await self._async_execute(
                TraderData(status=STATUS_WAITING, message=str(err))
            )

        plan = await self.hass.async_add_executor_job(
            make_plan,
            slots,
            battery,
            Strategy(
                mode=settings.mode,
                min_profit=settings.min_profit,
                allow_grid_export=settings.allow_grid_export,
                hold_stores_solar=self.controller.entities.hold_stores_solar,
            ),
        )
        current = plan.slots[0]
        return await self._async_execute(
            TraderData(
                status=current.action,
                plan=plan,
                power_w=current.power_w,
                reason=self.text(current),
            )
        )

    def _planned_status(self, data: TraderData) -> str:
        """Status without a previous write error."""
        if data.current is not None:
            return data.current.action
        return STATUS_OFF if data.status == STATUS_OFF else STATUS_WAITING

    def _marstek_action(self, data: TraderData) -> tuple[str, int]:
        """What to tell the Marstek for the planned action."""
        current = data.current
        if self.blocked and data.status != STATUS_OFF:
            if current and current.action == ACTION_HOLD and current.standby:
                return MARSTEK_STANDBY, 0
            return ACTION_HOLD, 0
        if current is None:
            # Off or no plan: let the Marstek follow the house by itself.
            return ACTION_SELF, 0
        action = current.action
        if (
            action == ACTION_DISCHARGE
            and not current.forced
            and not self.settings.allow_grid_export
        ):
            # Without export, self-consumption follows the real house load,
            # which a fixed discharge power cannot.
            action = ACTION_SELF
        if action == ACTION_SELF and self._below_reserve(current):
            # The Marstek does not know the reserve: stop it from discharging.
            return ACTION_HOLD, 0
        if action == ACTION_SELF:
            return ACTION_SELF, 0
        if action == ACTION_HOLD and current.standby:
            # Exporting the solar surplus is worth more than storing it.
            return MARSTEK_STANDBY, 0
        return action, current.power_w

    def _below_reserve(self, current: PlannedSlot) -> bool:
        """Battery at the reserve while the house needs power from the battery."""
        soc = self.inputs.soc()
        if soc is None or soc > self.settings.min_soc:
            return False
        if self.controller.entities.hold_stores_solar:
            # Hold still stores a solar surplus, so the grid does not matter.
            return True
        grid = self.tracker.grid_power()
        if grid is not None:
            return grid >= 0
        return current.solar_kwh <= current.load_kwh

    def _discharge_limit(self) -> int:
        """Max Discharge Power of the Marstek outside hold."""
        return marstek_power(min(self.settings.max_discharge_power, MARSTEK_MAX_POWER))

    async def _async_execute(self, data: TraderData) -> TraderData:
        async with self._lock:
            return await self._async_execute_locked(data)

    async def _async_execute_locked(self, data: TraderData) -> TraderData:
        if data.status == STATUS_ERROR:
            data = replace(data, status=self._planned_status(data), message=None)
        if self.blocked and data.current is not None:
            # Waiting keeps its status and message; the Marstek holds anyway.
            if loads := self._active_loads():
                self._block_reason = describe_block(loads, self.language)
            data = replace(
                data, status=ACTION_HOLD, power_w=0, reason=self._block_reason
            )
        action, power_w = self._marstek_action(data)
        dry_run = self.settings.dry_run
        if data.status == STATUS_OFF and self._released:
            writes = []
        else:
            writes = self.controller.pending_writes(
                action, power_w, self._discharge_limit()
            )
        descriptions = [w.description for w in writes]
        if writes and not dry_run:
            try:
                await self.controller.async_write(writes)
            except HomeAssistantError as err:
                self.last_error = f"{dt_util.now():%H:%M} {err}"
                _LOGGER.error("Writing to the Marstek failed: %s", err)
                data = replace(data, status=STATUS_ERROR, message=str(err))
            else:
                self.last_error = None
        if not dry_run and data.status != STATUS_ERROR:
            self._released = data.status == STATUS_OFF
        self._last_apply = dt_util.now()
        data = replace(
            data,
            marstek_action=action if data.status != STATUS_OFF else None,
            writes=descriptions,
        )
        self._log_decision(data, dry_run)
        return data

    async def async_release(self) -> None:
        """Hand control back to the Marstek (self-consumption), e.g. on unload."""
        async with self._lock:
            writes = self.controller.pending_writes(
                ACTION_SELF, 0, self._discharge_limit()
            )
            if not writes:
                return
            try:
                await self.controller.async_write(writes)
            except HomeAssistantError as err:
                _LOGGER.warning(
                    "Could not return the Marstek to self-consumption: %s", err
                )

    def _log_decision(self, data: TraderData, dry_run: bool) -> None:
        key = (data.status, data.power_w, dry_run)
        if key == self._last_decision:
            return
        self._last_decision = key
        reason = data.reason or data.message or ""
        decision = Decision(
            dt_util.now(), data.status, data.power_w, reason, dry_run, data.writes
        )
        self.decisions.appendleft(decision)
        self.hass.bus.async_fire(
            EVENT_ACTION,
            {
                "status": data.status,
                "power_w": data.power_w,
                "marstek_power_w": marstek_power(data.power_w) if data.power_w else 0,
                "reason": reason,
                "dry_run": dry_run,
                "entity_id": f"sensor.{DOMAIN}_status",
            },
        )
