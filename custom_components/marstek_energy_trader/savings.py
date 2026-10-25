"""Measure what the battery actually saved (no Home Assistant imports).

Every measurement covers a short period. The Marstek energy counters tell how
much was charged and discharged; the battery and grid power tell where that
energy came from or went to:

- charged from the grid costs the buy price;
- charged from solar costs the feed-in price you missed;
- discharged into the house saves the buy price;
- discharged to the grid earns the feed-in price.

Savings = value of the discharged energy - cost of the charged energy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class SavingsTotals:
    """Running totals. Money in €, energy in kWh."""

    savings: float = 0.0
    charge_cost: float = 0.0
    discharge_value: float = 0.0
    grid_to_battery: float = 0.0
    solar_to_battery: float = 0.0
    battery_to_home: float = 0.0
    battery_to_grid: float = 0.0
    # Energy in the battery and what it cost, for the average stored price.
    stored_kwh: float = 0.0
    stored_cost: float = 0.0

    @property
    def average_stored_price(self) -> float | None:
        if self.stored_kwh <= 0.01:
            return None
        return self.stored_cost / self.stored_kwh

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SavingsTotals:
        names = {field.name for field in fields(cls)}
        return cls(**{k: float(v) for k, v in (data or {}).items() if k in names})


def _share(part_w: float | None, total_w: float) -> float:
    """Fraction of total_w explained by part_w (0-1)."""
    if part_w is None:
        return 1.0
    if total_w <= 0:
        return 1.0 if part_w > 0 else 0.0
    return max(0.0, min(1.0, part_w / total_w))


def account(
    totals: SavingsTotals,
    *,
    charged_kwh: float,
    discharged_kwh: float,
    battery_w: float | None,
    grid_w: float | None,
    buy: float,
    sell: float,
) -> float:
    """Add one measurement to the totals and return the savings it adds.

    battery_w: positive while charging, negative while discharging.
    grid_w: positive while importing, negative while exporting; None if unknown,
    in which case charging counts as from the grid and discharging as to the house.
    """
    charged_kwh = max(charged_kwh, 0.0)
    discharged_kwh = max(discharged_kwh, 0.0)
    change = 0.0

    if charged_kwh:
        charge_w = abs(battery_w) if battery_w else 0.0
        from_grid = charged_kwh * _share(
            None if grid_w is None else max(grid_w, 0.0), charge_w
        )
        from_solar = charged_kwh - from_grid
        cost = from_grid * buy + from_solar * sell
        totals.grid_to_battery += from_grid
        totals.solar_to_battery += from_solar
        totals.charge_cost += cost
        totals.stored_kwh += charged_kwh
        totals.stored_cost += cost
        change -= cost

    if discharged_kwh:
        discharge_w = abs(battery_w) if battery_w else 0.0
        to_grid = discharged_kwh * (
            0.0 if grid_w is None else _share(max(-grid_w, 0.0), discharge_w)
        )
        to_home = discharged_kwh - to_grid
        value = to_home * buy + to_grid * sell
        totals.battery_to_home += to_home
        totals.battery_to_grid += to_grid
        totals.discharge_value += value
        if totals.stored_kwh > 0:
            used = min(discharged_kwh, totals.stored_kwh)
            totals.stored_cost -= totals.stored_cost * used / totals.stored_kwh
            totals.stored_kwh -= used
        change += value

    totals.savings += change
    return change
