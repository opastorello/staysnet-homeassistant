"""StaysApi against a mocked Stays.net (login flow, JSON-RPC, re-login)."""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.staysnet import api as api_module
from custom_components.staysnet.api import (
    StaysApi,
    StaysApiError,
    StaysAuthError,
    detect_channel,
    extract_email,
    extract_name,
    extract_phone,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")

USER_ID = "c" * 24
COOKIE = "hdbid_sn_ghbrasil"


def test_extract_phone() -> None:
    assert extract_phone({"phones": [{"type": "MOBILE", "iso": "55", "num": "11987654321"}]}) == "+5511987654321"
    # num already carries the country code: no duplication
    assert extract_phone({"phones": [{"iso": "55", "num": "5511987654321"}]}) == "+5511987654321"
    assert extract_phone({"phones": [{"num": "123"}]}) == ""
    assert extract_phone({}) == ""


def test_extract_name_email_channel() -> None:
    assert extract_name({"name": "A B"}) == "A B"
    assert extract_name({"fName": "A", "lName": "B"}) == "A B"
    assert extract_email({"personal": {"email": "p@x.com"}}) == "p@x.com"
    assert extract_email({"emails": [{"type": "alt", "adr": "a@x"}, {"type": "main", "adr": "m@x"}]}) == "m@x"
    assert detect_channel({"_b_bookingcom": True}) == "Booking.com"
    assert detect_channel({}) == "Direct"


class FakeStays:
    """Minimal Stays.net: my.stays.net login + <system>.stays.net API."""

    def __init__(self) -> None:
        self.logins = 0
        self.expire_next = False
        self.rpc_calls: list[dict] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/restapi/undefined/users/findMyStays", self.find)
        app.router.add_get("/i/loginbysms", self.sms)
        app.router.add_get("/i/home", self.home)
        app.router.add_post("/jsonrpc", self.rpc)
        app.router.add_post("/i/calendars", self.calendars)
        return app

    async def find(self, request: web.Request) -> web.Response:
        form = await request.post()
        payload = json.loads(form["_t_jsonq"])
        if payload["password"] == "expired":
            return web.Response(text="Sua senha expirou, voce precisa trocar a senha")
        if payload["password"] != "secret":
            return web.Response(text="Login ou senha incorretos")
        self.logins += 1
        base = f"http://{request.host}"
        return web.json_response(
            [{"name": "ghbrasil", "url": f"{base}/i/loginbysms?smstoken=abc"}]
        )

    async def sms(self, request: web.Request) -> web.Response:
        # the real endpoint returns an empty cookie when XHR header is sent
        if request.headers.get("X-Requested-With"):
            return web.Response(status=302)
        resp = web.Response(status=302)
        resp.set_cookie(COOKIE, f"session-{self.logins}")
        return resp

    async def home(self, request: web.Request) -> web.Response:
        if request.cookies.get(COOKIE) != f"session-{self.logins}":
            return web.Response(status=302, headers={"Location": "/signin"})
        return web.Response(text=f"<script>dataLayer.push({{'userId':'{USER_ID}'}})</script>")

    def _check(self, request: web.Request) -> web.Response | None:
        if self.expire_next:
            self.expire_next = False
            return web.Response(status=302, headers={"Location": "/user/signin"})
        if request.cookies.get(COOKIE) != f"session-{self.logins}":
            return web.Response(status=302, headers={"Location": "/user/signin"})
        return None

    async def rpc(self, request: web.Request) -> web.Response:
        if (denied := self._check(request)) is not None:
            return denied
        body = await request.json()
        self.rpc_calls.append(body)
        assert body["uid"] == USER_ID
        method = body["method"]
        if method == "reserve.getCalendarEx":
            return web.json_response(
                {
                    "result": [
                        [
                            {"_id": "r1", "id": "GU01J", "type": "booked"},
                            {"_id": "f", "id": "F", "_t_isFake": True},
                        ]
                    ]
                }
            )
        if method == "reserve.getReserveForCalendar":
            return web.json_response({"result": [{"_t_client": {"name": "X"}}]})
        if method == "apartment.getSimpleApartments":
            return web.json_response(
                {
                    "result": [
                        [
                            {
                                "_id": "a1",
                                "id": "CE03J",
                                "arrival": {"_i_defCheckIn": 14},
                                "departure": {"_i_defCheckOut": 11},
                            }
                        ]
                    ]
                }
            )
        return web.json_response({"error": "unknown"})

    async def calendars(self, request: web.Request) -> web.Response:
        if (denied := self._check(request)) is not None:
            return denied
        return web.json_response(
            [{"_t_building": {"name": "B"}, "apartments": [{"_id": "a1", "id": "CE03J", "avatar": "Casa"}]}]
        )


@pytest.fixture
async def stays(monkeypatch: pytest.MonkeyPatch):
    fake = FakeStays()
    server = TestServer(fake.app())
    await server.start_server()
    host = f"{server.host}:{server.port}"
    monkeypatch.setattr(
        api_module, "LOGIN_URL", f"http://{host}/restapi/undefined/users/findMyStays"
    )
    monkeypatch.setattr(api_module, "BASE_URL_TEMPLATE", f"http://{host}")
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        yield fake, StaysApi(session, "user@example.com", "secret"), host
    await server.close()


async def _login(client: StaysApi, host: str) -> None:
    await client.async_login()


async def test_login_discovers_ids(stays) -> None:
    fake, client, host = stays
    await _login(client, host)
    assert client.system_id == "ghbrasil"
    assert client.user_id == USER_ID
    assert fake.logins == 1


async def test_login_bad_password(stays) -> None:
    fake, client, host = stays
    client._password = "wrong"
    with pytest.raises(StaysAuthError):
        await client.async_login()


async def test_login_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_module, "LOGIN_URL", "http://127.0.0.1:1/x")
    async with aiohttp.ClientSession() as session:
        with pytest.raises(StaysApiError):
            await StaysApi(session, "u", "p").async_login()


async def test_calls_and_filtering(stays) -> None:
    fake, client, host = stays
    await _login(client, host)

    apts = await client.async_get_apartments("2026-10-01", "2026-11-01")
    assert apts[0]["_id"] == "a1" and apts[0]["_t_building"] == {"name": "B"}

    res = await client.async_get_reservations("a1", "2026-10-01", "2026-11-01")
    assert [r["id"] for r in res] == ["GU01J"]  # fake entry filtered
    call = fake.rpc_calls[-1]
    assert call["params"][0] == ""  # reserve.* uses '' as first param
    assert call["params"][1] == "a1"

    details = await client.async_get_reservation_details("r1")
    assert details["_t_client"]["name"] == "X"

    times = await client.async_get_apartment_times()
    assert times["a1"] == (14, 11) and times["CE03J"] == (14, 11)
    # apartment.* prepends the system_id
    assert fake.rpc_calls[-1]["params"][0] == "ghbrasil"


async def test_relogin_when_session_expires(stays) -> None:
    fake, client, host = stays
    await _login(client, host)
    fake.expire_next = True  # next call is redirected to signin
    res = await client.async_get_reservations("a1", "2026-10-01", "2026-11-01")
    assert [r["id"] for r in res] == ["GU01J"]
    assert fake.logins == 2


async def test_login_password_expired(stays) -> None:
    fake, client, host = stays
    client._password = "expired"
    with pytest.raises(StaysAuthError, match="password_expired"):
        await client.async_login()


async def test_concurrent_requests_share_one_relogin(stays) -> None:
    """Single-session server: N parallel requests must trigger ONE re-login."""
    fake, client, host = stays
    await _login(client, host)  # logins == 1
    fake.logins += 1  # server drops our session
    results = await asyncio.gather(
        *(client.async_get_reservations("a1", "2026-10-01", "2026-11-01") for _ in range(3))
    )
    assert all(len(r) == 1 for r in results)
    assert fake.logins == 3  # initial + invalidation bump + exactly one re-login


async def test_rejected_right_after_login_is_not_auth_error(stays) -> None:
    """Fresh login but still redirected: transient API error, not a reauth."""
    fake, client, host = stays
    await _login(client, host)
    orig = fake._check

    def always_deny(request):
        return web.Response(status=302, headers={"Location": "/user/signin"})

    fake._check = always_deny
    with pytest.raises(StaysApiError) as err:
        await client.async_get_reservations("a1", "2026-10-01", "2026-11-01")
    assert not isinstance(err.value, StaysAuthError)
    fake._check = orig
