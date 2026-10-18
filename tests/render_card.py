"""Render the Plex Picks card in headless Chrome or Edge from sample data built by the model itself, at panel width
(the Watch dashboard), at phone width, with the detail sheet open, and in compact mode. Saves PNGs under tests/out.

The page inlines the card's script (file:// pages cannot fetch), stubs the slice of `hass` the card uses (callWS
returns the sample payload, callService logs, subscribeEvents is a no-op) and defines a stand-in `home-assistant`
element so the card registers at once. Posters are SVG data URLs so the layout is judged, not Plex's artwork.

    python tests/render_card.py -> tests/out/card_panel.png, card_phone.png, card_sheet.png, card_compact.png
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _load  # noqa: E402,F401
from custom_components.plex_picks import model as m  # noqa: E402

ROOT = os.path.dirname(HERE)
CARD = os.path.join(ROOT, "custom_components", "plex_picks", "www", "plex-picks-card.js")
OUT = os.path.join(HERE, "out")
BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]

PALETTE = ["#8e3b2f", "#2f5d8e", "#3b7a57", "#6b4c9a", "#b06d1f", "#1f6f7a", "#7a1f4e", "#4a4a4a", "#9a7b2a", "#2a6e9a"]


def poster(title: str, i: int) -> str:
    colour = PALETTE[i % len(PALETTE)]
    words = title.split()
    lines = [" ".join(words[:2]), " ".join(words[2:5])]
    text = "".join(f'<text x="150" y="{230 + 44 * n}" font-family="Segoe UI, Arial" font-size="34" font-weight="700" fill="#fff" text-anchor="middle">{l}</text>' for n, l in enumerate(lines) if l)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{colour}"/><stop offset="1" stop-color="#111"/></linearGradient></defs><rect width="300" height="450" fill="url(#g)"/>{text}</svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def art(i: int) -> str:
    colour = PALETTE[i % len(PALETTE)]
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{colour}"/><stop offset="1" stop-color="#0a0a0a"/></linearGradient></defs><rect width="1280" height="720" fill="url(#g)"/><circle cx="980" cy="200" r="160" fill="rgba(255,255,255,0.08)"/></svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


MOVIES = [("Heat", 1995, ["Crime", "Thriller"], ["Al Pacino", "Robert De Niro", "Val Kilmer"], "Michael Mann"),
          ("Casino", 1995, ["Crime", "Drama"], ["Robert De Niro", "Sharon Stone", "Joe Pesci"], "Martin Scorsese"),
          ("The Departed", 2006, ["Crime", "Thriller"], ["Leonardo DiCaprio", "Matt Damon"], "Martin Scorsese"),
          ("Sicario", 2015, ["Action", "Crime"], ["Emily Blunt", "Benicio del Toro"], "Denis Villeneuve"),
          ("Collateral", 2004, ["Crime", "Thriller"], ["Tom Cruise", "Jamie Foxx"], "Michael Mann"),
          ("The Town", 2010, ["Crime", "Drama"], ["Ben Affleck", "Rebecca Hall"], "Ben Affleck"),
          ("Prisoners", 2013, ["Drama", "Thriller"], ["Hugh Jackman", "Jake Gyllenhaal"], "Denis Villeneuve"),
          ("Zodiac", 2007, ["Crime", "Mystery"], ["Jake Gyllenhaal", "Mark Ruffalo"], "David Fincher"),
          ("Nightcrawler", 2014, ["Crime", "Drama"], ["Jake Gyllenhaal", "Rene Russo"], "Dan Gilroy"),
          ("Hell or High Water", 2016, ["Crime", "Drama"], ["Chris Pine", "Ben Foster"], "David Mackenzie"),
          ("Wind River", 2017, ["Crime", "Mystery"], ["Jeremy Renner", "Elizabeth Olsen"], "Taylor Sheridan"),
          ("Blue Ruin", 2013, ["Thriller"], ["Macon Blair"], "Jeremy Saulnier")]
SHOWS = [("Slow Horses", 2022, 24, 18), ("The Bear", 2022, 28, 10), ("Shetland", 2013, 42, 42), ("Mare of Easttown", 2021, 7, 0),
         ("Happy Valley", 2014, 18, 6), ("Line of Duty", 2012, 36, 36), ("The Night Of", 2016, 8, 0), ("Broadchurch", 2013, 24, 24)]


def movie_md(i, title, year, genres, cast, director):
    return {"ratingKey": str(1000 + i), "type": "movie", "title": title, "year": year, "duration": (95 + 7 * i) * 60000, "audienceRating": 6.5 + (i % 4) * 0.7,
            "contentRating": "15", "summary": f"{title} is a tightly wound film that rewards a second look, with a cast on top form and a city that feels alive at night.",
            "tagline": "Nothing stays buried.", "studio": "Studio", "thumb": "/x", "art": "/y",
            "Genre": [{"tag": g} for g in genres], "Director": [{"tag": director}], "Role": [{"tag": c} for c in cast],
            "UltraBlurColors": {"topLeft": PALETTE[i % len(PALETTE)].lstrip("#"), "topRight": "120303", "bottomRight": "321e0b", "bottomLeft": "904612"}}


def show_md(i, title, year, leaf, viewed):
    return {"ratingKey": str(2000 + i), "type": "show", "title": title, "year": year, "leafCount": leaf, "viewedLeafCount": viewed, "audienceRating": 8.1,
            "contentRating": "15", "summary": f"{title}: a slow-burn series with a lot to say about loyalty.", "thumb": "/x", "art": "/y",
            "Genre": [{"tag": "Drama"}, {"tag": "Crime"}], "Role": [{"tag": "Gary Oldman"}, {"tag": "Kristin Scott Thomas"}]}


def episode_md(i, show, show_rk, season, number, name, offset, duration):
    return {"ratingKey": str(3000 + i), "type": "episode", "title": name, "index": number, "parentIndex": season, "grandparentTitle": show,
            "grandparentRatingKey": str(show_rk), "parentRatingKey": "x", "grandparentYear": 2022, "duration": duration, "viewOffset": offset,
            "summary": "Picking up where the last one left off.", "Genre": [{"tag": "Drama"}]}


def sample_payload():
    cont = [episode_md(1, "Slow Horses", 2000, 3, 4, "Uninvited Guests", 1300000, 2800000), episode_md(2, "Shetland", 2002, 8, 2, "Episode 2", 2200000, 3500000),
            movie_md(99, "Heat", 1995, ["Crime"], ["Al Pacino"], "Michael Mann") | {"viewOffset": 4200000, "duration": 10200000}]
    rows = [m.continue_row(cont, None)]
    because = {"ratingKey": "500", "title": "Because you watched GoodFellas", "updatedAt": 1792289926, "thumb": "/c"}
    rows.append(m.row_from_collection(because, ["shortlist", "shortlist_themarkchristie"], [movie_md(i, *mv) for i, mv in enumerate(MOVIES)], "Movies", "movie"))
    picked = {"ratingKey": "501", "title": "✨ TV Shows Picked for You", "updatedAt": 1792289926, "thumb": "/c"}
    rows.append(m.row_from_collection(picked, ["shortlist", "shortlist_themarkchristie"], [show_md(i, *s) for i, s in enumerate(SHOWS)], "TV Shows", "show"))
    picked4k = {"ratingKey": "502", "title": "✨ 4K Movies Picked for You", "updatedAt": 1792289926, "thumb": "/c"}
    rows.append(m.row_from_collection(picked4k, ["shortlist", "shortlist_themarkchristie"], [movie_md(20 + i, *mv) for i, mv in enumerate(MOVIES[5:11])], "4K Movies", "movie"))
    again = {"ratingKey": "503", "title": "Watch it again", "updatedAt": 1792289926, "thumb": "/c"}
    rows.append(m.row_from_collection(again, ["shortlist", "shortlist_themarkchristie"], [movie_md(40 + i, *mv) for i, mv in enumerate(MOVIES[::2])], "Movies", "movie"))
    rows.append(m.recent_row([movie_md(60 + i, *mv) for i, mv in enumerate(MOVIES[3:9])] + [show_md(60 + i, *s) for i, s in enumerate(SHOWS[:3])], limit=12))
    rows = m.sort_rows(rows, ["Movies", "4K Movies", "TV Shows"])
    payload = m.payload({"id": "a099b0a8a4a4b25746d4d5ac24f48b730371d228", "name": "Mark's Media Pro"}, rows, 1792300000)
    n = 0
    for row in payload["rows"]:
        for item in row["items"]:
            item["thumb"] = poster(item["title"], n)
            item["art"] = art(n)
            n += 1
    return {"payload": payload, "players": [{"entity_id": "media_player.77_oled_tq77s85daexxc", "name": '77" OLED', "state": "on"}],
            "server": payload["server"], "shortlist": {}, "generated_at": 1792300000}


def page(data: dict, config: dict, width: int, open_row: int | None, dark: bool) -> str:
    script = open(CARD, encoding="utf-8").read()
    theme = ("--primary-text-color:#e1e1e1;--secondary-text-color:#9b9b9b;--card-background-color:#1c1c1c;--ha-card-background:#1c1c1c;--secondary-background-color:#2b2b2b;--primary-background-color:#111;--primary-color:#e5a00d;--text-primary-color:#111;--divider-color:#333;--error-color:#ef5350;"
             if dark else
             "--primary-text-color:#212121;--secondary-text-color:#727272;--card-background-color:#fff;--ha-card-background:#fff;--secondary-background-color:#f0f0f0;--primary-background-color:#fafafa;--primary-color:#e5a00d;--text-primary-color:#fff;--divider-color:#e0e0e0;--error-color:#db4437;")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Plex Picks card</title>
<style>html{{{theme}}} body{{margin:0;padding:12px;background:var(--primary-background-color);font-family:Roboto,'Segoe UI',Arial,sans-serif;width:{width}px;box-sizing:border-box}} ha-card{{display:block;background:var(--ha-card-background);border-radius:12px;box-shadow:0 2px 6px rgba(0,0,0,0.2)}} #err{{position:fixed;top:0;left:0;z-index:99;color:#ff0;background:rgba(0,0,0,0.85);font:12px monospace;white-space:pre-wrap}}</style></head>
<body><div id="err"></div><plex-picks-card id="card"></plex-picks-card>
<script>
window.onerror=(m,s,l,c)=>{{document.getElementById('err').textContent+=m+' @'+l+':'+c+'\\n';}};
customElements.define('home-assistant', class extends HTMLElement{{}});
const DATA={json.dumps(data)};
const hass={{language:'en',states:{{'media_player.77_oled_tq77s85daexxc':{{state:'on',attributes:{{friendly_name:'77" OLED'}}}}}},
  callWS:async(msg)=>{{ if(msg.type==='plex_picks/data') return DATA; throw new Error('unknown '+msg.type); }},
  callService:async(d,s,data)=>{{ console.log('service',d,s,JSON.stringify(data)); return {{}}; }},
  connection:{{subscribeEvents:async()=>()=>{{}}}} }};
</script>
<script>{script}</script>
<script>
const card=document.getElementById('card'); card.setConfig({json.dumps(config)}); card.hass=hass;
{f"setTimeout(()=>{{const rows=card.shadowRoot.querySelectorAll('.row'); const t=rows[{open_row}] && rows[{open_row}].querySelector('.tile'); if(t) t.click(); window.scrollTo(0,0);}}, 400);" if open_row is not None else ""}
{"setTimeout(()=>{const s=card.shadowRoot.querySelector('.sheet'); const r=s&&s.getBoundingClientRect(); document.getElementById('err').textContent+='viewport '+window.innerWidth+'x'+window.innerHeight+' sheet '+(r?Math.round(r.left)+'..'+Math.round(r.right)+' w'+Math.round(r.width):'none')+' scrollW '+document.documentElement.scrollWidth;}, 900);" if os.environ.get("PP_DEBUG") else ""}
</script></body></html>"""


def shoot(html: str, out: str, width: int, height: int) -> None:
    browser = next((b for b in BROWSERS if os.path.exists(b)), None)
    if not browser:
        raise SystemExit("no Chrome or Edge found")
    tmp = tempfile.mkdtemp(prefix="plexpicks-")
    try:
        path = os.path.join(tmp, "card.html")
        open(path, "w", encoding="utf-8").write(html)
        profile = os.path.join(tmp, "profile")
        url = "file:///" + path.replace("\\", "/")
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}", "--virtual-time-budget=3000",
                        f"--window-size={width + 24},{height}", f"--screenshot={out}", url], capture_output=True, timeout=120)
        if not os.path.exists(out):
            raise SystemExit(f"no screenshot produced for {out}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    data = sample_payload()
    panel = {"rows": ["continue", "because", "picked", "again", "recent"]}
    shoot(page(data, panel, 1180, None, True), os.path.join(OUT, "card_panel.png"), 1180, 1500)
    # headless Chrome's smallest viewport is 512 wide: 488 + the 24 of padding, so the phone shots are not cropped
    shoot(page(data, panel, 488, None, True), os.path.join(OUT, "card_phone.png"), 488, 1400)
    shoot(page(data, panel, 1180, 0, True), os.path.join(OUT, "card_sheet.png"), 1180, 1100)
    shoot(page(data, panel, 1180, 1, True), os.path.join(OUT, "card_sheet_because.png"), 1180, 1100)
    shoot(page(data, panel, 488, 1, True), os.path.join(OUT, "card_sheet_phone.png"), 488, 900)
    shoot(page(data, {"compact": True, "rows": ["because"]}, 520, None, False), os.path.join(OUT, "card_compact.png"), 520, 380)
    print("written:", ", ".join(os.path.join(OUT, f) for f in ("card_panel.png", "card_phone.png", "card_sheet.png", "card_sheet_because.png", "card_sheet_phone.png", "card_compact.png")))


if __name__ == "__main__":
    main()
