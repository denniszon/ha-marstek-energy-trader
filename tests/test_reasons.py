"""Texts for planner reasons."""

from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.marstek_energy_trader.planner import Reason
from custom_components.marstek_energy_trader.reasons import TEXTS, describe

TZ = ZoneInfo("Europe/Amsterdam")


def test_texts_have_the_same_codes() -> None:
    assert TEXTS["en"].keys() == TEXTS["nl"].keys()


def test_describe() -> None:
    reason = Reason(
        "charge_cheap",
        {
            "price": 0.12,
            "later_price": 0.38,
            "later_start": datetime(2026, 10, 26, 18, tzinfo=TZ),
        },
    )
    assert describe(reason, "nl", TZ) == (
        "Laden voor €0.12: de energie wordt om 18:00 gebruikt bij €0.38"
    )
    assert describe(Reason("charge_cheap", {"price": 0.12}), "en-GB", TZ).startswith(
        "Charging at €0.12: cheaper"
    )
    assert describe(Reason("self_store_solar", {"kwh": 1.234}), "fr", TZ) == (
        "Storing 1.23 kWh of solar surplus"
    )
