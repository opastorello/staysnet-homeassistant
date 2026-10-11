"""Setup, entities and stay logic."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .conftest import APT_ID


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_occupied_stay(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    """Mid-stay: occupied, guest details exposed, cancelled/blocked ignored."""
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)

    assert hass.states.get("binary_sensor.casa_cynthia_occupied").state == "on"
    assert hass.states.get("sensor.casa_cynthia_stay_status").state == "occupied"

    guest = hass.states.get("sensor.casa_cynthia_guest")
    assert guest.state == "Maria Silva"
    attrs = guest.attributes
    assert attrs["which"] == "current"
    assert attrs["channel"] == "Airbnb"
    assert attrs["guest_phone"] == "+5511987654321"
    assert attrs["door_code"] == "4321"
    assert attrs["door_code_source"] == "phone"
    assert attrs["guest_count"] == 3
    assert attrs["confirmation_code"] == "HMABC123"
    # PII that must NOT be exposed
    assert "000.000.000-00" not in str(attrs)

    # checkout of the current stay uses the Stays property hour (10:00)
    checkout = hass.states.get("sensor.casa_cynthia_next_check_out").state
    assert checkout.startswith("2026-10-12T")
    # next check-in is the 'altered' reservation (14th)
    assert hass.states.get("sensor.casa_cynthia_next_check_in").state.startswith(
        "2026-10-14T"
    )

    calendar = hass.states.get("calendar.casa_cynthia")
    assert calendar.state == "on"
    assert calendar.attributes["message"] == "Maria Silva"


async def test_vacant_and_checkin_today(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-13 12:00:00+00:00")
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.casa_cynthia_stay_status").state == "vacant"
    assert hass.states.get("binary_sensor.casa_cynthia_occupied").state == "off"
    guest = hass.states.get("sensor.casa_cynthia_guest")
    assert guest.state == "João Souza"  # fName/lName fallback
    assert guest.attributes["which"] == "next"
    # no phone (Booking-style): falls back to the confirmation code
    assert guest.attributes["door_code"] == "3210"
    assert guest.attributes["door_code_source"] == "confirmation_code"


async def test_status_changes_without_new_poll(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    """The clock tick re-evaluates states from cached data (no API call)."""
    freezer.move_to("2026-10-14 21:55:00+00:00")  # 14:55 local, check-in is 15:00
    await _setup(hass, config_entry)
    calls = mock_api.async_get_reservations.call_count
    assert hass.states.get("binary_sensor.casa_cynthia_occupied").state == "off"

    freezer.move_to("2026-10-14 22:01:00+00:00")  # 6 min later (< 15 min poll)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.casa_cynthia_occupied").state == "on"
    assert mock_api.async_get_reservations.call_count == calls


async def test_calendar_events_range(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    entity = hass.data["calendar"].get_entity("calendar.casa_cynthia")
    from homeassistant.util import dt as dt_util

    events = await entity.async_get_events(
        hass,
        dt_util.parse_datetime("2026-10-01T00:00:00+00:00"),
        dt_util.parse_datetime("2026-11-01T00:00:00+00:00"),
    )
    # 2 stays only: cancelled and blocked are excluded
    assert [e.summary for e in events] == ["Maria Silva", "João Souza"]


async def test_fallback_hours_when_stays_has_none(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    mock_api.async_get_apartment_times.return_value = {}
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    # default fallback checkout 11:00 (local time zone)
    from homeassistant.util import dt as dt_util

    checkout = dt_util.parse_datetime(
        hass.states.get("sensor.casa_cynthia_next_check_out").state
    )
    assert dt_util.as_local(checkout).strftime("%H:%M") == "11:00"


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_api, config_entry
) -> None:
    from custom_components.staysnet.api import StaysAuthError

    mock_api.async_get_apartment_times.side_effect = StaysAuthError("expired")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(config_entry.async_get_active_flows(hass, {"reauth"}))


async def test_unload(hass: HomeAssistant, mock_api, config_entry) -> None:
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert APT_ID  # keep import used


async def test_apartment_times_cached_between_polls(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert mock_api.async_get_apartment_times.call_count == 1
    # ...but refreshed after the TTL
    freezer.move_to("2026-10-10 22:00:00+00:00")
    await coordinator.async_refresh()
    assert mock_api.async_get_apartment_times.call_count == 2


async def test_finished_stay_details_not_refetched_every_poll(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    from .conftest import calendar_entries

    past = {
        "_id": "r0",
        "id": "GU00J",
        "type": "booked",
        "_dtcheckIn": "2026-09-25",
        "_dtcheckOut": "2026-09-28",
    }
    mock_api.async_get_reservations.return_value = [past, *calendar_entries()]
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    coordinator = config_entry.runtime_data

    def calls(rid: str) -> int:
        return sum(
            1
            for c in mock_api.async_get_reservation_details.call_args_list
            if c.args[0] == rid
        )

    assert calls("r0") == 1 and calls("r1") == 1
    freezer.tick(timedelta(minutes=16))
    await coordinator.async_refresh()
    assert calls("r0") == 1  # finished stay: cached for 24h
    assert calls("r1") == 2  # ongoing stay: refreshed every 15 min


async def test_removed_property_device_is_deleted(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    from homeassistant.helpers import device_registry as dr

    from custom_components.staysnet.const import (
        CONF_PROPERTIES,
        CONF_PROPERTY_CODES,
        CONF_PROPERTY_NAMES,
        DOMAIN,
    )

    other = "b" * 24
    freezer.move_to("2026-10-10 15:00:00+00:00")
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_PROPERTIES: [APT_ID, other],
            CONF_PROPERTY_NAMES: {APT_ID: "Casa Cynthia", other: "Apto Centro"},
            CONF_PROPERTY_CODES: {APT_ID: "CE03J", other: "XX01J"},
        },
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    assert registry.async_get_device_by_identifier((DOMAIN, other), config_entry.entry_id) is not None

    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_PROPERTIES: [APT_ID],
            CONF_PROPERTY_NAMES: {APT_ID: "Casa Cynthia"},
            CONF_PROPERTY_CODES: {APT_ID: "CE03J"},
        },
    )
    await hass.async_block_till_done()
    assert registry.async_get_device_by_identifier((DOMAIN, other), config_entry.entry_id) is None
    assert registry.async_get_device_by_identifier((DOMAIN, APT_ID), config_entry.entry_id) is not None


async def test_pii_not_recorded_and_diagnostics_redacted(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    from custom_components.staysnet.calendar import StaysCalendar
    from custom_components.staysnet.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.staysnet.sensor import StaysSensor

    for attr in ("guest_email", "guest_phone", "door_code"):
        assert attr in StaysSensor._unrecorded_attributes
    assert "description" in StaysCalendar._unrecorded_attributes

    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    text = str(diag)
    assert "secret" not in text and "user@example.com" not in text
    assert "Maria" not in text and "5511987654321" not in text
    assert "maria@example.com" not in text
    first = diag["properties"][APT_ID]["reservations"][0]
    assert first["has_phone"] is True
    assert first["door_code_source"] == "phone"
    # raw Stays strings are exposed to see where check-in/out hours come from
    assert first["stays_raw_check_in"].startswith("2026-10-08")
    assert diag["properties"][APT_ID]["blocks"] == 1


async def test_times_come_from_the_reservation_then_the_property(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    """Stays puts the wall-clock time in _dtcheckIn/_dtcheckOut ("Z" is cosmetic)."""
    entries = [
        {  # custom times negotiated in Stays: early check-in, late check-out
            "_id": "r1",
            "id": "GU01J",
            "type": "booked",
            "_dtcheckIn": "2026-10-08T12:30:00.000Z",
            "_dtcheckOut": "2026-10-12T16:45:00.000Z",
        },
        {  # date only / midnight: no time info -> property default (15h / 10h in the mock)
            "_id": "r2",
            "id": "GU02J",
            "type": "booked",
            "_dtcheckIn": "2026-10-14T00:00:00.000Z",
            "_dtcheckOut": "2026-10-17",
        },
    ]
    mock_api.async_get_reservations.return_value = entries
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)

    attrs = hass.states.get("sensor.casa_cynthia_guest").attributes
    assert attrs["check_in"] == "2026-10-08T12:30:00-07:00"
    assert attrs["check_out"] == "2026-10-12T16:45:00-07:00"
    nxt = hass.states.get("sensor.casa_cynthia_next_guest").attributes
    assert nxt["check_in"] == "2026-10-14T15:00:00-07:00"
    assert nxt["check_out"] == "2026-10-17T10:00:00-07:00"


def test_time_from_raw() -> None:
    from datetime import time

    from custom_components.staysnet.coordinator import _time_from_raw

    assert _time_from_raw("2026-10-10T14:00:00.000Z") == time(14, 0)
    assert _time_from_raw("2026-10-10T09:15:00+00:00") == time(9, 15)
    assert _time_from_raw("2026-10-10T00:00:00.000Z") is None
    assert _time_from_raw("2026-10-10") is None
    assert _time_from_raw("") is None and _time_from_raw(None) is None
    assert _time_from_raw("2026-10-10T25:99:00") is None
