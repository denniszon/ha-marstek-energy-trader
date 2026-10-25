"""Marstek Energy Trader: charge and discharge a Marstek Venus on price and sun."""

from __future__ import annotations

from datetime import timedelta

import probatio

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import ACTIONS, CONF_MARSTEK_ENTRY, DOMAIN
from .coordinator import TraderConfigEntry, TraderCoordinator
from .marstek import resolve_entities

PLATFORMS = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_OVERRIDE_SCHEMA = probatio.Schema(
    {
        probatio.Required("action"): probatio.In(ACTIONS),
        probatio.Optional("start"): cv.datetime,
        probatio.Exclusive("end", "until"): cv.datetime,
        probatio.Exclusive("hours", "until"): probatio.All(
            probatio.Coerce(float), probatio.Range(min=0.25, max=48)
        ),
        probatio.Optional("power_w"): probatio.All(
            probatio.Coerce(float), probatio.Range(min=100, max=2500)
        ),
    }
)


def _coordinator(hass: HomeAssistant) -> TraderCoordinator:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_loaded"
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services."""

    async def set_override(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        start = call.data.get("start") or dt_util.now()
        start = (
            dt_util.as_local(start)
            if start.tzinfo
            else start.replace(tzinfo=dt_util.get_default_time_zone())
        )
        if "end" in call.data:
            end = call.data["end"]
            if end.tzinfo is None:
                end = end.replace(tzinfo=dt_util.get_default_time_zone())
        else:
            end = start + timedelta(hours=call.data.get("hours", 1))
        if end <= start:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="end_before_start"
            )
        coordinator.set_override(
            call.data["action"], start, end, call.data.get("power_w")
        )
        await coordinator.async_refresh()

    async def clear_override(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        coordinator.clear_overrides()
        await coordinator.async_refresh()

    async def replan(call: ServiceCall) -> None:
        await _coordinator(hass).async_refresh()

    hass.services.async_register(
        DOMAIN, "set_override", set_override, schema=SET_OVERRIDE_SCHEMA
    )
    hass.services.async_register(DOMAIN, "clear_override", clear_override)
    hass.services.async_register(DOMAIN, "replan", replan)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TraderConfigEntry) -> bool:
    """Set up the trader from a config entry."""
    options = {**entry.data, **entry.options}
    marstek = resolve_entities(hass, options[CONF_MARSTEK_ENTRY])
    if not marstek.usable:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="marstek_entities",
            translation_placeholders={
                "entities": ", ".join(marstek.missing + marstek.disabled)
            },
        )

    coordinator = TraderCoordinator(hass, entry, marstek)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Entities have restored their settings now, so the first plan uses them.
    await coordinator.async_start()
    await coordinator.async_refresh()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TraderConfigEntry) -> bool:
    """Unload a config entry; the Marstek goes back to self-consumption."""
    coordinator = entry.runtime_data
    if not coordinator.settings.dry_run:
        await coordinator.async_release()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
