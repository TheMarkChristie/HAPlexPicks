"""The image proxy: /api/plex_picks/img/<thumb|art>/<rating_key>/<WxH>.

Requires a signed-in Home Assistant user (or a signed path from the websocket payload), fetches the transcoded
image from Plex with the server-side token, and lets the browser cache it for a day.

Plex is asked gently: at most four image fetches at a time, and every image is kept in memory for a day (up to
600 of them, about 5 MB of posters), so a dashboard opening fifty posters does not become fifty Plex requests on
every visit. Signed URLs change per websocket read, so browser caching alone would not do this."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import IMAGE_KINDS, IMAGE_URL
from .plex_api import PlexError

_LOGGER = logging.getLogger(__name__)
_SIZE = re.compile(r"^(\d{2,4})x(\d{2,4})$")
CACHE_MAX = 600
CACHE_TTL = 86400
CONCURRENT_FETCHES = 4


class _ImageCache:
    def __init__(self) -> None:
        self._items: OrderedDict[tuple, tuple[float, bytes, str]] = OrderedDict()

    def get(self, key: tuple) -> tuple[bytes, str] | None:
        hit = self._items.get(key)
        if hit is None:
            return None
        ts, body, ctype = hit
        if time.monotonic() - ts > CACHE_TTL:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return body, ctype

    def put(self, key: tuple, body: bytes, ctype: str) -> None:
        self._items[key] = (time.monotonic(), body, ctype)
        self._items.move_to_end(key)
        while len(self._items) > CACHE_MAX:
            self._items.popitem(last=False)


class PlexPicksImageView(HomeAssistantView):
    url = IMAGE_URL + "/{kind}/{rating_key}/{size}"
    name = "api:plex_picks:img"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cache = _ImageCache()
        self._sem = asyncio.Semaphore(CONCURRENT_FETCHES)

    async def get(self, request: web.Request, kind: str, rating_key: str, size: str) -> web.Response:
        from . import coordinator_for  # noqa: PLC0415

        if kind not in IMAGE_KINDS or not rating_key.isdigit():
            return web.Response(status=400, text="bad request")
        m = _SIZE.match(size)
        if not m:
            return web.Response(status=400, text="bad size")
        width, height = min(int(m.group(1)), 2000), min(int(m.group(2)), 2000)
        etag = f'"{kind}-{rating_key}-{width}x{height}"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers={"ETag": etag, "Cache-Control": "private, max-age=86400"})

        key = (kind, rating_key, width, height)
        hit = self._cache.get(key)
        if hit is None:
            coordinator = coordinator_for(self.hass)
            if coordinator is None:
                return web.Response(status=503, text="Plex Picks is not set up")
            async with self._sem:
                hit = self._cache.get(key)  # another request may have fetched it while we waited
                if hit is None:
                    try:
                        body, content_type = await coordinator.client.image(rating_key, kind, width, height)
                    except PlexError as err:
                        _LOGGER.debug("Image %s/%s failed: %s", kind, rating_key, err)
                        return web.Response(status=502 if err.status else 504, text="Plex did not answer",
                                            headers={"Cache-Control": "no-store"})
                    content_type = content_type.split(";")[0].strip() or "image/jpeg"
                    self._cache.put(key, body, content_type)
                    hit = (body, content_type)
        body, content_type = hit
        return web.Response(body=body, content_type=content_type,
                            headers={"Cache-Control": "private, max-age=86400", "ETag": etag})
