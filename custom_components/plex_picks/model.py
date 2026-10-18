"""Pure data shaping for Plex Picks: Plex JSON in, rows and items out. No Home Assistant imports, so it is testable
on its own (tests/test_model.py).

A row is one of Shortlist's collections (labelled shortlist_<slug>), or the owner's Continue Watching, or the
server's recently added titles. An item is one film or show, with the fields the card draws and the keys the image
proxy and the play service need. Image URLs are not here: the websocket layer signs them per request.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .const import LABEL_PLAIN, LABEL_PREFIX

KIND_CONTINUE = "continue"
KIND_BECAUSE = "because"
KIND_PICKED = "picked"
KIND_AGAIN = "again"
KIND_POPULAR = "popular"
KIND_SEASONAL = "seasonal"
KIND_REQUESTS = "requests"
KIND_SHORTLIST = "shortlist"
KIND_RECENT = "recent"

# the order rows are shown in: what you were watching first, Shortlist's rows, then what is new
KIND_ORDER = [KIND_CONTINUE, KIND_BECAUSE, KIND_PICKED, KIND_AGAIN, KIND_POPULAR, KIND_SEASONAL, KIND_REQUESTS, KIND_SHORTLIST, KIND_RECENT]

_LEADING_SYMBOLS = re.compile(r"^[^\w\"'(]+", re.UNICODE)
_BECAUSE = re.compile(r"because you watched\s*(.*)$", re.IGNORECASE)
# Shortlist signs its row names with an invisible tail of zero-width characters; Plex keeps them in the title
_INVISIBLE = re.compile(r"[​‌‍⁠﻿]+")


def _visible(text: str | None) -> str:
    return _INVISIBLE.sub("", text or "")


@dataclass
class Item:
    rating_key: str
    title: str
    type: str                      # movie | show | episode | season
    year: int | None = None
    thumb_key: str | None = None   # whose poster to show (the show's for an episode)
    art_key: str | None = None     # whose backdrop to show
    summary: str = ""
    tagline: str = ""
    duration_ms: int | None = None
    rating: float | None = None            # critic
    audience_rating: float | None = None
    content_rating: str | None = None
    genres: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    studio: str | None = None
    progress: float | None = None          # 0..1 for something part watched
    subtitle: str | None = None            # "S2 E7 · Chirp" for an episode
    added_at: int | None = None
    last_viewed_at: int | None = None
    view_count: int = 0
    leaf_count: int | None = None
    viewed_leaf_count: int | None = None
    library: str | None = None
    colors: dict[str, str] | None = None   # Plex's UltraBlurColors, when the server sends them
    play_key: str | None = None            # what to hand to play_media (the episode for Continue Watching, else the item)


@dataclass
class Row:
    id: str
    kind: str
    title: str
    slug: str | None = None        # whose row (Shortlist's user slug); None for shared rows
    library: str | None = None
    library_type: str | None = None
    seed: str | None = None        # "GoodFellas" from "Because you watched GoodFellas"
    summary: str = ""
    count: int = 0
    updated_at: int | None = None
    thumb_key: str | None = None
    items: list[Item] = field(default_factory=list)


def slug_from_labels(labels: list[str] | None) -> str | None:
    """shortlist_<slug> -> slug; the plain 'shortlist' label names nobody. Plex title-cases tag names in a
    collection's detail record ("Shortlist_mark"), so the comparison ignores case and keeps the slug lower-case."""
    for label in labels or []:
        low = str(label).lower()
        if low.startswith(LABEL_PREFIX) and low != LABEL_PLAIN:
            return low[len(LABEL_PREFIX):]
    return None


def is_shortlist_collection(labels: list[str] | None) -> bool:
    return any(str(label).lower() == LABEL_PLAIN or str(label).lower().startswith(LABEL_PREFIX) for label in labels or [])


def clean_title(title: str | None) -> str:
    """Shortlist's default names start with a sparkle and end in an invisible signature; the card prefers words."""
    raw = _visible(title).strip()
    return _LEADING_SYMBOLS.sub("", raw).strip() or raw


def kind_from_title(title: str | None) -> str:
    t = (title or "").lower()
    if "because you watched" in t:
        return KIND_BECAUSE
    if "picked for you" in t:
        return KIND_PICKED
    if "watch it again" in t:
        return KIND_AGAIN
    if "popular" in t:
        return KIND_POPULAR
    if "request" in t:
        return KIND_REQUESTS
    if any(w in t for w in ("christmas", "halloween", "easter", "summer", "winter", "spring", "autumn", "season", "holiday")):
        return KIND_SEASONAL
    return KIND_SHORTLIST


def seed_from_title(title: str | None) -> str | None:
    m = _BECAUSE.search(_visible(title))
    if not m:
        return None
    seed = m.group(1).strip().strip(":").strip()
    return seed or None


def _tags(md: dict[str, Any], key: str, limit: int | None = None) -> list[str]:
    tags = [str(t.get("tag")) for t in md.get(key, []) or [] if t.get("tag")]
    return tags[:limit] if limit else tags


def _float(v: Any) -> float | None:
    try:
        return round(float(v), 1) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def item_from_metadata(md: dict[str, Any], library: str | None = None) -> Item:
    """One Plex Metadata entry -> Item. Episodes and seasons are shown as their show, but keep their own key to play."""
    kind = str(md.get("type") or "movie")
    rating_key = str(md.get("ratingKey"))
    if kind == "episode":
        title = md.get("grandparentTitle") or md.get("title") or ""
        thumb_key = str(md.get("grandparentRatingKey") or rating_key)
        art_key = thumb_key
        season, number = _int(md.get("parentIndex")), _int(md.get("index"))
        subtitle = " ".join(p for p in [f"S{season}" if season is not None else "", f"E{number}" if number is not None else ""] if p)
        if md.get("title"):
            subtitle = f"{subtitle} · {md.get('title')}" if subtitle else str(md.get("title"))
    elif kind == "season":
        title = md.get("parentTitle") or md.get("title") or ""
        thumb_key = str(md.get("parentRatingKey") or rating_key)
        art_key = thumb_key
        subtitle = str(md.get("title") or "")
    else:
        title = md.get("title") or ""
        thumb_key = rating_key
        art_key = rating_key
        subtitle = None

    duration, offset = _int(md.get("duration")), _int(md.get("viewOffset"))
    progress = None
    if duration and offset is not None and duration > 0:
        progress = max(0.0, min(1.0, offset / duration))

    colors = md.get("UltraBlurColors")
    if isinstance(colors, dict):
        colors = {k: str(v) for k, v in colors.items() if isinstance(v, str)}
    else:
        colors = None

    year = _int(md.get("year")) or _int(md.get("parentYear")) or _int(md.get("grandparentYear"))
    return Item(
        rating_key=rating_key,
        title=str(title),
        type=kind,
        year=year,
        thumb_key=thumb_key,
        art_key=art_key,
        summary=str(md.get("summary") or ""),
        tagline=str(md.get("tagline") or ""),
        duration_ms=duration,
        rating=_float(md.get("rating")),
        audience_rating=_float(md.get("audienceRating")),
        content_rating=md.get("contentRating"),
        genres=_tags(md, "Genre", 6),
        directors=_tags(md, "Director", 3),
        cast=_tags(md, "Role", 12),
        studio=md.get("studio"),
        progress=progress,
        subtitle=subtitle,
        added_at=_int(md.get("addedAt")),
        last_viewed_at=_int(md.get("lastViewedAt")),
        view_count=_int(md.get("viewCount")) or 0,
        leaf_count=_int(md.get("leafCount")),
        viewed_leaf_count=_int(md.get("viewedLeafCount")),
        library=library or md.get("librarySectionTitle"),
        colors=colors,
        play_key=rating_key,
    )


def row_from_collection(collection: dict[str, Any], labels: list[str] | None, children: list[dict[str, Any]],
                        library: str | None, library_type: str | None) -> Row:
    """A Shortlist collection and its children -> Row. The order of children is the order Shortlist wrote."""
    raw_title = str(collection.get("title") or "")
    title = clean_title(raw_title)
    return Row(
        id=f"c{collection.get('ratingKey')}",
        kind=kind_from_title(raw_title),
        title=title,
        slug=slug_from_labels(labels),
        library=library or collection.get("librarySectionTitle"),
        library_type=library_type,
        seed=seed_from_title(raw_title),
        summary=str(collection.get("summary") or ""),
        count=len(children),
        updated_at=_int(collection.get("updatedAt")),
        thumb_key=str(collection.get("ratingKey")) if collection.get("thumb") else None,
        items=[item_from_metadata(md, library) for md in children],
    )


def continue_row(metadata: list[dict[str, Any]], slug: str | None, title: str = "Continue watching") -> Row:
    items = [item_from_metadata(md) for md in metadata]
    return Row(id="continue", kind=KIND_CONTINUE, title=title, slug=slug, count=len(items), items=items)


def recent_row(metadata: list[dict[str, Any]], title: str = "New on the server", limit: int = 20) -> Row:
    """Recently added, one entry per show (Plex lists seasons and episodes separately)."""
    seen: set[str] = set()
    items: list[Item] = []
    for md in metadata:
        item = item_from_metadata(md)
        if item.thumb_key in seen:
            continue
        seen.add(item.thumb_key or item.rating_key)
        if item.type in ("episode", "season"):
            # show the show, play the show
            item.play_key = item.thumb_key
            item.type = "show"
            item.rating_key = item.thumb_key or item.rating_key
            item.subtitle = None
            item.progress = None
        items.append(item)
        if len(items) >= limit:
            break
    return Row(id="recent", kind=KIND_RECENT, title=title, count=len(items), items=items)


def sort_rows(rows: list[Row], library_order: list[str] | None = None) -> list[Row]:
    """Kinds in KIND_ORDER; within a kind, libraries in the server's order."""
    lib_rank = {name: i for i, name in enumerate(library_order or [])}

    def key(row: Row) -> tuple:
        k = KIND_ORDER.index(row.kind) if row.kind in KIND_ORDER else len(KIND_ORDER)
        return (k, lib_rank.get(row.library or "", 999), row.title.lower())

    return sorted(rows, key=key)


def people_from_rows(rows: list[Row]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        if row.slug and row.slug not in seen:
            seen.append(row.slug)
    return seen


def payload(server: dict[str, Any], rows: list[Row], generated_at: int) -> dict[str, Any]:
    """The whole thing as plain dicts: what the websocket sends and the sensors summarise."""
    return {
        "server": server,
        "generated_at": generated_at,
        "people": people_from_rows(rows),
        "rows": [asdict(row) for row in rows],
    }


def summarise(rows: list[Row]) -> list[dict[str, Any]]:
    """Small enough for a sensor attribute: one line per row."""
    return [{"id": r.id, "kind": r.kind, "title": r.title, "library": r.library, "slug": r.slug, "count": r.count} for r in rows]
