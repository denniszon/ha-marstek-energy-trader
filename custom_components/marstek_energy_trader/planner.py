"""Battery schedule optimiser (no Home Assistant imports).

The planner looks at every time slot for which a price is known and picks one
action per slot: charge from the grid, discharge, hold the energy, or follow the
house (self-consumption). Dynamic programming over the state of charge finds the
cheapest combination for the whole horizon, so a cheap night can be used for an
expensive evening even when there are cheaper hours in between.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import math
from statistics import median
from typing import Any

from .const import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    ACTION_HOLD,
    ACTION_SELF,
    MODE_PRICE,
    MODE_SOLAR,
    MODE_SOLAR_PRICE,
)

# Allowed actions per mode, in order of preference when they cost the same.
MODE_ACTIONS = {
    MODE_SOLAR: (ACTION_SELF,),
    MODE_PRICE: (ACTION_HOLD, ACTION_CHARGE, ACTION_DISCHARGE),
    MODE_SOLAR_PRICE: (ACTION_SELF, ACTION_HOLD, ACTION_CHARGE, ACTION_DISCHARGE),
}

# Charging or discharging below this power is not worth switching modes for.
MIN_POWER_W = 100
# Number of state-of-charge steps between empty and full.
SOC_STEPS = 100
EPSILON = 1e-9


@dataclass(frozen=True)
class Slot:
    """Input for one time slot. Energy in kWh, prices in €/kWh."""

    start: datetime
    end: datetime
    buy: float
    sell: float
    solar_kwh: float = 0.0
    load_kwh: float = 0.0
    forced_action: str | None = None
    forced_power_w: float | None = None

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    @property
    def net_kwh(self) -> float:
        """Energy the house needs from the grid without battery (negative: surplus)."""
        return self.load_kwh - self.solar_kwh


@dataclass(frozen=True)
class Battery:
    """Battery limits. Powers in W, energy in kWh, efficiency as a fraction."""

    capacity_kwh: float
    soc_kwh: float
    min_soc_kwh: float
    max_soc_kwh: float
    max_charge_w: float
    max_discharge_w: float
    efficiency: float = 0.85


@dataclass(frozen=True)
class Strategy:
    """How eager the planner is to trade."""

    mode: str
    # Minimum profit per discharged kWh; covers battery wear and uncertainty.
    min_profit: float = 0.04
    # May the battery deliver more than the house uses (export to the grid)?
    allow_grid_export: bool = False


@dataclass(frozen=True)
class Reason:
    """Why an action was chosen; turned into text by reasons.describe()."""

    code: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlannedSlot:
    """The planned action for one slot and its expected effect."""

    start: datetime
    end: datetime
    action: str
    power_w: int
    soc_start: float
    soc_end: float
    buy: float
    sell: float
    solar_kwh: float
    load_kwh: float
    # Energy into (+) or out of (-) the battery, measured on the AC side.
    battery_kwh: float
    # Energy from (+) or to (-) the grid.
    grid_kwh: float
    cost: float
    baseline_cost: float
    reason: Reason
    forced: bool = False

    @property
    def savings(self) -> float:
        """Cost without battery minus cost with this plan, for this slot."""
        return self.baseline_cost - self.cost


@dataclass(frozen=True)
class Plan:
    """Result of the planner."""

    slots: list[PlannedSlot]
    expected_savings: float
    # Value per kWh of energy left in the battery at the end of the horizon.
    terminal_price: float


def grid_cost(grid_kwh: float, buy: float, sell: float) -> float:
    """Cost of importing (positive) or exporting (negative) energy."""
    return grid_kwh * (buy if grid_kwh > 0 else sell)


@dataclass(frozen=True)
class _Option:
    action: str
    target: int
    battery_kwh: float


class _Grid:
    """Discrete state-of-charge grid and the moves possible within one slot."""

    def __init__(self, battery: Battery, strategy: Strategy) -> None:
        self.battery = battery
        self.strategy = strategy
        self.step = battery.capacity_kwh / SOC_STEPS
        self.eta = math.sqrt(battery.efficiency)
        self.low = min(SOC_STEPS, math.ceil(battery.min_soc_kwh / self.step - EPSILON))
        self.high = max(0, math.floor(battery.max_soc_kwh / self.step + EPSILON))

    def index(self, soc_kwh: float) -> int:
        return max(0, min(SOC_STEPS, round(soc_kwh / self.step)))

    def options(
        self,
        slot: Slot,
        i: int,
        actions: tuple[str, ...],
        allow_export: bool | None = None,
    ) -> list[_Option]:
        if allow_export is None:
            allow_export = self.strategy.allow_grid_export
        options: list[_Option] = []
        step, eta, hours = self.step, self.eta, slot.hours
        room = max(0, self.high - i)
        available = max(0, i - self.low)
        min_moves = max(1, math.ceil(MIN_POWER_W / 1000 * hours * eta / step))
        for action in actions:
            if action == ACTION_HOLD:
                options.append(_Option(ACTION_HOLD, i, 0.0))
            elif action == ACTION_SELF:
                options.append(self._self_consumption(slot, i, room, available))
            elif action == ACTION_CHARGE:
                most = int(
                    self.battery.max_charge_w / 1000 * hours * eta / step + EPSILON
                )
                options.extend(
                    _Option(ACTION_CHARGE, i + moves, moves * step / eta)
                    for moves in range(min_moves, min(most, room) + 1)
                )
            elif action == ACTION_DISCHARGE:
                most = int(
                    self.battery.max_discharge_w / 1000 * hours / eta / step + EPSILON
                )
                if not allow_export:
                    most = min(most, int(max(slot.net_kwh, 0) / eta / step + EPSILON))
                options.extend(
                    _Option(ACTION_DISCHARGE, i - moves, -moves * step * eta)
                    for moves in range(min_moves, min(most, available) + 1)
                )
        return options

    def _self_consumption(
        self, slot: Slot, i: int, room: int, available: int
    ) -> _Option:
        """Follow the house: cover the shortage or store the surplus."""
        step, eta, hours, net = self.step, self.eta, slot.hours, slot.net_kwh
        if net > 0:
            wanted = min(net, self.battery.max_discharge_w / 1000 * hours)
            moves = min(int(wanted / eta / step + EPSILON), available)
            return _Option(ACTION_SELF, i - moves, -moves * step * eta)
        wanted = min(-net, self.battery.max_charge_w / 1000 * hours)
        moves = min(int(wanted * eta / step + EPSILON), room)
        return _Option(ACTION_SELF, i + moves, moves * step / eta)


def _forced_options(
    grid: _Grid, slot: Slot, i: int
) -> tuple[list[_Option], tuple[str, ...]]:
    """Options for a slot with a manual override: only the chosen action."""
    action = slot.forced_action
    # A manual discharge runs at the chosen power, also when that means export.
    options = grid.options(slot, i, (action,), allow_export=True)
    if action in (ACTION_CHARGE, ACTION_DISCHARGE) and options:
        wanted = slot.forced_power_w or (
            grid.battery.max_charge_w
            if action == ACTION_CHARGE
            else grid.battery.max_discharge_w
        )
        wanted_kwh = wanted / 1000 * slot.hours
        options = [
            min(options, key=lambda option: abs(abs(option.battery_kwh) - wanted_kwh))
        ]
    if not options:
        options = [_Option(ACTION_HOLD, i, 0.0)]
    return options, (action, ACTION_HOLD)


def make_plan(slots: list[Slot], battery: Battery, strategy: Strategy) -> Plan:
    """Find the cheapest action per slot."""
    if not slots:
        return Plan([], 0.0, 0.0)
    grid = _Grid(battery, strategy)
    actions = MODE_ACTIONS[strategy.mode]
    terminal_price = median(slot.buy for slot in slots)
    # Energy left at the end is worth what it saves later, minus losses and wear.
    terminal_value = max(terminal_price * grid.eta - strategy.min_profit, 0.0)

    states = range(SOC_STEPS + 1)
    future = [-i * grid.step * terminal_value for i in states]
    choices: list[list[_Option]] = [[] for _ in slots]
    for t in range(len(slots) - 1, -1, -1):
        slot = slots[t]
        current: list[float] = []
        for i in states:
            if slot.forced_action:
                options, order = _forced_options(grid, slot, i)
            else:
                options, order = grid.options(slot, i, actions), actions
            best: _Option | None = None
            best_key: tuple[float, int] = (math.inf, 0)
            for option in options:
                cost = (
                    grid_cost(slot.net_kwh + option.battery_kwh, slot.buy, slot.sell)
                    + strategy.min_profit * max(-option.battery_kwh, 0.0)
                    + future[option.target]
                )
                key = (round(cost, 9), order.index(option.action))
                if key < best_key:
                    best, best_key = option, key
            assert best is not None
            choices[t].append(best)
            current.append(best_key[0])
        future = current

    planned: list[PlannedSlot] = []
    i = grid.index(battery.soc_kwh)
    for t, slot in enumerate(slots):
        option = choices[t][i]
        grid_kwh = slot.net_kwh + option.battery_kwh
        planned.append(
            PlannedSlot(
                start=slot.start,
                end=slot.end,
                action=option.action,
                power_w=round(abs(option.battery_kwh) / slot.hours * 1000),
                soc_start=round(i * grid.step / battery.capacity_kwh * 100, 1),
                soc_end=round(
                    option.target * grid.step / battery.capacity_kwh * 100, 1
                ),
                buy=slot.buy,
                sell=slot.sell,
                solar_kwh=slot.solar_kwh,
                load_kwh=slot.load_kwh,
                battery_kwh=option.battery_kwh,
                grid_kwh=grid_kwh,
                cost=grid_cost(grid_kwh, slot.buy, slot.sell),
                baseline_cost=grid_cost(slot.net_kwh, slot.buy, slot.sell),
                reason=Reason("pending"),
                forced=slot.forced_action is not None,
            )
        )
        i = option.target

    stored_gain = (
        (planned[-1].soc_end - planned[0].soc_start) / 100 * battery.capacity_kwh
    )
    expected = sum(slot.savings for slot in planned) + stored_gain * terminal_value
    explained = [
        _with_reason(slot, planned[index + 1 :]) for index, slot in enumerate(planned)
    ]
    return Plan(explained, round(expected, 4), terminal_price)


def _delivering(slots: list[PlannedSlot]) -> list[PlannedSlot]:
    return [slot for slot in slots if slot.battery_kwh < 0]


def _reason(slot: PlannedSlot, later: list[PlannedSlot]) -> Reason:
    if slot.forced:
        return Reason("override")
    if slot.action == ACTION_CHARGE:
        if slot.buy < 0:
            return Reason("charge_negative", {"price": slot.buy})
        if slot.grid_kwh < 0:
            # Still exporting: the charge comes from the solar surplus.
            return Reason("charge_solar", {"kwh": round(slot.battery_kwh, 2)})
        if use := max(_delivering(later), key=lambda s: s.buy, default=None):
            return Reason(
                "charge_cheap",
                {"price": slot.buy, "later_price": use.buy, "later_start": use.start},
            )
        return Reason("charge_cheap", {"price": slot.buy})
    if slot.action == ACTION_DISCHARGE:
        cheapest = min(later, key=lambda s: s.buy, default=None)
        params: dict[str, Any] = {"price": slot.buy}
        if cheapest:
            params |= {"later_price": cheapest.buy, "later_start": cheapest.start}
        if slot.grid_kwh < 0:
            return Reason("discharge_export", params | {"price": slot.sell})
        return Reason("discharge_expensive", params)
    if slot.action == ACTION_HOLD:
        if use := next(iter(_delivering(later)), None):
            return Reason(
                "hold_for_later",
                {"price": slot.buy, "later_price": use.buy, "later_start": use.start},
            )
        return Reason("hold", {"price": slot.buy})
    if slot.battery_kwh < 0:
        return Reason("self_cover", {"price": slot.buy})
    if slot.battery_kwh > 0:
        return Reason("self_store_solar", {"kwh": round(slot.battery_kwh, 2)})
    if slot.solar_kwh > slot.load_kwh:
        return Reason("self_full", {})
    return Reason("self_empty", {})


def _with_reason(slot: PlannedSlot, later: list[PlannedSlot]) -> PlannedSlot:
    return replace(slot, reason=_reason(slot, later))
