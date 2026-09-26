"""Turn each report on or off, and choose whether the daily one waits for sunset."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import options
from .const import CONF_DAILY_AFTER_SUNSET, PERIOD_ENABLE
from .entity import SettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    switches = [
        OptionSwitch(entry, key, default, f"{period.capitalize()} report",
                     f"{period}_enabled", "mdi:calendar-check")
        for period, (key, default) in PERIOD_ENABLE.items()
    ]
    switches.append(OptionSwitch(entry, CONF_DAILY_AFTER_SUNSET, True,
                                 "Daily report waits for sunset", "daily_after_sunset",
                                 "mdi:weather-sunset-down"))
    async_add_entities(switches)


class OptionSwitch(SettingEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, key: str, default: bool, name: str,
                 slug: str, icon: str) -> None:
        super().__init__(entry, name, slug)
        self._key = key
        self._default = default
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        value = options(self._entry).get(self._key)
        return self._default if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set_option(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set_option(self._key, False)
