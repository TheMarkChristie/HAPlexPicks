"""A small Plex Media Server client over aiohttp: the handful of calls Plex Picks needs, JSON in, dicts out.

Reads, plus the few writes the To watch rows need (one collection per library, its label, its place on Home). The
token goes in a header and is never logged. Everything returns the MediaContainer dict (or raises PlexError with the
HTTP status), so the coordinator stays short. PlexDiscover is the owner's Watchlist on plex.tv.

Every request, the coordinator's reads and the image proxy's alike, goes through one limiter: one at a time, a third
of a second apart. Mark's server sits behind a provider's proxy that answered 504 for two hours after a burst of about
110 requests (18 Oct 2026), so Plex is asked gently.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from .const import DISCOVER_URL, IMAGE_TIMEOUT_SECONDS, PLEX_GAP_SECONDS, PLEX_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

CLIENT_ID = "home-assistant-plex-picks"
TIMEOUT = aiohttp.ClientTimeout(total=PLEX_TIMEOUT_SECONDS)
IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=IMAGE_TIMEOUT_SECONDS)


class PlexError(Exception):
    def __init__(self, status: int, path: str, detail: str = "") -> None:
        super().__init__(f"Plex {status} on {path} {detail}".strip())
        self.status = status
        self.path = path

    @property
    def unsupported(self) -> bool:
        """The server does not offer this listing (as opposed to failing to answer)."""
        return self.status in (400, 404)


class PlexAuthError(PlexError):
    """401 or 403: the token is not accepted."""


class PlexClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._token = token
        self._gate = asyncio.Lock()
        self._next_at = 0.0

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Plex-Token": self._token,
            "Accept": "application/json",
            "X-Plex-Client-Identifier": CLIENT_ID,
            "X-Plex-Product": "Home Assistant Plex Picks",
            "X-Plex-Version": "0.2",
            "X-Plex-Platform": "Home Assistant",
            "X-Plex-Device-Name": "Plex Picks",
        }

    async def _request(self, path: str, params: dict[str, Any] | None = None, accept_json: bool = True,
                       timeout: aiohttp.ClientTimeout = TIMEOUT, method: str = "GET") -> aiohttp.ClientResponse:
        headers = dict(self.headers)
        if not accept_json:
            headers.pop("Accept", None)
        loop = asyncio.get_running_loop()
        async with self._gate:                       # one request at a time, PLEX_GAP_SECONDS apart
            wait = self._next_at - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await self._session.request(method, self.url + path, params=params, headers=headers, timeout=timeout)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise PlexError(0, path, str(err)) from err
            finally:
                self._next_at = loop.time() + PLEX_GAP_SECONDS
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
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PlexError(0, path, str(err)) from err
        finally:
            resp.release()
        return data.get("MediaContainer", {}) if isinstance(data, dict) else {}

    async def send(self, method: str, path: str, **params: Any) -> dict[str, Any]:
        """A write (POST, PUT, DELETE). Plex answers some writes with an empty body or XML, which is not an error:
        the MediaContainer comes back when there is JSON, else {}."""
        resp = await self._request(path, params or None, method=method)
        try:
            text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PlexError(0, path, str(err)) from err
        finally:
            resp.release()
        try:
            data = json.loads(text) if text and text.lstrip().startswith("{") else {}
        except ValueError:
            data = {}
        return data.get("MediaContainer", {}) if isinstance(data, dict) else {}

    async def paged(self, path: str, size: int = 100, limit: int | None = None, **params: Any) -> list[dict[str, Any]]:
        """Every Metadata entry of a listing, page by page."""
        items: list[dict[str, Any]] = []
        start = 0
        while True:
            page = await self.get(path, **params, **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": size})
            batch = page.get("Metadata", []) or []
            items.extend(batch)
            if not batch or (limit and len(items) >= limit):
                break
            total = page.get("totalSize")
            if total is not None:
                if len(items) >= int(total):
                    break
            elif len(batch) < size:
                # no total from the server: a short page is the last one
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
        [{key: '12345', title: 'shortlist'}, ...]. Shortlist labels collections, so both listings are asked.
        A listing the server does not offer (400, 404) is skipped; a server that does not answer (a timeout, 429, 5xx)
        raises, so the caller keeps what it knew instead of concluding the library has no Shortlist rows."""
        seen: dict[str, dict[str, Any]] = {}
        for params in ({}, {"type": 18}):
            try:
                for lab in (await self.get(f"/library/sections/{section_key}/label", **params)).get("Directory", []) or []:
                    seen.setdefault(str(lab.get("key")), lab)
            except PlexError as err:
                if not err.unsupported:
                    raise
                _LOGGER.debug("label listing %s for section %s not offered: %s", params, section_key, err)
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
        """The owner's Continue Watching hub; On Deck only when the server has no such hub (a failure raises)."""
        try:
            hubs = (await self.get("/hubs/continueWatching", **{"X-Plex-Container-Size": limit})).get("Hub", []) or []
            for hub in hubs:
                if hub.get("Metadata"):
                    return list(hub["Metadata"])[:limit]
            if hubs:
                return []
        except PlexError as err:
            if not err.unsupported:
                raise
            _LOGGER.debug("continueWatching hub not offered (%s); using On Deck", err)
        return (await self.get("/library/onDeck", **{"X-Plex-Container-Size": limit})).get("Metadata", []) or []

    async def recently_added(self, limit: int = 40) -> list[dict[str, Any]]:
        return (await self.get("/library/recentlyAdded", **{"X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit})).get("Metadata", []) or []

    async def image(self, rating_key: str, kind: str, width: int, height: int) -> tuple[bytes, str]:
        """A transcoded poster (thumb) or backdrop (art) for one item. Anything but an image is refused."""
        path = f"/library/metadata/{rating_key}/{'thumb' if kind == 'thumb' else 'art'}"
        resp = await self._request("/photo/:/transcode", {"url": path, "width": width, "height": height, "minSize": 1, "upscale": 1},
                                   accept_json=False, timeout=IMAGE_TIMEOUT)
        try:
            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                raise PlexError(502, path, f"not an image ({content_type or 'no type'})")
            return await resp.read(), content_type
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PlexError(0, path, str(err)) from err
        finally:
            resp.release()

    # ---- the To watch rows: titles by guid, one collection per library, its label and its place on Home ----

    async def by_guid(self, guid: str) -> list[dict[str, Any]]:
        """Every copy of one title on the server (a film can be in Movies and in 4K Movies), each with its library
        (librarySectionID) and the owner's watched state (viewCount, or leafCount and viewedLeafCount for a show)."""
        return (await self.get("/library/all", guid=guid)).get("Metadata", []) or []

    async def collection_labels(self, section_key: str) -> list[dict[str, Any]]:
        """The labels on one library's collections only (type 18): one request."""
        try:
            return (await self.get(f"/library/sections/{section_key}/label", type=18)).get("Directory", []) or []
        except PlexError as err:
            if not err.unsupported:
                raise
            return []

    @staticmethod
    def items_uri(machine_id: str, rating_keys: list[str]) -> str:
        return f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{','.join(rating_keys)}"

    async def create_collection(self, section_key: str, media_type: str, title: str, machine_id: str,
                                rating_keys: list[str]) -> dict[str, Any]:
        """A plain (not smart) collection in one library holding these items; returns the new collection."""
        mc = await self.send("POST", "/library/collections", type=1 if media_type == "movie" else 2, title=title,
                             smart=0, sectionId=section_key, uri=self.items_uri(machine_id, rating_keys))
        md = mc.get("Metadata") or []
        return md[0] if md else {}

    async def add_to_collection(self, collection_key: str, machine_id: str, rating_keys: list[str]) -> None:
        await self.send("PUT", f"/library/collections/{collection_key}/items", uri=self.items_uri(machine_id, rating_keys))

    async def remove_from_collection(self, collection_key: str, rating_key: str) -> None:
        await self.send("DELETE", f"/library/collections/{collection_key}/items/{rating_key}")

    async def move_in_collection(self, collection_key: str, rating_key: str, after: str | None = None) -> None:
        """Move an item in a custom-ordered collection: after another item, or to the front."""
        params = {"after": after} if after else {}
        await self.send("PUT", f"/library/collections/{collection_key}/items/{rating_key}/move", **params)

    async def custom_order(self, collection_key: str) -> None:
        """Keep the collection in the order its items are put in (collectionSort 2), not by release date."""
        await self.send("PUT", f"/library/collections/{collection_key}/prefs", collectionSort=2)

    async def set_labels(self, section_key: str, collection_key: str, labels: list[str]) -> None:
        """Set a collection's labels (replacing any) and lock them."""
        params: dict[str, Any] = {"type": 18, "id": collection_key, "label.locked": 1}
        for i, label in enumerate(labels):
            params[f"label[{i}].tag.tag"] = label
        await self.send("PUT", f"/library/sections/{section_key}/all", **params)

    async def delete_metadata(self, rating_key: str) -> None:
        await self.send("DELETE", f"/library/metadata/{rating_key}")

    async def shelves(self, section_key: str) -> list[dict[str, Any]]:
        """The library's shelf management list, in its order (identifier, title, promotedTo... flags)."""
        return (await self.get(f"/hubs/sections/{section_key}/manage")).get("Hub", []) or []

    async def managed_hub(self, section_key: str, collection_key: str) -> dict[str, Any] | None:
        """The collection's entry in the library's shelf management (Recommended, Home, friends' Home), if it has one."""
        hubs = (await self.get(f"/hubs/sections/{section_key}/manage", metadataItemId=collection_key)).get("Hub", []) or []
        return hubs[0] if hubs else None

    async def show_on_home(self, section_key: str, collection_key: str, recommended: bool, own_home: bool,
                           shared_home: bool) -> str:
        """Where the collection shows: the library's Recommended, the owner's Home, friends' Home. Returns its shelf
        identifier (custom.collection.<library>.<collection>)."""
        flags = {"promotedToRecommended": int(recommended), "promotedToOwnHome": int(own_home),
                 "promotedToSharedHome": int(shared_home)}
        hub = await self.managed_hub(section_key, collection_key)
        identifier = str((hub or {}).get("identifier") or f"custom.collection.{section_key}.{collection_key}")
        if hub:
            await self.send("PUT", f"/hubs/sections/{section_key}/manage/{identifier}", **flags)
        else:
            await self.send("POST", f"/hubs/sections/{section_key}/manage", metadataItemId=collection_key, **flags)
        return identifier

    async def move_shelf(self, section_key: str, identifier: str, after: str | None = None) -> None:
        """Move a shelf in the library's management list: after another shelf, or to the top."""
        params = {"after": after} if after else {}
        await self.send("PUT", f"/hubs/sections/{section_key}/manage/{identifier}/move", **params)


class PlexDiscover:
    """The owner's Watchlist, which plex.tv keeps (not the server): read it, add to it, take off it."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Plex-Token": self._token, "Accept": "application/json", "X-Plex-Client-Identifier": CLIENT_ID,
                "X-Plex-Product": "Home Assistant Plex Picks"}

    async def _call(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session.request(method, DISCOVER_URL + path, params=params, headers=self.headers,
                                             timeout=TIMEOUT) as resp:
                if resp.status in (401, 403):
                    raise PlexAuthError(resp.status, path)
                if resp.status >= 400:
                    raise PlexError(resp.status, path)
                text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PlexError(0, path, str(err)) from err
        try:
            data = json.loads(text) if text and text.lstrip().startswith("{") else {}
        except ValueError:
            data = {}
        return data.get("MediaContainer", {}) if isinstance(data, dict) else {}

    async def watchlist(self, size: int = 100) -> list[dict[str, Any]]:
        """Every title on the Watchlist, the most recently added first: guid (plex://movie/<id>), type, title, year."""
        items: list[dict[str, Any]] = []
        start = 0
        while True:
            page = await self._call("GET", "/library/sections/watchlist/all", {
                "sort": "watchlistedAt:desc", "X-Plex-Container-Start": start, "X-Plex-Container-Size": size})
            batch = page.get("Metadata", []) or []
            items.extend(batch)
            total = page.get("totalSize")
            if not batch or (total is not None and len(items) >= int(total)) or (total is None and len(batch) < size):
                return items
            start += len(batch)

    async def change(self, discover_key: str, add: bool) -> None:
        """Put a title on the Watchlist or take it off, by its plex.tv key (the last part of its plex:// guid)."""
        await self._call("PUT", f"/actions/{'addToWatchlist' if add else 'removeFromWatchlist'}", {"ratingKey": discover_key})
