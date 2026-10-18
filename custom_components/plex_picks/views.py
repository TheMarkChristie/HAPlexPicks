"""The image proxy: /api/plex_picks/img/<thumb|art>/<rating_key>/<WxH>.

Requires a signed-in Home Assistant user (or a signed path from the websocket payload), fetches the transcoded
image from Plex with the server-side token, and lets the browser cache it for a day.

Plex is asked gently: the client's limiter spaces every request, every image is kept in memory for a day (up to 600
of them or 40 MB, whichever comes first), only the three sizes the card uses are served, and after three failures in
a row Plex is left alone for two minutes (a failed image is not asked again for a minute), so a struggling server is
not hammered by every poster on the screen. Only images are passed on, with headers that stop a browser treating the
answer as anything else."""
from __future__ import annotations

import logging
import time
from collections import OrderedDict

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import IMAGE_KINDS, IMAGE_SIZES, IMAGE_URL
from .plex_api import PlexError

_LOGGER = logging.getLogger(__name__)
CACHE_MAX = 600
CACHE_MAX_BYTES = 40 * 1024 * 1024
CACHE_TTL = 86400
FAILURES_TO_PAUSE = 3
PAUSE_SECONDS = 120
FAILED_KEY_SECONDS = 60
SAFE = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"}


class _ImageCache:
    def __init__(self) -> None:
        self._items: OrderedDict[tuple, tuple[float, bytes, str]] = OrderedDict()
        self._bytes = 0

    def get(self, key: tuple) -> tuple[bytes, str] | None:
        hit = self._items.get(key)
        if hit is None:
            return None
        ts, body, ctype = hit
        if time.monotonic() - ts > CACHE_TTL:
            self._drop(key)
            return None
        self._items.move_to_end(key)
        return body, ctype

    def _drop(self, key: tuple) -> None:
        old = self._items.pop(key, None)
        if old:
            self._bytes -= len(old[1])

    def put(self, key: tuple, body: bytes, ctype: str) -> None:
        self._drop(key)
        self._items[key] = (time.monotonic(), body, ctype)
        self._bytes += len(body)
        while self._items and (len(self._items) > CACHE_MAX or self._bytes > CACHE_MAX_BYTES):
            self._drop(next(iter(self._items)))


class PlexPicksImageView(HomeAssistantView):
    url = IMAGE_URL + "/{kind}/{rating_key}/{size}"
    name = "api:plex_picks:img"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cache = _ImageCache()
        self._failures = 0
        self._paused_until = 0.0
        self._failed: dict[tuple, float] = {}

    def _refuse(self, status: int, text: str, retry: int | None = None) -> web.Response:
        headers = {"Cache-Control": "no-store", **SAFE}
        if retry:
            headers["Retry-After"] = str(retry)
        return web.Response(status=status, text=text, headers=headers)

    async def get(self, request: web.Request, kind: str, rating_key: str, size: str) -> web.Response:
        from . import coordinator_for  # noqa: PLC0415

        if kind not in IMAGE_KINDS or not rating_key.isdigit() or size not in IMAGE_SIZES:
            return self._refuse(400, "bad request")
        width, height = (int(x) for x in size.split("x"))
        etag = f'"{kind}-{rating_key}-{size}"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers={"ETag": etag, "Cache-Control": "private, max-age=86400", **SAFE})

        key = (kind, rating_key, width, height)
        hit = self._cache.get(key)
        if hit is None:
            coordinator = coordinator_for(self.hass)
            if coordinator is None:
                return self._refuse(503, "Plex Picks is not set up")
            now = time.monotonic()
            if now < self._paused_until:
                return self._refuse(503, "Plex is not answering; trying again shortly", PAUSE_SECONDS)
            if now - self._failed.get(key, -1e9) < FAILED_KEY_SECONDS:
                return self._refuse(502, "Plex did not answer for this image")
            try:
                body, content_type = await coordinator.client.image(rating_key, kind, width, height)
            except PlexError as err:
                _LOGGER.debug("Image %s/%s failed: %s", kind, rating_key, err)
                self._failures += 1
                self._failed[key] = time.monotonic()
                if len(self._failed) > 500:
                    self._failed = {k: t for k, t in self._failed.items() if time.monotonic() - t < FAILED_KEY_SECONDS}
                if self._failures >= FAILURES_TO_PAUSE:
                    self._paused_until = time.monotonic() + PAUSE_SECONDS
                    self._failures = 0
                return self._refuse(502 if err.status else 504, "Plex did not answer")
            self._failures = 0
            self._failed.pop(key, None)
            self._cache.put(key, body, content_type)
            hit = (body, content_type)
        body, content_type = hit
        return web.Response(body=body, content_type=content_type,
                            headers={"Cache-Control": "private, max-age=86400", "ETag": etag, **SAFE})
