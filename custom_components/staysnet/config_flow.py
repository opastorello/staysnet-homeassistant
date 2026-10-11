"""Config flow for Stays.net."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)
from homeassistant.util import dt as dt_util

from . import StaysConfigEntry
from .api import PASSWORD_EXPIRED, StaysApi, StaysApiError, StaysAuthError
from .const import (
    CONF_CHECKIN_TIME,
    CONF_CHECKOUT_TIME,
    CONF_DAYS_AHEAD,
    CONF_PASSWORD,
    CONF_PROPERTIES,
    CONF_PROPERTY_CODES,
    CONF_PROPERTY_NAMES,
    CONF_SCAN_INTERVAL,
    CONF_SYSTEM_ID,
    CONF_USERNAME,
    DEFAULT_CHECKIN_TIME,
    DEFAULT_CHECKOUT_TIME,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


def _auth_error(err: StaysAuthError) -> str:
    return PASSWORD_EXPIRED if str(err) == PASSWORD_EXPIRED else "invalid_auth"


def _make_api(hass: HomeAssistant, username: str, password: str) -> StaysApi:
    return StaysApi(
        async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar()),
        username,
        password,
    )


async def _fetch_apartments(
    api: StaysApi,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Login and return ({id: list label}, {id: device name}, {id: Stays code})."""
    today = dt_util.now().date()
    apartments = await api.async_get_apartments(
        today.isoformat(), (today + timedelta(days=30)).isoformat()
    )
    labels: dict[str, str] = {}
    names: dict[str, str] = {}
    codes: dict[str, str] = {}
    for apt in apartments:
        apt_id = apt.get("_id")
        if not apt_id:
            continue
        name = apt.get("avatar") or apt.get("id") or apt_id
        code = apt.get("id")
        labels[apt_id] = f"{name} ({code})" if code and code != name else name
        names[apt_id] = name
        if code:
            codes[apt_id] = code
    ordered = dict(sorted(labels.items(), key=lambda kv: kv[1].lower()))
    return ordered, names, codes


def _property_selector(labels: dict[str, str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=k, label=v) for k, v in labels.items()],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


class StaysConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._labels: dict[str, str] = {}
        self._names: dict[str, str] = {}
        self._codes: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api = _make_api(
                self.hass, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            try:
                await api.async_login()
                self._labels, self._names, self._codes = await _fetch_apartments(api)
            except StaysAuthError as err:
                errors["base"] = _auth_error(err)
            except StaysApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(
                    f"{api.system_id}_{user_input[CONF_USERNAME].lower()}"
                )
                self._abort_if_unique_id_configured()
                self._credentials = {
                    **user_input,
                    CONF_SYSTEM_ID: api.system_id or "",
                }
                if not self._labels:
                    return self.async_abort(reason="no_properties")
                return await self.async_step_properties()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_properties(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input[CONF_PROPERTIES]
            if not selected:
                errors["base"] = "no_selection"
            else:
                return self.async_create_entry(
                    title=f"Stays.net ({self._credentials[CONF_SYSTEM_ID]})",
                    data=self._credentials,
                    options={
                        CONF_PROPERTIES: selected,
                        CONF_PROPERTY_NAMES: {
                            k: self._names[k] for k in selected if k in self._names
                        },
                        CONF_PROPERTY_CODES: {
                            k: self._codes[k] for k in selected if k in self._codes
                        },
                    },
                )

        return self.async_show_form(
            step_id="properties",
            data_schema=vol.Schema(
                {vol.Required(CONF_PROPERTIES): _property_selector(self._labels)}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Stays.net forces periodic password changes: ask for the new one."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api = _make_api(
                self.hass, entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            try:
                await api.async_login()
            except StaysAuthError as err:
                errors["base"] = _auth_error(err)
            except StaysApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: StaysConfigEntry) -> OptionsFlow:
        return StaysOptionsFlow()


class StaysOptionsFlow(OptionsFlow):
    """Change properties, polling and check-in/out times."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        errors: dict[str, str] = {}

        try:
            labels, names, codes = await _fetch_apartments(entry.runtime_data.api)
        except StaysApiError:
            names = dict(entry.options.get(CONF_PROPERTY_NAMES, {}))
            labels = dict(names)
            codes = dict(entry.options.get(CONF_PROPERTY_CODES, {}))
            errors["base"] = "cannot_connect"

        if user_input is not None and not errors:
            selected = user_input[CONF_PROPERTIES]
            if not selected:
                errors["base"] = "no_selection"
            else:
                return self.async_create_entry(
                    data={
                        **user_input,
                        CONF_PROPERTY_NAMES: {
                            k: names[k] for k in selected if k in names
                        },
                        CONF_PROPERTY_CODES: {
                            k: codes[k] for k in selected if k in codes
                        },
                    }
                )

        opts = entry.options
        current = [p for p in opts.get(CONF_PROPERTIES, []) if p in labels]
        schema = vol.Schema(
            {
                vol.Required(CONF_PROPERTIES, default=current): _property_selector(
                    labels
                ),
                vol.Required(
                    CONF_CHECKIN_TIME,
                    default=opts.get(CONF_CHECKIN_TIME, DEFAULT_CHECKIN_TIME),
                ): TimeSelector(),
                vol.Required(
                    CONF_CHECKOUT_TIME,
                    default=opts.get(CONF_CHECKOUT_TIME, DEFAULT_CHECKOUT_TIME),
                ): TimeSelector(),
                vol.Required(
                    CONF_DAYS_AHEAD,
                    default=opts.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=7, max=330, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5,
                        max=120,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
