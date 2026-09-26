"""Where reports are sent, typed on the device page.

One field whatever the method, read according to the Messaging select:

  Telegram        chat entities and/or chat IDs, comma separated:
                  notify.my_bot_family, -1001234567890
  Notify entity   notify entities, comma separated: notify.my_phone
  Notify service  one service name: notify.mobile_app_my_phone

It is checked the same way as the Configure form, and a bad value is refused
with the reason rather than stored.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import options
from .config_flow import _check_delivery, delivery_method
from .const import (
    CONF_NOTIFY_ENTITIES,
    CONF_NOTIFY_SERVICE,
    CONF_TELEGRAM_CHAT_IDS,
    CONF_TELEGRAM_ENTITIES,
    DELIVERY_NOTIFY_ENTITY,
    DELIVERY_NOTIFY_SERVICE,
    DELIVERY_TELEGRAM,
)
from .entity import SettingEntity

REASONS = {
    "bad_chat_id": "Telegram chat IDs must be whole numbers, for example -1001234567890.",
    "no_chat": "Give at least one Telegram chat entity or chat ID.",
    "no_telegram": "The Telegram bot integration is not set up, so there is nothing to send through.",
    "unknown_service": "There is no such service. Check the name under Developer tools > Actions.",
    "not_notify": "Give notify entities, for example notify.my_phone.",
    "none": "Messaging is set to don't send. Choose a method in Messaging first.",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SendTo(entry)])


def _items(text: str) -> list[str]:
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


class SendTo(SettingEntity, TextEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:send-outline"
    _attr_native_max = 255

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "Send to", "send_to")

    @property
    def native_value(self) -> str:
        config = options(self._entry)
        method = delivery_method(config)
        if method == DELIVERY_TELEGRAM:
            parts = [*(config.get(CONF_TELEGRAM_ENTITIES) or []),
                     *(str(c) for c in config.get(CONF_TELEGRAM_CHAT_IDS) or [])]
        elif method == DELIVERY_NOTIFY_ENTITY:
            parts = list(config.get(CONF_NOTIFY_ENTITIES) or [])
        elif method == DELIVERY_NOTIFY_SERVICE:
            parts = [config.get(CONF_NOTIFY_SERVICE) or ""]
        else:
            parts = []
        return ", ".join(p for p in parts if p)

    async def async_set_value(self, value: str) -> None:
        method = delivery_method(options(self._entry))
        items = _items(value)

        if method == DELIVERY_TELEGRAM:
            entities = [i for i in items if i.startswith("notify.")]
            ids = ", ".join(i for i in items if not i.startswith("notify."))
            user_input = {CONF_TELEGRAM_ENTITIES: entities, CONF_TELEGRAM_CHAT_IDS: ids}
        elif method == DELIVERY_NOTIFY_ENTITY:
            if not items or not all(i.startswith("notify.") for i in items):
                raise ServiceValidationError(REASONS["not_notify"])
            user_input = {CONF_NOTIFY_ENTITIES: items}
        elif method == DELIVERY_NOTIFY_SERVICE:
            user_input = {CONF_NOTIFY_SERVICE: items[0] if items else ""}
        else:
            raise ServiceValidationError(REASONS["none"])

        cleaned, errors = _check_delivery(self.hass, method, user_input)
        if errors:
            raise ServiceValidationError(REASONS.get(next(iter(errors.values())), "Not a valid target."))
        self._set_options(cleaned)
