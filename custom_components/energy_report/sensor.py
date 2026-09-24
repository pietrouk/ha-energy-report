"""The entities this integration creates.

Two kinds, both of which exist so that recorder statistics have something to
read later:

  * a house-load energy counter, derived from the flows around it, for systems
    whose inverter does not report one, and
  * a rate mirror, which copies whatever entity holds the current price into a
    real sensor. The price source is often not in the sensor domain at all -
    Predbat's predbat.rates, for instance - and so gets no statistics of its
    own. Without a mirror there is no historical rate and every kWh has to be
    valued at whatever the price happens to be when the report runs.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import (
    CONF_CHARGE_ENERGY,
    CONF_DISCHARGE_ENERGY,
    CONF_EXPORT_ENERGY,
    CONF_HOUSE_ENERGY,
    CONF_IMPORT_ENERGY,
    CONF_IMPORT_RATE,
    CONF_EXPORT_RATE,
    CONF_PV_ENERGY,
    CONF_RATE_SCALE,
    DOMAIN,
    RATE_SCALES,
    DEFAULT_RATE_SCALE,
)

_LOGGER = logging.getLogger(__name__)

# A single five-minute statistics bucket needs at least one recorded state in it
# or no row is produced at all. A flat tariff can sit unchanged for months, so
# the mirror is written on a timer as well as on change.
MIRROR_INTERVAL = timedelta(minutes=1)

# Largest plausible step from one counter reading to the next, in kWh. A jump
# past this is an integration restarting or a counter being rewritten, not
# energy that flowed, and is dropped rather than added.
MAX_STEP_KWH = 25.0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = entry.data
    entities: list[SensorEntity] = []

    if not data.get(CONF_HOUSE_ENERGY):
        sources = {
            data.get(CONF_PV_ENERGY): 1,
            data.get(CONF_IMPORT_ENERGY): 1,
            data.get(CONF_DISCHARGE_ENERGY): 1,
            data.get(CONF_EXPORT_ENERGY): -1,
            data.get(CONF_CHARGE_ENERGY): -1,
        }
        sources.pop(None, None)
        if sources:
            entities.append(HouseLoadEnergy(entry, sources))

    divisor = RATE_SCALES[data.get(CONF_RATE_SCALE, DEFAULT_RATE_SCALE)]
    for key, name, slug in (
        (CONF_IMPORT_RATE, "Import rate", "import_rate"),
        (CONF_EXPORT_RATE, "Export rate", "export_rate"),
    ):
        if data.get(key):
            entities.append(RateMirror(entry, data[key], name, slug, divisor))

    async_add_entities(entities)


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, name: str, slug: str) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{slug}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Energy Report",
            "entry_type": "service",
        }


class HouseLoadEnergy(_Base, RestoreSensor):
    """House consumption, worked out from everything around it.

    house = solar + import + discharge - export - charge

    Each source is a counter, so what is accumulated is the step between
    readings. A counter that goes down has been reset, and only its new value
    counts - carrying the negative would subtract a whole day's energy in one go.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:home-lightning-bolt"

    def __init__(self, entry: ConfigEntry, sources: dict[str, int]) -> None:
        super().__init__(entry, "House load energy", "house_load_energy")
        self._sources = sources
        self._last: dict[str, float] = {}
        self._total = 0.0

    @property
    def native_value(self) -> float:
        return round(self._total, 3)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (restored := await self.async_get_last_sensor_data()) is not None:
            try:
                self._total = float(restored.native_value)
            except (TypeError, ValueError):
                self._total = 0.0
        # Seed from the current readings so the first step after a restart is
        # measured from now, not from whatever was last seen before the restart.
        for entity_id in self._sources:
            if (state := self.hass.states.get(entity_id)) is not None:
                try:
                    self._last[entity_id] = float(state.state)
                except (TypeError, ValueError):
                    pass
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, list(self._sources), self._source_changed
            )
        )

    @callback
    def _source_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        if new_state is None:
            return
        try:
            value = float(new_state.state)
        except (TypeError, ValueError):
            # unknown / unavailable: hold the last reading so the gap is skipped
            # rather than counted as a reset down to zero and back up.
            return

        previous = self._last.get(entity_id)
        self._last[entity_id] = value
        if previous is None:
            return

        step = value - previous if value >= previous else value
        if step < 0 or step > MAX_STEP_KWH:
            return

        self._total += self._sources[entity_id] * step
        if self._total < 0:
            # Only reachable if an export or charge counter ran ahead of the
            # others mid-cycle. Clamping keeps total_increasing honest.
            self._total = 0.0
        self.async_write_ha_state()


class RateMirror(_Base):
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
        currency = self._entry.data.get("currency") or ""
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
