"""Valuing charged and discharged energy."""

import pytest

from custom_components.marstek_energy_trader.savings import SavingsTotals, account


def test_charge_from_grid_and_discharge_to_home() -> None:
    totals = SavingsTotals()
    account(
        totals,
        charged_kwh=1.0,
        discharged_kwh=0,
        battery_w=2000,
        grid_w=2400,
        buy=0.12,
        sell=0.12,
    )
    account(
        totals,
        charged_kwh=0,
        discharged_kwh=0.85,
        battery_w=-800,
        grid_w=0,
        buy=0.40,
        sell=0.40,
    )
    assert totals.grid_to_battery == 1.0
    assert totals.battery_to_home == 0.85
    assert totals.savings == pytest.approx(0.85 * 0.40 - 0.12)


def test_solar_charge_costs_the_feed_in_price() -> None:
    totals = SavingsTotals()
    # Exporting while charging: everything comes from solar.
    account(
        totals,
        charged_kwh=0.5,
        discharged_kwh=0,
        battery_w=1000,
        grid_w=-300,
        buy=0.30,
        sell=0.05,
    )
    assert totals.solar_to_battery == 0.5
    assert totals.charge_cost == pytest.approx(0.025)


def test_partly_from_grid() -> None:
    totals = SavingsTotals()
    account(
        totals,
        charged_kwh=1.0,
        discharged_kwh=0,
        battery_w=2000,
        grid_w=500,
        buy=0.30,
        sell=0.05,
    )
    assert totals.grid_to_battery == pytest.approx(0.25)
    assert totals.solar_to_battery == pytest.approx(0.75)


def test_discharge_to_grid_earns_the_feed_in_price() -> None:
    totals = SavingsTotals()
    account(
        totals,
        charged_kwh=0,
        discharged_kwh=1.0,
        battery_w=-2000,
        grid_w=-1500,
        buy=0.40,
        sell=0.30,
    )
    assert totals.battery_to_grid == pytest.approx(0.75)
    assert totals.discharge_value == pytest.approx(0.25 * 0.40 + 0.75 * 0.30)


def test_without_grid_sensor() -> None:
    totals = SavingsTotals()
    account(
        totals,
        charged_kwh=1.0,
        discharged_kwh=0,
        battery_w=None,
        grid_w=None,
        buy=0.10,
        sell=0.05,
    )
    account(
        totals,
        charged_kwh=0,
        discharged_kwh=1.0,
        battery_w=None,
        grid_w=None,
        buy=0.30,
        sell=0.05,
    )
    assert totals.grid_to_battery == 1.0
    assert totals.battery_to_home == 1.0


def test_average_stored_price() -> None:
    totals = SavingsTotals()
    account(
        totals,
        charged_kwh=2.0,
        discharged_kwh=0,
        battery_w=2000,
        grid_w=2000,
        buy=0.10,
        sell=0.10,
    )
    account(
        totals,
        charged_kwh=2.0,
        discharged_kwh=0,
        battery_w=2000,
        grid_w=2000,
        buy=0.20,
        sell=0.20,
    )
    assert totals.average_stored_price == pytest.approx(0.15)
    account(
        totals,
        charged_kwh=0,
        discharged_kwh=4.0,
        battery_w=-2000,
        grid_w=0,
        buy=0.4,
        sell=0.4,
    )
    assert totals.average_stored_price is None


def test_round_trip_through_dict() -> None:
    totals = SavingsTotals(savings=1.5, grid_to_battery=3)
    assert SavingsTotals.from_dict(totals.as_dict()) == totals
    assert SavingsTotals.from_dict(None) == SavingsTotals()
