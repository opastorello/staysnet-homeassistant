"""Sensors: status, guests, stay timeline and occupancy counters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StaysConfigEntry
from .const import (
    STATUS_CHECKIN_TODAY,
    STATUS_CHECKOUT_TODAY,
    STATUS_OCCUPIED,
    STATUS_VACANT,
)
from .coordinator import PropertyState, Reservation, StaysCoordinator
from .entity import StaysAccountEntity, StaysEntity


def _guest_attrs(res: Reservation | None, which: str) -> dict[str, Any]:
    return res.as_details(which) if res else {}


def _focus(state: PropertyState) -> Reservation | None:
    """The stay in progress, otherwise the next one."""
    return state.current or state.next


def _name(res: Reservation | None) -> str | None:
    return (res.guest_name or res.code) if res else None


def _nights_remaining(state: PropertyState, today: date) -> int | None:
    cur = state.current
    return max(0, (cur.end.date() - today).days) if cur else None


def _current_night(state: PropertyState, today: date) -> int | None:
    cur = state.current
    if not cur:
        return None
    return min(cur.nights, (today - cur.start.date()).days + 1)


def _stay_progress(state: PropertyState, now: datetime) -> float | None:
    cur = state.current
    if not cur:
        return None
    total = (cur.end - cur.start).total_seconds()
    return round(min(100.0, (now - cur.start).total_seconds() * 100 / total), 1)


def _days_to_checkin(state: PropertyState, today: date) -> int | None:
    return (state.next.start.date() - today).days if state.next else None


def _vacant_days(state: PropertyState, today: date) -> int | None:
    """Days the property stays empty until the next arrival (None while occupied)."""
    if state.current:
        return None
    return _days_to_checkin(state, today)


@dataclass(frozen=True, kw_only=True)
class StaysSensorDescription(SensorEntityDescription):
    """Sensor computed from the property state."""

    value_fn: Callable[[PropertyState, datetime], Any]
    attrs_fn: Callable[[PropertyState], dict[str, Any]] | None = None


SENSORS: tuple[StaysSensorDescription, ...] = (
    StaysSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[
            STATUS_VACANT,
            STATUS_CHECKIN_TODAY,
            STATUS_OCCUPIED,
            STATUS_CHECKOUT_TODAY,
        ],
        value_fn=lambda s, now: s.status,
    ),
    StaysSensorDescription(
        key="guest",
        translation_key="guest",
        icon="mdi:account-key",
        value_fn=lambda s, now: _name(_focus(s)),
        attrs_fn=lambda s: _guest_attrs(
            _focus(s), "current" if s.current else "next"
        ),
    ),
    StaysSensorDescription(
        key="next_guest",
        translation_key="next_guest",
        icon="mdi:account-arrow-right",
        value_fn=lambda s, now: _name(s.next),
        attrs_fn=lambda s: _guest_attrs(s.next, "next"),
    ),
    StaysSensorDescription(
        key="last_guest",
        translation_key="last_guest",
        icon="mdi:account-arrow-left",
        value_fn=lambda s, now: _name(s.last),
        attrs_fn=lambda s: _guest_attrs(s.last, "last"),
    ),
    StaysSensorDescription(
        key="reservation",
        translation_key="reservation",
        icon="mdi:identifier",
        value_fn=lambda s, now: (_focus(s).code if _focus(s) else None),
    ),
    StaysSensorDescription(
        key="channel",
        translation_key="channel",
        icon="mdi:source-branch",
        value_fn=lambda s, now: (_focus(s).channel if _focus(s) else None),
    ),
    StaysSensorDescription(
        key="guests",
        translation_key="guests",
        icon="mdi:account-group",
        value_fn=lambda s, now: (_focus(s).guest_count if _focus(s) else None),
    ),
    StaysSensorDescription(
        key="nights",
        translation_key="nights",
        icon="mdi:weather-night",
        value_fn=lambda s, now: (_focus(s).nights if _focus(s) else None),
    ),
    StaysSensorDescription(
        key="next_checkin",
        translation_key="next_checkin",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s, now: s.next.start if s.next else None,
    ),
    StaysSensorDescription(
        key="next_checkout",
        translation_key="next_checkout",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s, now: (_focus(s).end if _focus(s) else None),
    ),
    StaysSensorDescription(
        key="last_checkout",
        translation_key="last_checkout",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s, now: s.last.end if s.last else None,
    ),
    StaysSensorDescription(
        key="days_to_checkin",
        translation_key="days_to_checkin",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda s, now: _days_to_checkin(s, now.date()),
    ),
    StaysSensorDescription(
        key="vacant_days",
        translation_key="vacant_days",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda s, now: _vacant_days(s, now.date()),
    ),
    StaysSensorDescription(
        key="nights_remaining",
        translation_key="nights_remaining",
        icon="mdi:weather-night",
        value_fn=lambda s, now: _nights_remaining(s, now.date()),
    ),
    StaysSensorDescription(
        key="current_night",
        translation_key="current_night",
        icon="mdi:numeric",
        value_fn=lambda s, now: _current_night(s, now.date()),
    ),
    StaysSensorDescription(
        key="stay_progress",
        translation_key="stay_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s, now: _stay_progress(s, now),
    ),
    StaysSensorDescription(
        key="upcoming_7d",
        translation_key="upcoming_7d",
        icon="mdi:calendar-clock",
        value_fn=lambda s, now: sum(
            1 for r in s.upcoming if (r.start.date() - now.date()).days <= 7
        ),
    ),
    StaysSensorDescription(
        key="upcoming_total",
        translation_key="upcoming_total",
        icon="mdi:calendar-multiple",
        value_fn=lambda s, now: len(s.upcoming),
    ),
    StaysSensorDescription(
        key="month_occupancy",
        translation_key="month_occupancy",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-donut",
        value_fn=lambda s, now: s.month_occupancy,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaysConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        StaysSensor(coordinator, apt_id, description)
        for apt_id in coordinator.property_ids
        for description in SENSORS
    ]
    entities.append(StaysLastUpdate(coordinator, entry))
    async_add_entities(entities)


class StaysSensor(StaysEntity, SensorEntity):
    """A sensor computed from the property's reservations."""

    entity_description: StaysSensorDescription
    # Personal data / door code must not end up in the recorder history.
    _unrecorded_attributes = frozenset(
        {"guest_email", "guest_phone", "door_code", "confirmation_code"}
    )

    def __init__(
        self,
        coordinator: StaysCoordinator,
        apartment_id: str,
        description: StaysSensorDescription,
    ) -> None:
        super().__init__(coordinator, apartment_id)
        self.entity_description = description
        self._attr_unique_id = f"{apartment_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        from homeassistant.util import dt as dt_util

        return self.entity_description.value_fn(self.stay, dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.stay) if fn else None


class StaysLastUpdate(StaysAccountEntity, SensorEntity):
    """When the data was last fetched successfully from Stays."""

    _attr_translation_key = "last_update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: StaysCoordinator, entry: StaysConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_update"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_success_at
