"""Sensors: rate mirrors, and when each report next goes out.

A rate mirror copies whatever entity holds the current price into a real sensor
the recorder keeps statistics for. The price source is often not in the sensor
domain at all - Predbat's predbat.rates, for instance - and so gets no
statistics of its own. Without a mirror there is no historical rate, and every
kWh would have to be valued at whatever the price happened to be when the
report ran.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from . import options
from .const import (
    CONF_CURRENCY,
    CONF_EXPORT_RATE,
    CONF_IMPORT_RATE,
    CONF_RATE_SCALE,
    DEFAULT_RATE_SCALE,
    DOMAIN,
    PERIODS,
    RATE_SCALES,
)
from .entity import EnergyReportEntity, ScheduleEntity

_LOGGER = logging.getLogger(__name__)

# A single five-minute statistics bucket needs at least one recorded state in it
# or no row is produced at all. A flat tariff can sit unchanged for months, so
# the mirror is written on a timer as well as on change.
MIRROR_INTERVAL = timedelta(minutes=1)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = options(entry)
    entities: list[SensorEntity] = [NextReport(entry, period) for period in PERIODS]

    divisor = RATE_SCALES[data.get(CONF_RATE_SCALE, DEFAULT_RATE_SCALE)]
    for key, name, slug in (
        (CONF_IMPORT_RATE, "Import rate", "import_rate"),
        (CONF_EXPORT_RATE, "Export rate", "export_rate"),
    ):
        if data.get(key):
            entities.append(RateMirror(entry, data[key], name, slug, divisor))

    async_add_entities(entities)


class RateMirror(EnergyReportEntity, SensorEntity):
    """Copy a price into a sensor the recorder will keep statistics for."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cash"

    def __init__(
        self, entry: ConfigEntry, source: str, name: str, slug: str, divisor: float
    ) -> None:
        super().__init__(entry, name, slug)
        self._source = source
        self._divisor = divisor or 1.0
        self._value: float | None = None

    @property
    def native_value(self) -> float | None:
        return self._value

    @property
    def native_unit_of_measurement(self) -> str | None:
        currency = options(self._entry).get(CONF_CURRENCY) or ""
        return f"{currency}/kWh" if currency else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._refresh()
        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._source], self._changed)
        )
        self.async_on_remove(
            async_track_time_interval(self.hass, self._tick, MIRROR_INTERVAL)
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
        try:
            value = round(float(state.state) / self._divisor, 6)
        except (TypeError, ValueError):
            # Hold the last good price. Blanking it would punch a hole in the
            # statistics and leave that bucket unpriced for no good reason.
            return
        self._value = value
        self.async_write_ha_state()


class NextReport(ScheduleEntity, SensorEntity):
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
