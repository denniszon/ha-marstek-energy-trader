"""Scenarios for the planner."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.marstek_energy_trader.planner import (
    Battery,
    Slot,
    Strategy,
    make_plan,
)

TZ = ZoneInfo("Europe/Amsterdam")
START = datetime(2026, 10, 26, 0, 0, tzinfo=TZ)
# A typical winter day: cheap night, expensive evening.
DAY = [0.20, 0.18, 0.15, 0.14, 0.14, 0.16, 0.22, 0.30, 0.32, 0.28, 0.24, 0.20]
DAY += [0.18, 0.19, 0.21, 0.25, 0.30, 0.38, 0.42, 0.40, 0.33, 0.28, 0.24, 0.22]


def _slots(prices, *, solar=None, load=0.4, minutes=60, sell=None):
    step = timedelta(minutes=minutes)
    return [
        Slot(
            start=START + step * i,
            end=START + step * (i + 1),
            buy=price,
            sell=price if sell is None else sell,
            solar_kwh=(solar[i] if solar else 0.0),
            load_kwh=load * minutes / 60,
        )
        for i, price in enumerate(prices)
    ]


def _battery(soc=1.0, **kwargs):
    values = {
        "capacity_kwh": 5.12,
        "soc_kwh": soc,
        "min_soc_kwh": 1.0,
        "max_soc_kwh": 5.12,
        "max_charge_w": 2500,
        "max_discharge_w": 2500,
        "efficiency": 0.85,
    }
    return Battery(**(values | kwargs))


def _actions(plan):
    return [slot.action for slot in plan.slots]


def test_charges_at_night_and_discharges_in_the_evening() -> None:
    plan = make_plan(_slots(DAY), _battery(), Strategy("price", 0.04, True))
    actions = _actions(plan)
    assert actions[3] == "charge"
    assert actions[18] == "discharge"
    # Never charges in the expensive evening.
    assert "charge" not in actions[16:21]
    assert plan.expected_savings > 0
    assert plan.slots[3].reason.code == "charge_cheap"


def test_small_spread_is_not_traded() -> None:
    flat = [0.25, 0.24, 0.26, 0.25, 0.27, 0.26] * 4
    plan = make_plan(_slots(flat), _battery(), Strategy("price", 0.04, True))
    assert set(_actions(plan)) == {"hold"}
    assert plan.expected_savings == 0


def test_higher_minimum_profit_trades_less() -> None:
    eager = make_plan(_slots(DAY), _battery(), Strategy("price", 0.0, True))
    careful = make_plan(_slots(DAY), _battery(), Strategy("price", 0.15, True))
    charged = [
        sum(s.battery_kwh for s in plan.slots if s.action == "charge")
        for plan in (eager, careful)
    ]
    assert charged[0] > charged[1]


def test_no_export_limits_discharge_to_the_house() -> None:
    plan = make_plan(_slots(DAY), _battery(), Strategy("price", 0.04, False))
    for slot in plan.slots:
        assert slot.grid_kwh >= -1e-6


def test_expected_sun_reduces_grid_charging() -> None:
    solar = [0.0] * 8 + [1.5, 2.5, 3.0, 3.0, 3.0, 2.5, 1.5, 0.5] + [0.0] * 8
    strategy = Strategy("solar_price", 0.04, False)

    def grid_charged(plan):
        return sum(max(0.0, min(s.battery_kwh, s.grid_kwh)) for s in plan.slots)

    sunny = make_plan(_slots(DAY, solar=solar, sell=0.10), _battery(), strategy)
    cloudy = make_plan(_slots(DAY, sell=0.10), _battery(), strategy)
    # Only what the house needs before the sun comes up is bought at night.
    assert grid_charged(sunny) < grid_charged(cloudy)
    assert grid_charged(sunny) <= 1.0
    assert any(slot.reason.code == "self_store_solar" for slot in sunny.slots)


def test_negative_price_charges() -> None:
    prices = [0.20] * 10 + [-0.05, -0.10] + [0.30] * 12
    plan = make_plan(
        _slots(prices, sell=0.0), _battery(), Strategy("solar_price", 0.04, False)
    )
    assert plan.slots[11].action == "charge"
    assert plan.slots[11].reason.code == "charge_negative"


def test_soc_limits_are_respected() -> None:
    battery = _battery(soc=2.56, min_soc_kwh=1.5, max_soc_kwh=4.6)
    plan = make_plan(_slots(DAY), battery, Strategy("price", 0.0, True))
    for slot in plan.slots:
        assert 1.5 / 5.12 * 100 - 1 <= slot.soc_end <= 4.6 / 5.12 * 100 + 1


def test_solar_mode_only_follows_the_house() -> None:
    plan = make_plan(_slots(DAY), _battery(soc=3.0), Strategy("solar", 0.04, False))
    assert set(_actions(plan)) == {"self_consumption"}
    assert all(slot.battery_kwh <= 0 for slot in plan.slots)


def test_quarter_hour_slots() -> None:
    quarters = [price for price in DAY for _ in range(4)]
    plan = make_plan(
        _slots(quarters, minutes=15), _battery(), Strategy("price", 0.04, True)
    )
    assert len(plan.slots) == 96
    assert plan.slots[14].action == "charge"
    assert max(slot.power_w for slot in plan.slots) <= 2500


def test_override_is_followed() -> None:
    slots = _slots(DAY)
    slots[18] = Slot(
        slots[18].start,
        slots[18].end,
        slots[18].buy,
        slots[18].sell,
        load_kwh=0.4,
        forced_action="hold",
    )
    plan = make_plan(slots, _battery(), Strategy("price", 0.04, True))
    assert plan.slots[18].action == "hold"
    assert plan.slots[18].reason.code == "override"


def test_forced_charge_power() -> None:
    slots = _slots(DAY)
    slots[0] = Slot(
        slots[0].start,
        slots[0].end,
        slots[0].buy,
        slots[0].sell,
        load_kwh=0.4,
        forced_action="charge",
        forced_power_w=1000,
    )
    plan = make_plan(slots, _battery(), Strategy("price", 0.04, True))
    assert plan.slots[0].action == "charge"
    assert abs(plan.slots[0].power_w - 1000) < 60


def test_dst_day_with_25_hours() -> None:
    start = datetime(2026, 10, 25, 0, 0, tzinfo=TZ)
    hours = [start + timedelta(hours=h) for h in range(26)]
    slots = [
        Slot(a, b, DAY[i % 24], DAY[i % 24], load_kwh=0.4)
        for i, (a, b) in enumerate(zip(hours, hours[1:], strict=False))
    ]
    plan = make_plan(slots, _battery(), Strategy("price", 0.04, True))
    assert len(plan.slots) == 25
    assert all(slot.end > slot.start for slot in plan.slots)


def test_empty_input() -> None:
    plan = make_plan([], _battery(), Strategy("price"))
    assert plan.slots == []
