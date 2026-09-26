"""The time each report goes out."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import options
from .const import PERIOD_TIME
from .entity import SettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        ReportTime(entry, key, default, f"{period.capitalize()} report time", f"{period}_time")
        for period, (key, default) in PERIOD_TIME.items()
    )


class ReportTime(SettingEntity, TimeEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clock-outline"

    def __init__(self, entry: ConfigEntry, key: str, default: str, name: str, slug: str) -> None:
        super().__init__(entry, name, slug)
        self._key = key
        self._default = default

    @property
    def native_value(self) -> time | None:
        value = options(self._entry).get(self._key) or self._default
        return dt_util.parse_time(value) or dt_util.parse_time(self._default)

    async def async_set_value(self, value: time) -> None:
        self._set_option(self._key, value.strftime("%H:%M:%S"))
