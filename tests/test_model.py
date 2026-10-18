"""The pure model: Plex JSON shapes in, rows and items out. Run: python -m pytest tests -q"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load  # noqa: E402,F401
from custom_components.plex_picks import model as m  # noqa: E402


def movie(rk="1734361", title="Matchbox the Movie", year=2026, **extra):
    md = {
        "ratingKey": rk, "type": "movie", "title": title, "year": year, "summary": "Friends reconnect.", "tagline": "Save the world.",
        "duration": 7567296, "rating": 2.6, "audienceRating": 5.7, "contentRating": "gb/12", "studio": "Mattel Studios",
        "thumb": f"/library/metadata/{rk}/thumb/1", "art": f"/library/metadata/{rk}/art/1", "addedAt": 1791558075,
        "Genre": [{"tag": "Action"}, {"tag": "Adventure"}, {"tag": "Comedy"}],
        "Director": [{"tag": "Sam Hargrave"}],
        "Role": [{"tag": "John Cena", "role": "Sean"}, {"tag": "Jessica Biel", "role": "Aimee"}],
        "UltraBlurColors": {"topLeft": "442411", "topRight": "120303", "bottomRight": "321e0b", "bottomLeft": "904612"},
    }
    md.update(extra)
    return md


def episode(rk="169641", show_rk="169490", offset=625000, duration=1300000):
    return {
        "ratingKey": rk, "type": "episode", "title": "Chirp", "index": 7, "parentIndex": 2, "grandparentTitle": "Modern Family",
        "grandparentRatingKey": show_rk, "parentRatingKey": "169634", "grandparentYear": 2009, "duration": duration, "viewOffset": offset,
        "thumb": f"/library/metadata/{rk}/thumb/1", "grandparentThumb": f"/library/metadata/{show_rk}/thumb/1",
    }


def test_slug_and_labels():
    assert m.slug_from_labels(["shortlist", "shortlist_themarkchristie"]) == "themarkchristie"
    assert m.slug_from_labels(["shortlist"]) is None
    assert m.slug_from_labels(["PMM"]) is None
    assert m.is_shortlist_collection(["shortlist"]) is True
    assert m.is_shortlist_collection(["PMM", "deployarr_managed"]) is False


def test_labels_as_plex_returns_them_in_a_detail_record():
    # Plex title-cases tag names in /library/collections/<id>: seen live on 18 Oct 2026
    assert m.is_shortlist_collection(["Shortlist_themarkchristie", "Shortlist"]) is True
    assert m.slug_from_labels(["Shortlist_themarkchristie", "Shortlist"]) == "themarkchristie"


def test_reasons_from_shortlist_user_rows_and_apply():
    entries = [
        {"collection_id": 1, "slug": "picked", "picks": [
            {"rank": 1, "title": "Heat", "reason": "Because you watched Scream 7", "rating_key": 1001, "media_type": "movie",
             "collection_slug": "picked", "library": "Movies", "section_key": "1", "seed_title": "Scream 7", "sources": ["tmdb_similar"], "affinity": 0.8},
            {"rank": 2, "title": "Lost", "reason": "Popular", "rating_key": 0, "media_type": "movie", "collection_slug": "picked", "library": "Movies",
             "section_key": "1", "seed_title": None, "sources": [], "affinity": 1.0},
        ]},
        {"collection_id": 1, "slug": "picked", "picks": [
            {"rank": 1, "title": "Heat", "reason": "Because you watched Heat (1995) in 4K", "rating_key": 2001, "media_type": "movie",
             "collection_slug": "picked", "library": "4K Movies", "section_key": "2", "seed_title": "Scream 7", "sources": ["tmdb_similar", "tmdb_discover"], "affinity": 0.6},
        ]},
    ]
    reasons = m.reasons_from_user_rows(entries)
    assert set(reasons) == {"1001", "2001"}          # rating_key 0 is skipped
    assert reasons["1001"]["seed"] == "Scream 7" and reasons["1001"]["sources"] == ["tmdb_similar"]
    rows = [m.Row(id="c1", kind=m.KIND_PICKED, title="Picked", items=[m.item_from_metadata(movie("1001", "Heat")), m.item_from_metadata(movie("1002", "Casino"))]),
            m.Row(id="continue", kind=m.KIND_CONTINUE, title="Continue", items=[m.item_from_metadata(movie("1001", "Heat"))])]
    assert m.apply_reasons(rows, reasons) == 1
    heat, casino = rows[0].items
    assert heat.reason == "Because you watched Scream 7" and heat.seed == "Scream 7" and heat.sources == ["tmdb_similar"] and heat.affinity == 0.8
    assert casino.reason is None
    assert rows[1].items[0].reason is None             # Continue watching is never "explained"
    assert m.apply_reasons(rows, {}) == 0


def test_invisible_signature_is_stripped_from_titles():
    signed = "✨ 4K Movies Picked for You​‌‌​‌"
    assert m.clean_title(signed) == "4K Movies Picked for You"
    assert m.kind_from_title(signed) == m.KIND_PICKED
    assert m.seed_from_title("Because you watched Heat​‌") == "Heat"
    row = m.row_from_collection({"ratingKey": "1", "title": signed}, ["Shortlist", "Shortlist_mark"], [], "4K Movies", "movie")
    assert row.title == "4K Movies Picked for You" and row.slug == "mark" and row.kind == m.KIND_PICKED


def test_kinds_titles_and_seeds():
    assert m.kind_from_title("✨ Movies Picked for You") == m.KIND_PICKED
    assert m.clean_title("✨ Movies Picked for You") == "Movies Picked for You"
    assert m.kind_from_title("Because you watched GoodFellas") == m.KIND_BECAUSE
    assert m.seed_from_title("Because you watched GoodFellas") == "GoodFellas"
    assert m.seed_from_title("Because you watched drama like GoodFellas") == "drama like GoodFellas"
    assert m.seed_from_title("Watch it again") is None
    assert m.kind_from_title("Watch it again") == m.KIND_AGAIN
    assert m.kind_from_title("Popular on this server") == m.KIND_POPULAR
    assert m.kind_from_title("Your requests") == m.KIND_REQUESTS
    assert m.kind_from_title("Halloween frights") == m.KIND_SEASONAL
    assert m.kind_from_title("Something else") == m.KIND_SHORTLIST


def test_movie_item():
    it = m.item_from_metadata(movie(), "4K Movies")
    assert it.rating_key == "1734361" and it.thumb_key == "1734361" and it.art_key == "1734361" and it.play_key == "1734361"
    assert it.title == "Matchbox the Movie" and it.year == 2026 and it.type == "movie"
    assert it.genres == ["Action", "Adventure", "Comedy"] and it.directors == ["Sam Hargrave"] and it.cast == ["John Cena", "Jessica Biel"]
    assert it.rating == 2.6 and it.audience_rating == 5.7 and it.content_rating == "gb/12"
    assert it.duration_ms == 7567296 and it.progress is None and it.library == "4K Movies"
    assert it.colors == {"topLeft": "442411", "topRight": "120303", "bottomRight": "321e0b", "bottomLeft": "904612"}


def test_episode_item_shows_the_show_but_plays_the_episode():
    it = m.item_from_metadata(episode())
    assert it.title == "Modern Family" and it.type == "episode"
    assert it.thumb_key == "169490" and it.art_key == "169490"
    assert it.rating_key == "169641" and it.play_key == "169641"
    assert it.subtitle == "S2 E7 · Chirp"
    assert it.year == 2009
    assert abs(it.progress - 625000 / 1300000) < 1e-6


def test_progress_is_clamped_and_optional():
    assert m.item_from_metadata(episode(offset=5000000)).progress == 1.0
    assert m.item_from_metadata(movie(duration=None)).progress is None


def test_row_from_collection_keeps_order_and_slug():
    coll = {"ratingKey": "555", "title": "Because you watched GoodFellas", "summary": "Picked nightly", "updatedAt": 1792289926, "thumb": "/x"}
    children = [movie("1"), movie("2", "Casino"), movie("3", "Heat")]
    row = m.row_from_collection(coll, ["shortlist", "shortlist_mark"], children, "Movies", "movie")
    assert row.id == "c555" and row.kind == m.KIND_BECAUSE and row.slug == "mark" and row.seed == "GoodFellas"
    assert row.library == "Movies" and row.library_type == "movie" and row.count == 3 and row.thumb_key == "555"
    assert [i.rating_key for i in row.items] == ["1", "2", "3"]


def test_recent_row_dedupes_episodes_into_their_show():
    md = [episode("1", "100"), episode("2", "100"), movie("3", "Heat"), {"ratingKey": "4", "type": "season", "title": "Season 3", "parentTitle": "Dark", "parentRatingKey": "200"}]
    row = m.recent_row(md, limit=10)
    assert row.kind == m.KIND_RECENT
    assert [i.rating_key for i in row.items] == ["100", "3", "200"]
    show = row.items[0]
    assert show.type == "show" and show.play_key == "100" and show.subtitle is None and show.progress is None and show.title == "Modern Family"
    assert row.items[2].title == "Dark"


def test_recent_row_limit():
    md = [movie(str(i), f"M{i}") for i in range(30)]
    assert m.recent_row(md, limit=5).count == 5


def test_sort_rows_by_kind_then_library_order():
    rows = [
        m.Row(id="c1", kind=m.KIND_PICKED, title="Picked", library="TV Shows"),
        m.Row(id="recent", kind=m.KIND_RECENT, title="New"),
        m.Row(id="c2", kind=m.KIND_BECAUSE, title="Because", library="Movies"),
        m.Row(id="continue", kind=m.KIND_CONTINUE, title="Continue"),
        m.Row(id="c3", kind=m.KIND_PICKED, title="Picked", library="Movies"),
    ]
    out = m.sort_rows(rows, ["Movies", "TV Shows"])
    assert [r.id for r in out] == ["c2", "continue", "c3", "c1", "recent"]   # Because you watched first


def test_people_payload_and_summary():
    rows = [m.Row(id="c1", kind=m.KIND_PICKED, title="P", slug="mark", count=2, items=[m.item_from_metadata(movie())]),
            m.Row(id="c2", kind=m.KIND_AGAIN, title="A", slug="mark", count=1), m.Row(id="recent", kind=m.KIND_RECENT, title="N")]
    assert m.people_from_rows(rows) == ["mark"]
    p = m.payload({"id": "abc", "name": "Server"}, rows, 1234)
    assert p["people"] == ["mark"] and p["generated_at"] == 1234 and p["server"]["id"] == "abc"
    assert p["rows"][0]["items"][0]["title"] == "Matchbox the Movie"
    s = m.summarise(rows)
    assert s[0] == {"id": "c1", "kind": "picked", "title": "P", "library": None, "slug": "mark", "count": 2}


def test_recent_row_keeps_only_films_and_tv():
    """The server-wide recently added list also has music (Mark's Audiobooks library): a TV row cannot play it."""
    md = [{"ratingKey": "9", "type": "album", "title": "An Audiobook", "parentTitle": "Author"},
          {"ratingKey": "10", "type": "track", "title": "Chapter 1"},
          movie(rk="11", title="A Film")]
    row = m.recent_row(md, limit=10)
    assert [i.title for i in row.items] == ["A Film"]

