"""Build every setup and options form with Home Assistant's own selector code.

Needs Home Assistant installed (pip install homeassistant). A selector config
that Home Assistant rejects - a number step below 0.001, say - raises only when
the form is built, which the user sees as "Unknown error occurred" on the step
before. hassfest and the HACS checks never build the forms, so nothing else
catches it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import voluptuous_serialize
from homeassistant.helpers import config_validation as cv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.energy_report import config_flow as cf  # noqa: E402

EXISTING = {
    "import_rate": "predbat.rates",
    "export_rate": "predbat.rates_export",
    "rate_scale": "per_kwh_minor",
    "fallback_import_rate": 25.47,
    "fallback_export_rate": 12,
    "currency": "£",
    "notify_service": "telegram_bot.send_message",
}

SCHEMAS = {
    "user": lambda: cf.ENERGY_SCHEMA,
    "rates": lambda: cf._rates_schema({}),
    "delivery": lambda: cf._delivery_schema({}),
    "options": lambda: cf._rates_schema(EXISTING).extend(cf._delivery_schema(EXISTING).schema),
}

for name, build in SCHEMAS.items():
    # Serialising is what the frontend request does; it fails the same way.
    voluptuous_serialize.convert(build(), custom_serializer=cv.custom_serializer)

# What the frontend sends back has to validate too, fractional prices included.
cf._rates_schema({})({"rate_scale": "per_kwh_minor", "fallback_import_rate": 25.47,
                      "fallback_export_rate": 0.1234, "currency": "£"})
cf._delivery_schema({})({"enable_daily": True, "daily_time": "19:00:00",
                         "daily_after_sunset": True, "enable_weekly": False,
                         "weekly_time": "08:00:00", "enable_monthly": False,
                         "monthly_time": "08:00:00"})

print(f"Config flow checks passed ({len(SCHEMAS)} forms)")
