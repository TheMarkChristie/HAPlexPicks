"""Reads the Shortlist rows (and the owner's Continue Watching and what is new) from Plex on a timer."""
from __future__ import annotations

import asyncio
import hashlib
import json
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
    DEFAULT_ROW_SIZE, EVENT_UPDATED, LABEL_CACHE_HOURS, LABEL_PLAIN, NAME, READ_BUDGET_SECONDS,
)
from .model import (
    KIND_CONTINUE, KIND_RECENT, Row, apply_reasons, continue_row, is_shortlist_collection, payload, reasons_from_user_rows,
    recent_row, row_from_collection, sort_rows,
)
from .plex_api import PlexAuthError, PlexClient, PlexError

_LOGGER = logging.getLogger(__name__)


def rows_version(data: dict[str, Any]) -> str:
    """A short fingerprint of what the card draws, so it can skip a redraw when nothing changed."""
    body = json.dumps(data.get("rows", []), sort_keys=True, default=str)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


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
        self.reasons: dict[str, dict[str, Any]] = {}            # plex rating key -> why Shortlist picked it
        self._labels: dict[str, tuple[float, str | None]] = {}   # library key -> (good until, Shortlist's label id)
        self._rescan_labels = False

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.entry.options)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with asyncio.timeout(READ_BUDGET_SECONDS):
                rows = await self._read_rows()
        except PlexAuthError as err:
            raise ConfigEntryAuthFailed("Plex rejected the token") from err
        except PlexError as err:
            raise UpdateFailed(str(err)) from err
        except TimeoutError as err:
            raise UpdateFailed(f"Plex took over {READ_BUDGET_SECONDS} s to answer") from err
        self.shortlist = await self._read_shortlist()
        if self._shortlist_configured():
            self.reasons = await self._read_reasons({r.slug for r in rows if r.slug}) or self.reasons
            explained = apply_reasons(rows, self.reasons)
            self.shortlist["explained"] = explained
        self.rows = rows
        now = int(time.time())
        body = payload(self.server, rows, now)
        version = rows_version(body)
        before = (self.data or {}).get("version")
        if version != before:
            self.hass.bus.async_fire(EVENT_UPDATED, {"entry_id": self.entry.entry_id, "generated_at": now,
                                                     "version": version, "rows": len(rows)})
        return {"payload": body, "shortlist": self.shortlist, "generated_at": now, "version": version}

    def _previous(self, kind: str) -> list[Row]:
        return [r for r in self.rows if r.kind == kind]

    async def _plain_label(self, section_key: str) -> str | None:
        """Shortlist's plain label id in one library (None: Shortlist has not written there), cached for a day."""
        hit = self._labels.get(section_key)
        if hit and not self._rescan_labels and time.monotonic() < hit[0]:
            return hit[1]
        labels = await self.client.labels(section_key)
        plain = next((lab for lab in labels if str(lab.get("title") or "").lower() == LABEL_PLAIN), None)
        label_id = str(plain.get("key")) if plain else None
        self._labels[section_key] = (time.monotonic() + LABEL_CACHE_HOURS * 3600, label_id)
        return label_id

    async def _read_rows(self) -> list[Row]:
        opts = self.options
        row_size = int(opts.get(CONF_ROW_SIZE, DEFAULT_ROW_SIZE))
        rows: list[Row] = []

        if opts.get(CONF_CONTINUE, True):
            try:
                rows.append(continue_row(await self.client.continue_watching(20), None))
            except PlexAuthError:
                raise
            except PlexError as err:
                _LOGGER.debug("Continue Watching not read (%s); keeping the last one", err)
                rows += self._previous(KIND_CONTINUE)

        sections = await self.client.sections()
        order = [str(s.get("title")) for s in sections]
        for section in sections:
            if section.get("type") not in ("movie", "show"):
                continue
            key, title, lib_type = str(section.get("key")), str(section.get("title")), str(section.get("type"))
            label_id = await self._plain_label(key)
            if label_id is None:
                continue  # Shortlist has not written to this library
            for coll in await self.client.collections(key, label_id=label_id):
                rating_key = str(coll.get("ratingKey"))
                detail = await self.client.collection(rating_key) or coll
                tags = [str(t.get("tag")) for t in detail.get("Label", []) or []]
                if not is_shortlist_collection(tags):
                    continue
                children = await self.client.children(rating_key, limit=row_size)
                rows.append(row_from_collection(detail, tags, children, title, lib_type))
        self._rescan_labels = False

        if opts.get(CONF_RECENT, True):
            try:
                size = int(opts.get(CONF_RECENT_SIZE, DEFAULT_RECENT_SIZE))
                rows.append(recent_row(await self.client.recently_added(size * 3), limit=size))
            except PlexAuthError:
                raise
            except PlexError as err:
                _LOGGER.debug("Recently added not read (%s); keeping the last one", err)
                rows += self._previous(KIND_RECENT)

        return sort_rows(rows, order)

    def _shortlist_configured(self) -> bool:
        return bool(str(self.options.get(CONF_SHORTLIST_URL) or "").strip() and str(self.options.get(CONF_SHORTLIST_TOKEN) or "").strip())

    async def _shortlist_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """One read from Shortlist's API with the owner token (Authorization: Bearer shl_...). Never raises:
        a problem comes back as {"_error": ...}."""
        url = str(self.options.get(CONF_SHORTLIST_URL) or "").rstrip("/")
        token = str(self.options.get(CONF_SHORTLIST_TOKEN) or "").strip()
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(f"{url}{path}", params=params, headers={"Authorization": f"Bearer {token}"},
                                   timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status >= 400:
                    return {"_error": f"HTTP {resp.status}" + (" (token not accepted)" if resp.status in (401, 403) else "")}
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            return {"_error": str(err)}

    async def _read_shortlist(self) -> dict[str, Any]:
        """Shortlist's latest run, when its URL and owner API token are configured. Never fatal."""
        if not self._shortlist_configured():
            return {}
        data = await self._shortlist_get("/api/runs", {"limit": 1})
        if isinstance(data, dict) and data.get("_error"):
            return {"error": data["_error"]}
        runs = data.get("runs") if isinstance(data, dict) else data
        if isinstance(runs, dict):
            runs = runs.get("items") or runs.get("data") or []
        run = (runs or [None])[0] if isinstance(runs, list) else None
        if not isinstance(run, dict):
            return {"error": "no runs yet"}
        return {k: run.get(k) for k in ("id", "status", "started_at", "finished_at", "dry_run", "trigger", "stats") if k in run}

    async def _read_reasons(self, slugs: set[str]) -> dict[str, dict[str, Any]]:
        """Why each delivered title was picked, from Shortlist's picks ledger: GET /api/users, then
        GET /api/users/<id>/rows for the people whose rows are on Plex. Empty when Shortlist cannot be read."""
        users = await self._shortlist_get("/api/users")
        if not isinstance(users, list):
            _LOGGER.debug("Shortlist users not read: %s", users)
            return {}
        reasons: dict[str, dict[str, Any]] = {}
        for user in users:
            slug = str(user.get("slug") or "")
            if slugs and slug not in slugs:
                continue
            rows = await self._shortlist_get(f"/api/users/{user.get('id')}/rows")
            if not isinstance(rows, list):
                _LOGGER.debug("Shortlist rows for %s not read: %s", slug, rows)
                continue
            reasons.update(reasons_from_user_rows(rows))
        return reasons

    async def async_refresh_now(self, rescan_labels: bool = True) -> None:
        """Read Plex again now; by default also look Shortlist's labels up again (Shortlist may have written to a
        library it had not used before)."""
        self._rescan_labels = rescan_labels
        await self.async_request_refresh()
