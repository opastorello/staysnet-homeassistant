"""Diagnostics (credentials redacted, no guest personal data)."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import StaysConfigEntry
from .const import CONF_PASSWORD, CONF_USERNAME

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}

# Fields of a Stays reservation that may carry the platform's check-in/out times.
_TIME_KEY = re.compile(r"check|time|hour|arriv|depart|travel|eta", re.IGNORECASE)
_PII_KEY = re.compile(r"mail|phone|name|doc|cpf|address|adr|passport|cnpj", re.IGNORECASE)


def _scan(obj: dict[str, Any], depth: int = 2) -> dict[str, Any]:
    """Time-looking scalar fields (2 levels deep), never personal data."""
    out: dict[str, Any] = {}
    for key, value in obj.items():
        if _PII_KEY.search(key):
            continue
        if isinstance(value, dict) and depth > 0:
            if inner := _scan(value, depth - 1):
                out[key] = inner
        elif _TIME_KEY.search(key) and not isinstance(value, dict | list):
            out[key] = value if isinstance(value, int | float | bool | None) else str(value)[:80]
    return out


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: StaysConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    reservations = data.reservations if data else {}
    blocks = data.blocks if data else {}
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT
        ),
        "last_update_success": coordinator.last_update_success,
        "apartment_hours": {k: list(v) for k, v in coordinator._apt_times.items()},
        # Stay dates and channel only: no names, phones, e-mails or codes.
        "properties": {
            apt_id: {
                "blocks": len(blocks.get(apt_id, [])),
                "reservations": [
                    {
                        "reservation": r.code,
                        "check_in": r.start.isoformat(),
                        "check_out": r.end.isoformat(),
                        # exactly what Stays sent, to see where the hours come from
                        "stays_raw_check_in": r.raw_start,
                        "stays_raw_check_out": r.raw_end,
                        "platform_time_fields": _scan(
                            coordinator._details_cache.get(r.mongo_id, (None, {}))[1]
                        ),
                        "channel": r.channel,
                        "guest_count": r.guest_count,
                        "has_phone": bool(r.guest_phone),
                        "door_code_source": r.door_code_and_source[1],
                    }
                    for r in rs
                ],
            }
            for apt_id, rs in reservations.items()
        },
    }
