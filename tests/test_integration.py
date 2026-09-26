"""Setup, Configure and the device-page entities, against a real Home Assistant.

    pip install pytest-homeassistant-custom-component
    pytest tests/test_integration.py

Every chat ID here is made up.
"""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
    mock_restore_cache_with_extra_data,
)

from custom_components.energy_report.const import DOMAIN

CHAT = -1001234567890

ENERGY = {
    "pv_energy": "sensor.pv_total",
    "import_energy": "sensor.grid_in",
    "export_energy": "sensor.grid_out",
    "charge_energy": "sensor.batt_in",
    "discharge_energy": "sensor.batt_out",
}
RATES = {
    "rate_scale": "per_kwh_minor",
    "fallback_import_rate": 25.47,
    "fallback_export_rate": 12,
    "currency": "£",
}
SCHEDULE = {
    "enable_daily": True, "daily_time": "19:00:00", "daily_after_sunset": True,
    "enable_weekly": False, "weekly_time": "08:00:00",
    "enable_monthly": False, "monthly_time": "08:00:00",
}


@pytest.fixture
async def world(hass: HomeAssistant):
    """Counters with statistics, a power sensor, a price and a Telegram bot."""
    for entity_id in ENERGY.values():
        hass.states.async_set(entity_id, "100", {
            "state_class": "total_increasing", "device_class": "energy", "unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_power", "1200", {
        "state_class": "measurement", "device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set("sensor.pv_watts_total", "5", {"device_class": "power"})
    hass.states.async_set("sensor.tariff", "25.47")
    return {
        "telegram": async_mock_service(hass, "telegram_bot", "send_message"),
        "notify": async_mock_service(hass, "notify", "send_message"),
    }


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Energy Report",
                            data={**ENERGY, **RATES, **SCHEDULE, "import_rate": "sensor.tariff", **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_flow_all_the_way(hass: HomeAssistant, world) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"

    # A power sensor where a counter belongs is refused, on that field.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENERGY, "pv_energy": "sensor.pv_watts_total"})
    assert result["errors"] == {"pv_energy": "not_a_total"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENERGY, "pv_power": "sensor.pv_power"})
    assert result["step_id"] == "rates"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], RATES)
    assert result["step_id"] == "delivery"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"delivery": "telegram"})
    assert result["step_id"] == "delivery_details"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"telegram_chat_ids": "abc"})
    assert result["errors"] == {"telegram_chat_ids": "bad_chat_id"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "no_chat"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"telegram_chat_ids": f"{CHAT}, 42"})
    assert result["step_id"] == "schedule"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], SCHEDULE)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["telegram_chat_ids"] == [CHAT, 42]
    assert result["data"]["pv_power"] == "sensor.pv_power"


async def test_no_delivery_skips_the_details(hass: HomeAssistant, world) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENERGY)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], RATES)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"delivery": "none"})
    assert result["step_id"] == "schedule"


async def test_device_page_entities(hass: HomeAssistant, world) -> None:
    entry = await _setup(hass, delivery="telegram", telegram_chat_ids=[CHAT])
    for entity_id in (
        "switch.energy_report_daily_report", "switch.energy_report_weekly_report",
        "switch.energy_report_monthly_report", "switch.energy_report_daily_report_waits_for_sunset",
        "time.energy_report_daily_report_time", "time.energy_report_weekly_report_time",
        "time.energy_report_monthly_report_time",
        "button.energy_report_send_daily_report_now", "button.energy_report_send_weekly_report_now",
        "button.energy_report_send_monthly_report_now",
        "sensor.energy_report_next_daily_report", "sensor.energy_report_next_weekly_report",
        "sensor.energy_report_next_monthly_report", "sensor.energy_report_import_rate",
    ):
        assert hass.states.get(entity_id) is not None, entity_id

    assert hass.states.get("switch.energy_report_daily_report").state == "on"
    assert hass.states.get("switch.energy_report_weekly_report").state == "off"
    assert hass.states.get("sensor.energy_report_next_daily_report").state not in ("unknown", "unavailable")
    assert hass.states.get("sensor.energy_report_next_weekly_report").state == "unknown"
    assert hass.states.get("time.energy_report_daily_report_time").state == "19:00:00"

    # Turning the weekly report on schedules it, without reloading the entry:
    # the rate mirror keeps its state object rather than being recreated.
    mirror_before = hass.states.get("sensor.energy_report_import_rate").last_changed
    await hass.services.async_call("switch", "turn_on",
                                   {"entity_id": "switch.energy_report_weekly_report"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["enable_weekly"] is True
    assert hass.states.get("switch.energy_report_weekly_report").state == "on"
    assert hass.states.get("sensor.energy_report_next_weekly_report").state not in ("unknown", "unavailable")
    assert hass.states.get("sensor.energy_report_import_rate").last_changed == mirror_before

    await hass.services.async_call("time", "set_value",
                                   {"entity_id": "time.energy_report_weekly_report_time", "time": "09:30:00"},
                                   blocking=True)
    await hass.async_block_till_done()
    assert entry.options["weekly_time"] == "09:30:00"
    nxt = dt_util.as_local(dt_util.parse_datetime(
        hass.states.get("sensor.energy_report_next_weekly_report").state))
    assert (nxt.weekday(), nxt.hour, nxt.minute) == (0, 9, 30)

    await hass.services.async_call("switch", "turn_off",
                                   {"entity_id": "switch.energy_report_daily_report"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.energy_report_next_daily_report").state == "unknown"


async def test_send_button_telegram(hass: HomeAssistant, world) -> None:
    await _setup(hass, delivery="telegram", telegram_chat_ids=[CHAT],
                 telegram_entities=["notify.bot_chat"])
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.energy_report_send_weekly_report_now"}, blocking=True)
    calls = world["telegram"]
    assert len(calls) == 2
    assert all(c.data["parse_mode"] == "html" for c in calls)
    assert "<b>SOLAR SUMMARY - Last week</b>" in calls[0].data["message"]
    assert {tuple(c.data.get("entity_id", [])) for c in calls} >= {("notify.bot_chat",)}
    assert [c.data["chat_id"] for c in calls if "chat_id" in c.data] == [[CHAT]]


async def test_send_button_notify_entity_is_plain_text(hass: HomeAssistant, world) -> None:
    await _setup(hass, delivery="notify_entity", notify_entities=["notify.phone"])
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.energy_report_send_monthly_report_now"}, blocking=True)
    (call,) = world["notify"]
    assert call.data["entity_id"] == ["notify.phone"]
    assert "<b>" not in call.data["message"]
    assert call.data["message"].startswith("SOLAR SUMMARY - Last month")


async def test_send_button_without_delivery_says_why(hass: HomeAssistant, world) -> None:
    from homeassistant.exceptions import HomeAssistantError

    await _setup(hass, delivery="none")
    with pytest.raises(HomeAssistantError, match="no delivery is configured"):
        await hass.services.async_call("button", "press",
                                       {"entity_id": "button.energy_report_send_weekly_report_now"}, blocking=True)


async def test_entry_from_before_delivery_choice_keeps_working(hass: HomeAssistant, world) -> None:
    """0.2 entries only had a notify service; that must still be what they use."""
    legacy = async_mock_service(hass, "notify", "mobile_app_phone")
    await _setup(hass, notify_service="notify.mobile_app_phone")
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.energy_report_send_weekly_report_now"}, blocking=True)
    (call,) = legacy
    assert "<b>" not in call.data["message"]


async def test_configure_menu(hass: HomeAssistant, world) -> None:
    entry = await _setup(hass, pv_power="sensor.pv_power", delivery="telegram", telegram_chat_ids=[CHAT])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"energy", "rates", "delivery", "schedule"}
    shown = result["description_placeholders"]
    assert shown["delivery"] == f"Telegram to chat {CHAT}"
    assert shown["schedule"] == "daily at 19:00 or sunset if later"
    assert "solar sensor.pv_total" in shown["energy"] and "solar power sensor.pv_power" in shown["energy"]

    # Energy entities can be changed, and an optional one cleared: stored as
    # None so the value from setup does not show through.
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "energy"})
    assert result["step_id"] == "energy"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**ENERGY, "pv_energy": "sensor.batt_out"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["pv_energy"] == "sensor.batt_out"
    assert entry.options["pv_power"] is None

    # Delivery: switch to a notify entity.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "delivery"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"delivery": "notify_entity"})
    assert result["step_id"] == "delivery_details"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"notify_entities": ["notify.phone"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["delivery"] == "notify_entity"
    assert entry.options["pv_energy"] == "sensor.batt_out"  # earlier change kept

    # Telegram details come back pre-filled as text.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "delivery"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"delivery": "telegram"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"telegram_chat_ids": "7, 8"})
    await hass.async_block_till_done()
    assert entry.options["telegram_chat_ids"] == [7, 8]

    # Schedule from the menu reaches the device page entities.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "schedule"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**SCHEDULE, "enable_monthly": True})
    await hass.async_block_till_done()
    assert hass.states.get("switch.energy_report_monthly_report").state == "on"
    assert hass.states.get("sensor.energy_report_next_monthly_report").state not in ("unknown", "unavailable")


async def test_unknown_notify_service_is_refused(hass: HomeAssistant, world) -> None:
    entry = await _setup(hass, delivery="none")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "delivery"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"delivery": "notify_service"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"notify_service": "notify.nope"})
    assert result["errors"] == {"notify_service": "unknown_service"}


async def test_daily_waits_for_sunset_without_the_sun_integration(hass: HomeAssistant, world) -> None:
    """Sunset comes from the home location, not from sun.sun, so it works with
    the sun integration not loaded at all."""
    from homeassistant.helpers.sun import get_astral_event_date

    await _setup(hass, delivery="none", daily_time="12:00:00")
    nxt = dt_util.as_local(dt_util.parse_datetime(
        hass.states.get("sensor.energy_report_next_daily_report").state))
    sunset = dt_util.as_local(get_astral_event_date(hass, "sunset", nxt.date()))
    assert abs((nxt - sunset).total_seconds()) < 1

    await hass.services.async_call("switch", "turn_off",
                                   {"entity_id": "switch.energy_report_daily_report_waits_for_sunset"},
                                   blocking=True)
    await hass.async_block_till_done()
    nxt = dt_util.as_local(dt_util.parse_datetime(
        hass.states.get("sensor.energy_report_next_daily_report").state))
    assert (nxt.hour, nxt.minute) == (12, 0)


async def test_rate_mirror_keeps_its_price_across_a_restart(hass: HomeAssistant, world) -> None:
    """Straight after a restart the source is often unknown - Predbat republishes
    on its next cycle - and the mirror must hold the last price, not go blank."""
    hass.states.async_set("sensor.tariff", "unknown")
    mock_restore_cache_with_extra_data(hass, [(
        State("sensor.energy_report_import_rate", "0.2547"),
        {"native_value": 0.2547, "native_unit_of_measurement": "£/kWh"},
    )])
    await _setup(hass, delivery="none")
    assert hass.states.get("sensor.energy_report_import_rate").state == "0.2547"

    hass.states.async_set("sensor.tariff", "35.66")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.energy_report_import_rate").state == "0.3566"
