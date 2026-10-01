"""Setup, Configure and the device-page entities, against a real Home Assistant.

    pip install pytest-homeassistant-custom-component
    pytest tests/test_integration.py

Every chat ID here is made up.
"""

from __future__ import annotations

from datetime import timedelta

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
LEVEL = {"state_class": "measurement", "device_class": "battery", "unit_of_measurement": "%"}
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
    hass.states.async_set("sensor.batt_level", "50", LEVEL)
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
        "switch.solar_battery_reports_daily_report", "switch.solar_battery_reports_weekly_report",
        "switch.solar_battery_reports_monthly_report", "switch.solar_battery_reports_daily_report_waits_for_sunset",
        "time.solar_battery_reports_daily_report_time", "time.solar_battery_reports_weekly_report_time",
        "time.solar_battery_reports_monthly_report_time",
        "button.solar_battery_reports_send_daily_report_now", "button.solar_battery_reports_send_weekly_report_now",
        "button.solar_battery_reports_send_monthly_report_now",
        "button.solar_battery_reports_preview_daily_report", "button.solar_battery_reports_preview_weekly_report",
        "button.solar_battery_reports_preview_monthly_report", "sensor.solar_battery_reports_report_preview",
        "sensor.solar_battery_reports_next_daily_report", "sensor.solar_battery_reports_next_weekly_report",
        "sensor.solar_battery_reports_next_monthly_report", "sensor.solar_battery_reports_import_rate_recorded",
        "select.solar_battery_reports_messaging_method", "text.solar_battery_reports_messaging_send_to",
    ):
        assert hass.states.get(entity_id) is not None, entity_id

    assert hass.states.get("switch.solar_battery_reports_daily_report").state == "on"
    assert hass.states.get("switch.solar_battery_reports_weekly_report").state == "off"
    assert hass.states.get("sensor.solar_battery_reports_next_daily_report").state not in ("unknown", "unavailable")
    assert hass.states.get("sensor.solar_battery_reports_next_weekly_report").state == "unknown"
    assert hass.states.get("time.solar_battery_reports_daily_report_time").state == "19:00:00"

    # Turning the weekly report on schedules it, without reloading the entry:
    # the rate mirror keeps its state object rather than being recreated.
    mirror_before = hass.states.get("sensor.solar_battery_reports_import_rate_recorded").last_changed
    await hass.services.async_call("switch", "turn_on",
                                   {"entity_id": "switch.solar_battery_reports_weekly_report"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["enable_weekly"] is True
    assert hass.states.get("switch.solar_battery_reports_weekly_report").state == "on"
    assert hass.states.get("sensor.solar_battery_reports_next_weekly_report").state not in ("unknown", "unavailable")
    assert hass.states.get("sensor.solar_battery_reports_import_rate_recorded").last_changed == mirror_before

    await hass.services.async_call("time", "set_value",
                                   {"entity_id": "time.solar_battery_reports_weekly_report_time", "time": "09:30:00"},
                                   blocking=True)
    await hass.async_block_till_done()
    assert entry.options["weekly_time"] == "09:30:00"
    nxt = dt_util.as_local(dt_util.parse_datetime(
        hass.states.get("sensor.solar_battery_reports_next_weekly_report").state))
    assert (nxt.weekday(), nxt.hour, nxt.minute) == (0, 9, 30)

    await hass.services.async_call("switch", "turn_off",
                                   {"entity_id": "switch.solar_battery_reports_daily_report"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.solar_battery_reports_next_daily_report").state == "unknown"


async def test_send_button_telegram(hass: HomeAssistant, world) -> None:
    await _setup(hass, delivery="telegram", telegram_chat_ids=[CHAT],
                 telegram_entities=["notify.bot_chat"])
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.solar_battery_reports_send_weekly_report_now"}, blocking=True)
    calls = world["telegram"]
    assert len(calls) == 2
    assert all(c.data["parse_mode"] == "html" for c in calls)
    # Telegram's HTML mode needs & escaped; plain-text delivery unescapes it.
    assert "<b>SOLAR &amp; BATTERY REPORT</b>\nLast week (" in calls[0].data["message"]
    assert {tuple(c.data.get("entity_id", [])) for c in calls} >= {("notify.bot_chat",)}
    assert [c.data["chat_id"] for c in calls if "chat_id" in c.data] == [[CHAT]]


async def test_send_button_notify_entity_is_plain_text(hass: HomeAssistant, world) -> None:
    await _setup(hass, delivery="notify_entity", notify_entities=["notify.phone"])
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.solar_battery_reports_send_monthly_report_now"}, blocking=True)
    (call,) = world["notify"]
    assert call.data["entity_id"] == ["notify.phone"]
    assert "<b>" not in call.data["message"]
    assert call.data["message"].startswith("SOLAR & BATTERY REPORT\nLast month (")


async def test_send_button_without_delivery_says_why(hass: HomeAssistant, world) -> None:
    from homeassistant.exceptions import HomeAssistantError

    await _setup(hass, delivery="none")
    with pytest.raises(HomeAssistantError, match="no messaging is configured"):
        await hass.services.async_call("button", "press",
                                       {"entity_id": "button.solar_battery_reports_send_weekly_report_now"}, blocking=True)


async def test_entry_from_before_delivery_choice_keeps_working(hass: HomeAssistant, world) -> None:
    """0.2 entries only had a notify service; that must still be what they use."""
    legacy = async_mock_service(hass, "notify", "mobile_app_phone")
    await _setup(hass, notify_service="notify.mobile_app_phone")
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.solar_battery_reports_send_weekly_report_now"}, blocking=True)
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
    assert shown["prices"].startswith(
        "import from sensor.tariff, recorded as sensor.solar_battery_reports_import_rate_recorded")
    assert "export: no rate entity, every period at the fallback 12p" in shown["prices"]

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
    assert hass.states.get("switch.solar_battery_reports_monthly_report").state == "on"
    assert hass.states.get("sensor.solar_battery_reports_next_monthly_report").state not in ("unknown", "unavailable")


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
        hass.states.get("sensor.solar_battery_reports_next_daily_report").state))
    sunset = dt_util.as_local(get_astral_event_date(hass, "sunset", nxt.date()))
    assert abs((nxt - sunset).total_seconds()) < 1

    await hass.services.async_call("switch", "turn_off",
                                   {"entity_id": "switch.solar_battery_reports_daily_report_waits_for_sunset"},
                                   blocking=True)
    await hass.async_block_till_done()
    nxt = dt_util.as_local(dt_util.parse_datetime(
        hass.states.get("sensor.solar_battery_reports_next_daily_report").state))
    assert (nxt.hour, nxt.minute) == (12, 0)


async def test_rate_mirror_keeps_its_price_across_a_restart(hass: HomeAssistant, world) -> None:
    """Straight after a restart the source is often unknown - Predbat republishes
    on its next cycle - and the mirror must hold the last price, not go blank."""
    hass.states.async_set("sensor.tariff", "unknown")
    mock_restore_cache_with_extra_data(hass, [(
        State("sensor.solar_battery_reports_import_rate_recorded", "0.2547"),
        {"native_value": 0.2547, "native_unit_of_measurement": "£/kWh"},
    )])
    await _setup(hass, delivery="none")
    assert hass.states.get("sensor.solar_battery_reports_import_rate_recorded").state == "0.2547"

    hass.states.async_set("sensor.tariff", "35.66")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.solar_battery_reports_import_rate_recorded").state == "0.3566"


async def test_messaging_from_the_device_page(hass: HomeAssistant, world) -> None:
    from homeassistant.exceptions import ServiceValidationError

    await _setup(hass, delivery="none")
    assert hass.states.get("select.solar_battery_reports_messaging_method").state == "none"
    assert hass.states.get("text.solar_battery_reports_messaging_send_to").state == ""
    mirror_before = hass.states.get("sensor.solar_battery_reports_import_rate_recorded").last_changed

    # A target cannot be set while messaging is off: it would not be clear what it is for.
    with pytest.raises(ServiceValidationError, match="Choose a method first"):
        await hass.services.async_call("text", "set_value", {
            "entity_id": "text.solar_battery_reports_messaging_send_to", "value": "notify.phone"}, blocking=True)

    await hass.services.async_call("select", "select_option", {
        "entity_id": "select.solar_battery_reports_messaging_method", "option": "telegram"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("select.solar_battery_reports_messaging_method").state == "telegram"

    with pytest.raises(ServiceValidationError, match="whole numbers"):
        await hass.services.async_call("text", "set_value", {
            "entity_id": "text.solar_battery_reports_messaging_send_to", "value": "family chat"}, blocking=True)

    await hass.services.async_call("text", "set_value", {
        "entity_id": "text.solar_battery_reports_messaging_send_to", "value": f"notify.bot_chat, {CHAT}"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("text.solar_battery_reports_messaging_send_to").state == f"notify.bot_chat, {CHAT}"
    # Applied in place: the entry was not reloaded.
    assert hass.states.get("sensor.solar_battery_reports_import_rate_recorded").last_changed == mirror_before

    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.solar_battery_reports_send_weekly_report_now"}, blocking=True)
    assert len(world["telegram"]) == 2

    await hass.services.async_call("select", "select_option", {
        "entity_id": "select.solar_battery_reports_messaging_method", "option": "notify_entity"}, blocking=True)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError, match="notify entities"):
        await hass.services.async_call("text", "set_value", {
            "entity_id": "text.solar_battery_reports_messaging_send_to", "value": "-12345"}, blocking=True)
    await hass.services.async_call("text", "set_value", {
        "entity_id": "text.solar_battery_reports_messaging_send_to", "value": "notify.phone"}, blocking=True)
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.solar_battery_reports_send_weekly_report_now"}, blocking=True)
    assert world["notify"][-1].data["entity_id"] == ["notify.phone"]


async def test_rate_sensor_says_where_its_price_comes_from(hass: HomeAssistant, world) -> None:
    await _setup(hass, delivery="none")
    attrs = hass.states.get("sensor.solar_battery_reports_import_rate_recorded").attributes
    assert attrs["friendly_name"] == "Solar & Battery reports Import rate (recorded)"
    assert attrs["recorded_from"] == "sensor.tariff"
    assert attrs["source_units"] == "hundredths per kWh"
    assert attrs["configured_fallback_rate"] == 0.2547


async def test_old_default_title_is_renamed(hass: HomeAssistant, world) -> None:
    from homeassistant.helpers import device_registry as dr

    entry = await _setup(hass, delivery="none")
    assert entry.title == "Solar & Battery reports"
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.name == "Solar & Battery reports"


async def test_a_title_the_user_chose_is_kept(hass: HomeAssistant, world) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Home energy",
                            data={**ENERGY, **RATES, **SCHEDULE, "delivery": "none"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.title == "Home energy"


async def test_daily_report_says_when_the_battery_first_peaked(hass: HomeAssistant, world) -> None:
    """Through the recorder's real state history: full at 14:15, a dip, full
    again from 15:50 - the report gives 14:15, to the minute."""
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    await _setup(hass, delivery="none", battery_soc="sensor.batt_level")
    end = dt_util.start_of_local_day()
    start = end - timedelta(days=1)
    for hour, minute, level in ((10, 0, "62"), (14, 15, "100"), (15, 5, "99"),
                                (15, 50, "100"), (16, 10, "unavailable"), (16, 11, "97")):
        moment = start.replace(hour=hour, minute=minute, second=7)
        hass.states.async_set("sensor.batt_level", level, LEVEL, timestamp=moment.timestamp())
    await async_wait_recording_done(hass)

    daily = await hass.services.async_call(
        DOMAIN, "generate", {"period": "daily", "start": start, "end": end},
        blocking=True, return_response=True)
    battery = daily["message"].split("🔋 Battery</b>\n", 1)[1].split("\n\n", 1)[0]
    assert battery.splitlines()[-1] == "Peak charge 100%, first reached at 14:15"
    assert daily["battery_peak_percent"] == 100.0
    assert dt_util.parse_datetime(daily["battery_peak_at"]) == start.replace(hour=14, minute=15, second=7)

    weekly = await hass.services.async_call(
        DOMAIN, "generate", {"period": "weekly", "start": start, "end": end},
        blocking=True, return_response=True)
    assert "Peak charge" not in weekly["message"]
    assert weekly["battery_peak_percent"] is None


async def test_battery_level_in_setup_and_configure(hass: HomeAssistant, world) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENERGY, "battery_soc": "sensor.nope"})
    assert result["errors"] == {"battery_soc": "not_found"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENERGY, "battery_soc": "sensor.batt_level"})
    assert result["step_id"] == "rates"

    entry = await _setup(hass, delivery="none", battery_soc="sensor.batt_level")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "battery level sensor.batt_level" in result["description_placeholders"]["energy"]


async def test_bill_carries_the_standing_charge_for_the_days_covered(hass: HomeAssistant, world) -> None:
    """An entity in pounds, a fixed number in pence, or neither - never in the earnings."""
    end = dt_util.start_of_local_day()
    window = {"start": end - timedelta(days=7), "end": end}

    async def bill(**extra) -> tuple[str, float | None]:
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_remove(entry.entry_id)
        await _setup(hass, delivery="none", **extra)
        result = await hass.services.async_call(
            DOMAIN, "generate", {"period": "weekly", **window}, blocking=True, return_response=True)
        line = result["message"].split("🧾 Bill</b>\n", 1)[1].split("\n", 1)[0]
        assert "standing" not in result["message"].split("☀️", 1)[0]
        return line, result["standing_charge"]

    assert await bill() == ("Bill total £0.00: £0.00 imported, £0.00 exported", None)

    hass.states.async_set("sensor.standing", "0.4242")
    line, standing = await bill(standing_charge_entity="sensor.standing", standing_charge_scale="per_kwh")
    assert line.endswith(", £2.97 standing charge") and standing == pytest.approx(2.9694)

    line, _ = await bill(standing_charge=42.42, standing_charge_scale="per_kwh_minor")
    assert line == "Bill total £2.97: £0.00 imported, £0.00 exported, £2.97 standing charge"

    # An entity with no number falls back to the fixed charge rather than dropping it.
    hass.states.async_set("sensor.standing", "unavailable")
    line, _ = await bill(standing_charge_entity="sensor.standing", standing_charge=40,
                         standing_charge_scale="per_kwh_minor")
    assert line.endswith(", £2.80 standing charge")


async def test_preview_shows_the_report_without_sending_it(hass: HomeAssistant, world) -> None:
    from homeassistant.components.persistent_notification import _async_get_or_create_notifications

    await _setup(hass, delivery="telegram", telegram_chat_ids=[CHAT])
    assert hass.states.get("sensor.solar_battery_reports_report_preview").state == "unknown"

    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.solar_battery_reports_preview_weekly_report"}, blocking=True)
    await hass.async_block_till_done()
    assert world["telegram"] == [] and world["notify"] == []

    (notification,) = [n for key, n in _async_get_or_create_notifications(hass).items() if "preview" in key]
    assert notification["title"] == "Weekly report preview (not sent)"
    assert notification["message"].startswith("**SOLAR & BATTERY REPORT**  \nLast week (")
    assert "**🧾 Bill**  \nBill total " in notification["message"]

    state = hass.states.get("sensor.solar_battery_reports_report_preview")
    assert state.state.startswith("Weekly: ")
    assert state.attributes["message"].startswith("SOLAR & BATTERY REPORT\nLast week (")
    assert "<b>" not in state.attributes["message"]

    # A second preview replaces the first rather than piling up.
    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.solar_battery_reports_preview_monthly_report"}, blocking=True)
    await hass.async_block_till_done()
    previews = [n for key, n in _async_get_or_create_notifications(hass).items() if "preview" in key]
    assert [n["title"] for n in previews] == ["Monthly report preview (not sent)"]
    assert hass.states.get("sensor.solar_battery_reports_report_preview").state.startswith("Monthly: ")


async def _battery_line(hass: HomeAssistant, period: str, start, end) -> str:
    result = await hass.services.async_call(
        DOMAIN, "generate", {"period": period, "start": start, "end": end}, blocking=True, return_response=True)
    return result["message"].split("🔋 Battery</b>\n", 1)[1].split("\n\n", 1)[0].splitlines()[-1]


async def test_level_at_each_end_from_state_history(hass: HomeAssistant) -> None:
    """Recent periods read the exact level in force at each end. No world
    fixture: the recorder only answers "what was the state at" for moments
    after the first state it ever wrote, so the oldest goes in first."""
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    end = dt_util.start_of_local_day()
    start = end - timedelta(days=7)
    for moment, level in ((start - timedelta(hours=3), "62"), (start + timedelta(hours=5), "90"),
                          (end - timedelta(hours=2), "50"), (end + timedelta(minutes=30), "70")):
        hass.states.async_set("sensor.batt_level", level, LEVEL, timestamp=moment.timestamp())
    await async_wait_recording_done(hass)

    await _setup(hass, delivery="none", battery_soc="sensor.batt_level", battery_capacity=15.7)
    # No energy statistics here, so nothing came out: the 1.9 kWh fall is loss.
    assert await _battery_line(hass, "weekly", start, end) == \
        "Level 62% → 50%: ran on 1.9 kWh stored earlier, counted now it's used; 1.9 lost in the battery"


async def test_level_at_each_end_from_statistics_once_history_is_gone(hass: HomeAssistant) -> None:
    """A month back is past purge_keep_days: the hourly means beside each end."""
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    hass.states.async_set("sensor.batt_level", "50", LEVEL)
    end = dt_util.start_of_local_day() - timedelta(days=40)
    start = end - timedelta(days=30)
    async_import_statistics(hass, {
        "mean_type": StatisticMeanType.ARITHMETIC, "has_sum": False, "name": None, "source": "recorder",
        "statistic_id": "sensor.batt_level", "unit_class": None, "unit_of_measurement": "%",
    }, [{"start": start - timedelta(hours=1), "mean": 90.0, "min": 90.0, "max": 90.0},
        {"start": start, "mean": 41.0, "min": 40.0, "max": 42.0},
        {"start": end - timedelta(hours=1), "mean": 3.0, "min": 3.0, "max": 3.0},
        {"start": end, "mean": 80.0, "min": 80.0, "max": 80.0}])
    await async_wait_recording_done(hass)

    await _setup(hass, delivery="none", battery_soc="sensor.batt_level", battery_capacity=15.7)
    assert (await _battery_line(hass, "monthly", start, end)).startswith("Level 41% → 3%: ")

    # Without a capacity it says only what the counters know.
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(entry.entry_id)
    await _setup(hass, delivery="none", battery_soc="sensor.batt_level")
    assert (await _battery_line(hass, "monthly", start, end)) == "Net 0.0 kWh"
