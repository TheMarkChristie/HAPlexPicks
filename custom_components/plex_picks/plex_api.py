"""A small Plex Media Server client over aiohttp: the handful of reads Plex Picks needs, JSON in, dicts out.

Only reads. The token goes in a header and is never logged. Everything returns the MediaContainer dict (or raises
PlexError with the HTTP status), so the coordinator stays short.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CLIENT_ID = "home-assistant-plex-picks"
TIMEOUT = aiohttp.ClientTimeout(total=45)


class PlexError(Exception):
    def __init__(self, status: int, path: str, detail: str = "") -> None:
        super().__init__(f"Plex {status} on {path} {detail}".strip())
        self.status = status
        self.path = path


class PlexAuthError(PlexError):
    """401 or 403: the token is not accepted."""


class PlexClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._token = token

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Plex-Token": self._token,
            "Accept": "application/json",
            "X-Plex-Client-Identifier": CLIENT_ID,
            "X-Plex-Product": "Home Assistant Plex Picks",
            "X-Plex-Version": "0.1",
            "X-Plex-Platform": "Home Assistant",
            "X-Plex-Device-Name": "Plex Picks",
        }

    async def _request(self, path: str, params: dict[str, Any] | None = None, accept_json: bool = True) -> aiohttp.ClientResponse:
        headers = dict(self.headers)
        if not accept_json:
            headers.pop("Accept", None)
        try:
            resp = await self._session.get(self.url + path, params=params, headers=headers, timeout=TIMEOUT)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PlexError(0, path, str(err)) from err
        if resp.status in (401, 403):
            resp.release()
            raise PlexAuthError(resp.status, path)
        if resp.status >= 400:
            resp.release()
            raise PlexError(resp.status, path)
        return resp

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        resp = await self._request(path, params or None)
        try:
            data = await resp.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError) as err:
            raise PlexError(resp.status, path, "not JSON") from err
        finally:
            resp.release()
        return data.get("MediaContainer", {}) if isinstance(data, dict) else {}

    async def paged(self, path: str, size: int = 100, limit: int | None = None, **params: Any) -> list[dict[str, Any]]:
        """Every Metadata entry of a listing, page by page."""
        items: list[dict[str, Any]] = []
        start = 0
        while True:
            page = await self.get(path, **params, **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": size})
            batch = page.get("Metadata", []) or []
            items.extend(batch)
            total = int(page.get("totalSize") or page.get("size") or 0)
            if not batch or len(items) >= total or (limit and len(items) >= limit):
                break
            start += size
        return items[:limit] if limit else items

    # ---- the reads ----

    async def identity(self) -> dict[str, Any]:
        """Unauthenticated: machineIdentifier and version. Proves the server is reachable."""
        return await self.get("/identity")

    async def root(self) -> dict[str, Any]:
        """Authenticated: friendlyName, myPlexUsername and the owner features. Proves the token."""
        return await self.get("/")

    async def sections(self) -> list[dict[str, Any]]:
        return (await self.get("/library/sections")).get("Directory", []) or []

    async def labels(self, section_key: str) -> list[dict[str, Any]]:
        """The labels in use in one library, on its items and on its collections (type 18):
        [{key: '12345', title: 'shortlist'}, ...]. Shortlist labels collections, so both listings are asked."""
        seen: dict[str, dict[str, Any]] = {}
        for params in ({}, {"type": 18}):
            try:
                for lab in (await self.get(f"/library/sections/{section_key}/label", **params)).get("Directory", []) or []:
                    seen.setdefault(str(lab.get("key")), lab)
            except PlexError as err:
                _LOGGER.debug("label listing %s for section %s failed: %s", params, section_key, err)
        return list(seen.values())

    async def collections(self, section_key: str, label_id: str | None = None, size: int = 50) -> list[dict[str, Any]]:
        """Collections in one library, optionally only those carrying a label (by label id, as python-plexapi does:
        /library/sections/<key>/all?type=18&label=<id>)."""
        params: dict[str, Any] = {"type": 18}
        if label_id is not None:
            params["label"] = label_id
        return await self.paged(f"/library/sections/{section_key}/all", size=size, **params)

    async def collection(self, rating_key: str) -> dict[str, Any]:
        md = (await self.get(f"/library/collections/{rating_key}")).get("Metadata", []) or []
        return md[0] if md else {}

    async def children(self, rating_key: str, limit: int = 40) -> list[dict[str, Any]]:
        return await self.paged(f"/library/collections/{rating_key}/children", size=min(limit, 50), limit=limit)

    async def continue_watching(self, limit: int = 20) -> list[dict[str, Any]]:
        """The owner's Continue Watching hub; On Deck when the hub is empty or missing."""
        try:
            hubs = (await self.get("/hubs/continueWatching", **{"X-Plex-Container-Size": limit})).get("Hub", []) or []
            for hub in hubs:
                if hub.get("Metadata"):
                    return list(hub["Metadata"])[:limit]
        except PlexError as err:
            _LOGGER.debug("continueWatching hub unavailable (%s); using On Deck", err)
        return (await self.get("/library/onDeck", **{"X-Plex-Container-Size": limit})).get("Metadata", []) or []

    async def recently_added(self, limit: int = 40) -> list[dict[str, Any]]:
        return (await self.get("/library/recentlyAdded", **{"X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit})).get("Metadata", []) or []

    async def image(self, rating_key: str, kind: str, width: int, height: int) -> tuple[bytes, str]:
        """A transcoded poster (thumb) or backdrop (art) for one item."""
        path = f"/library/metadata/{rating_key}/{'thumb' if kind == 'thumb' else 'art'}"
        resp = await self._request("/photo/:/transcode", {"url": path, "width": width, "height": height, "minSize": 1, "upscale": 1}, accept_json=False)
        try:
            body = await resp.read()
            return body, resp.headers.get("Content-Type", "image/jpeg")
        finally:
            resp.release()
