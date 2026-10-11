"""Diagnostics (credentials redacted, no guest personal data)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import StaysConfigEntry
from .const import CONF_PASSWORD, CONF_USERNAME

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: StaysConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT
        ),
        "last_update_success": coordinator.last_update_success,
        "apartment_hours": {k: list(v) for k, v in coordinator._apt_times.items()},
        # Stay dates and channel only: no names, phones, e-mails or codes.
        "properties": {
            apt_id: [
                {
                    "reservation": r.code,
                    "check_in": r.start.isoformat(),
                    "check_out": r.end.isoformat(),
                    "channel": r.channel,
                    "guest_count": r.guest_count,
                    "has_phone": bool(r.guest_phone),
                }
                for r in reservations
            ]
            for apt_id, reservations in (coordinator.data or {}).items()
        },
    }
