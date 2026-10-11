"""Calendar platform: one calendar per property with its reservations."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StaysConfigEntry
from .coordinator import Reservation, StaysCoordinator
from .entity import StaysEntity


def reservation_description(res: Reservation) -> str:
    lines = [f"Canal: {res.channel}"]
    if res.guest_count:
        lines.append(f"Hóspedes: {res.guest_count}")
    if res.guest_phone:
        lines.append(f"Telefone: {res.guest_phone}")
    if res.guest_email:
        lines.append(f"E-mail: {res.guest_email}")
    if res.confirmation_code:
        lines.append(f"Confirmação: {res.confirmation_code}")
    lines.append(f"Reserva Stays: {res.code}")
    return "\n".join(lines)


def to_event(res: Reservation) -> CalendarEvent:
    return CalendarEvent(
        start=res.start,
        end=res.end,
        summary=res.guest_name or f"Reserva {res.code}",
        description=reservation_description(res),
        uid=res.mongo_id or res.code,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        StaysCalendar(coordinator, apt_id) for apt_id in coordinator.property_ids
    )


class StaysCalendar(StaysEntity, CalendarEntity):
    """Reservations of one property."""

    _attr_name = None  # use the device (property) name
    # The description carries guest phone/e-mail: keep it out of the recorder.
    _unrecorded_attributes = frozenset({"description"})

    def __init__(self, coordinator: StaysCoordinator, apartment_id: str) -> None:
        super().__init__(coordinator, apartment_id)
        self._attr_unique_id = f"{apartment_id}_calendar"

    @property
    def event(self) -> CalendarEvent | None:
        """Current stay, otherwise the next one."""
        state = self.stay
        res = state.current or state.next
        return to_event(res) if res else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            to_event(r)
            for r in self.coordinator.data.get(self._apartment_id, [])
            if r.start < end_date and r.end > start_date
        ]
