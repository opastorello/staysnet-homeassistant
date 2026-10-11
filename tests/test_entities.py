"""The sensors / binary sensors / calendars / button added on top of the basics."""

from __future__ import annotations

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_init import _setup


def state(hass: HomeAssistant, entity_id: str):
    st = hass.states.get(entity_id)
    assert st is not None, entity_id
    return st.state


async def test_mid_stay_sensors(
    hass: HomeAssistant, mock_api, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    # 2026-10-10 08:00 local: Maria (8th -> 12th) is in the house, Joao arrives 14th
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)

    p = "casa_cynthia"
    assert state(hass, f"sensor.{p}_next_guest") == "João Souza"
    assert state(hass, f"sensor.{p}_last_guest") == "unknown"  # nothing finished yet
    assert state(hass, f"sensor.{p}_reservation") == "GU01J"
    assert state(hass, f"sensor.{p}_channel") == "Airbnb"
    assert state(hass, f"sensor.{p}_guests") == "3"
    assert state(hass, f"sensor.{p}_nights") == "4"
    assert state(hass, f"sensor.{p}_nights_remaining") == "2"
    assert state(hass, f"sensor.{p}_current_night") == "3"
    assert state(hass, f"sensor.{p}_days_to_check_in") == "4"
    assert state(hass, f"sensor.{p}_vacant_days") == "unknown"  # occupied
    assert state(hass, f"sensor.{p}_arrivals_in_7_days") == "1"
    assert state(hass, f"sensor.{p}_upcoming_reservations") == "1"
    # October: nights 8,9,10,11 + 14,15,16 = 7 of 31
    assert float(state(hass, f"sensor.{p}_occupancy_this_month")) == pytest.approx(22.6, abs=0.1)
    assert 0 < float(state(hass, f"sensor.{p}_stay_progress")) < 100

    assert state(hass, f"binary_sensor.{p}_check_in_today") == "off"
    assert state(hass, f"binary_sensor.{p}_check_out_today") == "off"
    assert state(hass, f"binary_sensor.{p}_same_day_turnover") == "off"


async def test_vacant_between_stays(
    hass: HomeAssistant, mock_api, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-13 19:00:00+00:00")  # 12:00 local on the 13th
    await _setup(hass, config_entry)
    p = "casa_cynthia"
    assert state(hass, f"sensor.{p}_last_guest") == "Maria Silva"
    last_out = dt_util.parse_datetime(state(hass, f"sensor.{p}_last_check_out"))
    assert dt_util.as_local(last_out).strftime("%d/%m %H:%M") == "12/10 10:00"
    assert state(hass, f"sensor.{p}_vacant_days") == "1"
    assert state(hass, f"sensor.{p}_nights_remaining") == "unknown"
    assert state(hass, f"sensor.{p}_current_night") == "unknown"
    assert state(hass, f"sensor.{p}_stay_progress") == "unknown"


async def test_turnover_day(
    hass: HomeAssistant, mock_api, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Checkout and check-in on the same day."""
    from .conftest import calendar_entries

    entries = calendar_entries()
    entries[1] = {**entries[1], "_dtcheckIn": "2026-10-12"}  # Joao arrives when Maria leaves
    mock_api.async_get_reservations.return_value = entries
    freezer.move_to("2026-10-12 16:00:00+00:00")  # 09:00 local on the 12th
    await _setup(hass, config_entry)
    p = "casa_cynthia"
    assert state(hass, f"binary_sensor.{p}_check_in_today") == "on"
    assert state(hass, f"binary_sensor.{p}_check_out_today") == "on"
    assert state(hass, f"binary_sensor.{p}_same_day_turnover") == "on"
    assert state(hass, f"sensor.{p}_stay_status") == "checkout_today"


async def test_blocks_calendar(
    hass: HomeAssistant, mock_api, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    assert state(hass, "calendar.casa_cynthia_blocks") == "off"  # block is on the 25th
    entity = hass.data["calendar"].get_entity("calendar.casa_cynthia_blocks")
    events = await entity.async_get_events(
        hass,
        dt_util.parse_datetime("2026-10-01T00:00:00+00:00"),
        dt_util.parse_datetime("2026-11-01T00:00:00+00:00"),
    )
    assert [e.summary for e in events] == ["Bloqueio"]
    # the stays calendar does NOT include the block
    stays = hass.data["calendar"].get_entity("calendar.casa_cynthia")
    names = [
        e.summary
        for e in await stays.async_get_events(
            hass,
            dt_util.parse_datetime("2026-10-01T00:00:00+00:00"),
            dt_util.parse_datetime("2026-11-01T00:00:00+00:00"),
        )
    ]
    assert "Bloqueio" not in names


async def test_account_entities_and_refresh_button(
    hass: HomeAssistant, mock_api, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    assert state(hass, "sensor.stays_net_ghbrasil_last_update").startswith("2026-10-10T15:00")
    calls = mock_api.async_get_reservations.call_count
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.stays_net_ghbrasil_refresh_now"}, blocking=True
    )
    await hass.async_block_till_done()
    assert mock_api.async_get_reservations.call_count == calls + 1


async def test_get_stay_action(
    hass: HomeAssistant, mock_api, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    ev = "event.casa_cynthia_stay_events"

    async def get(which: str, entity: str = ev):
        return await hass.services.async_call(
            "staysnet",
            "get_stay",
            {"entity_id": entity, "which": which},
            blocking=True,
            return_response=True,
        )

    cur = await get("current")
    assert cur["found"] is True
    assert cur["guest_name"] == "Maria Silva"
    assert cur["door_code"] == "4321" and cur["guest_phone"] == "+5511987654321"
    nxt = await get("next")
    assert nxt["guest_name"] == "João Souza" and nxt["door_code"] == "3210"
    assert (await get("auto"))["guest_name"] == "Maria Silva"
    assert (await get("last")) == {"found": False, "which": "last"}
    # works with any entity of the property
    assert (await get("current", "sensor.casa_cynthia_guest"))["found"] is True

    for bad in ("button.stays_net_ghbrasil_refresh_now", "sensor.not_staysnet", "light.nope"):
        with pytest.raises(ServiceValidationError):
            await get("current", bad)
