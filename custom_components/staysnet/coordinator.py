"""Data coordinator for Stays.net."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    StaysApi,
    StaysApiError,
    StaysAuthError,
    detect_channel,
    extract_email,
    extract_name,
    extract_phone,
)
from .const import (
    CONF_CHECKIN_TIME,
    CONF_CHECKOUT_TIME,
    CONF_DAYS_AHEAD,
    CONF_PROPERTIES,
    CONF_PROPERTY_CODES,
    CONF_PROPERTY_NAMES,
    CONF_SCAN_INTERVAL,
    DEFAULT_CHECKIN_TIME,
    DEFAULT_CHECKOUT_TIME,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NON_STAY_TYPES,
    STATUS_CHECKIN_TODAY,
    STATUS_CHECKOUT_TODAY,
    STATUS_OCCUPIED,
    STATUS_VACANT,
)

_LOGGER = logging.getLogger(__name__)

# Stays-sync caps its window at 365 days; stay below that (30 back + <=330 ahead).
# Past days included so a stay that began before today is still seen as "current".
_LOOKBACK_DAYS = 30
_APT_TIMES_TTL = timedelta(hours=6)


@dataclass(frozen=True)
class Reservation:
    """A single stay."""

    mongo_id: str
    code: str
    apartment_id: str
    start: datetime
    end: datetime
    channel: str
    guest_name: str
    guest_email: str
    guest_phone: str
    guest_count: int | None
    confirmation_code: str

    @property
    def door_code(self) -> str:
        """Last 4 digits of the guest phone (common smart-lock convention)."""
        digits = re.sub(r"\D", "", self.guest_phone)
        return digits[-4:] if len(digits) >= 4 else ""


@dataclass
class PropertyState:
    """Computed view of a property at a point in time."""

    current: Reservation | None
    next: Reservation | None
    status: str


def _details_ttl(dates: tuple[str, str], today: date) -> timedelta:
    """How long cached guest details stay fresh.

    Imminent/ongoing stays refresh often (guest data like phone is filled in late),
    finished ones barely matter and far-future ones change slowly.
    """
    start, end = _parse_date(dates[0]), _parse_date(dates[1])
    if end is not None and end < today - timedelta(days=1):
        return timedelta(hours=24)
    if start is None or (start - today).days <= 7:
        return timedelta(minutes=15)
    return timedelta(hours=6)


def _parse_time(value: str, default: str) -> time:
    try:
        return time.fromisoformat(value or default)
    except ValueError:
        return time.fromisoformat(default)


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


class StaysCoordinator(DataUpdateCoordinator[dict[str, list[Reservation]]]):
    """Fetch reservations of the selected properties."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: StaysApi
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
        )
        self.api = api
        # mongo_id -> ((checkin, checkout), details, fetched_at). Details are refreshed
        # periodically because guest data (e.g. phone) can be filled in later.
        self._details_cache: dict[
            str, tuple[tuple[str, str], dict[str, Any], datetime]
        ] = {}
        # Property default check-in/out hours rarely change: refresh every few hours.
        self._apt_times: dict[str, tuple[int | None, int | None]] = {}
        self._apt_times_at: datetime | None = None

    @property
    def property_ids(self) -> list[str]:
        return list(self.config_entry.options.get(CONF_PROPERTIES, []))

    def property_name(self, apartment_id: str) -> str:
        names = self.config_entry.options.get(CONF_PROPERTY_NAMES, {})
        return names.get(apartment_id, apartment_id)

    async def _async_update_data(self) -> dict[str, list[Reservation]]:
        opts = self.config_entry.options
        days_ahead = opts.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD)
        checkin_t = _parse_time(opts.get(CONF_CHECKIN_TIME), DEFAULT_CHECKIN_TIME)
        checkout_t = _parse_time(opts.get(CONF_CHECKOUT_TIME), DEFAULT_CHECKOUT_TIME)

        today = dt_util.now().date()
        from_date = (today - timedelta(days=_LOOKBACK_DAYS)).isoformat()
        to_date = (today + timedelta(days=days_ahead)).isoformat()

        try:
            # Per-property default check-in/out hours configured in Stays; the
            # options only act as fallback if Stays doesn't provide them.
            apt_times = await self._apartment_times()

            result: dict[str, list[Reservation]] = {}
            sem = asyncio.Semaphore(3)
            for apt_id in self.property_ids:
                code = self.config_entry.options.get(CONF_PROPERTY_CODES, {}).get(apt_id)
                in_h, out_h = apt_times.get(apt_id) or apt_times.get(code, (None, None))
                apt_in = time(in_h) if in_h is not None else checkin_t
                apt_out = time(out_h) if out_h is not None else checkout_t
                entries = [
                    e
                    for e in await self.api.async_get_reservations(
                        apt_id, from_date, to_date
                    )
                    if (e.get("type") or "").lower() not in NON_STAY_TYPES
                ]
                details = await asyncio.gather(
                    *(self._details(e, sem) for e in entries)
                )
                reservations = [
                    r
                    for e, d in zip(entries, details, strict=True)
                    if (r := self._build(apt_id, e, d, apt_in, apt_out))
                ]
                reservations.sort(key=lambda r: r.start)
                result[apt_id] = reservations
        except StaysAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except StaysApiError as err:
            raise UpdateFailed(str(err)) from err

        # Drop cache entries for reservations that no longer exist.
        live = {r.mongo_id for rs in result.values() for r in rs}
        self._details_cache = {
            k: v for k, v in self._details_cache.items() if k in live
        }
        return result

    async def _apartment_times(self) -> dict[str, tuple[int | None, int | None]]:
        now = dt_util.utcnow()
        if self._apt_times_at is None or now - self._apt_times_at > _APT_TIMES_TTL:
            try:
                self._apt_times = await self.api.async_get_apartment_times()
            except StaysAuthError:
                raise
            except StaysApiError as err:
                _LOGGER.debug("Could not fetch apartment times: %s", err)
            self._apt_times_at = now  # also on failure: don't retry every poll
        return self._apt_times

    async def _details(
        self, entry: dict[str, Any], sem: asyncio.Semaphore
    ) -> dict[str, Any]:
        mongo_id = entry.get("_id") or ""
        dates = (
            (entry.get("_dtcheckIn") or "")[:10],
            (entry.get("_dtcheckOut") or "")[:10],
        )
        cached = self._details_cache.get(mongo_id)
        if cached and cached[0] == dates:
            ttl = _details_ttl(dates, dt_util.now().date())
            if dt_util.utcnow() - cached[2] < ttl:
                return cached[1]
        if not mongo_id:
            return {}
        async with sem:
            try:
                details = await self.api.async_get_reservation_details(mongo_id)
            except StaysAuthError:
                raise
            except StaysApiError as err:
                _LOGGER.warning("Could not fetch details for %s: %s", entry.get("id"), err)
                return cached[1] if cached else {}
        self._details_cache[mongo_id] = (dates, details, dt_util.utcnow())
        return details

    @staticmethod
    def _build(
        apt_id: str,
        entry: dict[str, Any],
        details: dict[str, Any],
        checkin_t: time,
        checkout_t: time,
    ) -> Reservation | None:
        start_d = _parse_date(entry.get("_dtcheckIn", ""))
        end_d = _parse_date(entry.get("_dtcheckOut", ""))
        if not start_d or not end_d:
            return None
        tz = dt_util.DEFAULT_TIME_ZONE
        client = details.get("_t_client") or details.get("client") or {}
        partner = details.get("_t_partner") or {}
        guests = details.get("_i_quantOfGuests")
        return Reservation(
            mongo_id=entry.get("_id", ""),
            code=entry.get("id", ""),
            apartment_id=apt_id,
            start=datetime.combine(start_d, checkin_t, tzinfo=tz),
            end=datetime.combine(end_d, checkout_t, tzinfo=tz),
            channel=partner.get("name") or detect_channel(entry),
            guest_name=extract_name(client),
            guest_email=extract_email(client),
            guest_phone=extract_phone(client),
            guest_count=int(guests) if isinstance(guests, int | float) else None,
            confirmation_code=details.get("pcode") or "",
        )

    def state_for(self, apartment_id: str) -> PropertyState:
        """Compute current/next stay and status for a property."""
        now = dt_util.now()
        today = now.date()
        current = nxt = None
        for res in self.data.get(apartment_id, []):
            if res.start <= now < res.end:
                current = res
            elif res.start > now and nxt is None:
                nxt = res

        if current:
            status = (
                STATUS_CHECKOUT_TODAY
                if current.end.date() == today
                else STATUS_OCCUPIED
            )
        elif nxt and nxt.start.date() == today:
            status = STATUS_CHECKIN_TODAY
        else:
            status = STATUS_VACANT
        return PropertyState(current=current, next=nxt, status=status)
