"""Data coordinator for Stays.net."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
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
    BLOCK_TYPES,
    CANCELLED_TYPES,
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
    EVENT_CANCELLED,
    EVENT_CHANGED,
    EVENT_CHECK_IN,
    EVENT_CHECK_OUT,
    EVENT_CREATED,
    STATUS_CHECKIN_TODAY,
    STATUS_CHECKOUT_TODAY,
    STATUS_OCCUPIED,
    STATUS_VACANT,
    event_signal,
)

_LOGGER = logging.getLogger(__name__)

# Stays-sync caps its window at 365 days: 35 back + at most 330 ahead.
# Past days keep a stay that began before today visible as "current" and feed the
# "last guest" / monthly occupancy sensors.
_LOOKBACK_DAYS = 35
_APT_TIMES_TTL = timedelta(hours=6)
# A reservation must be missing from this many consecutive polls before we call it
# cancelled (stays-sync documents that the calendar sometimes omits entries).
_CANCEL_AFTER_MISSES = 2


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
    raw_start: str = ""  # _dtcheckIn exactly as Stays sent it
    raw_end: str = ""

    @property
    def door_code_and_source(self) -> tuple[str, str]:
        """Last 4 digits of the phone; else last 4 digits of the confirmation code."""
        digits = re.sub(r"\D", "", self.guest_phone)
        if len(digits) >= 4:
            return digits[-4:], "phone"
        digits = re.sub(r"\D", "", self.confirmation_code)
        if len(digits) >= 4:
            return digits[-4:], "confirmation_code"
        return "", ""

    @property
    def door_code(self) -> str:
        return self.door_code_and_source[0]

    def as_details(self, which: str) -> dict[str, Any]:
        """Full guest/stay details (contains personal data: never put in events)."""
        door_code, source = self.door_code_and_source
        return {
            "which": which,
            "reservation_id": self.code,
            "confirmation_code": self.confirmation_code,
            "channel": self.channel,
            "guest_name": self.guest_name,
            "guest_email": self.guest_email,
            "guest_phone": self.guest_phone,
            "guest_count": self.guest_count,
            "door_code": door_code,
            "door_code_source": source,
            "nights": self.nights,
            "check_in": self.start.isoformat(),
            "check_out": self.end.isoformat(),
        }

    @property
    def nights(self) -> int:
        return max(0, (self.end.date() - self.start.date()).days)


@dataclass(frozen=True)
class Block:
    """A calendar block (blocked / maintenance): all-day, end exclusive."""

    mongo_id: str
    code: str
    apartment_id: str
    start: date
    end: date
    kind: str


@dataclass
class StaysData:
    """Everything fetched from Stays for the selected properties."""

    reservations: dict[str, list[Reservation]] = field(default_factory=dict)
    blocks: dict[str, list[Block]] = field(default_factory=dict)


@dataclass
class PropertyState:
    """Computed view of a property at a point in time."""

    current: Reservation | None
    next: Reservation | None
    last: Reservation | None
    status: str
    checkin_today: bool
    checkout_today: bool
    upcoming: list[Reservation]
    month_occupancy: float


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


def _diff(old: Reservation, new: Reservation) -> dict[str, dict[str, Any]]:
    """Meaningful changes between two versions of the same reservation."""
    changes: dict[str, dict[str, Any]] = {}
    if old.start != new.start:
        changes["check_in"] = {"old": old.start.isoformat(), "new": new.start.isoformat()}
    if old.end != new.end:
        changes["check_out"] = {"old": old.end.isoformat(), "new": new.end.isoformat()}
    if old.guest_count != new.guest_count:
        changes["guest_count"] = {"old": old.guest_count, "new": new.guest_count}
    if old.guest_name and old.guest_name != new.guest_name:
        changes["guest_name"] = {"old": old.guest_name, "new": new.guest_name}
    return changes


class StaysCoordinator(DataUpdateCoordinator[StaysData]):
    """Fetch reservations of the selected properties and raise stay events."""

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
        self.last_success_at: datetime | None = None
        # mongo_id -> ((checkin, checkout), details, fetched_at). Details are refreshed
        # periodically because guest data (e.g. phone) can be filled in later.
        self._details_cache: dict[
            str, tuple[tuple[str, str], dict[str, Any], datetime]
        ] = {}
        # Property default check-in/out hours rarely change: refresh every few hours.
        self._apt_times: dict[str, tuple[int | None, int | None]] = {}
        self._apt_times_at: datetime | None = None
        # Event detection state
        self._window_end: date | None = None
        self._missing: dict[str, dict[str, tuple[Reservation, int]]] = {}
        self._current: dict[str, Reservation | None] = {}

    @property
    def property_ids(self) -> list[str]:
        return list(self.config_entry.options.get(CONF_PROPERTIES, []))

    def property_name(self, apartment_id: str) -> str:
        names = self.config_entry.options.get(CONF_PROPERTY_NAMES, {})
        return names.get(apartment_id, apartment_id)

    # ── polling ─────────────────────────────────────────────

    async def _async_update_data(self) -> StaysData:
        opts = self.config_entry.options
        days_ahead = int(opts.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD))
        checkin_t = _parse_time(opts.get(CONF_CHECKIN_TIME), DEFAULT_CHECKIN_TIME)
        checkout_t = _parse_time(opts.get(CONF_CHECKOUT_TIME), DEFAULT_CHECKOUT_TIME)

        today = dt_util.now().date()
        from_date = (today - timedelta(days=_LOOKBACK_DAYS)).isoformat()
        window_end = today + timedelta(days=days_ahead)

        try:
            # Per-property default check-in/out hours configured in Stays; the
            # options only act as fallback if Stays doesn't provide them.
            apt_times = await self._apartment_times()

            new = StaysData()
            sem = asyncio.Semaphore(3)
            codes = opts.get(CONF_PROPERTY_CODES, {})
            for apt_id in self.property_ids:
                in_h, out_h = apt_times.get(apt_id) or apt_times.get(
                    codes.get(apt_id), (None, None)
                )
                apt_in = time(in_h) if in_h is not None else checkin_t
                apt_out = time(out_h) if out_h is not None else checkout_t

                raw = await self.api.async_get_reservations(
                    apt_id, from_date, window_end.isoformat()
                )
                stays: list[dict[str, Any]] = []
                blocks: list[Block] = []
                for entry in raw:
                    kind = (entry.get("type") or "").lower()
                    if kind in BLOCK_TYPES:
                        if block := self._build_block(apt_id, entry, kind):
                            blocks.append(block)
                    elif kind not in CANCELLED_TYPES:
                        stays.append(entry)

                details = await asyncio.gather(*(self._details(e, sem) for e in stays))
                reservations = [
                    r
                    for e, d in zip(stays, details, strict=True)
                    if (r := self._build(apt_id, e, d, apt_in, apt_out))
                ]
                new.reservations[apt_id] = sorted(reservations, key=lambda r: r.start)
                new.blocks[apt_id] = sorted(blocks, key=lambda b: b.start)
        except StaysAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except StaysApiError as err:
            raise UpdateFailed(str(err)) from err

        # Drop cache entries for reservations that no longer exist.
        live = {r.mongo_id for rs in new.reservations.values() for r in rs}
        self._details_cache = {
            k: v for k, v in self._details_cache.items() if k in live
        }

        self._detect_changes(new)
        self._window_end = window_end
        self.last_success_at = dt_util.utcnow()
        return new

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
                _LOGGER.warning(
                    "Could not fetch details for %s: %s", entry.get("id"), err
                )
                return cached[1] if cached else {}
        self._details_cache[mongo_id] = (dates, details, dt_util.utcnow())
        return details

    @staticmethod
    def _build_block(apt_id: str, entry: dict[str, Any], kind: str) -> Block | None:
        start_d = _parse_date(entry.get("_dtcheckIn", ""))
        end_d = _parse_date(entry.get("_dtcheckOut", ""))
        if not start_d or not end_d:
            return None
        return Block(
            mongo_id=entry.get("_id", ""),
            code=entry.get("id", ""),
            apartment_id=apt_id,
            start=start_d,
            end=end_d,
            kind=kind,
        )

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
            raw_start=str(entry.get("_dtcheckIn") or ""),
            raw_end=str(entry.get("_dtcheckOut") or ""),
        )

    # ── events ──────────────────────────────────────────────

    def _emit(
        self,
        res: Reservation,
        event_type: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Fire a bus event + notify the property's event entity.

        No phone/e-mail/door code here: events end up in the recorder.
        """
        data: dict[str, Any] = {
            "apartment_id": res.apartment_id,
            "property": self.property_name(res.apartment_id),
            "reservation_id": res.code,
            "guest_name": res.guest_name,
            "guest_count": res.guest_count,
            "channel": res.channel,
            "check_in": res.start.isoformat(),
            "check_out": res.end.isoformat(),
            **(extra or {}),
        }
        self.hass.bus.async_fire(f"{DOMAIN}_{event_type}", data)
        async_dispatcher_send(
            self.hass,
            event_signal(self.config_entry.entry_id, res.apartment_id),
            event_type,
            data,
        )

    def _detect_changes(self, new: StaysData) -> None:
        """Compare with the previous poll: created / changed / cancelled."""
        old = self.data
        if old is None:  # first poll is the baseline
            return
        now = dt_util.now()
        for apt_id in self.property_ids:
            old_by = {r.mongo_id or r.code: r for r in old.reservations.get(apt_id, [])}
            new_by = {r.mongo_id or r.code: r for r in new.reservations.get(apt_id, [])}
            missing = self._missing.setdefault(apt_id, {})

            # Reappeared after a transient omission: not a new reservation.
            reappeared = {k for k in missing if k in new_by}
            for key in reappeared:
                del missing[key]

            for key, res in new_by.items():
                prev = old_by.get(key)
                if prev is None:
                    if key in reappeared or res.end <= now:
                        continue
                    # Just rolled into the rolling window: it already existed.
                    if self._window_end and res.start.date() > self._window_end:
                        continue
                    self._emit(res, EVENT_CREATED)
                elif res.end > now and (changes := _diff(prev, res)):
                    self._emit(res, EVENT_CHANGED, {"changes": changes})

            for key, res in old_by.items():
                if key not in new_by and res.end > now:
                    missing[key] = (res, 1)
            for key, (res, misses) in list(missing.items()):
                if key in old_by:  # added this very round
                    continue
                if res.end <= now:
                    del missing[key]
                elif misses + 1 >= _CANCEL_AFTER_MISSES:
                    self._emit(res, EVENT_CANCELLED)
                    del missing[key]
                else:
                    missing[key] = (res, misses + 1)

    def async_check_stay_transitions(self) -> None:
        """Fire check_in / check_out when the current stay of a property changes.

        The first observation is only a baseline (no event after a restart).
        """
        if self.data is None:
            return
        for apt_id in self.property_ids:
            cur = self.state_for(apt_id).current
            if apt_id not in self._current:
                self._current[apt_id] = cur
                continue
            prev = self._current[apt_id]
            self._current[apt_id] = cur
            prev_id = prev.mongo_id or prev.code if prev else None
            cur_id = cur.mongo_id or cur.code if cur else None
            if prev_id == cur_id:
                continue
            if prev:
                self._emit(prev, EVENT_CHECK_OUT)
            if cur:
                self._emit(cur, EVENT_CHECK_IN)

    def async_tick(self) -> None:
        """Clock tick (every minute): events + re-evaluate states from cache."""
        self.async_check_stay_transitions()
        self.async_update_listeners()

    # ── derived state ───────────────────────────────────────

    def state_for(self, apartment_id: str) -> PropertyState:
        """Compute current/next/last stay, status and counters for a property."""
        now = dt_util.now()
        today = now.date()
        reservations = self.data.reservations.get(apartment_id, [])
        current = nxt = last = None
        upcoming: list[Reservation] = []
        for res in reservations:
            if res.start <= now < res.end:
                current = res
            elif res.start > now:
                upcoming.append(res)
                if nxt is None:
                    nxt = res
            elif res.end <= now:
                last = res  # sorted by start: keeps the latest finished one

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

        return PropertyState(
            current=current,
            next=nxt,
            last=last,
            status=status,
            checkin_today=any(r.start.date() == today for r in reservations),
            checkout_today=any(r.end.date() == today for r in reservations),
            upcoming=upcoming,
            month_occupancy=_month_occupancy(reservations, today),
        )


def _month_occupancy(reservations: list[Reservation], today: date) -> float:
    """% of this month's nights that are booked (night = day of check-in..checkout-1)."""
    first = today.replace(day=1)
    nxt_month = (first + timedelta(days=32)).replace(day=1)
    days = (nxt_month - first).days
    booked: set[date] = set()
    for res in reservations:
        night = max(res.start.date(), first)
        while night < min(res.end.date(), nxt_month):
            booked.add(night)
            night += timedelta(days=1)
    return round(len(booked) * 100 / days, 1)
