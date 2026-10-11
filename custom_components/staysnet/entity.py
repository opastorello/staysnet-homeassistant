"""Base entity."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PropertyState, StaysCoordinator


class StaysEntity(CoordinatorEntity[StaysCoordinator]):
    """Entity bound to one property (Stays apartment)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: StaysCoordinator, apartment_id: str) -> None:
        super().__init__(coordinator)
        self._apartment_id = apartment_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, apartment_id)},
            name=coordinator.property_name(apartment_id),
            manufacturer="Stays.net",
            model="Property",
        )

    @property
    def stay(self) -> PropertyState:
        return self.coordinator.state_for(self._apartment_id)


class StaysAccountEntity(CoordinatorEntity[StaysCoordinator]):
    """Entity bound to the Stays account (not a single property)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: StaysCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Stays.net",
            model="Account",
        )
