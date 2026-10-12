"""Sensors: rate mirrors, when each report next goes out, and the last preview.

A rate mirror copies whatever entity holds the current price into a real sensor
the recorder keeps statistics for. The price source is often not in the sensor
domain at all - Predbat's predbat.rates, for instance - and so gets no
statistics of its own. Without a mirror there is no historical rate, and every
kWh would have to be valued at whatever the price happened to be when the
report ran.
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_utc_time_change
from homeassistant.util import dt as dt_util

from . import options, plain
from .const import (
    CONF_CURRENCY,
    CONF_EXPORT_RATE,
    CONF_FALLBACK_EXPORT_RATE,
    CONF_FALLBACK_IMPORT_RATE,
    CONF_IMPORT_RATE,
    CONF_IMPORT_RATE_LOWER,
    CONF_RATE_SCALE,
    DEFAULT_RATE_SCALE,
    DOMAIN,
    PERIODS,
    RATE_SCALES,
    SIGNAL_PREVIEW,
)
from .entity import EnergyReportEntity, SettingEntity
from .report import scheduled_price

_LOGGER = logging.getLogger(__name__)

# A single five-minute statistics bucket needs at least one recorded state in it
# or no row is produced at all. A flat tariff can sit unchanged for months, so
# the mirror is written on a timer as well as on change.


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = options(entry)
    entities: list[SensorEntity] = [NextReport(entry, period) for period in PERIODS]
    entities.append(ReportPreview(entry))

    divisor = RATE_SCALES[data.get(CONF_RATE_SCALE) or DEFAULT_RATE_SCALE]
    for key, fallback_key, name, slug in (
        (CONF_IMPORT_RATE, CONF_FALLBACK_IMPORT_RATE, "Import rate (recorded)", "import_rate"),
        (CONF_EXPORT_RATE, CONF_FALLBACK_EXPORT_RATE, "Export rate (recorded)", "export_rate"),
    ):
        if data.get(key):
            entities.append(RateMirror(entry, data[key], name, slug, divisor,
                                       data.get(fallback_key),
                                       lower=data.get(CONF_IMPORT_RATE_LOWER) if key == CONF_IMPORT_RATE else None))

    async_add_entities(entities)


class RateMirror(EnergyReportEntity, RestoreSensor):
    """Copy a price into a sensor the recorder will keep statistics for.

    When the source carries its own schedule (a "rates" list or a "results"
    map, as Predbat's and Octopus's entities do), the price comes from that, on
    the minute it changes; otherwise from the source's state.

    This is not the fallback. It is the rate the reports use, recorded from the
    entity picked under Prices; its attributes say which one. The fallback is
    only for periods from before this sensor started recording.

    The last price survives a restart. Straight after one, the source is often
    unknown for a few minutes - Predbat republishes its entities on its next
    cycle - and a mirror starting empty would leave those buckets unpriced.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cash"

    def __init__(
        self, entry: ConfigEntry, source: str, name: str, slug: str, divisor: float,
        fallback: float | None, lower: str | None = None,
    ) -> None:
        super().__init__(entry, name, slug)
        self._source = source
        self._lower = lower
        self._divisor = divisor or 1.0
        self._value: float | None = None
        self._attr_extra_state_attributes = {
            "recorded_from": source,
            "source_units": "hundredths per kWh" if self._divisor == 100 else "whole units per kWh",
            # Only for periods before this sensor recorded anything, and only
            # when the source has no average of its own (Predbat's has one).
            "configured_fallback_rate": round(float(fallback) / self._divisor, 6) if fallback else None,
        }
        if lower:
            self._attr_extra_state_attributes["lower_price_from"] = lower

    @property
    def native_value(self) -> float | None:
        return self._value

    @property
    def native_unit_of_measurement(self) -> str | None:
        currency = options(self._entry).get(CONF_CURRENCY) or ""
        return f"{currency}/kWh" if currency else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            try:
                self._value = float(last.native_value)
            except (TypeError, ValueError):
                pass
        self._refresh()
        if self._value is not None:
            self.async_write_ha_state()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source, *([self._lower] if self._lower else [])], self._changed)
        )
        self.async_on_remove(
            # On the minute, every minute: prices change on minute boundaries
            # (on the half hour for Octopus), and a schedule says exactly which.
            async_track_utc_time_change(self.hass, self._tick, second=0)
        )

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        self._refresh()

    @callback
    def _tick(self, _now) -> None:
        self._refresh()

    @callback
    def _refresh(self) -> None:
        state = self.hass.states.get(self._source)
        if state is None:
            return
        # The price the source's own schedule says is in force now, if it has
        # one; its state only follows when its integration next updates.
        price = scheduled_price(state.attributes, dt_util.utcnow())
        try:
            value = round(float(state.state if price is None else price) / self._divisor, 6)
        except (TypeError, ValueError):
            # Hold the last good price. Blanking it would punch a hole in the
            # statistics and leave that bucket unpriced for no good reason.
            return
        # A free session, or anything else genuinely cheaper, from the second
        # price; never a dearer one, so a saving session's reward is left out.
        if (lower := self._lower_price()) is not None:
            value = min(value, lower)
        self._value = value
        self.async_write_ha_state()

    def _lower_price(self) -> float | None:
        """The second price in currency units per kWh, or None if it has no
        number. Its own unit says whether it is in pence (Predbat's are 'p');
        without one it is taken to be in the same units as the tariff."""
        if not self._lower or (state := self.hass.states.get(self._lower)) is None:
            return None
        price = scheduled_price(state.attributes, dt_util.utcnow())
        try:
            value = float(state.state if price is None else price)
        except (TypeError, ValueError):
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit.split("/")[0] in MINOR_UNIT_NAMES:
            divisor = 100.0
        elif unit:
            divisor = 1.0
        else:
            divisor = self._divisor
        return round(value / divisor, 6)


# Units that mean hundredths of the currency.
MINOR_UNIT_NAMES = {"p", "pence", "c", "cent", "cents", "¢"}


class NextReport(SettingEntity, SensorEntity):
    """When a report next goes out; unknown while that report is turned off.

    The daily one shows the later of the chosen time and sunset, when it is set
    to wait for sunset, so this is where to see which one won today.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: ConfigEntry, period: str) -> None:
        super().__init__(entry, f"Next {period} report", f"next_{period}")
        self._period = period

    @property
    def native_value(self) -> datetime | None:
        runtime = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        return runtime.get("next", {}).get(self._period)


class ReportPreview(EnergyReportEntity, SensorEntity):
    """The last report built by a Preview button, never sent.

    A state holds 255 characters at most, so the state names the report and the
    message itself is an attribute - kept out of the database, since the
    notification already shows it formatted.
    """

    _attr_icon = "mdi:file-eye-outline"
    _unrecorded_attributes = frozenset({"message"})

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "Report preview", "preview")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_PREVIEW.format(self._entry.entry_id), self.async_write_ha_state))

    @property
    def _preview(self) -> dict | None:
        return self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {}).get("preview")

    @property
    def native_value(self) -> str | None:
        if (shown := self._preview) is None:
            return None
        return f"{shown['period'].capitalize()}: {shown['span']}"

    @property
    def extra_state_attributes(self) -> dict | None:
        if (shown := self._preview) is None:
            return None
        return {
            "message": plain(shown["message"]),
            "period": shown["period"],
            "start": shown["start"],
            "end": shown["end"],
            "generated_at": shown["generated_at"].isoformat(),
        }
