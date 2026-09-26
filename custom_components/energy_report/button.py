"""Send a report now, through the configured delivery."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import run
from .const import PERIODS
from .entity import EnergyReportEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(SendButton(entry, period) for period in PERIODS)


class SendButton(EnergyReportEntity, ButtonEntity):
    _attr_icon = "mdi:send"

    def __init__(self, entry: ConfigEntry, period: str) -> None:
        super().__init__(entry, f"Send {period} report now", f"send_{period}")
        self._period = period

    async def async_press(self) -> None:
        # Errors are left to propagate: pressed from the UI, "no delivery is
        # configured" is exactly what the user needs to see.
        await run(self.hass, self._entry, self._period)
