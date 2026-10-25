"""Logbook text for action changes."""

from custom_components.marstek_energy_trader.const import DOMAIN, EVENT_ACTION
from custom_components.marstek_energy_trader.logbook import async_describe_events
from homeassistant.core import Event, HomeAssistant


def test_describe_event(hass: HomeAssistant) -> None:
    described = {}

    def register(domain, event_type, describe):
        described[(domain, event_type)] = describe

    async_describe_events(hass, register)
    describe = described[(DOMAIN, EVENT_ACTION)]
    entry = describe(
        Event(
            EVENT_ACTION,
            {
                "status": "charge",
                "power_w": 2000,
                "reason": "Charging at €0.14",
                "dry_run": True,
                "entity_id": f"sensor.{DOMAIN}_status",
            },
        )
    )
    assert entry["message"] == "[dry run] charge 2000 W: Charging at €0.14"
    assert entry["entity_id"] == f"sensor.{DOMAIN}_status"
