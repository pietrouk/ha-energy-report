"""How reports are sent, chosen from the device page."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import options
from .config_flow import delivery_method
from .const import CONF_DELIVERY, DELIVERY_METHODS
from .entity import SettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([MessagingSelect(entry)])


class MessagingSelect(SettingEntity, SelectEntity):
    """Telegram, a notify entity, a notify service, or nothing. Where it goes
    is the "Send to" text next to it."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:message-text-outline"
    _attr_translation_key = "messaging"
    _attr_options = DELIVERY_METHODS

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "Messaging method", "messaging")

    @property
    def current_option(self) -> str:
        return delivery_method(options(self._entry))

    async def async_select_option(self, option: str) -> None:
        self._set_option(CONF_DELIVERY, option)
