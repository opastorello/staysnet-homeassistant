"""Setup, entities and stay logic."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

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
    assert guest.attributes["door_code"] == ""


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
