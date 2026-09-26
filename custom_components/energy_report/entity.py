"""What every entity of this integration shares: the device it sits on."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_SCHEDULE


class EnergyReportEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, name: str, slug: str) -> None:
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{slug}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
            # Shown on the device page as a link to where Configure lives, for
            # the energy entities, prices and delivery.
            configuration_url=f"homeassistant://config/integrations/integration/{DOMAIN}",
        )


class SettingEntity(EnergyReportEntity):
    """An entity showing a setting, refreshed whenever settings change - from
    this entity, from another, or from the Configure dialog."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_SCHEDULE.format(self._entry.entry_id), self.async_write_ha_state
            )
        )

    def _set_option(self, key: str, value: object) -> None:
        self._set_options({key: value})

    def _set_options(self, changes: dict[str, object]) -> None:
        """Store settings. The update listener applies them and sends the signal
        that refreshes this entity and the others showing them."""
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, **changes}
        )
