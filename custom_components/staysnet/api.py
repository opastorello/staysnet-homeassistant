"""Async client for the (unofficial) Stays.net internal API."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from .const import BASE_URL_TEMPLATE, LOGIN_URL, OTA_FLAGS

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Session cookie lasts 24h; refresh a bit earlier.
_SESSION_TTL = timedelta(hours=20)


class StaysApiError(Exception):
    """Generic API/connection error."""


class StaysAuthError(StaysApiError):
    """Invalid credentials or expired password."""


class StaysApi:
    """Minimal Stays.net client: login, calendar, reservation details."""

    def __init__(
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self.system_id: str | None = None
        self.user_id: str | None = None
        self._base_url: str | None = None
        self._cookie: str | None = None
        self._cookie_at: datetime | None = None
        self._lock = asyncio.Lock()

    # ── auth ────────────────────────────────────────────────

    def _session_valid(self) -> bool:
        return bool(
            self._cookie
            and self.user_id
            and self._cookie_at
            and datetime.utcnow() - self._cookie_at < _SESSION_TTL
        )

    async def async_login(self) -> None:
        """Log in and discover system_id, user_id and base URL."""
        async with self._lock:
            await self._login()

    async def _login(self) -> None:
        try:
            payload = json.dumps(
                {
                    "login": self._username,
                    "password": self._password,
                    "_t_son": "out",
                    "_t_st": 3,
                }
            )
            async with self._session.post(
                LOGIN_URL,
                data={"_t_jsonq": payload},
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": "https://my.stays.net",
                    "Referer": "https://my.stays.net/my/user/signin/",
                },
                allow_redirects=False,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    raise StaysApiError(f"Login HTTP {resp.status}")
                text = await resp.text()

            try:
                data = json.loads(text)
            except ValueError as err:
                raise StaysAuthError("invalid_auth") from err

            if not isinstance(data, list) or not data:
                raise StaysAuthError("invalid_auth")
            entry = data[0]
            system_id = entry.get("name")
            sms_url = entry.get("url")
            if not system_id or not sms_url:
                raise StaysAuthError("invalid_auth")

            cookie_name = f"hdbid_sn_{system_id}"
            base_url = BASE_URL_TEMPLATE.format(system_id=system_id)

            # NOTE: no X-Requested-With here, otherwise the cookie comes back empty.
            browser_headers = {
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*",
            }
            async with self._session.get(
                sms_url,
                headers=browser_headers,
                allow_redirects=False,
                timeout=_TIMEOUT,
            ) as resp:
                morsel = resp.cookies.get(cookie_name)
                cookie = morsel.value if morsel else None
                if not cookie:
                    for header in resp.headers.getall("Set-Cookie", []):
                        match = re.search(rf"{cookie_name}=([^;]+)", header)
                        if match:
                            cookie = match.group(1)
                            break
            if not cookie:
                raise StaysAuthError("invalid_auth")

            async with self._session.get(
                f"{base_url}/i/home",
                headers={**browser_headers, "Cookie": f"{cookie_name}={cookie}"},
                allow_redirects=True,
                timeout=_TIMEOUT,
            ) as resp:
                html = await resp.text()
            user_id = _extract_user_id(html)
            if not user_id:
                raise StaysAuthError("invalid_auth")

        except (aiohttp.ClientError, TimeoutError) as err:
            raise StaysApiError(f"Connection error: {err}") from err

        self.system_id = system_id
        self.user_id = user_id
        self._base_url = base_url
        self._cookie = cookie
        self._cookie_at = datetime.utcnow()

    # ── low level ───────────────────────────────────────────

    async def _post(
        self, path: str, body: Any, *, _retry: bool = True
    ) -> Any:
        async with self._lock:
            if not self._session_valid():
                await self._login()

        assert self._base_url and self.system_id
        cookie_name = f"hdbid_sn_{self.system_id}"
        try:
            async with self._session.post(
                f"{self._base_url}{path}",
                json=body,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self._base_url}/",
                    "Origin": self._base_url,
                    "Cookie": f"{cookie_name}={self._cookie}",
                },
                allow_redirects=False,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status in (301, 302, 303, 401, 403):
                    expired = True
                else:
                    expired = False
                    if resp.status >= 400:
                        raise StaysApiError(f"HTTP {resp.status} on {path}")
                    try:
                        return await resp.json(content_type=None)
                    except ValueError:
                        expired = True  # HTML login page instead of JSON
        except (aiohttp.ClientError, TimeoutError) as err:
            raise StaysApiError(f"Connection error: {err}") from err

        if expired:
            if not _retry:
                raise StaysAuthError("Session rejected after re-login")
            self._cookie = None
            return await self._post(path, body, _retry=False)
        return None  # pragma: no cover

    async def _jsonrpc(self, method: str, params: list[Any]) -> Any:
        """Call Stays' custom JSON-RPC (reserve.* methods use '' as first param)."""
        async with self._lock:
            if not self._session_valid():
                await self._login()
        data = await self._post(
            "/jsonrpc", {"uid": self.user_id, "method": method, "params": params}
        )
        if isinstance(data, dict):
            if "error" in data:
                raise StaysApiError(f"JSON-RPC error: {data['error']}")
            return data.get("result", data)
        return data

    # ── high level ──────────────────────────────────────────

    async def async_get_apartments(
        self, from_date: str, to_date: str
    ) -> list[dict[str, Any]]:
        """Return all apartments (units) visible in the calendar."""
        qs = f"from={from_date}&to={to_date}&" + "&".join(
            f"type[]={t}" for t in ("reserved", "booked", "contract")
        )
        data = await self._post(f"/i/calendars?{qs}", {})
        apartments: list[dict[str, Any]] = []
        if isinstance(data, list):
            for group in data:
                building = group.get("_t_building") or {}
                for apt in group.get("apartments", []):
                    apt["_t_building"] = building
                    apartments.append(apt)
        return apartments

    async def async_get_reservations(
        self, apartment_id: str, from_date: str, to_date: str
    ) -> list[dict[str, Any]]:
        """Return calendar entries (summaries) for one apartment."""
        result = await self._jsonrpc(
            "reserve.getCalendarEx",
            [
                "",
                apartment_id,
                from_date,
                to_date,
                {
                    "rejectCanceled": True,
                    "includeLastMinute": True,  # as stays-sync; fakes filtered below
                    "fields": {
                        "_id": 1,
                        "id": 1,
                        "_dtcheckIn": 1,
                        "_dtcheckOut": 1,
                        "type": 1,
                        "_idclient": 1,
                        **{flag: 1 for flag in OTA_FLAGS},
                    },
                },
            ],
        )
        raw: list[Any] = []
        if isinstance(result, list) and result:
            raw = result[0] if isinstance(result[0], list) else result
        return [
            r
            for r in raw
            if isinstance(r, dict)
            and not r.get("_t_isFake")
            and not r.get("_t_lastMinuteBlock")
        ]

    async def async_get_apartment_times(self) -> dict[str, tuple[int | None, int | None]]:
        """Return {apartment _id: (default check-in hour, default check-out hour)}."""
        async with self._lock:
            if not self._session_valid():
                await self._login()
        # apartment.* methods take system_id as first param (unlike reserve.*)
        result = await self._jsonrpc(
            "apartment.getSimpleApartments",
            [
                self.system_id,
                {"active": "yes"},
                {"id": 1, "avatar": 1, "arrival": 1, "departure": 1},
            ],
        )
        apartments: list[Any] = []
        if isinstance(result, list) and result:
            apartments = result[0] if isinstance(result[0], list) else result
        times: dict[str, tuple[int | None, int | None]] = {}
        for apt in apartments:
            if not isinstance(apt, dict) or not apt.get("_id"):
                continue
            arrival = apt.get("arrival") or {}
            departure = apt.get("departure") or {}
            value = (
                _as_hour(arrival.get("_i_defCheckIn")),
                _as_hour(departure.get("_i_defCheckOut")),
            )
            times[apt["_id"]] = value
            if apt.get("id"):  # also by Stays code (e.g. NK01J)
                times[apt["id"]] = value
        return times

    async def async_get_reservation_details(
        self, reservation_mongo_id: str
    ) -> dict[str, Any]:
        """Return the full reservation (guest, partner, number of guests...)."""
        result = await self._jsonrpc(
            "reserve.getReserveForCalendar", ["", reservation_mongo_id]
        )
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return result[0]
        return result if isinstance(result, dict) else {}


def _as_hour(value: Any) -> int | None:
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


def _extract_user_id(html: str) -> str | None:
    if not html:
        return None
    for pattern in (
        r"['\"]userId['\"]:\s*['\"]([a-f0-9]{24})['\"]",
        r"_user:\s*\{\s*_id:\s*['\"]([a-f0-9]{24})['\"]",
        r"client_id['\"]?\s*[:=]\s*['\"]([a-f0-9]{24})['\"]",
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


def detect_channel(entry: dict[str, Any]) -> str:
    """Detect OTA channel from calendar flags."""
    for flag, name in OTA_FLAGS.items():
        if entry.get(flag):
            return name
    return "Direct"


def extract_phone(client: dict[str, Any]) -> str:
    """Return the guest's phone in +E.164-ish form, or ''."""
    phones = client.get("phones") or []
    if not phones:
        return ""
    phone = next((p for p in phones if p.get("type") == "MOBILE"), phones[0])
    iso = re.sub(r"\D", "", phone.get("iso") or "")
    num = re.sub(r"\D", "", phone.get("num") or "")
    if iso and num:
        candidate = f"+{num}" if num.startswith(iso) else f"+{iso}{num}"
    elif num:
        candidate = f"+{num}" if len(num) >= 10 else num
    else:
        return ""
    return candidate if 8 <= len(re.sub(r"\D", "", candidate)) <= 15 else ""


def extract_name(client: dict[str, Any]) -> str:
    name = client.get("name")
    if name:
        return name
    return f"{client.get('fName', '')} {client.get('lName', '')}".strip()


def extract_email(client: dict[str, Any]) -> str:
    personal = client.get("personal")
    if isinstance(personal, dict) and personal.get("email"):
        return personal["email"]
    emails = client.get("emails") or []
    if emails:
        main = next((e for e in emails if e.get("type") == "main"), emails[0])
        return main.get("adr", "") or ""
    return ""
