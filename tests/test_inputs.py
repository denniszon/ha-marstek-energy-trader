"""Feed-in price for each feed-in mode."""

from datetime import datetime, timedelta

import pytest

from custom_components.marstek_energy_trader.const import (
    CONF_FEED_IN_COST,
    CONF_FEED_IN_FIXED,
    CONF_FEED_IN_MODE,
    CONF_FEED_IN_OFFSET,
    FEED_IN_FIXED,
    FEED_IN_OFFSET,
    FEED_IN_SAME,
    FEED_IN_SENSOR,
)
from custom_components.marstek_energy_trader.inputs import TraderInputs
from custom_components.marstek_energy_trader.marstek import MarstekEntities
from custom_components.marstek_energy_trader.timeline import Interval
from homeassistant.core import HomeAssistant

from .common import TZ

MOMENT = datetime(2026, 11, 9, 12, 0, tzinfo=TZ)
# All-in buy price and a bare market price for the same hour.
BUY = 0.25
MARKET = 0.10


def _inputs(hass: HomeAssistant, **options) -> TraderInputs:
    inputs = TraderInputs(hass, options, MarstekEntities({}, [], []))
    inputs.feed_in_prices = [
        Interval(MOMENT, MOMENT + timedelta(hours=1), MARKET),
    ]
    return inputs


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({CONF_FEED_IN_MODE: FEED_IN_SAME}, BUY),
        ({CONF_FEED_IN_MODE: FEED_IN_OFFSET, CONF_FEED_IN_OFFSET: -0.13}, 0.12),
        ({CONF_FEED_IN_MODE: FEED_IN_FIXED, CONF_FEED_IN_FIXED: 0.07}, 0.07),
        ({CONF_FEED_IN_MODE: FEED_IN_SENSOR}, MARKET),
        # The amount is added to a separate feed-in sensor too.
        ({CONF_FEED_IN_MODE: FEED_IN_SENSOR, CONF_FEED_IN_OFFSET: 0.02}, 0.12),
        (
            {
                CONF_FEED_IN_MODE: FEED_IN_SENSOR,
                CONF_FEED_IN_OFFSET: 0.02,
                CONF_FEED_IN_COST: 0.01,
            },
            0.11,
        ),
    ],
)
def test_sell_price(hass: HomeAssistant, options: dict, expected: float) -> None:
    assert _inputs(hass, **options).sell_price(MOMENT, BUY) == pytest.approx(expected)


def test_sell_price_without_sensor_value_falls_back_to_buy(
    hass: HomeAssistant,
) -> None:
    inputs = _inputs(
        hass, **{CONF_FEED_IN_MODE: FEED_IN_SENSOR, CONF_FEED_IN_OFFSET: 0.02}
    )
    later = MOMENT + timedelta(hours=2)
    assert inputs.sell_price(later, BUY) == pytest.approx(BUY)
