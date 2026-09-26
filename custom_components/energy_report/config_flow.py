"""UI setup, and the Configure menu that changes any of it later.

The validation worth having is on the energy entities. Everything downstream
reads long-term statistics, which only exist for sensors whose state_class is
total or total_increasing. Point this at a power sensor and there is no "change"
to sum: the report builds fine and reports zeros, which is far harder to diagnose
than being told now.

Setup walks through every step once. Configure offers the same steps as a menu,
each saving on its own, so changing the delivery does not mean re-picking the
energy counters.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
    CONF_DELIVERY,
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
    CONF_NOTIFY_ENTITIES,
    CONF_NOTIFY_SERVICE,
    CONF_PV_ENERGY,
    CONF_PV_POWER,
    CONF_RATE_SCALE,
    CONF_TELEGRAM_CHAT_IDS,
    CONF_TELEGRAM_ENTITIES,
    CONF_WEEKLY_TIME,
    DEFAULT_CURRENCY,
    DEFAULT_DAILY_TIME,
    DEFAULT_MONTHLY_TIME,
    DEFAULT_RATE_SCALE,
    DEFAULT_WEEKLY_TIME,
    DELIVERY_METHODS,
    DELIVERY_NONE,
    DELIVERY_NOTIFY_ENTITY,
    DELIVERY_NOTIFY_SERVICE,
    DELIVERY_TELEGRAM,
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

ENERGY_KEYS = (CONF_PV_ENERGY, CONF_IMPORT_ENERGY, CONF_EXPORT_ENERGY,
               CONF_CHARGE_ENERGY, CONF_DISCHARGE_ENERGY, CONF_PV_POWER)


def _suggest(defaults: dict[str, Any], key: str) -> dict[str, Any]:
    """Pre-fill an optional field without making it impossible to clear."""
    return {"suggested_value": defaults.get(key)}


def _energy_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PV_ENERGY, description=_suggest(defaults, CONF_PV_ENERGY)): ENERGY_SELECTOR,
            vol.Required(CONF_IMPORT_ENERGY, description=_suggest(defaults, CONF_IMPORT_ENERGY)): ENERGY_SELECTOR,
            vol.Required(CONF_EXPORT_ENERGY, description=_suggest(defaults, CONF_EXPORT_ENERGY)): ENERGY_SELECTOR,
            vol.Optional(CONF_CHARGE_ENERGY, description=_suggest(defaults, CONF_CHARGE_ENERGY)): ENERGY_SELECTOR,
            vol.Optional(CONF_DISCHARGE_ENERGY, description=_suggest(defaults, CONF_DISCHARGE_ENERGY)): ENERGY_SELECTOR,
            vol.Optional(CONF_PV_POWER, description=_suggest(defaults, CONF_PV_POWER)): POWER_SELECTOR,
        }
    )


def _rates_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_IMPORT_RATE, description=_suggest(defaults, CONF_IMPORT_RATE)): ANY_SELECTOR,
            vol.Optional(CONF_EXPORT_RATE, description=_suggest(defaults, CONF_EXPORT_RATE)): ANY_SELECTOR,
            vol.Required(CONF_RATE_SCALE, default=defaults.get(CONF_RATE_SCALE) or DEFAULT_RATE_SCALE): SelectSelector(
                SelectSelectorConfig(
                    options=["per_kwh", "per_kwh_minor"], translation_key="rate_scale"
                )
            ),
            # "any", not a small step: Home Assistant refuses a step below 0.001,
            # and the whole form then fails to build with "Unknown error".
            vol.Required(CONF_FALLBACK_IMPORT_RATE, default=defaults.get(CONF_FALLBACK_IMPORT_RATE) or 0.0): NumberSelector(
                NumberSelectorConfig(min=0, step="any", mode="box")
            ),
            vol.Required(CONF_FALLBACK_EXPORT_RATE, default=defaults.get(CONF_FALLBACK_EXPORT_RATE) or 0.0): NumberSelector(
                NumberSelectorConfig(min=0, step="any", mode="box")
            ),
            vol.Required(CONF_CURRENCY, default=defaults.get(CONF_CURRENCY) or DEFAULT_CURRENCY): TextSelector(),
            vol.Optional(CONF_ARBITRAGE_ENERGY, description=_suggest(defaults, CONF_ARBITRAGE_ENERGY)): ANY_SELECTOR,
            vol.Optional(CONF_ARBITRAGE_GATE, description=_suggest(defaults, CONF_ARBITRAGE_GATE)): ANY_SELECTOR,
            vol.Optional(CONF_ARBITRAGE_GATE_STATE, description={"suggested_value": defaults.get(CONF_ARBITRAGE_GATE_STATE) or "off"}): TextSelector(),
        }
    )


def _delivery_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_DELIVERY, default=delivery_method(defaults)): SelectSelector(
                SelectSelectorConfig(
                    options=DELIVERY_METHODS, translation_key="delivery",
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _delivery_details_schema(method: str, defaults: dict[str, Any]) -> vol.Schema:
    if method == DELIVERY_TELEGRAM:
        chat_ids = defaults.get(CONF_TELEGRAM_CHAT_IDS)
        if chat_ids and not isinstance(chat_ids, str):
            chat_ids = ", ".join(str(c) for c in chat_ids)
        return vol.Schema(
            {
                vol.Optional(CONF_TELEGRAM_ENTITIES, description=_suggest(defaults, CONF_TELEGRAM_ENTITIES)): EntitySelector(
                    EntitySelectorConfig(domain="notify", integration="telegram_bot", multiple=True)
                ),
                vol.Optional(CONF_TELEGRAM_CHAT_IDS, description={"suggested_value": chat_ids or None}): TextSelector(),
            }
        )
    if method == DELIVERY_NOTIFY_ENTITY:
        return vol.Schema(
            {
                vol.Required(CONF_NOTIFY_ENTITIES, description=_suggest(defaults, CONF_NOTIFY_ENTITIES)): EntitySelector(
                    EntitySelectorConfig(domain="notify", multiple=True)
                ),
            }
        )
    return vol.Schema(
        {
            vol.Required(CONF_NOTIFY_SERVICE, description=_suggest(defaults, CONF_NOTIFY_SERVICE)): TextSelector(),
        }
    )


def _schedule_schema(defaults: dict[str, Any]) -> vol.Schema:
    def value(key: str, default: Any) -> Any:
        found = defaults.get(key)
        return default if found is None else found

    return vol.Schema(
        {
            vol.Required(CONF_ENABLE_DAILY, default=value(CONF_ENABLE_DAILY, True)): BooleanSelector(),
            vol.Required(CONF_DAILY_TIME, default=value(CONF_DAILY_TIME, DEFAULT_DAILY_TIME)): TimeSelector(),
            vol.Required(CONF_DAILY_AFTER_SUNSET, default=value(CONF_DAILY_AFTER_SUNSET, True)): BooleanSelector(),
            vol.Required(CONF_ENABLE_WEEKLY, default=value(CONF_ENABLE_WEEKLY, False)): BooleanSelector(),
            vol.Required(CONF_WEEKLY_TIME, default=value(CONF_WEEKLY_TIME, DEFAULT_WEEKLY_TIME)): TimeSelector(),
            vol.Required(CONF_ENABLE_MONTHLY, default=value(CONF_ENABLE_MONTHLY, False)): BooleanSelector(),
            vol.Required(CONF_MONTHLY_TIME, default=value(CONF_MONTHLY_TIME, DEFAULT_MONTHLY_TIME)): TimeSelector(),
        }
    )


def summary(hass: HomeAssistant, entry_id: str, config: dict[str, Any]) -> dict[str, str]:
    """What is set now, for the Configure menu - so it can be read without
    opening every step."""
    def listed(values: Any) -> str:
        return ", ".join(str(v) for v in values) if values else ""

    method = delivery_method(config)
    if method == DELIVERY_TELEGRAM:
        targets = [*(config.get(CONF_TELEGRAM_ENTITIES) or []),
                   *(f"chat {c}" for c in config.get(CONF_TELEGRAM_CHAT_IDS) or [])]
        delivery = f"Telegram to {listed(targets) or 'nobody yet'}"
    elif method == DELIVERY_NOTIFY_ENTITY:
        delivery = f"notify entities {listed(config.get(CONF_NOTIFY_ENTITIES)) or '(none picked)'}"
    elif method == DELIVERY_NOTIFY_SERVICE:
        delivery = f"the {config.get(CONF_NOTIFY_SERVICE)} service"
    else:
        delivery = "not sent - use the energy_report.generate action"

    def on(key: str, default: bool) -> bool:
        value = config.get(key)
        return default if value is None else bool(value)

    def hhmm(key: str, default: str) -> str:
        return (config.get(key) or default)[:5]

    parts = []
    if on(CONF_ENABLE_DAILY, True):
        daily = f"daily at {hhmm(CONF_DAILY_TIME, DEFAULT_DAILY_TIME)}"
        if on(CONF_DAILY_AFTER_SUNSET, True):
            daily += " or sunset if later"
        parts.append(daily)
    if on(CONF_ENABLE_WEEKLY, False):
        parts.append(f"weekly on Mondays at {hhmm(CONF_WEEKLY_TIME, DEFAULT_WEEKLY_TIME)}")
    if on(CONF_ENABLE_MONTHLY, False):
        parts.append(f"monthly on the 1st at {hhmm(CONF_MONTHLY_TIME, DEFAULT_MONTHLY_TIME)}")

    energy = [f"solar {config.get(CONF_PV_ENERGY)}", f"import {config.get(CONF_IMPORT_ENERGY)}",
              f"export {config.get(CONF_EXPORT_ENERGY)}"]
    if config.get(CONF_CHARGE_ENERGY):
        energy.append(f"charge {config.get(CONF_CHARGE_ENERGY)}")
    if config.get(CONF_DISCHARGE_ENERGY):
        energy.append(f"discharge {config.get(CONF_DISCHARGE_ENERGY)}")
    if config.get(CONF_PV_POWER):
        energy.append(f"solar power {config.get(CONF_PV_POWER)}")

    return {
        "delivery": delivery,
        "schedule": "; ".join(parts) or "no reports turned on",
        "energy": ", ".join(energy),
        "prices": _prices_summary(hass, entry_id, config),
    }


def _prices_summary(hass: HomeAssistant, entry_id: str, config: dict[str, Any]) -> str:
    """Which entity each rate comes from, what records it, and the fallback."""
    registry = er.async_get(hass)
    minor = (config.get(CONF_RATE_SCALE) or DEFAULT_RATE_SCALE) == "per_kwh_minor"
    currency = config.get(CONF_CURRENCY) or DEFAULT_CURRENCY
    parts = []
    for label, key, fallback_key in (
        ("import", CONF_IMPORT_RATE, CONF_FALLBACK_IMPORT_RATE),
        ("export", CONF_EXPORT_RATE, CONF_FALLBACK_EXPORT_RATE),
    ):
        source = config.get(key)
        mirror = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry_id}_{label}_rate") or f"the {label} rate (recorded) sensor"
        fallback = config.get(fallback_key) or 0
        shown = f"{fallback:g}{'p' if minor and currency == '£' else ''}" if fallback else "none"
        if source:
            parts.append(f"{label} from {source}, recorded as {mirror} "
                         f"(configured fallback {shown}, only for periods before recording "
                         "started when the source has no average of its own)")
        else:
            parts.append(f"{label}: no rate entity, every period at the fallback {shown}")
    return "; ".join(parts)


def delivery_method(config: dict[str, Any]) -> str:
    """The configured delivery method. Entries made before there was a choice
    only had a notify service, so that is what one of those is using."""
    method = config.get(CONF_DELIVERY)
    if method in DELIVERY_METHODS:
        return method
    return DELIVERY_NOTIFY_SERVICE if config.get(CONF_NOTIFY_SERVICE) else DELIVERY_NONE


def _complete(schema: vol.Schema, user_input: dict[str, Any]) -> dict[str, Any]:
    """Every key the step owns, with None for an optional field left empty.

    Options are layered over the original setup data, so a field cleared in
    Configure has to be stored as None - left out, the old value would show
    through from underneath.
    """
    return {str(key): user_input.get(str(key)) for key in schema.schema} | user_input


def _check_statistics(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    """Reject entities the recorder will never produce the right statistic for.

    Energy counters need a "change", which only total / total_increasing give.
    The optional PV power sensor needs a "max", which only measurement gives.
    """
    errors: dict[str, str] = {}
    for key in ENERGY_KEYS:
        entity_id = user_input.get(key)
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


def _parse_chat_ids(text: str | None) -> list[int] | None:
    """'-1001234567890, 12345' -> [-1001234567890, 12345]; None if not all numbers."""
    if not text or not text.strip():
        return []
    try:
        return [int(part) for part in text.replace(";", ",").split(",") if part.strip()]
    except ValueError:
        return None


def _check_delivery(hass: HomeAssistant, method: str,
                    user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate the delivery details and return them in the form stored."""
    errors: dict[str, str] = {}
    cleaned = dict(user_input)
    if method == DELIVERY_TELEGRAM:
        chat_ids = _parse_chat_ids(user_input.get(CONF_TELEGRAM_CHAT_IDS))
        if chat_ids is None:
            errors[CONF_TELEGRAM_CHAT_IDS] = "bad_chat_id"
        elif not chat_ids and not user_input.get(CONF_TELEGRAM_ENTITIES):
            errors["base"] = "no_chat"
        elif not hass.services.has_service("telegram_bot", "send_message"):
            errors["base"] = "no_telegram"
        cleaned[CONF_TELEGRAM_CHAT_IDS] = chat_ids or []
    elif method == DELIVERY_NOTIFY_SERVICE:
        target = (user_input.get(CONF_NOTIFY_SERVICE) or "").strip()
        domain, _, service = target.partition(".")
        if not service:
            domain, service = "notify", target
        if not hass.services.has_service(domain, service):
            errors[CONF_NOTIFY_SERVICE] = "unknown_service"
        cleaned[CONF_NOTIFY_SERVICE] = f"{domain}.{service}"
    return cleaned, errors


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
            step_id="user", data_schema=_energy_schema(user_input or {}), errors=errors
        )

    async def async_step_rates(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_delivery()
        return self.async_show_form(step_id="rates", data_schema=_rates_schema({}))

    async def async_step_delivery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_DELIVERY] == DELIVERY_NONE:
                return await self.async_step_schedule()
            return await self.async_step_delivery_details()
        return self.async_show_form(step_id="delivery", data_schema=_delivery_schema({}))

    async def async_step_delivery_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        method = self._data[CONF_DELIVERY]
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = _check_delivery(self.hass, method, user_input)
            if not errors:
                self._data.update(cleaned)
                return await self.async_step_schedule()
        return self.async_show_form(
            step_id="delivery_details",
            data_schema=_delivery_details_schema(method, user_input or {}),
            errors=errors,
        )

    async def async_step_schedule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="Energy Report", data=self._data)
        return self.async_show_form(step_id="schedule", data_schema=_schedule_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return EnergyReportOptionsFlow()


class EnergyReportOptionsFlow(OptionsFlow):
    """A menu of the setup steps, each saving on its own.

    Changing an energy counter is allowed, but reports reach back through
    history: a weekly report straddling the change reads the new counter for
    the whole week. The form says so.
    """

    def __init__(self) -> None:
        self._method: str | None = None

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _save(self, changes: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["delivery", "schedule", "energy", "rates"],
            description_placeholders=summary(self.hass, self.config_entry.entry_id, self._current),
        )

    async def async_step_energy(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        schema = _energy_schema(self._current)
        if user_input is not None:
            errors = _check_statistics(self.hass, user_input)
            if not errors:
                return self._save(_complete(schema, user_input))
            schema = _energy_schema(user_input)
        return self.async_show_form(step_id="energy", data_schema=schema, errors=errors)

    async def async_step_rates(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = _rates_schema(self._current)
        if user_input is not None:
            return self._save(_complete(schema, user_input))
        return self.async_show_form(step_id="rates", data_schema=schema)

    async def async_step_delivery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._method = user_input[CONF_DELIVERY]
            if self._method == DELIVERY_NONE:
                return self._save({CONF_DELIVERY: DELIVERY_NONE})
            return await self.async_step_delivery_details()
        return self.async_show_form(step_id="delivery", data_schema=_delivery_schema(self._current))

    async def async_step_delivery_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        method = self._method or delivery_method(self._current)
        schema = _delivery_details_schema(method, self._current)
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors = _check_delivery(self.hass, method, user_input)
            if not errors:
                return self._save({CONF_DELIVERY: method} | _complete(schema, cleaned))
            schema = _delivery_details_schema(method, user_input)
        return self.async_show_form(
            step_id="delivery_details", data_schema=schema, errors=errors,
        )

    async def async_step_schedule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        return self.async_show_form(step_id="schedule", data_schema=_schedule_schema(self._current))
