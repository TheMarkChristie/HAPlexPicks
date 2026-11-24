"""The To watch rows: where each Watchlist title goes, and one pass against a fake Plex. Run: python -m pytest tests -q"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load  # noqa: E402,F401
from custom_components.plex_picks import const, model  # noqa: E402
from custom_components.plex_picks import watchlist as w  # noqa: E402

LIBS = [w.Library("2", "4K Movies", "movie"), w.Library("3", "Kids Movies", "movie"), w.Library("1", "Movies", "movie"),
        w.Library("6", "4K TV Shows", "show"), w.Library("4", "TV Shows", "show")]


def test_discover_key():
    assert w.discover_key("plex://movie/5d776a319ab54400214fbf4b") == "5d776a319ab54400214fbf4b"
    assert w.discover_key("plex://show/68af9f683a2cf5507ddee977") == "68af9f683a2cf5507ddee977"
    assert w.discover_key("local://12345") is None
    assert w.discover_key("com.plexapp.agents.imdb://tt0111161?lang=en") is None
    assert w.discover_key(None) is None


def test_watched():
    assert w.is_watched({"type": "movie", "viewCount": 1}) is True
    assert w.is_watched({"type": "movie"}) is False
    assert w.is_watched({"type": "show", "leafCount": 10, "viewedLeafCount": 10}) is True
    assert w.is_watched({"type": "show", "leafCount": 12, "viewedLeafCount": 6}) is False     # The Crow Girl, half way
    assert w.is_watched({"type": "show", "leafCount": 0, "viewedLeafCount": 0}) is False


def test_preferred_order_and_names():
    assert w.parse_names(" Movies, TV Shows ,\n4K Movies,,") == ["Movies", "TV Shows", "4K Movies"]
    assert w.preferred_order(LIBS, []) == ["2", "3", "1", "6", "4"]                  # the server's order
    assert w.preferred_order(LIBS, ["movies", "TV Shows", "Nope"]) == ["1", "4", "2", "3", "6"]


def test_plan_one_library_per_title_newest_first():
    places = {
        "g1": [w.Copy("1", "11", False), w.Copy("2", "21", False)],   # Movies and 4K Movies
        "g2": [w.Copy("4", "41", False), w.Copy("6", "61", False)],   # TV Shows and 4K TV Shows
        "g3": [w.Copy("1", "13", True)],                              # watched: left out
        "g4": [],                                                     # not on the server
        "g5": [w.Copy("3", "35", False), w.Copy("2", "25", False)],   # Kids and 4K
    }
    guids = ["g1", "g2", "g3", "g4", "g5"]
    assert w.plan(guids, places, w.preferred_order(LIBS, [])) == {"2": ["21", "25"], "6": ["61"]}
    assert w.plan(guids, places, w.preferred_order(LIBS, ["Movies", "TV Shows"])) == {"1": ["11"], "4": ["41"], "2": ["25"]}


def test_changes():
    c = w.changes(["a", "b", "c"], ["b", "x", "c"], watched_now={"c"})
    assert c.remove == ["x", "c"] and c.add == ["a"] and c.want == ["a", "b"]


def test_shelf_after_shortlist_rows():
    shelves = [{"identifier": "custom.collection.1.10", "title": "Because you watched Heat​‌"},
               {"identifier": "custom.collection.1.11", "title": "Movies Picked for You‌​"},
               {"identifier": "movie.recentlyadded", "title": "Recently Added Movies"},
               {"identifier": "custom.collection.1.99", "title": "To watch"}]
    assert w.shelf_after(shelves, "custom.collection.1.99") == "custom.collection.1.11"
    assert w.shelf_after(shelves[2:], "custom.collection.1.99") is None


def test_item_watch_guid():
    assert model.item_from_metadata({"ratingKey": "1", "type": "movie", "title": "A", "guid": "plex://movie/abc"}).watch_guid == "plex://movie/abc"
    ep = {"ratingKey": "5", "type": "episode", "title": "E", "grandparentTitle": "S", "grandparentRatingKey": "3",
          "guid": "plex://episode/e1", "grandparentGuid": "plex://show/s1"}
    assert model.item_from_metadata(ep).watch_guid == "plex://show/s1"
    assert model.item_from_metadata({"ratingKey": "1", "type": "movie", "title": "A", "guid": "local://1"}).watch_guid is None
    assert model.payload({}, [], 0, ["plex://movie/abc"])["watchlist"] == ["plex://movie/abc"]


class FakePlex:
    """Just enough of PlexClient for one pass, recording every write."""

    def __init__(self):
        self.writes = []
        self.library = {   # guid -> copies on the server
            "plex://movie/m1": [{"type": "movie", "ratingKey": "11", "librarySectionID": 1}, {"type": "movie", "ratingKey": "21", "librarySectionID": 2}],
            "plex://show/s1": [{"type": "show", "ratingKey": "41", "librarySectionID": 4, "leafCount": 8, "viewedLeafCount": 2}],
            "plex://movie/m2": [{"type": "movie", "ratingKey": "12", "librarySectionID": 1, "viewCount": 1}],
        }
        self.collections = {}  # collection key -> (library key, [items])
        self.labels = {}       # library key -> label directory
        self.next_key = 900

    async def sections(self):
        return [{"key": lib.key, "title": lib.title, "type": lib.type} for lib in LIBS]

    async def by_guid(self, guid):
        return self.library.get(guid, [])

    async def collection_labels(self, section):
        return self.labels.get(section, [])

    async def collections(self, section, label_id=None):
        return [{"ratingKey": k} for k, (lib, _) in self.collections.items() if lib == section]

    async def children(self, key, limit=500):
        if key not in self.collections:
            err = Exception("gone")
            err.status = 404
            raise err
        return [{"ratingKey": rk, "type": "movie"} for rk in self.collections[key][1]]

    async def create_collection(self, section, media_type, title, machine, keys):
        self.next_key += 1
        key = str(self.next_key)
        self.collections[key] = (section, list(reversed(keys)))     # pretend Plex ignored the order given
        self.writes.append(("create", section, title, tuple(keys)))
        return {"ratingKey": key}

    async def set_labels(self, section, key, labels):
        self.labels[section] = [{"key": "77", "title": labels[0]}]
        self.writes.append(("label", key, tuple(labels)))

    async def custom_order(self, key):
        self.writes.append(("custom_order", key))

    async def move_in_collection(self, key, rk, after=None):
        items = self.collections[key][1]
        items.remove(rk)
        items.insert(0, rk)
        self.writes.append(("move", key, rk))

    async def show_on_home(self, section, key, recommended, own_home, shared_home):
        self.writes.append(("home", section, key, recommended, own_home, shared_home))
        return f"custom.collection.{section}.{key}"

    async def shelves(self, section):
        return [{"identifier": "custom.collection.1.5", "title": "Because​"}] if section == "1" else []

    async def move_shelf(self, section, identifier, after=None):
        self.writes.append(("shelf", section, identifier, after))

    async def add_to_collection(self, key, machine, keys):
        self.collections[key][1].extend(keys)
        self.writes.append(("add", key, tuple(keys)))

    async def remove_from_collection(self, key, rk):
        self.collections[key][1].remove(rk)
        self.writes.append(("remove", key, rk))

    async def delete_metadata(self, key):
        self.collections.pop(key, None)
        self.writes.append(("delete", key))


def _wl(*guids):
    return [{"guid": g, "title": g} for g in guids]


def test_sync_creates_updates_and_deletes(monkeypatch):
    monkeypatch.setattr(w, "WATCHLIST_LOOKUP_GAP_SECONDS", 0)
    plex, state = FakePlex(), w.State()

    # dry run: nothing written
    summary = asyncio.run(w.sync(plex, state, _wl("plex://movie/m1", "plex://show/s1", "plex://movie/m2"), "mid",
                                 ["Movies", "TV Shows"], dry_run=True))
    assert plex.writes == [] and summary["created"] == 2 and summary["rows"] == {"Movies": 1, "TV Shows": 1}

    # first pass: one collection in Movies (m1; m2 is watched), one in TV Shows, labelled, on Home, after Shortlist's rows
    plex, state = FakePlex(), w.State()
    asyncio.run(w.sync(plex, state, _wl("plex://movie/m1", "plex://show/s1", "plex://movie/m2"), "mid", ["Movies", "TV Shows"]))
    creates = [x for x in plex.writes if x[0] == "create"]
    assert creates == [("create", "1", const.WATCHLIST_TITLE, ("11",)), ("create", "4", const.WATCHLIST_TITLE, ("41",))]
    assert ("label", "901", (const.LABEL_WATCHLIST,)) in plex.writes
    assert ("home", "1", "901", False, True, False) in plex.writes
    assert ("shelf", "1", "custom.collection.1.901", "custom.collection.1.5") in plex.writes
    assert ("shelf", "4", "custom.collection.4.902", None) in plex.writes
    assert state.collections == {"1": "901", "4": "902"}

    # a new film on the Watchlist goes in at the front; the show leaves the Watchlist, so TV Shows' collection goes
    plex.library["plex://movie/m3"] = [{"type": "movie", "ratingKey": "13", "librarySectionID": 1}]
    plex.writes.clear()
    asyncio.run(w.sync(plex, state, _wl("plex://movie/m3", "plex://movie/m1"), "mid", ["Movies", "TV Shows"]))
    assert ("add", "901", ("13",)) in plex.writes and ("move", "901", "13") in plex.writes
    assert plex.collections["901"][1] == ["13", "11"]
    assert ("delete", "902") in plex.writes and "4" not in state.collections

    # the collection deleted by hand: found missing, made again
    del plex.collections["901"]
    plex.writes.clear()
    asyncio.run(w.sync(plex, state, _wl("plex://movie/m3", "plex://movie/m1"), "mid", ["Movies"]))
    assert [x[0] for x in plex.writes].count("create") == 1 and plex.collections[state.collections["1"]][1] == ["13", "11"]
