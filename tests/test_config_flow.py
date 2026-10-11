"""Config, reauth and options flows."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.staysnet.api import StaysApiError, StaysAuthError
from custom_components.staysnet.const import (
    CONF_PASSWORD,
    CONF_PROPERTIES,
    CONF_PROPERTY_CODES,
    CONF_PROPERTY_NAMES,
    CONF_USERNAME,
    DOMAIN,
)

from .conftest import APT_CODE, APT_ID

USER = {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"}


async def test_full_flow(hass: HomeAssistant, mock_api) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["step_id"] == "properties"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROPERTIES: [APT_ID]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Stays.net (ghbrasil)"
    assert result["data"][CONF_USERNAME] == "user@example.com"
    assert result["options"][CONF_PROPERTIES] == [APT_ID]
    assert result["options"][CONF_PROPERTY_NAMES] == {APT_ID: "Casa Cynthia"}
    assert result["options"][CONF_PROPERTY_CODES] == {APT_ID: APT_CODE}


async def test_invalid_auth_then_recover(hass: HomeAssistant, mock_api) -> None:
    mock_api.async_login.side_effect = StaysAuthError("bad")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["errors"] == {"base": "invalid_auth"}

    mock_api.async_login.side_effect = StaysApiError("down")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["errors"] == {"base": "cannot_connect"}

    mock_api.async_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["step_id"] == "properties"


async def test_no_properties_selected(hass: HomeAssistant, mock_api) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROPERTIES: []}
    )
    assert result["errors"] == {"base": "no_selection"}


async def test_no_apartments_aborts(hass: HomeAssistant, mock_api) -> None:
    mock_api.async_get_apartments.return_value = []
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_properties"


async def test_duplicate_account(hass: HomeAssistant, mock_api, config_entry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_api, config_entry) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_api.async_login.side_effect = StaysAuthError("nope")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_api.async_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-pass"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-pass"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_options_flow(hass: HomeAssistant, mock_api, config_entry) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PROPERTIES: [APT_ID, "b" * 24],
            "checkin_time": "15:00:00",
            "checkout_time": "10:00:00",
            "days_ahead": 120,
            "scan_interval": 10,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_PROPERTIES] == [APT_ID, "b" * 24]
    assert config_entry.options[CONF_PROPERTY_NAMES]["b" * 24] == "Apto Centro"
    assert config_entry.options[CONF_PROPERTY_CODES]["b" * 24] == "XX01J"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)
