"""Constants and configuration keys."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "energy_report"

# --- energy sources. Every one must be a sensor with long-term statistics,
# that is state_class total or total_increasing. A power sensor (state_class
# measurement) has no "change" to sum and silently reports nothing, so the
# config flow rejects one rather than letting it fail quietly later.
CONF_PV_ENERGY: Final = "pv_energy"
CONF_IMPORT_ENERGY: Final = "import_energy"
CONF_EXPORT_ENERGY: Final = "export_energy"
CONF_CHARGE_ENERGY: Final = "charge_energy"
CONF_DISCHARGE_ENERGY: Final = "discharge_energy"
# House load is not an input: it is worked out per bucket from the five counters
# above, which keeps it in step with them inside every five-minute bucket.

# --- optional: a PV power sensor (W, state_class measurement), for the peak.
CONF_PV_POWER: Final = "pv_power"

# --- rates. Any entity whose state is a number: a tariff sensor, or something
# like predbat.rates that is not in the sensor domain at all and so gets no
# statistics of its own. The integration mirrors whatever it is pointed at into
# a recorded sensor, which is what makes per-bucket pricing possible at all.
CONF_IMPORT_RATE: Final = "import_rate"
CONF_EXPORT_RATE: Final = "export_rate"
CONF_RATE_SCALE: Final = "rate_scale"
CONF_FALLBACK_IMPORT_RATE: Final = "fallback_import_rate"
CONF_FALLBACK_EXPORT_RATE: Final = "fallback_export_rate"

# --- optional extras
CONF_ARBITRAGE_ENERGY: Final = "arbitrage_sensor"
CONF_ARBITRAGE_GATE: Final = "arbitrage_gate"
CONF_ARBITRAGE_GATE_STATE: Final = "arbitrage_gate_state"
CONF_CURRENCY: Final = "currency"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_NOTIFY_DATA: Final = "notify_data"

# --- schedules
CONF_DAILY_TIME: Final = "daily_time"
CONF_DAILY_AFTER_SUNSET: Final = "daily_after_sunset"
CONF_WEEKLY_TIME: Final = "weekly_time"
CONF_MONTHLY_TIME: Final = "monthly_time"
CONF_ENABLE_DAILY: Final = "enable_daily"
CONF_ENABLE_WEEKLY: Final = "enable_weekly"
CONF_ENABLE_MONTHLY: Final = "enable_monthly"

DEFAULT_CURRENCY: Final = "£"
DEFAULT_DAILY_TIME: Final = "19:00:00"
DEFAULT_WEEKLY_TIME: Final = "08:00:00"
DEFAULT_MONTHLY_TIME: Final = "08:00:00"

# Rates are quoted either in whole currency units (0.2635) or in hundredths
# (26.35p). Dividing by this brings whatever the source reports into currency
# units per kWh, which is what report.py works in.
RATE_SCALES: Final = {"per_kwh": 1.0, "per_kwh_minor": 100.0}
DEFAULT_RATE_SCALE: Final = "per_kwh_minor"

PERIOD_DAILY: Final = "daily"
PERIOD_WEEKLY: Final = "weekly"
PERIOD_MONTHLY: Final = "monthly"
PERIODS: Final = [PERIOD_DAILY, PERIOD_WEEKLY, PERIOD_MONTHLY]

SERVICE_GENERATE: Final = "generate"
SERVICE_SEND: Final = "send"

# Five-minute statistics only survive recorder purge_keep_days, so they are used
# for the daily report - which never looks back further than 24 hours - and
# hourly for the rest. On a flat tariff the two agree exactly. On a half-hourly
# tariff the daily report is exact, every five-minute bucket falling inside one
# price, while weekly and monthly average within the hour. Nothing longer-lived
# than hourly statistics exists to do better.
PERIOD_RESOLUTION: Final = {
    PERIOD_DAILY: "5minute",
    PERIOD_WEEKLY: "hour",
    PERIOD_MONTHLY: "hour",
}

# state_class values that produce a summable "change" in long-term statistics.
STATISTIC_STATE_CLASSES: Final = {"total", "total_increasing"}

# The recorder compiles each five-minute bucket a few seconds after it closes.
# The daily window ends on a boundary; the report waits this long past it so the
# bucket just before exists before anything is read.
COMPILE_GRACE_SECONDS: Final = 60
