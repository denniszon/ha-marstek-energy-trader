"""Config and options flow: battery, prices and forecasts."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_BASE_LOAD,
    CONF_BATTERY_POWER_SIGN,
    CONF_CAPACITY,
    CONF_CONSUMPTION_SENSOR,
    CONF_EFFICIENCY,
    CONF_FEED_IN_COST,
    CONF_FEED_IN_FIXED,
    CONF_FEED_IN_MODE,
    CONF_FEED_IN_OFFSET,
    CONF_FEED_IN_SENSOR,
    CONF_FORECAST_SOLAR_ENTRY,
    CONF_GRID_SENSOR,
    CONF_GRID_SIGN,
    CONF_MARSTEK_ENTRY,
    CONF_PRICE_SENSOR,
    CONF_PV_POWER,
    CONF_SOLAR_SOURCE,
    CONF_SOLCAST_TODAY,
    CONF_SOLCAST_TOMORROW,
    CONF_WEATHER,
    DEFAULT_BASE_LOAD,
    DEFAULT_EFFICIENCY,
    DOMAIN,
    FEED_IN_FIXED,
    FEED_IN_OFFSET,
    FEED_IN_SAME,
    FEED_IN_SENSOR,
    MARSTEK_DOMAIN,
    SIGN_IMPORT_NEGATIVE,
    SIGN_IMPORT_POSITIVE,
    SIGN_POSITIVE_CHARGE,
    SIGN_POSITIVE_DISCHARGE,
    SOLAR_FORECAST_SOLAR,
    SOLAR_NONE,
    SOLAR_SOLCAST,
)
from .inputs import InputError, read_price_sensor
from .marstek import resolve_entities

TITLE = "Marstek Energy Trader"


def _choice(key: str, options: list[str]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=key,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _number(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX
    )
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _sensor(device_class: str | None = None) -> selector.EntitySelector:
    entity_filter: dict[str, Any] = {"domain": "sensor"}
    if device_class:
        entity_filter["device_class"] = device_class
    return selector.EntitySelector(
        selector.EntitySelectorConfig(filter=[entity_filter])
    )


BATTERY_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_MARSTEK_ENTRY): selector.ConfigEntrySelector(
            selector.ConfigEntrySelectorConfig(integration=MARSTEK_DOMAIN)
        ),
        probatio.Required(
            CONF_BATTERY_POWER_SIGN, default=SIGN_POSITIVE_DISCHARGE
        ): _choice(
            CONF_BATTERY_POWER_SIGN, [SIGN_POSITIVE_DISCHARGE, SIGN_POSITIVE_CHARGE]
        ),
        probatio.Optional(CONF_CAPACITY): _number(0.5, 100, 0.01, "kWh"),
        probatio.Required(CONF_EFFICIENCY, default=DEFAULT_EFFICIENCY): _number(
            50, 100, 1, "%"
        ),
    }
)

PRICES_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_PRICE_SENSOR): _sensor(),
        probatio.Required(CONF_FEED_IN_MODE, default=FEED_IN_SAME): _choice(
            CONF_FEED_IN_MODE,
            [FEED_IN_SAME, FEED_IN_SENSOR, FEED_IN_OFFSET, FEED_IN_FIXED],
        ),
        probatio.Optional(CONF_FEED_IN_SENSOR): _sensor(),
        probatio.Optional(CONF_FEED_IN_OFFSET, default=0): _number(-1, 1, 0.001),
        probatio.Optional(CONF_FEED_IN_FIXED, default=0): _number(-1, 2, 0.001),
        probatio.Optional(CONF_FEED_IN_COST, default=0): _number(0, 1, 0.001),
    }
)

SOURCES_SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_GRID_SENSOR): _sensor("power"),
        probatio.Required(CONF_GRID_SIGN, default=SIGN_IMPORT_POSITIVE): _choice(
            CONF_GRID_SIGN, [SIGN_IMPORT_POSITIVE, SIGN_IMPORT_NEGATIVE]
        ),
        probatio.Required(CONF_SOLAR_SOURCE, default=SOLAR_NONE): _choice(
            CONF_SOLAR_SOURCE, [SOLAR_NONE, SOLAR_FORECAST_SOLAR, SOLAR_SOLCAST]
        ),
        probatio.Optional(CONF_FORECAST_SOLAR_ENTRY): selector.ConfigEntrySelector(
            selector.ConfigEntrySelectorConfig(integration=SOLAR_FORECAST_SOLAR)
        ),
        probatio.Optional(CONF_SOLCAST_TODAY): _sensor(),
        probatio.Optional(CONF_SOLCAST_TOMORROW): _sensor(),
        probatio.Optional(CONF_WEATHER): selector.EntitySelector(
            selector.EntitySelectorConfig(filter=[{"domain": "weather"}])
        ),
        probatio.Optional(CONF_PV_POWER): _number(0, 100, 0.1, "kWp"),
        probatio.Optional(CONF_CONSUMPTION_SENSOR): _sensor(),
        probatio.Required(CONF_BASE_LOAD, default=DEFAULT_BASE_LOAD): _number(
            0, 10000, 10, "W"
        ),
    }
)


def _validate_battery(
    hass: HomeAssistant, data: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    marstek = resolve_entities(hass, data[CONF_MARSTEK_ENTRY])
    if marstek.missing:
        return {"base": "marstek_missing"}, {"entities": ", ".join(marstek.missing)}
    if marstek.disabled:
        return {"base": "marstek_disabled"}, {"entities": ", ".join(marstek.disabled)}
    return {}, {}


def _validate_prices(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    try:
        read_price_sensor(hass, data[CONF_PRICE_SENSOR])
    except InputError:
        errors[CONF_PRICE_SENSOR] = "invalid_price_sensor"
    if data[CONF_FEED_IN_MODE] == FEED_IN_SENSOR:
        if not data.get(CONF_FEED_IN_SENSOR):
            errors[CONF_FEED_IN_SENSOR] = "required"
        else:
            try:
                read_price_sensor(hass, data[CONF_FEED_IN_SENSOR])
            except InputError:
                errors[CONF_FEED_IN_SENSOR] = "invalid_price_sensor"
    return errors


def _validate_sources(data: dict[str, Any]) -> dict[str, str]:
    source = data[CONF_SOLAR_SOURCE]
    if source == SOLAR_FORECAST_SOLAR and not data.get(CONF_FORECAST_SOLAR_ENTRY):
        return {CONF_FORECAST_SOLAR_ENTRY: "required"}
    if source == SOLAR_SOLCAST and not data.get(CONF_SOLCAST_TODAY):
        return {CONF_SOLCAST_TODAY: "required"}
    return {}


def _merge(
    current: dict[str, Any], schema: probatio.Schema, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Replace the fields of one step; optional fields left empty are removed."""
    result = {k: v for k, v in current.items() if k not in schema.schema}
    return result | user_input


class _Steps:
    """Steps shared by the config flow and the options flow."""

    hass: HomeAssistant
    _data: dict[str, Any]

    def _form(
        self,
        step_id: str,
        schema: probatio.Schema,
        errors: dict[str, str] | None = None,
        placeholders: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(  # type: ignore[attr-defined]
                schema, self._data
            ),
            errors=errors or {},
            description_placeholders=placeholders,
        )

    async def _battery_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if user_input is not None:
            errors, placeholders = _validate_battery(self.hass, user_input)
            self._data = _merge(self._data, BATTERY_SCHEMA, user_input)
            if not errors:
                return await self.async_step_prices()
            return self._form(step_id, BATTERY_SCHEMA, errors, placeholders)
        return self._form(step_id, BATTERY_SCHEMA)

    async def async_step_prices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Price and feed-in settings."""
        if user_input is not None:
            errors = _validate_prices(self.hass, user_input)
            self._data = _merge(self._data, PRICES_SCHEMA, user_input)
            if not errors:
                return await self.async_step_sources()
            return self._form("prices", PRICES_SCHEMA, errors)
        return self._form("prices", PRICES_SCHEMA)

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Grid meter, solar forecast and consumption."""
        if user_input is not None:
            errors = _validate_sources(user_input)
            self._data = _merge(self._data, SOURCES_SCHEMA, user_input)
            if not errors:
                return self._finish()
            return self._form("sources", SOURCES_SCHEMA, errors)
        return self._form("sources", SOURCES_SCHEMA)

    def _finish(self) -> ConfigFlowResult:
        raise NotImplementedError


class TraderConfigFlow(_Steps, ConfigFlow, domain=DOMAIN):
    """Set up Marstek Energy Trader."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TraderOptionsFlow:
        return TraderOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the Marstek battery."""
        return await self._battery_step("user", user_input)

    def _finish(self) -> ConfigFlowResult:
        return self.async_create_entry(title=TITLE, data={}, options=self._data)


class TraderOptionsFlow(_Steps, OptionsFlowWithReload):
    """Change any setting of the trader; the integration reloads afterwards."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Battery settings."""
        if not self._data:
            self._data = dict(self.config_entry.options)
        return await self._battery_step("init", user_input)

    def _finish(self) -> ConfigFlowResult:
        return self.async_create_entry(data=self._data)
