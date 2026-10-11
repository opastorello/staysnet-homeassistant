"""The shipped blueprints must load, and the main ones must actually run."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from .conftest import calendar_entries
from .test_init import _setup

SRC = Path(__file__).parent.parent / "blueprints" / "automation" / "staysnet"
EV = "event.casa_cynthia_stay_events"
NEXT = "sensor.casa_cynthia_next_check_in"
OCC = "binary_sensor.casa_cynthia_occupied"


def act(name: str, **data: str) -> list[dict]:
    return [{"action": f"test.{name}", "data": data}]


INPUTS: dict[str, dict] = {
    "guest_arrival.yaml": {
        "stays_event": EV,
        "arrival_actions": act("arrived", guest="{{ stay.guest_name }}", code="{{ stay.door_code }}"),
    },
    "guest_departure.yaml": {
        "stays_event": EV,
        "departure_actions": act("left", guest="{{ stay.guest_name }}"),
    },
    "pre_arrival.yaml": {
        "next_checkin": NEXT,
        "lead_hours": 2,
        "pre_actions": act("prepare", guest="{{ stay.guest_name }}"),
    },
    "door_code.yaml": {
        "stays_event": EV,
        "next_checkin": NEXT,
        "lead_hours": 3,
        "set_actions": act("set_code", code="{{ stay.door_code }}"),
        "clear_actions": act("clear_code", guest="{{ stay.guest_name }}"),
    },
    "reservation_alert.yaml": {
        "stays_event": EV,
        "notify_created": True,
        "notify_changed": True,
        "notify_cancelled": True,
        "notify_actions": act("notify", title="{{ title }}", message="{{ message }}"),
    },
    "cleaning_alert.yaml": {
        "stays_event": EV,
        "cleaning_actions": act("clean", title="{{ title }}", message="{{ message }}", turnover="{{ turnover }}"),
    },
    "vacant_savings.yaml": {
        "occupied": OCC,
        "next_checkin": NEXT,
        "vacant_hours": 3,
        "arrival_buffer_hours": 6,
        "saving_actions": act("save"),
    },
}


@pytest.fixture(autouse=True)
def installed_blueprints(hass: HomeAssistant):
    """Copy the repo blueprints where Home Assistant looks for them."""
    dest = Path(hass.config.path("blueprints/automation/staysnet"))
    shutil.copytree(SRC, dest, dirs_exist_ok=True)
    yield
    shutil.rmtree(Path(hass.config.path("blueprints")), ignore_errors=True)


async def _use(hass: HomeAssistant, name: str) -> None:
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": {"use_blueprint": {"path": f"staysnet/{name}", "input": INPUTS[name]}}},
    )
    await hass.async_block_till_done()


def test_every_blueprint_is_covered() -> None:
    assert {p.name for p in SRC.glob("*.yaml")} == set(INPUTS)


@pytest.mark.parametrize("name", sorted(INPUTS))
async def test_blueprint_loads(
    hass: HomeAssistant, mock_api, config_entry, name: str, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    await _use(hass, name)
    automations = hass.states.async_entity_ids("automation")
    assert len(automations) == 1
    assert hass.states.get(automations[0]).state == "on"


async def test_guest_arrival_runs_with_stay_data(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-14 21:55:00+00:00")
    await _setup(hass, config_entry)
    calls = async_mock_service(hass, "test", "arrived")
    await _use(hass, "guest_arrival.yaml")

    freezer.move_to("2026-10-14 22:01:00+00:00")  # 15:01 local: João arrives
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data == {"guest": "João Souza", "code": 3210}  # HA renders digits as int


async def test_guest_departure_uses_the_finished_stay(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-17 16:55:00+00:00")
    await _setup(hass, config_entry)
    calls = async_mock_service(hass, "test", "left")
    await _use(hass, "guest_departure.yaml")
    freezer.move_to("2026-10-17 17:01:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data for c in calls] == [{"guest": "João Souza"}]


async def test_pre_arrival_fires_once_inside_the_lead_window(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-14 19:00:00+00:00")  # 12:00 local, check-in at 15:00 (3h)
    await _setup(hass, config_entry)
    calls = async_mock_service(hass, "test", "prepare")
    await _use(hass, "pre_arrival.yaml")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not calls  # 3h > 2h lead

    freezer.move_to("2026-10-14 20:30:00+00:00")  # 13:30 local: 1.5h left
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data for c in calls] == [{"guest": "João Souza"}]

    freezer.move_to("2026-10-14 20:45:00+00:00")  # still inside the window: no repeat
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_door_code_set_before_and_cleared_after(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-14 17:00:00+00:00")  # 10:00 local, check-in in 5h
    await _setup(hass, config_entry)
    set_calls = async_mock_service(hass, "test", "set_code")
    clear_calls = async_mock_service(hass, "test", "clear_code")
    await _use(hass, "door_code.yaml")

    freezer.move_to("2026-10-14 20:00:00+00:00")  # 13:00 local: inside the 3h lead
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data for c in set_calls] == [{"code": 3210}]
    assert not clear_calls

    freezer.move_to("2026-10-17 16:55:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.move_to("2026-10-17 17:01:00+00:00")  # check-out
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data for c in clear_calls] == [{"guest": "João Souza"}]


async def test_reservation_alert_message(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-10 15:00:00+00:00")
    await _setup(hass, config_entry)
    calls = async_mock_service(hass, "test", "notify")
    await _use(hass, "reservation_alert.yaml")

    new = {"_id": "r9", "id": "GU09J", "type": "booked", "_dtcheckIn": "2026-10-20", "_dtcheckOut": "2026-10-22"}
    mock_api.async_get_reservations.return_value = [*calendar_entries(), new]
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["title"] == "Stays · Casa Cynthia"
    assert calls[0].data["message"].startswith("Nova reserva: GU09J · 20/10 → 22/10")


async def test_cleaning_alert_flags_same_day_turnover(
    hass: HomeAssistant, mock_api, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    entries = calendar_entries()
    entries[1] = {**entries[1], "_dtcheckIn": "2026-10-12"}  # next guest arrives on checkout day
    mock_api.async_get_reservations.return_value = entries
    freezer.move_to("2026-10-12 16:55:00+00:00")  # 09:55 local
    await _setup(hass, config_entry)
    calls = async_mock_service(hass, "test", "clean")
    await _use(hass, "cleaning_alert.yaml")

    freezer.move_to("2026-10-12 17:01:00+00:00")  # 10:01: Maria leaves
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["turnover"] in (True, "True")
    assert "Virada hoje" in calls[0].data["title"]
    assert "João Souza" in calls[0].data["message"] and "15:00" in calls[0].data["message"]
