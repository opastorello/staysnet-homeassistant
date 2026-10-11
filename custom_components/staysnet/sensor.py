"""Sensors: status, guest, next check-in / check-out."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StaysConfigEntry
from .const import (
    STATUS_CHECKIN_TODAY,
    STATUS_CHECKOUT_TODAY,
    STATUS_OCCUPIED,
    STATUS_VACANT,
)
from .coordinator import StaysCoordinator
from .entity import StaysEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[StaysEntity] = []
    for apt_id in coordinator.property_ids:
        entities += [
            StaysStatus(coordinator, apt_id),
            StaysGuest(coordinator, apt_id),
            StaysNextCheckin(coordinator, apt_id),
            StaysNextCheckout(coordinator, apt_id),
        ]
    async_add_entities(entities)


class StaysStatus(StaysEntity, SensorEntity):
    """vacant / checkin_today / occupied / checkout_today."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        STATUS_VACANT,
        STATUS_CHECKIN_TODAY,
        STATUS_OCCUPIED,
        STATUS_CHECKOUT_TODAY,
    ]

    def __init__(self, coordinator: StaysCoordinator, apartment_id: str) -> None:
        super().__init__(coordinator, apartment_id)
        self._attr_unique_id = f"{apartment_id}_status"

    @property
    def native_value(self) -> str:
        return self.stay.status


class StaysGuest(StaysEntity, SensorEntity):
    """Name of the guest staying now, or arriving next. Details in attributes."""

    _attr_translation_key = "guest"
    _attr_icon = "mdi:account-key"

    def __init__(self, coordinator: StaysCoordinator, apartment_id: str) -> None:
        super().__init__(coordinator, apartment_id)
        self._attr_unique_id = f"{apartment_id}_guest"

    @property
    def native_value(self) -> str | None:
        state = self.stay
        res = state.current or state.next
        return (res.guest_name or res.code) if res else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.stay
        res = state.current or state.next
        if not res:
            return {}
        return {
            "which": "current" if state.current else "next",
            "reservation_id": res.code,
            "confirmation_code": res.confirmation_code,
            "channel": res.channel,
            "guest_name": res.guest_name,
            "guest_email": res.guest_email,
            "guest_phone": res.guest_phone,
            "guest_count": res.guest_count,
            "door_code": res.door_code,
            "check_in": res.start.isoformat(),
            "check_out": res.end.isoformat(),
        }


class StaysNextCheckin(StaysEntity, SensorEntity):
    """Timestamp of the next arrival (usable as a time trigger)."""

    _attr_translation_key = "next_checkin"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: StaysCoordinator, apartment_id: str) -> None:
        super().__init__(coordinator, apartment_id)
        self._attr_unique_id = f"{apartment_id}_next_checkin"

    @property
    def native_value(self) -> datetime | None:
        nxt = self.stay.next
        return nxt.start if nxt else None


class StaysNextCheckout(StaysEntity, SensorEntity):
    """Timestamp of the checkout of the current (or upcoming) stay."""

    _attr_translation_key = "next_checkout"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: StaysCoordinator, apartment_id: str) -> None:
        super().__init__(coordinator, apartment_id)
        self._attr_unique_id = f"{apartment_id}_next_checkout"

    @property
    def native_value(self) -> datetime | None:
        state = self.stay
        res = state.current or state.next
        return res.end if res else None
