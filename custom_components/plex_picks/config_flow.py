"""Set up Plex Picks: the Plex server's URL and the owner token, then options for the players and Shortlist."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_CONTINUE, CONF_INTERVAL, CONF_PLAYERS, CONF_PLEX_PLAYER_FOR, CONF_RECENT, CONF_RECENT_SIZE, CONF_ROW_SIZE,
    CONF_SERVER_ID, CONF_SERVER_NAME, CONF_SHORTLIST_TOKEN, CONF_SHORTLIST_URL, CONF_TOKEN, CONF_URL,
    DEFAULT_INTERVAL, DEFAULT_RECENT_SIZE, DEFAULT_ROW_SIZE, DOMAIN, MAX_INTERVAL, MIN_INTERVAL,
)
from .plex_api import PlexAuthError, PlexClient, PlexError

_LOGGER = logging.getLogger(__name__)


async def _probe(hass: HomeAssistant, url: str, token: str) -> dict[str, Any]:
    """Reach the server (identity needs no token) and prove the token (the root does)."""
    client = PlexClient(async_get_clientsession(hass), url, token)
    identity = await client.identity()
    root = await client.root()
    return {
        CONF_SERVER_ID: identity.get("machineIdentifier"),
        CONF_SERVER_NAME: root.get("friendlyName") or identity.get("machineIdentifier"),
        "version": identity.get("version"),
        "owner": root.get("myPlexUsername"),
    }


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema({
        vol.Required(CONF_URL, default=d.get(CONF_URL, "")): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
        vol.Required(CONF_TOKEN, default=d.get(CONF_TOKEN, "")): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    })


class PlexPicksConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = str(user_input[CONF_URL]).strip().rstrip("/")
            token = str(user_input[CONF_TOKEN]).strip()
            try:
                info = await _probe(self.hass, url, token)
            except PlexAuthError:
                errors["base"] = "invalid_auth"
            except PlexError as err:
                _LOGGER.debug("Plex not reachable at %s: %s", url, err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(str(info[CONF_SERVER_ID]))
                self._abort_if_unique_id_configured(updates={CONF_URL: url, CONF_TOKEN: token})
                return self.async_create_entry(
                    title=str(info[CONF_SERVER_NAME]),
                    data={CONF_URL: url, CONF_TOKEN: token, CONF_SERVER_ID: info[CONF_SERVER_ID], CONF_SERVER_NAME: info[CONF_SERVER_NAME]},
                    options={CONF_INTERVAL: DEFAULT_INTERVAL, CONF_ROW_SIZE: DEFAULT_ROW_SIZE, CONF_CONTINUE: True,
                             CONF_RECENT: True, CONF_RECENT_SIZE: DEFAULT_RECENT_SIZE, CONF_PLAYERS: []},
                )
        return self.async_show_form(step_id="user", data_schema=_user_schema(user_input), errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            token = str(user_input[CONF_TOKEN]).strip()
            try:
                await _probe(self.hass, entry.data[CONF_URL], token)
            except PlexAuthError:
                errors["base"] = "invalid_auth"
            except PlexError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
        schema = vol.Schema({vol.Required(CONF_TOKEN): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))})
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors,
                                    description_placeholders={"server": str(entry.data.get(CONF_SERVER_NAME, ""))})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PlexPicksOptionsFlow()


class PlexPicksOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            cleaned = dict(user_input)
            cleaned[CONF_SHORTLIST_URL] = str(cleaned.get(CONF_SHORTLIST_URL) or "").strip().rstrip("/")
            cleaned[CONF_SHORTLIST_TOKEN] = str(cleaned.get(CONF_SHORTLIST_TOKEN) or "").strip()
            cleaned[CONF_PLEX_PLAYER_FOR] = str(cleaned.get(CONF_PLEX_PLAYER_FOR) or "").strip()
            return self.async_create_entry(title="", data=cleaned)
        o = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_PLAYERS, default=list(o.get(CONF_PLAYERS, []))): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player", multiple=True)),
            vol.Optional(CONF_PLEX_PLAYER_FOR, description={"suggested_value": o.get(CONF_PLEX_PLAYER_FOR, "")}): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)),
            vol.Optional(CONF_CONTINUE, default=bool(o.get(CONF_CONTINUE, True))): selector.BooleanSelector(),
            vol.Optional(CONF_RECENT, default=bool(o.get(CONF_RECENT, True))): selector.BooleanSelector(),
            vol.Optional(CONF_RECENT_SIZE, default=int(o.get(CONF_RECENT_SIZE, DEFAULT_RECENT_SIZE))): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=60, step=1, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_ROW_SIZE, default=int(o.get(CONF_ROW_SIZE, DEFAULT_ROW_SIZE))): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=60, step=1, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_INTERVAL, default=int(o.get(CONF_INTERVAL, DEFAULT_INTERVAL))): selector.NumberSelector(
                selector.NumberSelectorConfig(min=MIN_INTERVAL, max=MAX_INTERVAL, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)),
            vol.Optional(CONF_SHORTLIST_URL, description={"suggested_value": o.get(CONF_SHORTLIST_URL, "")}): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Optional(CONF_SHORTLIST_TOKEN, description={"suggested_value": o.get(CONF_SHORTLIST_TOKEN, "")}): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
