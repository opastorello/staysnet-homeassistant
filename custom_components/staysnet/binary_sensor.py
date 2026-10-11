"""Binary sensors: occupied, check-in/out today, same-day turnover."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StaysConfigEntry
from .coordinator import PropertyState, StaysCoordinator
from .entity import StaysEntity


@dataclass(frozen=True, kw_only=True)
class StaysBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[PropertyState], bool]


BINARY_SENSORS: tuple[StaysBinaryDescription, ...] = (
    StaysBinaryDescription(
        key="occupied",
        translation_key="occupied",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        is_on_fn=lambda s: s.current is not None,
    ),
    StaysBinaryDescription(
        key="checkin_today",
        translation_key="checkin_today",
        icon="mdi:login",
        is_on_fn=lambda s: s.checkin_today,
    ),
    StaysBinaryDescription(
        key="checkout_today",
        translation_key="checkout_today",
        icon="mdi:logout",
        is_on_fn=lambda s: s.checkout_today,
    ),
    StaysBinaryDescription(
        key="turnover",
        translation_key="turnover",
        icon="mdi:swap-horizontal",
        is_on_fn=lambda s: s.checkin_today and s.checkout_today,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        StaysBinarySensor(coordinator, apt_id, description)
        for apt_id in coordinator.property_ids
        for description in BINARY_SENSORS
    )


class StaysBinarySensor(StaysEntity, BinarySensorEntity):
    entity_description: StaysBinaryDescription

    def __init__(
        self,
        coordinator: StaysCoordinator,
        apartment_id: str,
        description: StaysBinaryDescription,
    ) -> None:
        super().__init__(coordinator, apartment_id)
        self.entity_description = description
        self._attr_unique_id = f"{apartment_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.stay)
