"""Binary sensor: property occupied."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StaysConfigEntry
from .coordinator import StaysCoordinator
from .entity import StaysEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        StaysOccupied(coordinator, apt_id) for apt_id in coordinator.property_ids
    )


class StaysOccupied(StaysEntity, BinarySensorEntity):
    """On while a guest is in the property (between check-in and check-out)."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_translation_key = "occupied"

    def __init__(self, coordinator: StaysCoordinator, apartment_id: str) -> None:
        super().__init__(coordinator, apartment_id)
        self._attr_unique_id = f"{apartment_id}_occupied"

    @property
    def is_on(self) -> bool:
        return self.stay.current is not None
