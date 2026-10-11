"""Base entity."""

from __future__ import annotations

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
