"""Energy Report: a priced summary of where your solar, battery and grid energy went."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time, timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ARBITRAGE_ENERGY,
    CONF_ARBITRAGE_GATE,
    CONF_ARBITRAGE_GATE_STATE,
    CONF_CHARGE_ENERGY,
    CONF_CURRENCY,
    CONF_DAILY_AFTER_SUNSET,
    CONF_DISCHARGE_ENERGY,
    CONF_EXPORT_ENERGY,
    CONF_EXPORT_RATE,
    CONF_FALLBACK_EXPORT_RATE,
    CONF_FALLBACK_IMPORT_RATE,
    CONF_IMPORT_ENERGY,
    CONF_IMPORT_RATE,
    CONF_NOTIFY_ENTITIES,
    CONF_NOTIFY_SERVICE,
    CONF_PV_ENERGY,
    CONF_PV_POWER,
    CONF_RATE_SCALE,
    CONF_TELEGRAM_CHAT_IDS,
    CONF_TELEGRAM_ENTITIES,
    COMPILE_GRACE_SECONDS,
    DEFAULT_CURRENCY,
    DEFAULT_RATE_SCALE,
    DELIVERY_NOTIFY_ENTITY,
    DELIVERY_NOTIFY_SERVICE,
    DELIVERY_TELEGRAM,
    DOMAIN,
    PERIOD_DAILY,
    PERIOD_ENABLE,
    PERIOD_RESOLUTION,
    PERIOD_TIME,
    PERIOD_WEEKLY,
    PERIODS,
    RATE_SCALES,
    SCHEDULE_KEYS,
    SERVICE_GENERATE,
    SERVICE_SEND,
    SIGNAL_SCHEDULE,
)
from .config_flow import delivery_method
from .message import peak_text, render
from .report import choose_fallback, summarise
from .statistics import collect
from .window import span_label, window_for

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON, Platform.SENSOR, Platform.SWITCH, Platform.TIME]

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("period", default=PERIOD_DAILY): vol.In(PERIODS),
        vol.Optional("entry_id"): cv.string,
        vol.Optional("start"): cv.datetime,
        vol.Optional("end"): cv.datetime,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "timers": {}, "next": {}, "config": options(entry),
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_updated))
    _register_services(hass)
    _schedule_all(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    for cancel in hass.data[DOMAIN][entry.entry_id]["timers"].values():
        cancel()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_GENERATE)
            hass.services.async_remove(DOMAIN, SERVICE_SEND)
    return unloaded


async def _updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reschedule when only the schedule changed, reload for anything else.

    The schedule switches and time pickers write options on every change; a
    full reload for each would recreate every entity, rate mirrors included,
    each time a switch is flicked.
    """
    runtime = hass.data[DOMAIN][entry.entry_id]
    old, new = runtime["config"], options(entry)
    runtime["config"] = new
    changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
    if not changed:
        return
    if changed <= SCHEDULE_KEYS:
        _schedule_all(hass, entry)
        return
    await hass.config_entries.async_reload(entry.entry_id)


def options(entry: ConfigEntry) -> dict:
    """Options override data, so editing the entry does not need a reinstall."""
    return {**entry.data, **entry.options}


# --------------------------------------------------------------------------
# building a report


async def build(hass: HomeAssistant, entry: ConfigEntry, period: str,
                start: datetime | None = None, end: datetime | None = None) -> dict:
    """Collect, price and render one report. Returns every figure, not just text."""
    config = options(entry)
    if start is None or end is None:
        start, end = window_for(period, dt_util.now())
        if period == PERIOD_DAILY:
            # The window ends on a five-minute boundary. If that was only just
            # now, the recorder has not compiled the bucket before it yet.
            wait = (end + timedelta(seconds=COMPILE_GRACE_SECONDS) - dt_util.now()).total_seconds()
            if wait > 0:
                await asyncio.sleep(wait)

    collected = await collect(
        hass,
        start,
        end,
        PERIOD_RESOLUTION.get(period, "hour"),
        {
            "pv": config.get(CONF_PV_ENERGY),
            "imported": config.get(CONF_IMPORT_ENERGY),
            "exported": config.get(CONF_EXPORT_ENERGY),
            "charged": config.get(CONF_CHARGE_ENERGY),
            "discharged": config.get(CONF_DISCHARGE_ENERGY),
        },
        _own(hass, entry, "import_rate"),
        _own(hass, entry, "export_rate"),
        rate_divisor=1.0,  # the mirror already normalised the units
        extra_ids={"arbitrage": config.get(CONF_ARBITRAGE_ENERGY)},
        power_id=config.get(CONF_PV_POWER),
    )

    divisor = RATE_SCALES[config.get(CONF_RATE_SCALE, DEFAULT_RATE_SCALE)]
    totals = summarise(
        collected.buckets,
        fallback_import_rate=_fallback_rate(
            hass, config.get(CONF_IMPORT_RATE), divisor, config.get(CONF_FALLBACK_IMPORT_RATE),
            [b.import_rate for b in collected.buckets]),
        fallback_export_rate=_fallback_rate(
            hass, config.get(CONF_EXPORT_RATE), divisor, config.get(CONF_FALLBACK_EXPORT_RATE),
            [b.export_rate for b in collected.buckets]),
    )

    arbitrage, note = _arbitrage(hass, config, collected.extras)
    text = render(
        totals,
        period,
        span_label(period, start, end),
        currency=config.get(CONF_CURRENCY, DEFAULT_CURRENCY),
        arbitrage=arbitrage,
        arbitrage_note=note,
        has_battery=bool(config.get(CONF_CHARGE_ENERGY) or config.get(CONF_DISCHARGE_ENERGY)),
        peak=peak_text(period, collected.peak_watts, collected.peak_at),
    )

    return {
        "message": text,
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "solar": round(totals.pv, 3),
        "house": round(totals.house, 3),
        "imported": round(totals.imported, 3),
        "exported": round(totals.exported, 3),
        "charged": round(totals.charged, 3),
        "discharged": round(totals.discharged, 3),
        "solar_to_house": round(totals.solar_to_house, 3),
        "solar_to_battery": round(totals.solar_to_battery, 3),
        "solar_to_grid": round(totals.solar_to_grid, 3),
        "battery_to_house": round(totals.battery_to_house, 3),
        "battery_to_grid": round(totals.battery_to_grid, 3),
        "grid_to_house": round(totals.grid_to_house, 3),
        "grid_to_battery": round(totals.grid_to_battery, 3),
        "battery_churn": round(totals.battery_churn, 3),
        "grid_hunting": round(totals.grid_hunting, 3),
        "covered_percent": round(totals.covered, 1),
        "home_value": round(totals.home_value, 4),
        "export_value": round(totals.export_value, 4),
        "house_cost": round(totals.house_cost, 4),
        "battery_cost": round(totals.battery_cost, 4),
        "total_earnings": round(totals.total_earnings, 4),
        "arbitrage": None if arbitrage is None else round(arbitrage, 4),
        "peak_watts": collected.peak_watts,
        "peak_at": collected.peak_at.isoformat() if collected.peak_at else None,
        "estimated": totals.estimated,
        "buckets": totals.priced_buckets + totals.unpriced_buckets,
        "unpriced_buckets": totals.unpriced_buckets,
    }


def _own(hass: HomeAssistant, entry: ConfigEntry, slug: str) -> str | None:
    """Find an entity this integration created, by its unique id."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    return registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{slug}")


def _fallback_rate(hass: HomeAssistant, entity_id: str | None, divisor: float,
                   configured: float | None, recorded: list[float | None]) -> float:
    """Look up what choose_fallback() needs; the choice itself is in report.py."""
    state = hass.states.get(entity_id) if entity_id else None
    return choose_fallback(
        state.attributes.get("average") if state is not None else None,
        configured,
        recorded,
        state.state if state is not None else None,
        divisor,
    )


def _arbitrage(hass: HomeAssistant, config: dict, extras: dict) -> tuple[float | None, str | None]:
    """Optional third-party savings figure, with a gate that fails safe.

    The gate exists because a planner that is merely watching, or stopped, has
    not saved anything. Reporting its figure anyway would claim savings that
    were never made, so anything other than the expected gate state reports zero
    and says why.
    """
    entity_id = config.get(CONF_ARBITRAGE_ENERGY)
    if not entity_id:
        return None, None

    gate = config.get(CONF_ARBITRAGE_GATE)
    if gate:
        wanted = config.get(CONF_ARBITRAGE_GATE_STATE, "off")
        state = hass.states.get(gate)
        if state is None or state.state in ("unknown", "unavailable"):
            return 0.0, "the controller is not running"
        if state.state != wanted:
            return 0.0, "the controller is watching, not controlling"

    return extras.get("arbitrage", 0.0), None


# --------------------------------------------------------------------------
# services


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_GENERATE):
        return

    async def _pick(call: ServiceCall) -> ConfigEntry:
        entries = hass.config_entries.async_entries(DOMAIN)
        if entry_id := call.data.get("entry_id"):
            for entry in entries:
                if entry.entry_id == entry_id:
                    return entry
            raise vol.Invalid(f"no Energy Report entry with id {entry_id}")
        if not entries:
            raise vol.Invalid("Energy Report is not set up")
        return entries[0]

    async def generate(call: ServiceCall) -> ServiceResponse:
        entry = await _pick(call)
        return await build(hass, entry, call.data["period"],
                           call.data.get("start"), call.data.get("end"))

    async def send(call: ServiceCall) -> ServiceResponse:
        entry = await _pick(call)
        result = await build(hass, entry, call.data["period"],
                             call.data.get("start"), call.data.get("end"))
        await deliver(hass, entry, result["message"])
        return result

    hass.services.async_register(
        DOMAIN, SERVICE_GENERATE, generate, schema=SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND, send, schema=SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def plain(message: str) -> str:
    """The message without its Telegram HTML, for anything that would show the tags."""
    return re.sub(r"</?[a-z]+>", "", message)


async def deliver(hass: HomeAssistant, entry: ConfigEntry, message: str) -> None:
    """Send a report the configured way. Raises if no way is configured."""
    config = options(entry)
    method = delivery_method(config)

    if method == DELIVERY_TELEGRAM:
        base = {"message": message, "parse_mode": "html"}
        entities = config.get(CONF_TELEGRAM_ENTITIES) or []
        chat_ids = config.get(CONF_TELEGRAM_CHAT_IDS) or []
        if not entities and not chat_ids:
            raise HomeAssistantError("Energy Report: no Telegram chat is configured")
        # Two calls rather than one: which of entity_id and chat_id the bot
        # honours when it is given both has changed between releases.
        if entities:
            await hass.services.async_call(
                "telegram_bot", "send_message", base | {"entity_id": entities}, blocking=True)
        if chat_ids:
            await hass.services.async_call(
                "telegram_bot", "send_message", base | {"chat_id": chat_ids}, blocking=True)
        return

    if method == DELIVERY_NOTIFY_ENTITY:
        entities = config.get(CONF_NOTIFY_ENTITIES) or []
        if not entities:
            raise HomeAssistantError("Energy Report: no notify entity is configured")
        await hass.services.async_call(
            "notify", "send_message", {"entity_id": entities, "message": plain(message)},
            blocking=True)
        return

    if method == DELIVERY_NOTIFY_SERVICE:
        domain, _, service = (config.get(CONF_NOTIFY_SERVICE) or "").partition(".")
        if not service:
            domain, service = "notify", domain
        data = {"message": plain(message)}
        if domain == "telegram_bot":
            data = {"message": message, "parse_mode": "html"}
        await hass.services.async_call(domain, service, data, blocking=True)
        return

    raise HomeAssistantError(
        "Energy Report: no delivery is configured. Choose one under Configure, "
        f"or use {DOMAIN}.{SERVICE_GENERATE} and send the message yourself."
    )


async def run(hass: HomeAssistant, entry: ConfigEntry, period: str) -> dict:
    """Build a report and send it: what the schedule and the buttons do."""
    result = await build(hass, entry, period)
    await deliver(hass, entry, result["message"])
    return result


# --------------------------------------------------------------------------
# schedules


def _schedule_all(hass: HomeAssistant, entry: ConfigEntry) -> None:
    config = options(entry)
    runtime = hass.data[DOMAIN][entry.entry_id]
    for period, (key, default) in PERIOD_ENABLE.items():
        enabled = config.get(key)
        if default if enabled is None else enabled:
            _schedule(hass, entry, period)
        else:
            if cancel := runtime["timers"].pop(period, None):
                cancel()
            runtime["next"].pop(period, None)
    async_dispatcher_send(hass, SIGNAL_SCHEDULE.format(entry.entry_id))


def _parse_time(value: str, default: str) -> time:
    parsed = dt_util.parse_time(value or default) or dt_util.parse_time(default)
    return parsed


def _next_run(hass: HomeAssistant, entry: ConfigEntry, period: str,
              after: datetime) -> datetime:
    """When this period should next fire, strictly after `after`."""
    config = options(entry)
    key, default = PERIOD_TIME[period]
    at = _parse_time(config.get(key), default)
    candidate = dt_util.start_of_local_day(after).replace(
        hour=at.hour, minute=at.minute, second=at.second
    )

    if period == PERIOD_DAILY:
        while candidate <= after:
            candidate += timedelta(days=1)
        if config.get(CONF_DAILY_AFTER_SUNSET) is not False:
            # Whichever is later, the clock time or sunset. Compared as times,
            # never by asking the sun entity what state it is in: that state
            # lags the astronomical sunset by several minutes, which opens a
            # window where neither the clock nor the sun condition is true and
            # the report is silently skipped.
            sunset = get_astral_event_date(hass, "sunset", candidate.date())
            if sunset is not None:
                sunset = dt_util.as_local(sunset)
                if sunset > candidate:
                    candidate = sunset
        return candidate

    if period == PERIOD_WEEKLY:
        while candidate <= after or candidate.weekday() != 0:
            candidate += timedelta(days=1)
        return candidate

    while candidate <= after or candidate.day != 1:
        candidate += timedelta(days=1)
    return candidate


def _schedule(hass: HomeAssistant, entry: ConfigEntry, period: str) -> None:
    when = _next_run(hass, entry, period, dt_util.now())

    async def _fire(_now: datetime) -> None:
        # Re-arm first: a failure building or delivering the report must not
        # stop every future one.
        _schedule(hass, entry, period)
        try:
            await run(hass, entry, period)
        except Exception:  # noqa: BLE001 - a scheduled job has nowhere to raise
            _LOGGER.exception("energy_report: %s report failed", period)

    _LOGGER.debug("energy_report: next %s report at %s", period, when)
    # One timer per period, replaced rather than added to: _fire re-arms on every
    # run, so appending would leave a year of dead cancellers to unwind.
    runtime = hass.data[DOMAIN][entry.entry_id]
    if previous := runtime["timers"].get(period):
        previous()
    runtime["timers"][period] = async_track_point_in_time(hass, _fire, when)
    runtime["next"][period] = when
    async_dispatcher_send(hass, SIGNAL_SCHEDULE.format(entry.entry_id))
