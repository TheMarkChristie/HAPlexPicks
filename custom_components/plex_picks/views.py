"""The image proxy: /api/plex_picks/img/<thumb|art>/<rating_key>/<WxH>.

Requires a signed-in Home Assistant user (or a signed path from the websocket payload), fetches the transcoded
image from Plex with the server-side token, and lets the browser cache it for a day."""
from __future__ import annotations

import logging
import re

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import IMAGE_KINDS, IMAGE_URL
from .plex_api import PlexError

_LOGGER = logging.getLogger(__name__)
_SIZE = re.compile(r"^(\d{2,4})x(\d{2,4})$")


class PlexPicksImageView(HomeAssistantView):
    url = IMAGE_URL + "/{kind}/{rating_key}/{size}"
    name = "api:plex_picks:img"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, kind: str, rating_key: str, size: str) -> web.Response:
        from . import coordinator_for  # noqa: PLC0415

        if kind not in IMAGE_KINDS or not rating_key.isdigit():
            return web.Response(status=400, text="bad request")
        m = _SIZE.match(size)
        if not m:
            return web.Response(status=400, text="bad size")
        width, height = min(int(m.group(1)), 2000), min(int(m.group(2)), 2000)
        coordinator = coordinator_for(self.hass)
        if coordinator is None:
            return web.Response(status=503, text="Plex Picks is not set up")
        try:
            body, content_type = await coordinator.client.image(rating_key, kind, width, height)
        except PlexError as err:
            _LOGGER.debug("Image %s/%s failed: %s", kind, rating_key, err)
            return web.Response(status=502 if err.status else 504, text="Plex did not answer")
        return web.Response(body=body, content_type=content_type.split(";")[0] or "image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})
