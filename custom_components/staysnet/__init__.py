"""Stays.net integration: reservations and guests for smart-hosting automations."""

from __future__ import annotations

from datetime import datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import StaysApi
from .const import CONF_PASSWORD, CONF_USERNAME
from .coordinator import StaysCoordinator

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR, Platform.BINARY_SENSOR]

type StaysConfigEntry = ConfigEntry[StaysCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: StaysConfigEntry) -> bool:
    """Set up Stays.net from a config entry."""
    # Own session + DummyCookieJar: we send the Stays session cookie explicitly.
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
    api = StaysApi(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    coordinator = StaysCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Status/occupancy depend on the clock (check-in at 14:00, ...): re-evaluate
    # entity states every minute from cached data, without hitting the API.
    @callback
    def _tick(_now: datetime) -> None:
        coordinator.async_update_listeners()

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
