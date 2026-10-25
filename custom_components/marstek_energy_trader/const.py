"""Constants for Marstek Energy Trader."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "marstek_energy_trader"
MARSTEK_DOMAIN: Final = "marstek_modbus"

# Config entry options.
CONF_MARSTEK_ENTRY: Final = "marstek_entry"
CONF_BATTERY_POWER_SIGN: Final = "battery_power_sign"
CONF_CAPACITY: Final = "capacity_kwh"
CONF_EFFICIENCY: Final = "round_trip_efficiency"
CONF_PRICE_SENSOR: Final = "price_sensor"
CONF_FEED_IN_MODE: Final = "feed_in_mode"
CONF_FEED_IN_SENSOR: Final = "feed_in_sensor"
CONF_FEED_IN_OFFSET: Final = "feed_in_offset"
CONF_FEED_IN_FIXED: Final = "feed_in_fixed"
CONF_FEED_IN_COST: Final = "feed_in_cost"
CONF_GRID_SENSOR: Final = "grid_power_sensor"
CONF_GRID_SIGN: Final = "grid_power_sign"
CONF_SOLAR_SOURCE: Final = "solar_source"
CONF_FORECAST_SOLAR_ENTRY: Final = "forecast_solar_entry"
CONF_SOLCAST_TODAY: Final = "solcast_today"
CONF_SOLCAST_TOMORROW: Final = "solcast_tomorrow"
CONF_WEATHER: Final = "weather_entity"
CONF_PV_POWER: Final = "pv_peak_power"
CONF_CONSUMPTION_SENSOR: Final = "consumption_sensor"
CONF_BASE_LOAD: Final = "base_load"

# Sign of the Marstek battery_power sensor.
SIGN_POSITIVE_DISCHARGE: Final = "positive_discharge"
SIGN_POSITIVE_CHARGE: Final = "positive_charge"
# Sign of the grid power sensor.
SIGN_IMPORT_POSITIVE: Final = "import_positive"
SIGN_IMPORT_NEGATIVE: Final = "import_negative"

FEED_IN_SAME: Final = "same"
FEED_IN_SENSOR: Final = "sensor"
FEED_IN_OFFSET: Final = "offset"
FEED_IN_FIXED: Final = "fixed"

SOLAR_NONE: Final = "none"
SOLAR_FORECAST_SOLAR: Final = "forecast_solar"
SOLAR_SOLCAST: Final = "solcast"

DEFAULT_EFFICIENCY: Final = 85
DEFAULT_BASE_LOAD: Final = 400

# Operating modes (select entity).
MODE_OFF: Final = "off"
MODE_SOLAR: Final = "solar"
MODE_PRICE: Final = "price"
MODE_SOLAR_PRICE: Final = "solar_price"
MODES: Final = [MODE_OFF, MODE_SOLAR, MODE_PRICE, MODE_SOLAR_PRICE]

# Actions the planner can choose per time slot.
ACTION_CHARGE: Final = "charge"
ACTION_DISCHARGE: Final = "discharge"
ACTION_HOLD: Final = "hold"
ACTION_SELF: Final = "self_consumption"
ACTIONS: Final = [ACTION_CHARGE, ACTION_DISCHARGE, ACTION_HOLD, ACTION_SELF]

# Status of the trader besides the actions above.
STATUS_OFF: Final = "off"
STATUS_WAITING: Final = "waiting"
STATUS_ERROR: Final = "error"

OVERRIDE_AUTO: Final = "auto"

# Strategy presets: minimum profit per discharged kWh (€) and reserve SOC (%).
STRATEGY_CONSERVATIVE: Final = "conservative"
STRATEGY_BALANCED: Final = "balanced"
STRATEGY_AGGRESSIVE: Final = "aggressive"
STRATEGY_CUSTOM: Final = "custom"
STRATEGY_PRESETS: Final = {
    STRATEGY_CONSERVATIVE: {"min_profit": 0.08, "min_soc": 30},
    STRATEGY_BALANCED: {"min_profit": 0.04, "min_soc": 20},
    STRATEGY_AGGRESSIVE: {"min_profit": 0.02, "min_soc": 11},
}
STRATEGIES: Final = [*STRATEGY_PRESETS, STRATEGY_CUSTOM]

# Marstek entity keys (unique_id is "<marstek entry id>_<key>").
KEY_WORK_MODE: Final = "user_work_mode"
KEY_FORCE_MODE: Final = "force_mode"
KEY_CHARGE_POWER: Final = "set_charge_power"
KEY_DISCHARGE_POWER: Final = "set_discharge_power"
KEY_RS485: Final = "rs485_control_mode"
KEY_SOC: Final = "battery_soc"
KEY_BATTERY_POWER: Final = "battery_power"
KEY_CAPACITY: Final = "battery_total_energy"
KEY_TOTAL_CHARGED: Final = "total_charging_energy"
KEY_TOTAL_DISCHARGED: Final = "total_discharging_energy"

WORK_MODE_SELF_CONSUMPTION: Final = "anti_feed"
FORCE_STANDBY: Final = "standby"
FORCE_CHARGE: Final = "charge"
FORCE_DISCHARGE: Final = "discharge"

# The Marstek accepts power setpoints in steps of 50 W up to 2500 W.
MARSTEK_POWER_STEP: Final = 50
MARSTEK_MAX_POWER: Final = 2500

REPLAN_INTERVAL: Final = timedelta(minutes=15)
SAVINGS_INTERVAL: Final = timedelta(seconds=30)
CONSUMPTION_PROFILE_MAX_AGE: Final = timedelta(hours=6)
DECISION_LOG_SIZE: Final = 20

EVENT_ACTION: Final = f"{DOMAIN}_action"
