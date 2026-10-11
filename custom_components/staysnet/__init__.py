"""Stays.net integration: reservations and guests for smart-hosting automations."""

from __future__ import annotations

from datetime import datetime, timedelta

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .api import StaysApi
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import StaysCoordinator

SERVICE_GET_STAY = "get_stay"
GET_STAY_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("which", default="auto"): vol.In(
            ["auto", "current", "next", "last"]
        ),
    }
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.SENSOR,
]

type StaysConfigEntry = ConfigEntry[StaysCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the get_stay action (used by the blueprints)."""

    async def get_stay(call: ServiceCall) -> ServiceResponse:
        """Return guest/stay details of a property (not stored in the recorder)."""
        entity_id: str = call.data["entity_id"]
        ent = er.async_get(hass).async_get(entity_id)
        entry = (
            hass.config_entries.async_get_entry(ent.config_entry_id)
            if ent and ent.config_entry_id
            else None
        )
        apt_id = (ent.unique_id or "").split("_", 1)[0] if ent else ""
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
            or apt_id not in entry.runtime_data.property_ids
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_a_property_entity",
                translation_placeholders={"entity_id": entity_id},
            )
        state = entry.runtime_data.state_for(apt_id)
        which: str = call.data["which"]
        res = {
            "auto": state.current or state.next,
            "current": state.current,
            "next": state.next,
            "last": state.last,
        }[which]
        if res is None:
            return {"found": False, "which": which}
        return {"found": True, **res.as_details(which)}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_STAY,
        get_stay,
        schema=GET_STAY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: StaysConfigEntry) -> bool:
    """Set up Stays.net from a config entry."""
    # Own session + DummyCookieJar: we send the Stays session cookie explicitly.
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
    api = StaysApi(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    coordinator = StaysCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.async_check_stay_transitions()  # silent baseline for check_in/out events

    # Properties removed in the options flow: drop their now-orphaned devices.
    registry = dr.async_get(hass)
    wanted = {(DOMAIN, apt_id) for apt_id in coordinator.property_ids}
    wanted.add((DOMAIN, entry.entry_id))  # the account device
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & wanted:
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )

    # Status/occupancy depend on the clock (check-in at 14:00, ...): re-evaluate
    # entity states every minute from cached data, without hitting the API.
    @callback
    def _tick(_now: datetime) -> None:
        coordinator.async_tick()

    entry.async_on_unload(
        async_track_time_interval(hass, _tick, timedelta(minutes=1))
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: StaysConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: StaysConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
