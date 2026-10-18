"""The websocket commands the card uses:

  plex_picks/data       the rows it shows, with signed image URLs, plus the TVs it can play on
  plex_picks/subscribe  a message whenever the rows change (any signed-in user; subscribe_events would refuse a
                        custom event to non-admins)

Image URLs are signed with the asking user's refresh token, so a poster URL only works for a signed-in browser and the
Plex token stays on the server. A signed URL is reused until an hour before it expires, so repeated reads give the
browser the same address and its cache works."""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import BACKDROP, CONF_PLAYERS, DOMAIN, EVENT_UPDATED, IMAGE_URL, POSTER, SIGNED_URL_HOURS, WS_DATA, WS_SHORTLIST, WS_SUBSCRIBE

_SIGNED = f"{DOMAIN}_signed_urls"
_SIGNED_MAX = 8000


def _sign(hass: HomeAssistant, token_id: str | None, kind: str, key: str | None, size: str) -> str | None:
    if not key:
        return None
    path = f"{IMAGE_URL}/{kind}/{key}/{size}"
    memo: dict[tuple[str | None, str], tuple[float, str]] = hass.data.setdefault(_SIGNED, {})
    now = time.time()
    hit = memo.get((token_id, path))
    if hit and hit[0] - now > 3600:
        return hit[1]
    if len(memo) > _SIGNED_MAX:
        for k in [k for k, v in memo.items() if v[0] - now <= 3600]:
            memo.pop(k, None)
    url = async_sign_path(hass, path, timedelta(hours=SIGNED_URL_HOURS), refresh_token_id=token_id)
    memo[(token_id, path)] = (now + SIGNED_URL_HOURS * 3600, url)
    return url


def _device_name(hass: HomeAssistant, entity_id: str) -> str:
    entry = er.async_get(hass).async_get(entity_id)
    if entry and entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
        return device.name_by_user or device.name or entity_id
    state = hass.states.get(entity_id)
    return state.name if state else entity_id


def players(hass: HomeAssistant, configured: list[str] | None) -> list[dict[str, Any]]:
    """The TVs offered on the Play button: the options' list, else the house's Google Cast devices (Mark, 18 Oct 2026:
    "Google chromecasts should be the default options for casting or apps"); the card asks which one every time."""
    ids = list(configured or [])
    if not ids:
        ids = [e.entity_id for e in er.async_get(hass).entities.values()
               if e.platform == "cast" and e.domain == "media_player" and not e.disabled_by]
    out = []
    for entity_id in ids:
        state = hass.states.get(entity_id)
        out.append({"entity_id": entity_id, "name": _device_name(hass, entity_id),
                    "state": state.state if state else "unknown",
                    "available": bool(state) and state.state not in ("unavailable", "unknown")})
    return sorted(out, key=lambda p: p["name"].casefold()) if not configured else out


def _wanted(row: dict[str, Any], kinds: set[str], slug: str | None, libraries: set[str]) -> bool:
    if kinds and row.get("kind") not in kinds and row.get("id") not in kinds:
        return False
    if slug and row.get("slug") and row.get("slug") != slug:
        return False
    if libraries and row.get("library") and str(row.get("library")).lower() not in libraries:
        return False
    return bool(row.get("items"))


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_data)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_shortlist)


@websocket_api.websocket_command({
    vol.Required("type"): WS_SHORTLIST,
    vol.Optional("method", default="GET"): vol.In(["GET", "POST", "PATCH", "PUT", "DELETE"]),
    vol.Required("path"): str,
    vol.Optional("body"): dict,
    vol.Optional("params"): dict,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_shortlist(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Relay one request to Shortlist's API with the configured owner token: admins only, /api/... paths only.
    Shortlist lives on the home network, so this is how its rows, settings and runs are managed from outside."""
    from . import coordinator_for  # noqa: PLC0415

    coordinator = coordinator_for(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_ready", "Plex Picks is not set up")
        return
    path = str(msg["path"])
    if not path.startswith("/api/") or ".." in path:
        connection.send_error(msg["id"], "bad_path", "Only Shortlist /api/ paths are relayed")
        return
    result = await coordinator.shortlist_request(msg.get("method", "GET"), path, body=msg.get("body"), params=msg.get("params"))
    if result.get("status", 0) >= 400 or result.get("status", 0) == 0:
        connection.send_error(msg["id"], "shortlist_error", f"Shortlist {result.get('status')}: {str(result.get('body'))[:300]}")
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({
    vol.Required("type"): WS_DATA,
    vol.Optional("entry_id"): str,
    vol.Optional("art"): bool,
    vol.Optional("rows"): [str],             # the card's rows filter (kinds or row ids); its unlisted Shortlist rows
    vol.Optional("slug"): str,               #   come back too when it asks for "shortlist"
    vol.Optional("libraries"): [str],
    vol.Optional("max_items"): vol.All(int, vol.Range(min=1, max=200)),
})
@websocket_api.async_response
async def ws_data(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    from . import coordinator_for  # noqa: PLC0415  (avoid an import cycle at module load)

    coordinator = coordinator_for(hass, msg.get("entry_id"))
    if coordinator is None or not coordinator.data:
        connection.send_error(msg["id"], "not_ready", "Plex Picks has no data yet")
        return
    want_art = bool(msg.get("art", True))
    kinds = set(msg.get("rows") or [])
    libraries = {str(x).lower() for x in msg.get("libraries") or []}
    slug = msg.get("slug")
    limit = msg.get("max_items")
    token_id = connection.refresh_token_id
    source = coordinator.data.get("payload", {})
    rows = []
    for row in source.get("rows", []):
        if not _wanted(row, kinds, slug, libraries):
            continue
        items = []
        for item in row.get("items", [])[:limit] if limit else row.get("items", []):
            out = dict(item)
            out["thumb"] = _sign(hass, token_id, "thumb", item.get("thumb_key"), POSTER)
            out["art"] = _sign(hass, token_id, "art", item.get("art_key"), BACKDROP) if want_art else None
            items.append(out)
        rows.append({**{k: v for k, v in row.items() if k != "items"}, "items": items})
    connection.send_result(msg["id"], {
        "payload": {**{k: v for k, v in source.items() if k != "rows"}, "rows": rows},
        "players": players(hass, coordinator.options.get(CONF_PLAYERS)),
        "server": coordinator.server,
        "shortlist": coordinator.shortlist,
        "generated_at": coordinator.data.get("generated_at"),
        "version": coordinator.data.get("version"),
        "entry_id": coordinator.entry.entry_id,
    })


@websocket_api.websocket_command({vol.Required("type"): WS_SUBSCRIBE})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    @callback
    def forward(event: Event) -> None:
        connection.send_message(websocket_api.event_message(
            msg["id"], {"version": event.data.get("version"), "generated_at": event.data.get("generated_at")}))

    connection.subscriptions[msg["id"]] = hass.bus.async_listen(EVENT_UPDATED, forward)
    connection.send_result(msg["id"])
