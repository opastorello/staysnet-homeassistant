"""Button: refresh now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StaysConfigEntry
from .coordinator import StaysCoordinator
from .entity import StaysAccountEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([StaysRefreshButton(entry.runtime_data, entry)])


class StaysRefreshButton(StaysAccountEntity, ButtonEntity):
    """Fetch reservations from Stays right now."""

    _attr_translation_key = "refresh"

    def __init__(self, coordinator: StaysCoordinator, entry: StaysConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
