"""Stay events: check_in/out and reservation created/changed/cancelled."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from .conftest import calendar_entries
from .test_init import _setup

PII = {"guest_phone", "guest_email", "door_code", "confirmation_code"}


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_check_in_event_and_event_entity(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-14 21:55:00+00:00")  # 14:55 local, check-in 15:00
    ins = async_capture_events(hass, "staysnet_check_in")
    outs = async_capture_events(hass, "staysnet_check_out")
    await _setup(hass, config_entry)
    assert not ins and not outs  # startup is only a baseline

    freezer.move_to("2026-10-14 22:01:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(ins) == 1 and not outs
    data = ins[0].data
    assert data["reservation_id"] == "GU02J" and data["guest_name"] == "João Souza"
    assert data["property"] == "Casa Cynthia"
    assert not PII & set(data)  # recorded events carry no personal data

    ev = hass.states.get("event.casa_cynthia_stay_events")
    assert ev.attributes["event_type"] == "check_in"
    assert ev.attributes["guest_name"] == "João Souza"


async def test_check_out_event(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-17 16:55:00+00:00")  # 09:55 local, check-out 10:00
    outs = async_capture_events(hass, "staysnet_check_out")
    await _setup(hass, config_entry)
    assert not outs

    freezer.move_to("2026-10-17 17:01:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [e.data["reservation_id"] for e in outs] == ["GU02J"]
    assert hass.states.get("event.casa_cynthia_stay_events").attributes["event_type"] == "check_out"


async def test_reservation_created_and_window_rollover(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    created = async_capture_events(hass, "staysnet_reservation_created")
    await _setup(hass, config_entry)

    entries = calendar_entries()
    new = {"_id": "r9", "id": "GU09J", "type": "booked", "_dtcheckIn": "2026-10-20", "_dtcheckOut": "2026-10-22"}
    mock_api.async_get_reservations.return_value = [*entries, new]
    await _poll(hass, config_entry)
    assert [e.data["reservation_id"] for e in created] == ["GU09J"]
    assert not PII & set(created[0].data)

    # a reservation that merely rolled into the 90-day window already existed
    far = {"_id": "r10", "id": "GU10J", "type": "booked", "_dtcheckIn": "2027-02-01", "_dtcheckOut": "2027-02-03"}
    mock_api.async_get_reservations.return_value = [*entries, new, far]
    await _poll(hass, config_entry)
    assert len(created) == 1


async def test_reservation_changed(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    changed = async_capture_events(hass, "staysnet_reservation_changed")
    await _setup(hass, config_entry)

    entries = calendar_entries()
    entries[1] = {**entries[1], "_dtcheckIn": "2026-10-15"}  # arrival moved a day later
    mock_api.async_get_reservations.return_value = entries
    await _poll(hass, config_entry)

    assert len(changed) == 1
    change = changed[0].data["changes"]["check_in"]
    assert change["old"].startswith("2026-10-14") and change["new"].startswith("2026-10-15")
    assert "check_out" not in changed[0].data["changes"]

    await _poll(hass, config_entry)  # nothing new
    assert len(changed) == 1


async def test_cancellation_is_debounced(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    cancelled = async_capture_events(hass, "staysnet_reservation_cancelled")
    created = async_capture_events(hass, "staysnet_reservation_created")
    await _setup(hass, config_entry)
    entries = calendar_entries()

    # missing once, then back (calendar quirk): no cancel and no "created"
    mock_api.async_get_reservations.return_value = [e for e in entries if e["_id"] != "r2"]
    await _poll(hass, config_entry)
    assert not cancelled
    mock_api.async_get_reservations.return_value = entries
    await _poll(hass, config_entry)
    assert not cancelled and not created

    # really gone: cancelled once, after the second consecutive miss
    mock_api.async_get_reservations.return_value = [e for e in entries if e["_id"] != "r2"]
    await _poll(hass, config_entry)
    assert not cancelled
    await _poll(hass, config_entry)
    assert [e.data["reservation_id"] for e in cancelled] == ["GU02J"]
    await _poll(hass, config_entry)
    assert len(cancelled) == 1
