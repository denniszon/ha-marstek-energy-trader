"""Readable explanations for planner decisions (no Home Assistant imports)."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any

from .planner import Reason

TEXTS = {
    "en": {
        "override": "Manual override",
        "charge_negative": "Charging: the price is negative ({price}), you get paid",
        "charge_cheap": "Charging at {price}: the energy is used at {later_start} "
        "for {later_price}",
        "charge_cheap_short": "Charging at {price}: cheaper than the rest of the plan",
        "charge_solar": "Storing {kwh} kWh of the solar surplus at a fixed power",
        "discharge_expensive": "Discharging at {price}: cheaper again at "
        "{later_start} ({later_price})",
        "discharge_export": "Selling to the grid at {price}: cheaper again at "
        "{later_start} ({later_price})",
        "discharge_expensive_short": "Discharging at {price}",
        "discharge_export_short": "Selling to the grid at {price}",
        "hold_for_later": "Holding energy for {later_start} ({later_price}); "
        "now {price}",
        "hold": "Waiting: the price ({price}) is not worth using the battery for",
        "self_cover": "Covering the house from the battery (price {price})",
        "self_store_solar": "Storing {kwh} kWh of solar surplus",
        "self_full": "Battery full, the solar surplus goes to the grid",
        "self_empty": "Battery at its reserve, the house uses the grid",
        "pending": "",
    },
    "nl": {
        "override": "Handmatig overschreven",
        "charge_negative": "Laden: de prijs is negatief ({price}), je krijgt geld",
        "charge_cheap": "Laden voor {price}: de energie wordt om {later_start} "
        "gebruikt bij {later_price}",
        "charge_cheap_short": "Laden voor {price}: goedkoper dan de rest van de "
        "planning",
        "charge_solar": "{kwh} kWh van het zonne-overschot opslaan met vast vermogen",
        "discharge_expensive": "Ontladen bij {price}: om {later_start} weer "
        "goedkoper ({later_price})",
        "discharge_export": "Terugleveren voor {price}: om {later_start} weer "
        "goedkoper ({later_price})",
        "discharge_expensive_short": "Ontladen bij {price}",
        "discharge_export_short": "Terugleveren voor {price}",
        "hold_for_later": "Energie bewaren voor {later_start} ({later_price}); "
        "nu {price}",
        "hold": "Wachten: de prijs ({price}) is het gebruik van de batterij niet waard",
        "self_cover": "Huis gebruikt de batterij (prijs {price})",
        "self_store_solar": "{kwh} kWh zonne-overschot opslaan",
        "self_full": "Batterij vol, het zonne-overschot gaat naar het net",
        "self_empty": "Batterij op reserve, het huis gebruikt het net",
        "pending": "",
    },
}


def _format(value: Any, tz: tzinfo) -> str:
    if isinstance(value, datetime):
        return value.astimezone(tz).strftime("%H:%M")
    if isinstance(value, float):
        return f"€{value:.2f}"
    return str(value)


def describe(reason: Reason, language: str, tz: tzinfo) -> str:
    """Text for a reason in the given language (English if not available)."""
    texts = TEXTS.get(language.split("-")[0], TEXTS["en"])
    code = reason.code
    if "later_start" not in reason.params and f"{code}_short" in texts:
        code = f"{code}_short"
    params = {key: _format(value, tz) for key, value in reason.params.items()}
    if "kwh" in reason.params:
        params["kwh"] = f"{reason.params['kwh']:.2f}"
    return texts[code].format(**params)
