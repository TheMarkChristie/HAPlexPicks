"""Services: plex_picks.refresh (read Plex again now) and plex_picks.play (play a title on a TV).

Play works through the core Plex integration: a title is handed to media_player.play_media as plex://<server>/<key>.
A Plex client (the Plex app on a TV, or a Cast device) takes that directly. A Samsung or Android TV entity is first
told to open Plex, the Plex integration is asked to scan for clients, and the Plex client that appears for that TV
is the one that plays."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er

from .const import (
    ATTR_MEDIA_TYPE, ATTR_PLAYER, ATTR_RATING_KEY, ATTR_RESUME, CONF_PLEX_PLAYER_FOR, DOMAIN, PLAY_WAIT_SECONDS,
    PLEX_SCHEME, SERVICE_PLAY, SERVICE_REFRESH,
)

_LOGGER = logging.getLogger(__name__)

PLAY_SCHEMA = vol.Schema({
    vol.Required(ATTR_RATING_KEY): cv.string,
    vol.Required(ATTR_PLAYER): cv.entity_id,
    vol.Optional(ATTR_MEDIA_TYPE, default="movie"): cv.string,
    vol.Optional(ATTR_RESUME, default=False): cv.boolean,
})


@callback
def async_register_services(hass: HomeAssistant) -> None:
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    hass.services.async_register(DOMAIN, SERVICE_PLAY, _play, schema=PLAY_SCHEMA)


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    for name in (SERVICE_REFRESH, SERVICE_PLAY):
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)


async def _refresh(call: ServiceCall) -> None:
    from . import coordinators  # noqa: PLC0415

    for coordinator in coordinators(call.hass):
        await coordinator.async_refresh_now()


def _mapping(text: str) -> dict[str, str]:
    """'media_player.tv = media_player.plex_tv' lines -> {tv: plex client}."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line:
            a, b = (p.strip() for p in line.split("=", 1))
            if a and b:
                out[a] = b
    return out


def _platform(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    return entry.platform if entry else None


def _plex_players(hass: HomeAssistant) -> list[str]:
    reg = er.async_get(hass)
    return [e.entity_id for e in reg.entities.values() if e.platform == "plex" and e.domain == "media_player"]


def _plex_scan_buttons(hass: HomeAssistant) -> list[str]:
    reg = er.async_get(hass)
    return [e.entity_id for e in reg.entities.values() if e.platform == "plex" and e.domain == "button"]


def _available(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    return bool(state) and state.state not in ("unavailable", "unknown")


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if len(t) > 2}


async def _launch_plex_on(hass: HomeAssistant, player: str, platform: str | None) -> None:
    """Ask a TV to open its Plex app. Samsung: select the Plex source. Android TV: start the Plex activity."""
    state = hass.states.get(player)
    if platform == "samsungtv":
        if not state or state.state == "off":
            await hass.services.async_call("media_player", "turn_on", {"entity_id": player}, blocking=True)
            await asyncio.sleep(4)
            state = hass.states.get(player)
        sources = (state.attributes.get("source_list") if state else None) or []
        plex_source = next((s for s in sources if "plex" in str(s).lower()), None)
        if plex_source and (state is None or str(state.attributes.get("source", "")).lower() != str(plex_source).lower()):
            await hass.services.async_call("media_player", "select_source", {"entity_id": player, "source": plex_source}, blocking=True)
        return
    if platform == "androidtv_remote":
        ent = er.async_get(hass).async_get(player)
        remote = None
        if ent and ent.device_id:
            remote = next((e.entity_id for e in er.async_entries_for_device(er.async_get(hass), ent.device_id) if e.domain == "remote"), None)
        if remote:
            await hass.services.async_call("remote", "turn_on", {"entity_id": remote, "activity": "com.plexapp.android"}, blocking=True)
        return
    if state and state.state == "off":
        await hass.services.async_call("media_player", "turn_on", {"entity_id": player}, blocking=False)


async def _find_plex_client(hass: HomeAssistant, player: str, preferred: str | None) -> str:
    """The Plex client entity for a TV: the configured one, else the one that appears after a scan."""
    before = {e for e in _plex_players(hass) if _available(hass, e)}
    deadline = asyncio.get_running_loop().time() + PLAY_WAIT_SECONDS
    tv_state = hass.states.get(player)
    tv_words = _tokens(tv_state.name if tv_state else player)
    scanned_at = 0.0
    while True:
        if preferred and _available(hass, preferred):
            return preferred
        if not preferred:
            candidates = [e for e in _plex_players(hass) if _available(hass, e)]
            named = [e for e in candidates if tv_words & _tokens(hass.states.get(e).name)]
            appeared = [e for e in candidates if e not in before]
            for pool in (named, appeared):
                if pool:
                    return pool[0]
        now = asyncio.get_running_loop().time()
        if now >= deadline:
            break
        if now - scanned_at > 8:
            for button in _plex_scan_buttons(hass):
                await hass.services.async_call("button", "press", {"entity_id": button}, blocking=False)
            scanned_at = now
        await asyncio.sleep(2)
    raise HomeAssistantError(f"No Plex client appeared for {player} within {PLAY_WAIT_SECONDS} s. Is the Plex app open on it, and is the Plex integration set up?")


async def _play(call: ServiceCall) -> None:
    from . import coordinator_for  # noqa: PLC0415

    hass = call.hass
    coordinator = coordinator_for(hass)
    if coordinator is None:
        raise HomeAssistantError("Plex Picks is not set up")
    rating_key = str(call.data[ATTR_RATING_KEY])
    player = str(call.data[ATTR_PLAYER])
    media_type = str(call.data.get(ATTR_MEDIA_TYPE) or "movie")
    resume = bool(call.data.get(ATTR_RESUME))
    server_id = coordinator.server.get("id")
    if not server_id:
        raise HomeAssistantError("The Plex server id is unknown; set the integration up again")

    platform = _platform(hass, player)
    if platform in ("plex", "cast"):
        target = player
    else:
        preferred = _mapping(str(coordinator.options.get(CONF_PLEX_PLAYER_FOR, ""))).get(player)
        await _launch_plex_on(hass, player, platform)
        target = await _find_plex_client(hass, player, preferred)

    content_id = f"{PLEX_SCHEME}{server_id}/{rating_key}" + ("?resume=1" if resume else "")
    _LOGGER.debug("Playing %s on %s (%s)", content_id, target, media_type)
    await hass.services.async_call("media_player", "play_media", {
        "entity_id": target, "media_content_id": content_id, "media_content_type": media_type,
    }, blocking=True)
