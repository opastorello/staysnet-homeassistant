"""Event entity: check-in, check-out and reservation changes of a property."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StaysConfigEntry
from .const import STAY_EVENT_TYPES, event_signal
from .coordinator import StaysCoordinator
from .entity import StaysEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        StaysEventEntity(coordinator, apt_id, entry.entry_id)
        for apt_id in coordinator.property_ids
    )


class StaysEventEntity(StaysEntity, EventEntity):
    """Fires check_in / check_out / reservation_created|changed|cancelled."""

    _attr_translation_key = "stay_events"
    _attr_event_types = STAY_EVENT_TYPES

    def __init__(
        self, coordinator: StaysCoordinator, apartment_id: str, entry_id: str
    ) -> None:
        super().__init__(coordinator, apartment_id)
        self._entry_id = entry_id
        self._attr_unique_id = f"{apartment_id}_stay_events"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                event_signal(self._entry_id, self._apartment_id),
                self._handle_event,
            )
        )

    @callback
    def _handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
