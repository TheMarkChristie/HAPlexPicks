"""Reads the Shortlist rows (and the owner's Continue Watching and what is new) from Plex on a timer."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CONTINUE, CONF_INTERVAL, CONF_RECENT, CONF_RECENT_SIZE, CONF_ROW_SIZE, CONF_SERVER_ID, CONF_SERVER_NAME,
    CONF_SHORTLIST_TOKEN, CONF_SHORTLIST_URL, CONF_TOKEN, CONF_URL, DEFAULT_INTERVAL, DEFAULT_RECENT_SIZE,
    DEFAULT_ROW_SIZE, DOMAIN, EVENT_UPDATED, LABEL_PLAIN, NAME,
)
from .model import Row, continue_row, is_shortlist_collection, payload, recent_row, row_from_collection, sort_rows
from .plex_api import PlexAuthError, PlexClient, PlexError

_LOGGER = logging.getLogger(__name__)


class PlexPicksCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = int(entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL))
        super().__init__(hass, _LOGGER, name=NAME, config_entry=entry, update_interval=timedelta(minutes=minutes))
        self.entry = entry
        self.client = PlexClient(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
        self.server: dict[str, Any] = {
            "id": entry.data.get(CONF_SERVER_ID),
            "name": entry.data.get(CONF_SERVER_NAME),
            "url": entry.data[CONF_URL],
        }
        self.rows: list[Row] = []
        self.shortlist: dict[str, Any] = {}

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.entry.options)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            rows = await self._read_rows()
        except PlexAuthError as err:
            raise ConfigEntryAuthFailed("Plex rejected the token") from err
        except PlexError as err:
            raise UpdateFailed(str(err)) from err
        self.rows = rows
        self.shortlist = await self._read_shortlist()
        now = int(time.time())
        data = {"payload": payload(self.server, rows, now), "shortlist": self.shortlist, "generated_at": now}
        self.hass.bus.async_fire(EVENT_UPDATED, {"entry_id": self.entry.entry_id, "generated_at": now, "rows": len(rows)})
        return data

    async def _read_rows(self) -> list[Row]:
        opts = self.options
        row_size = int(opts.get(CONF_ROW_SIZE, DEFAULT_ROW_SIZE))
        rows: list[Row] = []

        if opts.get(CONF_CONTINUE, True):
            try:
                rows.append(continue_row(await self.client.continue_watching(20), None))
            except PlexError as err:
                _LOGGER.debug("Continue Watching unavailable: %s", err)

        sections = await self.client.sections()
        order = [str(s.get("title")) for s in sections]
        for section in sections:
            if section.get("type") not in ("movie", "show"):
                continue
            key, title, lib_type = str(section.get("key")), str(section.get("title")), str(section.get("type"))
            labels = await self.client.labels(key)
            plain = next((lab for lab in labels if lab.get("title") == LABEL_PLAIN), None)
            if plain is None:
                continue  # Shortlist has not written to this library
            for coll in await self.client.collections(key, label_id=str(plain.get("key"))):
                rating_key = str(coll.get("ratingKey"))
                detail = await self.client.collection(rating_key) or coll
                tags = [str(t.get("tag")) for t in detail.get("Label", []) or []]
                if not is_shortlist_collection(tags):
                    continue
                children = await self.client.children(rating_key, limit=row_size)
                rows.append(row_from_collection(detail, tags, children, title, lib_type))

        if opts.get(CONF_RECENT, True):
            try:
                size = int(opts.get(CONF_RECENT_SIZE, DEFAULT_RECENT_SIZE))
                rows.append(recent_row(await self.client.recently_added(size * 3), limit=size))
            except PlexError as err:
                _LOGGER.debug("Recently added unavailable: %s", err)

        return sort_rows(rows, order)

    async def _read_shortlist(self) -> dict[str, Any]:
        """Shortlist's latest run, when its URL and owner API token are configured. Never fatal."""
        url = str(self.options.get(CONF_SHORTLIST_URL) or "").rstrip("/")
        token = str(self.options.get(CONF_SHORTLIST_TOKEN) or "")
        if not url or not token:
            return {}
        session = async_get_clientsession(self.hass)
        for headers in ({"Authorization": f"Bearer {token}"}, {"X-API-Token": token}, {"X-Api-Key": token}):
            try:
                async with session.get(f"{url}/api/runs", params={"limit": 1}, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status in (401, 403):
                        continue
                    if resp.status >= 400:
                        return {"error": f"HTTP {resp.status}"}
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                return {"error": str(err)}
            runs = data.get("runs") if isinstance(data, dict) else data
            if isinstance(runs, dict):
                runs = runs.get("items") or runs.get("data") or []
            run = (runs or [None])[0] if isinstance(runs, list) else None
            if not isinstance(run, dict):
                return {"error": "no runs yet"}
            keep = {k: run.get(k) for k in ("id", "status", "started_at", "finished_at", "dry_run", "trigger", "stats") if k in run}
            return keep
        return {"error": "token not accepted"}

    async def async_refresh_now(self) -> None:
        await self.async_request_refresh()
