"""Shared fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.staysnet.const import (
    CONF_PASSWORD,
    CONF_PROPERTIES,
    CONF_PROPERTY_CODES,
    CONF_PROPERTY_NAMES,
    CONF_SYSTEM_ID,
    CONF_USERNAME,
    DOMAIN,
)

APT_ID = "aaaaaaaaaaaaaaaaaaaaaaaa"
APT_CODE = "CE03J"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components in every test."""
    yield


def calendar_entries() -> list[dict]:
    return [
        {  # current stay (Airbnb)
            "_id": "r1",
            "id": "GU01J",
            "type": "booked",
            "_dtcheckIn": "2026-10-08T00:00:00.000Z",
            "_dtcheckOut": "2026-10-12T00:00:00.000Z",
            "_b_airbnb": True,
        },
        {  # next stay, type 'altered' must still count as a reservation
            "_id": "r2",
            "id": "GU02J",
            "type": "altered",
            "_dtcheckIn": "2026-10-14",
            "_dtcheckOut": "2026-10-17",
        },
        {  # cancelled / blocked / maintenance must be ignored
            "_id": "r3",
            "id": "GU03J",
            "type": "canceled",
            "_dtcheckIn": "2026-10-20",
            "_dtcheckOut": "2026-10-22",
        },
        {
            "_id": "r4",
            "id": "BL01J",
            "type": "blocked",
            "_dtcheckIn": "2026-10-25",
            "_dtcheckOut": "2026-10-27",
        },
    ]


DETAILS = {
    "r1": {
        "_t_client": {
            "name": "Maria Silva",
            "emails": [{"type": "main", "adr": "maria@example.com"}],
            "phones": [{"type": "MOBILE", "iso": "55", "num": "11987654321"}],
            "documents": [{"_s_type": "cpf", "_s_number": "000.000.000-00"}],
        },
        "_t_partner": {"name": "Airbnb"},
        "_i_quantOfGuests": 3,
        "pcode": "HMABC123",
    },
    "r2": {
        "_t_client": {"fName": "João", "lName": "Souza", "phones": []},
        "_i_quantOfGuests": 2,
        "pcode": "9876543210",
    },
}


@pytest.fixture
def mock_api():
    """Patch StaysApi (integration + config flow) with a fake."""
    with (
        patch("custom_components.staysnet.StaysApi") as cls,
        patch("custom_components.staysnet.config_flow.StaysApi", new=cls),
    ):
        api = MagicMock()
        api.system_id = "ghbrasil"
        api.async_login = AsyncMock()
        api.async_get_apartments = AsyncMock(
            return_value=[
                {"_id": APT_ID, "id": APT_CODE, "avatar": "Casa Cynthia"},
                {"_id": "b" * 24, "id": "XX01J", "avatar": "Apto Centro"},
            ]
        )
        api.async_get_apartment_times = AsyncMock(
            return_value={APT_ID: (15, 10), APT_CODE: (15, 10)}
        )
        api.async_get_reservations = AsyncMock(return_value=calendar_entries())
        api.async_get_reservation_details = AsyncMock(
            side_effect=lambda rid: DETAILS.get(rid, {})
        )
        cls.return_value = api
        yield api


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Stays.net (ghbrasil)",
        unique_id="ghbrasil_user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_SYSTEM_ID: "ghbrasil",
        },
        options={
            CONF_PROPERTIES: [APT_ID],
            CONF_PROPERTY_NAMES: {APT_ID: "Casa Cynthia"},
            CONF_PROPERTY_CODES: {APT_ID: APT_CODE},
        },
    )
