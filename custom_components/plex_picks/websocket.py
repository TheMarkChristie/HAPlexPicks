"""The websocket command the card reads: every row with its items and signed image URLs, plus the players.

Signing happens here, per request, with the asking user's refresh token, so a poster URL only works for a signed-in
browser and the Plex token stays on the server."""
from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback

from .const import CONF_PLAYERS, IMAGE_URL, SIGNED_URL_HOURS, WS_DATA

POSTER = "300x450"
BACKDROP = "1280x720"
ROW_ART = "600x338"


def _sign(hass: HomeAssistant, kind: str, key: str | None, size: str) -> str | None:
    if not key:
        return None
    return async_sign_path(hass, f"{IMAGE_URL}/{kind}/{key}/{size}", timedelta(hours=SIGNED_URL_HOURS))


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_data)


@websocket_api.websocket_command({vol.Required("type"): WS_DATA, vol.Optional("entry_id"): str, vol.Optional("art"): bool})
@websocket_api.async_response
async def ws_data(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    from . import coordinator_for  # noqa: PLC0415  (avoid an import cycle at module load)

    coordinator = coordinator_for(hass, msg.get("entry_id"))
    if coordinator is None or not coordinator.data:
        connection.send_error(msg["id"], "not_ready", "Plex Picks has no data yet")
        return
    want_art = bool(msg.get("art", True))
    payload = copy.deepcopy(coordinator.data.get("payload", {}))
    for row in payload.get("rows", []):
        row["thumb"] = _sign(hass, "thumb", row.get("thumb_key"), ROW_ART)
        for item in row.get("items", []):
            item["thumb"] = _sign(hass, "thumb", item.get("thumb_key"), POSTER)
            item["art"] = _sign(hass, "art", item.get("art_key"), BACKDROP) if want_art else None
    players = []
    for entity_id in coordinator.options.get(CONF_PLAYERS, []) or []:
        state = hass.states.get(entity_id)
        players.append({"entity_id": entity_id, "name": (state.name if state else entity_id), "state": state.state if state else "unknown"})
    connection.send_result(msg["id"], {
        "payload": payload,
        "players": players,
        "server": coordinator.server,
        "shortlist": coordinator.shortlist,
        "generated_at": coordinator.data.get("generated_at"),
        "entry_id": coordinator.entry.entry_id,
    })
