"""The To watch rows: Plex's own Watchlist copied into a "To watch" collection in each library that holds one of its
titles, shown on the owner's Plex Home and nowhere else.

Plex keeps the Watchlist on plex.tv, apart from the libraries, so its titles never appear as a row on Home. This module
works out where each title lives on the server and keeps one plain collection per library in line with the list: the
newest additions first, a title gone from the list or watched taken out, a library left with nothing losing its
collection. It imports no Home Assistant code, so the coordinator and tools/watchlist_sync.py run the same thing.

A title held in several libraries (Movies and 4K Movies) goes in one only, so it shows once: the first of the preferred
libraries that holds it, else the first in the server's own order. A film counts as watched once played; a show once
every episode is.

Plex is asked gently, as everywhere in Plex Picks: each title is looked up once and remembered for a day, the
libraries are searched for the collections once a day, and a quiet pass costs a request per collection.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable

from .const import (
    LABEL_WATCHLIST, PLEX_SCHEME, WATCHLIST_LOOKUP_GAP_SECONDS, WATCHLIST_PLACE_HOURS, WATCHLIST_SCAN_HOURS,
    WATCHLIST_TITLE,
)

_LOGGER = logging.getLogger(__name__)

ROW_TYPES = ("movie", "show")
_MARKER = re.compile("[​‌‍⁠﻿]")    # Shortlist's invisible title signature


def discover_key(guid: str | None) -> str | None:
    """plex://movie/5d776a319ab54400214fbf4b -> 5d776a319ab54400214fbf4b, the key plex.tv's Watchlist actions take.
    Only plex:// guids are on plex.tv; a local or legacy-agent guid has no Watchlist entry."""
    if not isinstance(guid, str) or not guid.startswith(PLEX_SCHEME):
        return None
    key = guid.rstrip("/").rsplit("/", 1)[-1]
    return key or None


def is_watched(md: dict[str, Any]) -> bool:
    """A film once played; a show once every episode is (new episodes make it unwatched again)."""
    if md.get("type") == "show":
        leaves = int(md.get("leafCount") or 0)
        return leaves > 0 and int(md.get("viewedLeafCount") or 0) >= leaves
    return int(md.get("viewCount") or 0) > 0


@dataclass
class Copy:
    section: str        # library key
    rating_key: str
    watched: bool


@dataclass
class Library:
    key: str
    title: str
    type: str           # movie | show


@dataclass
class State:
    """What a pass remembers for the next one."""
    places: dict[str, tuple[float, list[Copy]]] = field(default_factory=dict)   # guid -> (look again after, copies)
    collections: dict[str, str] = field(default_factory=dict)                   # library key -> To watch collection
    scanned_until: float = 0.0                                                  # search the libraries again after

    def forget_collections(self) -> None:
        self.collections.clear()
        self.scanned_until = 0.0


def preferred_order(libraries: list[Library], preferred_names: list[str]) -> list[str]:
    """Library keys in the order a title's copy is chosen from: the preferred names first (as given, any case),
    then every other library in the server's order."""
    by_name = {lib.title.casefold(): lib.key for lib in libraries}
    first = [by_name[n.strip().casefold()] for n in preferred_names if n.strip().casefold() in by_name]
    seen = set(first)
    return first + [lib.key for lib in libraries if lib.key not in seen and not seen.add(lib.key)]


def parse_names(text: str | None) -> list[str]:
    return [n.strip() for n in str(text or "").replace("\n", ",").split(",") if n.strip()]


def choose(copies: list[Copy], order: list[str]) -> Copy | None:
    rank = {key: i for i, key in enumerate(order)}
    ranked = sorted((c for c in copies if c.section in rank), key=lambda c: rank[c.section])
    return ranked[0] if ranked else None


def plan(guids: list[str], places: dict[str, list[Copy]], order: list[str]) -> dict[str, list[str]]:
    """library key -> the rating keys its To watch row should hold, newest on the Watchlist first. Watched titles and
    titles not on the server are left out; each title goes in one library only."""
    rows: dict[str, list[str]] = {}
    for guid in guids:
        copy = choose(places.get(guid) or [], order)
        if copy is None or copy.watched:
            continue
        keys = rows.setdefault(copy.section, [])
        if copy.rating_key not in keys:
            keys.append(copy.rating_key)
    return rows


@dataclass
class Changes:
    """One library's work: what to take out, what to put in, and the order to end in."""
    remove: list[str]
    add: list[str]
    want: list[str]


def changes(want: list[str], current: list[str], watched_now: set[str]) -> Changes:
    """Bring a collection holding `current` (Plex's order) in line with `want`. An item Plex now reports watched is
    taken out even if the remembered place said otherwise."""
    want = [k for k in want if k not in watched_now]
    have, keep = set(current), set(want)
    return Changes(remove=[k for k in current if k not in keep], add=[k for k in want if k not in have], want=want)


def shelf_after(shelves: list[dict[str, Any]], own: str) -> str | None:
    """Where a new To watch shelf goes in a library's shelf list: straight after Shortlist's rows (Mark, 18 Oct 2026:
    Because you watched stays at the top), else at the top (None). Shortlist's rows are told by the invisible marker
    it ends their titles with."""
    after = None
    for shelf in shelves:
        identifier = str(shelf.get("identifier") or "")
        if identifier != own and _MARKER.search(str(shelf.get("title") or "")):
            after = identifier
    return after


def _jitter(guid: str) -> float:
    """Up to two hours, the same for a title every time, so the daily look-ups spread over several passes."""
    return float(zlib.crc32(guid.encode("utf-8")) % 7200)


async def sync(client: Any, state: State, watchlist: list[dict[str, Any]], machine_id: str,
               preferred: list[str] | None = None, dry_run: bool = False,
               note: Callable[[str], None] | None = None) -> dict[str, Any]:
    """One pass: bring every To watch collection in line with the Watchlist. `client` is a PlexClient; `watchlist` is
    PlexDiscover.watchlist(). With dry_run nothing is written and `note` hears what would be. Returns a summary.
    A PlexError stops the pass and is raised; what was learnt so far stays in `state`."""
    say = note or (lambda text: _LOGGER.debug("%s", text))
    now = time.time()
    summary: dict[str, Any] = {"watchlist": len(watchlist), "looked_up": 0, "created": 0, "deleted": 0, "added": 0,
                               "removed": 0, "rows": {}, "not_on_server": []}

    libraries = [Library(str(s.get("key")), str(s.get("title")), str(s.get("type")))
                 for s in await client.sections() if s.get("type") in ROW_TYPES]
    by_key = {lib.key: lib for lib in libraries}
    order = preferred_order(libraries, preferred or [])

    # where each title lives (looked up once, remembered for a day)
    guids = [str(m.get("guid")) for m in watchlist if discover_key(m.get("guid"))]
    titles = {str(m.get("guid")): str(m.get("title") or m.get("guid")) for m in watchlist}
    for guid in list(state.places):
        if guid not in guids:
            state.places.pop(guid, None)
    for guid in guids:
        hit = state.places.get(guid)
        if hit and now < hit[0]:
            continue
        if summary["looked_up"]:
            await asyncio.sleep(WATCHLIST_LOOKUP_GAP_SECONDS)
        metadata = await client.by_guid(guid)
        copies = [Copy(str(md.get("librarySectionID")), str(md.get("ratingKey")), is_watched(md))
                  for md in metadata if md.get("type") in ROW_TYPES and str(md.get("librarySectionID")) in by_key]
        state.places[guid] = (now + WATCHLIST_PLACE_HOURS * 3600 + _jitter(guid), copies)
        summary["looked_up"] += 1
    places = {guid: state.places[guid][1] for guid in guids if guid in state.places}
    summary["not_on_server"] = [titles[g] for g in guids if not places.get(g)]
    wanted = plan(guids, places, order)

    # where the To watch collections are (every library searched once a day)
    if now >= state.scanned_until:
        found: dict[str, str] = {}
        for lib in libraries:
            label = next((lab for lab in await client.collection_labels(lib.key)
                          if str(lab.get("title") or "").casefold() == LABEL_WATCHLIST), None)
            if label is None:
                continue
            ours = await client.collections(lib.key, label_id=str(label.get("key")))
            if ours:
                found[lib.key] = str(ours[0].get("ratingKey"))
                for extra in ours[1:]:       # one per library; a second (an interrupted pass) is folded away
                    say(f"delete a second To watch collection in {lib.title} ({extra.get('ratingKey')})")
                    if not dry_run:
                        await client.delete_metadata(str(extra.get("ratingKey")))
        state.collections = found
        state.scanned_until = now + WATCHLIST_SCAN_HOURS * 3600

    for key in sorted(set(wanted) | set(state.collections), key=lambda k: order.index(k) if k in order else 999):
        lib = by_key.get(key)
        if lib is None:                      # a library that has gone
            state.collections.pop(key, None)
            continue
        want = wanted.get(key, [])
        collection = state.collections.get(key)
        children: list[dict[str, Any]] | None = None
        if collection:
            try:
                children = await client.children(collection, limit=500)
            except Exception as err:          # noqa: BLE001  (a 404: deleted by hand or by Plex; anything else re-raised)
                if getattr(err, "status", None) != 404:
                    raise
                state.collections.pop(key, None)
                collection = None

        if collection and children is not None:
            current = [str(md.get("ratingKey")) for md in children]
            watched_now = {str(md.get("ratingKey")) for md in children if is_watched(md)}
            for guid, (until, copies) in state.places.items():     # remember what Plex now says is watched
                for c in copies:
                    if c.rating_key in watched_now:
                        c.watched = True
            work = changes(want, current, watched_now)
            if not work.want:
                say(f"{lib.title}: nothing left to watch; delete the To watch collection")
                if not dry_run:
                    await client.delete_metadata(collection)
                state.collections.pop(key, None)
                summary["deleted"] += 1
                continue
            for rk in work.remove:
                say(f"{lib.title}: take out {rk}")
                if not dry_run:
                    await client.remove_from_collection(collection, rk)
            if work.add:
                say(f"{lib.title}: put in {len(work.add)}")
                if not dry_run:
                    await client.add_to_collection(collection, machine_id, work.add)
                    # newest on the Watchlist first: move each new one to the front, oldest of them first
                    for rk in reversed(work.add):
                        await client.move_in_collection(collection, rk)
            summary["removed"] += len(work.remove)
            summary["added"] += len(work.add)
            summary["rows"][lib.title] = len(work.want)
            continue

        if not want:
            continue
        say(f"{lib.title}: create To watch with {len(want)}")
        summary["created"] += 1
        summary["added"] += len(want)
        summary["rows"][lib.title] = len(want)
        if dry_run:
            continue
        created = await client.create_collection(key, lib.type, WATCHLIST_TITLE, machine_id, want)
        collection = str(created.get("ratingKey") or "")
        if not collection:
            raise RuntimeError(f"Plex did not return the new To watch collection in {lib.title}")
        state.collections[key] = collection
        await client.set_labels(key, collection, [LABEL_WATCHLIST])
        await client.custom_order(collection)
        placed = [str(md.get("ratingKey")) for md in await client.children(collection, limit=500)]
        if placed != want:
            for rk in reversed(want):
                await client.move_in_collection(collection, rk)
        shelf = await client.show_on_home(key, collection, recommended=False, own_home=True, shared_home=False)
        await client.move_shelf(key, shelf, after=shelf_after(await client.shelves(key), shelf))

    return summary
