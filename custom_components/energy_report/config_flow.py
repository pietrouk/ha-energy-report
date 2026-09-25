"""UI setup.

The one piece of validation worth having is on the energy entities. Everything
downstream reads long-term statistics, which only exist for sensors whose
state_class is total or total_increasing. Point this at a power sensor and there
is no "change" to sum: the report builds fine and reports zeros, which is far
harder to diagnose than being told now.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TimeSelector,
)

from .const import (
    CONF_ARBITRAGE_ENERGY,
    CONF_ARBITRAGE_GATE,
    CONF_ARBITRAGE_GATE_STATE,
    CONF_CHARGE_ENERGY,
    CONF_CURRENCY,
    CONF_DAILY_AFTER_SUNSET,
    CONF_DAILY_TIME,
    CONF_DISCHARGE_ENERGY,
    CONF_ENABLE_DAILY,
    CONF_ENABLE_MONTHLY,
    CONF_ENABLE_WEEKLY,
    CONF_EXPORT_ENERGY,
    CONF_EXPORT_RATE,
    CONF_FALLBACK_EXPORT_RATE,
    CONF_FALLBACK_IMPORT_RATE,
    CONF_IMPORT_ENERGY,
    CONF_IMPORT_RATE,
    CONF_MONTHLY_TIME,
    CONF_NOTIFY_SERVICE,
    CONF_PV_ENERGY,
    CONF_PV_POWER,
    CONF_RATE_SCALE,
    CONF_WEEKLY_TIME,
    DEFAULT_CURRENCY,
    DEFAULT_DAILY_TIME,
    DEFAULT_MONTHLY_TIME,
    DEFAULT_RATE_SCALE,
    DEFAULT_WEEKLY_TIME,
    DOMAIN,
    STATISTIC_STATE_CLASSES,
)

ENERGY_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", device_class="energy")
)
POWER_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", device_class="power")
)
ANY_SELECTOR = EntitySelector(EntitySelectorConfig())

ENERGY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PV_ENERGY): ENERGY_SELECTOR,
        vol.Required(CONF_IMPORT_ENERGY): ENERGY_SELECTOR,
        vol.Required(CONF_EXPORT_ENERGY): ENERGY_SELECTOR,
        vol.Optional(CONF_CHARGE_ENERGY): ENERGY_SELECTOR,
        vol.Optional(CONF_DISCHARGE_ENERGY): ENERGY_SELECTOR,
        vol.Optional(CONF_PV_POWER): POWER_SELECTOR,
    }
)


def _rates_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_IMPORT_RATE, description={"suggested_value": defaults.get(CONF_IMPORT_RATE)}): ANY_SELECTOR,
            vol.Optional(CONF_EXPORT_RATE, description={"suggested_value": defaults.get(CONF_EXPORT_RATE)}): ANY_SELECTOR,
            vol.Required(CONF_RATE_SCALE, default=defaults.get(CONF_RATE_SCALE, DEFAULT_RATE_SCALE)): SelectSelector(
                SelectSelectorConfig(
                    options=["per_kwh", "per_kwh_minor"], translation_key="rate_scale"
                )
            ),
            vol.Required(CONF_FALLBACK_IMPORT_RATE, default=defaults.get(CONF_FALLBACK_IMPORT_RATE, 0.0)): NumberSelector(
                NumberSelectorConfig(min=0, step=0.0001, mode="box")
            ),
            vol.Required(CONF_FALLBACK_EXPORT_RATE, default=defaults.get(CONF_FALLBACK_EXPORT_RATE, 0.0)): NumberSelector(
                NumberSelectorConfig(min=0, step=0.0001, mode="box")
            ),
            vol.Required(CONF_CURRENCY, default=defaults.get(CONF_CURRENCY, DEFAULT_CURRENCY)): TextSelector(),
        }
    )


def _delivery_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_NOTIFY_SERVICE, description={"suggested_value": defaults.get(CONF_NOTIFY_SERVICE)}): TextSelector(),
            vol.Required(CONF_ENABLE_DAILY, default=defaults.get(CONF_ENABLE_DAILY, True)): BooleanSelector(),
            vol.Required(CONF_DAILY_TIME, default=defaults.get(CONF_DAILY_TIME, DEFAULT_DAILY_TIME)): TimeSelector(),
            vol.Required(CONF_DAILY_AFTER_SUNSET, default=defaults.get(CONF_DAILY_AFTER_SUNSET, True)): BooleanSelector(),
            vol.Required(CONF_ENABLE_WEEKLY, default=defaults.get(CONF_ENABLE_WEEKLY, False)): BooleanSelector(),
            vol.Required(CONF_WEEKLY_TIME, default=defaults.get(CONF_WEEKLY_TIME, DEFAULT_WEEKLY_TIME)): TimeSelector(),
            vol.Required(CONF_ENABLE_MONTHLY, default=defaults.get(CONF_ENABLE_MONTHLY, False)): BooleanSelector(),
            vol.Required(CONF_MONTHLY_TIME, default=defaults.get(CONF_MONTHLY_TIME, DEFAULT_MONTHLY_TIME)): TimeSelector(),
            vol.Optional(CONF_ARBITRAGE_ENERGY, description={"suggested_value": defaults.get(CONF_ARBITRAGE_ENERGY)}): ANY_SELECTOR,
            vol.Optional(CONF_ARBITRAGE_GATE, description={"suggested_value": defaults.get(CONF_ARBITRAGE_GATE)}): ANY_SELECTOR,
            vol.Optional(CONF_ARBITRAGE_GATE_STATE, description={"suggested_value": defaults.get(CONF_ARBITRAGE_GATE_STATE, "off")}): TextSelector(),
        }
    )


def _check_statistics(hass, user_input: dict[str, Any]) -> dict[str, str]:
    """Reject entities the recorder will never produce the right statistic for.

    Energy counters need a "change", which only total / total_increasing give.
    The optional PV power sensor needs a "max", which only measurement gives.
    """
    errors: dict[str, str] = {}
    for key, entity_id in user_input.items():
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            errors[key] = "not_found"
            continue
        state_class = state.attributes.get("state_class")
        if key == CONF_PV_POWER:
            if state_class != "measurement":
                errors[key] = "not_a_measurement"
        elif state_class not in STATISTIC_STATE_CLASSES:
            errors[key] = "not_a_total"
    return errors


class EnergyReportConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _check_statistics(self.hass, user_input)
            if not errors:
                self._data.update(user_input)
                return await self.async_step_rates()
        return self.async_show_form(
            step_id="user", data_schema=ENERGY_SCHEMA, errors=errors
        )

    async def async_step_rates(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_delivery()
        return self.async_show_form(step_id="rates", data_schema=_rates_schema({}))

    async def async_step_delivery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="Energy Report", data=self._data)
        return self.async_show_form(step_id="delivery", data_schema=_delivery_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return EnergyReportOptionsFlow()


class EnergyReportOptionsFlow(OptionsFlow):
    """Prices, delivery and schedule can be changed later; the energy entities
    cannot. Reports reach back through history, so swapping a counter mid-life
    would price old periods from one device and new ones from another."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = _rates_schema(current).extend(_delivery_schema(current).schema)
        return self.async_show_form(step_id="init", data_schema=schema)
