"""Logbook entries for every change of action, with the reason."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_ACTION


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[
        [str, str, Callable[[Event], dict[str, str | None]]], None
    ],
) -> None:
    """Describe the action events."""

    @callback
    def describe(event: Event) -> dict[str, str | None]:
        data = event.data
        message = str(data.get("status"))
        if data.get("power_w"):
            message += f" {data['power_w']} W"
        if data.get("reason"):
            message += f": {data['reason']}"
        if data.get("dry_run"):
            message = f"[dry run] {message}"
        return {
            LOGBOOK_ENTRY_NAME: "Marstek Energy Trader",
            LOGBOOK_ENTRY_MESSAGE: message,
            LOGBOOK_ENTRY_ENTITY_ID: data.get("entity_id"),
        }

    async_describe_event(DOMAIN, EVENT_ACTION, describe)
