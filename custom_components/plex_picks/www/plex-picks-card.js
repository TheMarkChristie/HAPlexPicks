/*
 * Plex Picks card: Shortlist's recommendation rows from Plex, Netflix-style, with Play on a TV.
 *
 *   type: custom:plex-picks-card
 *   rows: [continue, because, picked, again, shortlist, recent]   # which rows (kinds or row ids); default: every row
 *   slug: themarkchristie                              # whose Shortlist rows; default: everyone's
 *   libraries: [Movies, TV Shows]                      # only rows for these libraries; default: all
 *   compact: false                                     # true: one row, smaller posters, for a Home dashboard section
 *   max_items: 40                                      # per row
 *   players: [media_player.tv]                         # the TVs offered on Play; default: the integration's list
 *                                                      #   (its options, else the house's Google Cast devices)
 *   title: ""                                          # a heading above the rows; none by default
 *   merge_libraries: true                              # one films row and one shows row per kind, whatever the libraries; false keeps Plex's per-library rows
 *
 * The card subscribes to plex_picks/subscribe while it is on screen (any signed-in user) and reads plex_picks/data
 * when the rows change, so it keeps refreshing after the dashboard moves it between views. It keeps one ha-card and
 * redraws only the rows, and only when they changed, so an open detail sheet, the scroll position of each row and the
 * keyboard focus survive a refresh. The detail sheet is a native modal dialog.
 *
 * The integration serves this file and registers it as a dashboard resource; nothing to install.
 * Copyright (c) 2026 Mark Christie. MIT licence.
 */
(function () {
  const STR = {
    en: {
      continue: "Continue watching", recent: "New on the server", notSetUp: "Plex Picks is not set up yet. Add the integration under Settings > Devices & services.",
      noRows: "No rows yet. Shortlist builds them on its first run; this card fills in once they exist.",
      noMatch: "No rows match this card's filters.", loading: "Loading your picks", watched: (p) => `${p}% watched`,
      waiting: "Waiting for Plex. The rows appear once the first read is done.", stale: "Could not refresh; showing the last rows.",
      playOn: "Play on", openPlex: "Open in Plex", close: "Close", because: "Because you watched", resume: "Resume",
      sending: (tv) => `Sending to ${tv}…`, sent: (tv) => `Playing on ${tv}`, failed: "Could not play", min: "min", h: "h",
      cast: "Cast", director: "Director", directors: "Directors", episodes: (n, t) => `${n} of ${t} episodes watched`,
      noPlayers: "No TVs found. Plex Picks offers the house's Google Cast devices, or the players in its options.", lib: "Library",
      unavailable: "unavailable",
      pickedFilms: "Films picked for you", pickedShows: "Shows picked for you", againFilms: "Films to watch again", againShows: "Shows to watch again",
      popularFilms: "Popular films on the server", popularShows: "Popular shows on the server",
    },
    nb: {
      continue: "Fortsett å se", recent: "Nytt på serveren", notSetUp: "Plex Picks er ikke satt opp ennå. Legg til integrasjonen under Innstillinger > Enheter og tjenester.",
      noRows: "Ingen rader ennå. Shortlist lager dem ved første kjøring; kortet fylles når de finnes.",
      noMatch: "Ingen rader passer kortets filtre.", loading: "Henter forslag", watched: (p) => `${p} % sett`,
      waiting: "Venter på Plex. Radene kommer når den første lesingen er ferdig.", stale: "Kunne ikke oppdatere; viser de siste radene.",
      playOn: "Spill på", openPlex: "Åpne i Plex", close: "Lukk", because: "Fordi du så", resume: "Fortsett",
      sending: (tv) => `Sender til ${tv}…`, sent: (tv) => `Spiller på ${tv}`, failed: "Kunne ikke spille", min: "min", h: "t",
      cast: "Skuespillere", director: "Regi", directors: "Regi", episodes: (n, t) => `${n} av ${t} episoder sett`,
      noPlayers: "Fant ingen TV-er. Plex Picks tilbyr husets Google Cast-enheter, eller spillerne i valgene.", lib: "Bibliotek",
      unavailable: "utilgjengelig",
      pickedFilms: "Filmer valgt for deg", pickedShows: "Serier valgt for deg", againFilms: "Filmer å se igjen", againShows: "Serier å se igjen",
      popularFilms: "Populære filmer på serveren", popularShows: "Populære serier på serveren",
    },
  };

  const CSS = `
    :host { display: block; --pp-poster-w: 150px; --pp-gap: 12px; --pp-radius: 10px; }
    :host([compact]) { --pp-poster-w: 112px; --pp-gap: 10px; }
    @media (max-width: 600px) { :host { --pp-poster-w: 118px; } :host([compact]) { --pp-poster-w: 98px; } }
    ha-card { display: block; padding: 6px 0 10px; overflow: hidden; }
    .title { font-size: 1.3em; font-weight: 500; margin: 10px 16px 2px; color: var(--primary-text-color); }
    .msg { padding: 18px 16px; color: var(--secondary-text-color); line-height: 1.45; }
    .notice { padding: 6px 16px 0; font-size: 0.8em; color: var(--secondary-text-color); }
    .row { margin: 6px 0 2px; }
    .rowhead { display: flex; align-items: baseline; gap: 10px; padding: 8px 16px 6px; }
    .rowhead h3 { margin: 0; font-size: 1.05em; font-weight: 600; color: var(--primary-text-color); letter-spacing: 0.01em; }
    .pill { font-size: 0.72em; padding: 2px 8px; border-radius: 999px; background: var(--secondary-background-color, rgba(127,127,127,0.18)); color: var(--secondary-text-color); white-space: nowrap; }
    .count { margin-left: auto; font-size: 0.78em; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
    .scroller { display: flex; gap: var(--pp-gap); overflow-x: auto; overflow-y: hidden; padding: 4px 16px 12px; scroll-snap-type: x proximity; scrollbar-width: thin; -webkit-overflow-scrolling: touch; margin: 0; list-style: none; }
    .scroller::-webkit-scrollbar { height: 6px; } .scroller::-webkit-scrollbar-thumb { background: rgba(127,127,127,0.35); border-radius: 3px; }
    .cell { flex: 0 0 var(--pp-poster-w); width: var(--pp-poster-w); scroll-snap-align: start; }
    .tile { display: block; width: 100%; background: none; border: 0; padding: 0; margin: 0; text-align: left; cursor: pointer; color: inherit; font: inherit; border-radius: var(--pp-radius); }
    .tile:focus-visible { outline: 3px solid var(--primary-color); outline-offset: 3px; }
    .poster { position: relative; width: 100%; aspect-ratio: 2 / 3; border-radius: var(--pp-radius); overflow: hidden; background: var(--secondary-background-color, #2a2a2a); box-shadow: 0 2px 10px rgba(0,0,0,0.25); transition: transform 160ms ease, box-shadow 160ms ease; }
    .tile:hover .poster, .tile:focus-visible .poster { transform: translateY(-3px) scale(1.03); box-shadow: 0 8px 22px rgba(0,0,0,0.35); }
    .poster img { display: block; width: 100%; height: 100%; object-fit: cover; }
    .poster .ph { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 10px; text-align: center; font-weight: 600; color: var(--primary-text-color); background: linear-gradient(160deg, rgba(229,160,13,0.55), rgba(60,60,60,0.6)); }
    .bar { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; background: rgba(0,0,0,0.5); }
    .bar > i { display: block; height: 100%; background: var(--primary-color, #e5a00d); }
    .badge { position: absolute; top: 6px; left: 6px; font-size: 0.68em; padding: 1px 6px; border-radius: 4px; background: rgba(0,0,0,0.65); color: #fff; letter-spacing: 0.04em; }
    .cap { padding: 6px 2px 0; }
    .cap .t { font-size: 0.86em; line-height: 1.25; color: var(--primary-text-color); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .cap .m { font-size: 0.75em; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
    :host([compact]) .cap .m { display: none; }
    @media (prefers-reduced-motion: reduce) { .poster, .tile:hover .poster { transition: none; transform: none; } }

    dialog.sheet { padding: 0; border: 0; width: min(820px, calc(100vw - 32px)); max-width: none; max-height: min(92vh, 900px); overflow: auto; border-radius: 16px; background: var(--card-background-color, #1e1e1e); color: var(--primary-text-color); box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
    dialog.sheet::backdrop { background: rgba(0,0,0,0.6); backdrop-filter: blur(6px); }
    dialog.sheet:focus-visible { outline: none; }
    .hero { position: relative; aspect-ratio: 16 / 9; max-height: 46vh; background-size: cover; background-position: center top; }
    .hero::after { content: ""; position: absolute; inset: 0; background: linear-gradient(to bottom, rgba(0,0,0,0.05) 30%, var(--card-background-color, #1e1e1e) 100%); }
    .hero .close { position: absolute; top: 10px; right: 10px; z-index: 1; width: 40px; height: 40px; border-radius: 50%; border: 0; background: rgba(0,0,0,0.55); color: #fff; font-size: 22px; line-height: 40px; text-align: center; cursor: pointer; }
    .hero .close:focus-visible { outline: 3px solid #fff; }
    .body { position: relative; padding: 0 22px 22px; margin-top: -56px; }
    .body h2 { margin: 0 0 4px; font-size: 1.7em; line-height: 1.15; text-shadow: 0 1px 6px rgba(0,0,0,0.6); }
    .body .sub { color: var(--secondary-text-color); margin-bottom: 8px; }
    .meta { display: flex; flex-wrap: wrap; gap: 6px 12px; font-size: 0.9em; color: var(--secondary-text-color); margin: 6px 0 10px; }
    .meta b { color: var(--primary-text-color); font-weight: 600; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 12px; }
    .chip { font-size: 0.78em; padding: 3px 9px; border-radius: 999px; background: var(--secondary-background-color, rgba(127,127,127,0.2)); color: var(--primary-text-color); }
    .reason { font-size: 0.9em; color: var(--secondary-text-color); margin: 0 0 10px; }
    .reason b { color: var(--primary-text-color); }
    .summary { line-height: 1.5; margin: 0 0 14px; }
    .people { font-size: 0.9em; color: var(--secondary-text-color); margin: 0 0 14px; line-height: 1.5; }
    .people b { color: var(--primary-text-color); font-weight: 600; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .btn { display: inline-flex; align-items: center; gap: 8px; padding: 10px 16px; border-radius: 10px; border: 0; cursor: pointer; font: inherit; font-weight: 600; background: var(--primary-color, #e5a00d); color: var(--text-primary-color, #fff); }
    .btn svg { width: 16px; height: 16px; flex: 0 0 16px; }
    .btn.secondary { background: var(--secondary-background-color, rgba(127,127,127,0.2)); color: var(--primary-text-color); text-decoration: none; }
    .btn:focus-visible { outline: 3px solid var(--primary-text-color); outline-offset: 2px; }
    .btn[disabled] { opacity: 0.6; cursor: default; }
    .status { width: 100%; font-size: 0.88em; color: var(--secondary-text-color); min-height: 1.2em; }
    .status.err { color: var(--error-color, #db4437); }
    @media (max-width: 600px) { dialog.sheet { width: 100vw; max-height: 96vh; margin: auto 0 0 0; border-radius: 16px 16px 0 0; } .body { padding: 0 16px 18px; } .body h2 { font-size: 1.35em; } }
  `;

  const PLAY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
  const DEFAULTS = { rows: null, exclude: [], slug: null, libraries: null, compact: false, max_items: 40, players: null, title: "", merge_libraries: true };
  const MERGEABLE = ["picked", "again", "because", "popular", "seasonal", "shortlist"];
  const RELOAD_MS = 6 * 3600 * 1000;           // fresh signed image addresses well before they expire (12 h)
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  class PlexPicksCard extends HTMLElement {
    static getStubConfig() { return { rows: ["continue", "because", "picked", "again", "shortlist", "recent"] }; }

    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = { ...DEFAULTS };
      this._data = null;
      this._error = null;          // "notSetUp" | "waiting" | null
      this._notice = "";
      this._loading = false;
      this._loadedAt = 0;
      this._sub = null;            // the subscribe promise (resolves to the unsubscribe function)
      this._timer = null;
      this._retry = null;
      this._sheet = null;
    }

    setConfig(config) {
      if (config && config.rows && !Array.isArray(config.rows)) throw new Error("rows must be a list");
      this._config = { ...DEFAULTS, ...(config || {}) };
      if (this._config.compact) this.setAttribute("compact", ""); else this.removeAttribute("compact");
      if (this._hass && this.isConnected) this._load(true);
      this._render();
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (first && this.isConnected) this._start();
    }

    get hass() { return this._hass; }

    getCardSize() { return this._config.compact ? 4 : 12; }

    connectedCallback() {
      this._render();
      if (this._hass) this._start();
    }

    disconnectedCallback() { this._stop(); }

    /* on screen: listen for changes, refresh the signed image addresses now and then, and read now if the last read
       is old (the rows may have changed while the card was off screen) */
    _start() {
      if (!this._sub && this._hass && this._hass.connection) {
        const sub = this._hass.connection.subscribeMessage((ev) => {
          if (!this._data || !ev || ev.version !== this._data.version) this._load();
        }, { type: "plex_picks/subscribe" });
        this._sub = sub;
        sub.catch(() => { if (this._sub === sub) this._sub = null; });
      }
      if (!this._timer) this._timer = setInterval(() => this._load(true), RELOAD_MS);
      if (!this._data || Date.now() - this._loadedAt > 5 * 60 * 1000) this._load();
    }

    _stop() {
      const sub = this._sub;
      this._sub = null;
      if (sub) sub.then((unsub) => unsub()).catch(() => {});
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
      if (this._retry) { clearTimeout(this._retry); this._retry = null; }
    }

    _t(key, ...args) {
      const lang = (this._hass && this._hass.language) || "en";
      const table = /^(nb|nn|no)/.test(lang) ? STR.nb : STR.en;
      const v = table[key] != null ? table[key] : STR.en[key];
      return typeof v === "function" ? v(...args) : v;
    }

    _query() {
      const q = { type: "plex_picks/data", art: !this._config.compact };
      if (Array.isArray(this._config.rows) && this._config.rows.length) q.rows = this._config.rows.map(String);
      if (this._config.slug) q.slug = String(this._config.slug);
      if (Array.isArray(this._config.libraries) && this._config.libraries.length) q.libraries = this._config.libraries.map(String);
      if (this._config.max_items) q.max_items = Math.max(1, Math.min(200, Number(this._config.max_items) || 40));
      return q;
    }

    async _load(force) {
      if (!this._hass || this._loading) return;
      this._loading = true;
      try {
        const data = await this._hass.callWS(this._query());
        const same = !force && this._data && data.version && data.version === this._data.version;
        const players = this._data && JSON.stringify(this._data.players) === JSON.stringify(data.players);
        this._data = data;
        this._loadedAt = Date.now();
        const hadProblem = this._error || this._notice;
        this._error = null;
        this._notice = "";
        if (!same || !players || hadProblem) this._render();
      } catch (e) {
        const code = e && (e.code || (e.error && e.error.code));
        if (code === "unknown_command") this._error = "notSetUp";
        else if (!this._data) this._error = "waiting";
        else this._notice = this._t("stale");
        if (code !== "unknown_command" && !this._retry) {
          this._retry = setTimeout(() => { this._retry = null; this._load(); }, 30000);
        }
        this._render();
      } finally {
        this._loading = false;
      }
    }

    _rows() {
      const all = (this._data && this._data.payload && this._data.payload.rows) || [];
      const want = this._config.rows ? this._config.rows.map(String) : null;
      const skip = (this._config.exclude || []).map(String);
      const libs = this._config.libraries ? this._config.libraries.map((l) => String(l).toLowerCase()) : null;
      let rows = all.filter((r) => {
        if (want && !want.includes(r.kind) && !want.includes(r.id)) return false;
        if (skip.includes(r.kind) || skip.includes(r.id)) return false;
        if (this._config.slug && r.slug && r.slug !== this._config.slug) return false;
        if (libs && r.library && !libs.includes(String(r.library).toLowerCase())) return false;
        return r.items && r.items.length;
      });
      if (this._config.merge_libraries !== false) rows = this._merge(rows);
      if (want) {
        const rank = (r) => Math.min(...[want.indexOf(r.kind), want.indexOf(r.id)].filter((i) => i >= 0));
        rows = rows.slice().sort((a, b) => rank(a) - rank(b));
      }
      if (this._config.compact) rows = rows.slice(0, 1);
      return rows;
    }

    /* Shortlist writes one collection per library (Plex allows nothing else), so "Movies Picked for You" and
     * "4K Movies Picked for You" arrive as two rows. Here rows of the same kind, person and media type become one:
     * films in one row, shows in another, the same title from two libraries shown once. */
    _merge(rows) {
      const out = [];
      const groups = new Map();
      for (const r of rows) {
        if (!MERGEABLE.includes(r.kind) || !r.library_type) { out.push(r); continue; }
        const tail = r.kind === "because" ? (r.seed || r.title) : (r.kind === "seasonal" || r.kind === "shortlist" ? r.title : "");
        const key = [r.kind, r.slug || "", r.library_type, tail].join("|");
        let g = groups.get(key);
        if (!g) {
          g = { ...r, id: "m:" + key, library: null, libraries: [], items: [], count: 0, merged: true, _seen: new Set() };
          groups.set(key, g);
          out.push(g);
        }
        g.libraries.push(r.library);
        for (const it of r.items) {
          const sig = `${it.title}|${it.year || ""}`.toLowerCase();
          if (g._seen.has(sig)) continue;
          g._seen.add(sig);
          g.items.push(it);
        }
        g.count = g.items.length;
      }
      return out;
    }

    _players() {
      const ids = this._config.players;
      if (ids && ids.length) {
        return ids.map((id) => {
          const s = this._hass && this._hass.states[id];
          return { entity_id: id, name: s ? (s.attributes.friendly_name || id) : id, available: !!s && s.state !== "unavailable" };
        });
      }
      return (this._data && this._data.players) || [];
    }

    _rowTitle(row) {
      if (row.kind === "continue") return this._t("continue");
      if (row.kind === "recent") return this._t("recent");
      if (row.kind === "because" && row.seed) return `${this._t("because")} ${row.seed}`;
      if (row.merged) {
        const films = row.library_type === "movie";
        if (row.kind === "picked") return this._t(films ? "pickedFilms" : "pickedShows");
        if (row.kind === "again") return this._t(films ? "againFilms" : "againShows");
        if (row.kind === "popular") return this._t(films ? "popularFilms" : "popularShows");
      }
      return row.title;
    }

    /* the card's frame is made once; later renders replace only the content, keeping each row's scroll position and
       the focused tile, so a refresh does not move anything under the user's finger */
    _shell() {
      const root = this.shadowRoot;
      let content = root.querySelector(".content");
      if (!content) {
        root.innerHTML = `<style>${CSS}</style><ha-card><div class="content"></div></ha-card>`;
        content = root.querySelector(".content");
        content.addEventListener("click", (ev) => {
          const tile = ev.target.closest && ev.target.closest(".tile");
          if (tile) this._open(tile.dataset.row, tile.dataset.key, tile);
        });
        content.addEventListener("keydown", (ev) => {
          const sc = ev.target.closest && ev.target.closest(".scroller");
          if (sc) this._arrows(ev, sc);
        });
      }
      return content;
    }

    _render() {
      if (!this.shadowRoot) return;
      const content = this._shell();
      let body;
      if (this._error === "notSetUp") {
        body = `<div class="msg">${esc(this._t("notSetUp"))}</div>`;
      } else if (this._error === "waiting") {
        body = `<div class="msg" aria-busy="true">${esc(this._t("waiting"))}</div>`;
      } else if (!this._data) {
        body = `<div class="msg" aria-busy="true">${esc(this._t("loading"))}…</div>`;
      } else {
        const rows = this._rows();
        const any = ((this._data.payload || {}).rows || []).length;
        if (!rows.length) body = `<div class="msg">${esc(this._t(any ? "noMatch" : "noRows"))}</div>`;
        else body = rows.map((r) => this._renderRow(r)).join("");
      }
      const title = this._config.title ? `<div class="title">${esc(this._config.title)}</div>` : "";
      const notice = this._notice ? `<div class="notice" role="status">${esc(this._notice)}</div>` : "";
      const scroll = {};
      content.querySelectorAll(".row").forEach((sec) => { scroll[sec.dataset.row] = sec.querySelector(".scroller").scrollLeft; });
      const active = this.shadowRoot.activeElement;
      const focus = active && active.classList && active.classList.contains("tile") ? [active.dataset.row, active.dataset.key] : null;
      content.innerHTML = `${title}${notice}${body}`;
      content.querySelectorAll(".row").forEach((sec) => {
        if (scroll[sec.dataset.row]) sec.querySelector(".scroller").scrollLeft = scroll[sec.dataset.row];
      });
      if (focus) {
        const again = [...content.querySelectorAll(".tile")].find((t) => t.dataset.row === focus[0] && t.dataset.key === focus[1]);
        if (again) again.focus({ preventScroll: true });
      }
    }

    _renderRow(row) {
      const items = row.items.slice(0, this._config.max_items || 40);
      const showLib = row.library && ["picked", "again", "popular", "seasonal", "shortlist"].includes(row.kind);
      const head = `<div class="rowhead"><h3>${esc(this._rowTitle(row))}</h3>${showLib ? `<span class="pill">${esc(row.library)}</span>` : ""}<span class="count">${items.length}</span></div>`;
      const tiles = items.map((it) => this._renderTile(row, it)).join("");
      return `<section class="row" data-row="${esc(row.id)}" aria-label="${esc(this._rowTitle(row))}">${head}<div class="scroller" role="list">${tiles}</div></section>`;
    }

    _renderTile(row, it) {
      const pct = it.progress != null ? Math.round(it.progress * 100) : null;
      const meta = [it.subtitle, it.year, it.library && row.kind === "recent" ? it.library : null].filter(Boolean).join(" · ");
      const label = [it.title, it.year ? `(${it.year})` : "", pct != null ? this._t("watched", pct) : ""].filter(Boolean).join(" ");
      const badge = it.type === "show" || it.type === "episode" ? "TV" : (it.library && /4k/i.test(it.library) ? "4K" : "");
      const img = it.thumb
        ? `<img src="${esc(it.thumb)}" alt="" loading="lazy" decoding="async" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><div class="ph" style="display:none">${esc(it.title)}</div>`
        : `<div class="ph">${esc(it.title)}</div>`;
      return `<div class="cell" role="listitem"><button class="tile" data-row="${esc(row.id)}" data-key="${esc(it.rating_key)}" aria-label="${esc(label)}">
        <div class="poster">${img}${badge ? `<span class="badge">${badge}</span>` : ""}${pct != null ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ""}</div>
        <div class="cap"><div class="t">${esc(it.title)}</div>${meta ? `<div class="m">${esc(meta)}</div>` : ""}</div>
      </button></div>`;
    }

    _arrows(ev, scroller) {
      if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
      const tiles = Array.from(scroller.querySelectorAll(".tile"));
      const i = tiles.indexOf(this.shadowRoot.activeElement);
      if (i < 0) return;
      const next = tiles[i + (ev.key === "ArrowRight" ? 1 : -1)];
      if (next) { ev.preventDefault(); next.focus(); next.scrollIntoView({ block: "nearest", inline: "nearest" }); }
    }

    _find(rowId, key) {
      // rendered rows first (merged rows exist only there), then the raw payload
      const raw = (this._data && this._data.payload && this._data.payload.rows) || [];
      const row = this._rows().find((r) => r.id === rowId) || raw.find((r) => r.id === rowId);
      const item = row && row.items.find((i) => String(i.rating_key) === String(key));
      return { row, item };
    }

    _dur(ms) {
      if (!ms) return "";
      const m = Math.round(ms / 60000);
      return m >= 60 ? `${Math.floor(m / 60)} ${this._t("h")} ${m % 60} ${this._t("min")}` : `${m} ${this._t("min")}`;
    }

    /* the detail sheet: a native modal dialog in the top layer, outside the rows, so a refresh does not close it;
       the page behind is inert, Escape closes it, and focus goes back to the tile */
    _open(rowId, key) {
      const { row, item } = this._find(rowId, key);
      if (!item) return;
      if (this._sheet) this._sheet.close();
      const players = this._players();
      const server = (this._data && this._data.server) || {};
      const c = item.colors || {};
      const hex = c.topLeft && /^[0-9a-f]{6}$/i.test(c.topLeft) ? `#${c.topLeft}` : null;
      const tintTop = hex ? `${hex}99` : "rgba(0,0,0,0.35)";
      const tintFlat = hex || "rgb(48,48,48)";
      const heroStyle = item.art
        ? `background-image: linear-gradient(to bottom, ${tintTop}, transparent 45%), url('${esc(item.art)}')`
        : `background: linear-gradient(160deg, ${tintFlat}, var(--card-background-color, #1e1e1e))`;
      const meta = [item.year, this._dur(item.duration_ms), item.content_rating, item.audience_rating ? `★ ${item.audience_rating}` : (item.rating ? `★ ${item.rating}` : null),
        item.leaf_count ? this._t("episodes", item.viewed_leaf_count || 0, item.leaf_count) : null].filter(Boolean);
      const pct = item.progress != null ? Math.round(item.progress * 100) : null;
      const reason = row && row.kind === "because" && row.seed ? `<p class="reason">${esc(this._t("because"))} <b>${esc(row.seed)}</b></p>` : (row && !["continue", "recent"].includes(row.kind) ? `<p class="reason">${esc(this._rowTitle(row))}${row.library ? ` · ${esc(row.library)}` : ""}</p>` : "");
      const people = [
        item.directors && item.directors.length ? `<b>${esc(item.directors.length > 1 ? this._t("directors") : this._t("director"))}:</b> ${esc(item.directors.join(", "))}` : "",
        item.cast && item.cast.length ? `<b>${esc(this._t("cast"))}:</b> ${esc(item.cast.join(", "))}` : "",
      ].filter(Boolean).join("<br>");
      const buttons = players.length
        ? players.map((p) => {
            const off = p.available === false;
            return `<button class="btn play" data-player="${esc(p.entity_id)}" data-name="${esc(p.name)}"${off ? " disabled" : ""}>${PLAY_ICON}${esc(this._t("playOn"))} ${esc(p.name)}${off ? ` (${esc(this._t("unavailable"))})` : ""}</button>`;
          }).join("")
        : `<span class="status">${esc(this._t("noPlayers"))}</span>`;
      const plexUrl = server.id ? `https://app.plex.tv/desktop/#!/server/${esc(server.id)}/details?key=%2Flibrary%2Fmetadata%2F${esc(item.rating_key)}` : "https://app.plex.tv/";
      const dialog = document.createElement("dialog");
      dialog.className = "sheet";
      dialog.setAttribute("aria-labelledby", "pp-title");
      dialog.innerHTML = `
          <div class="hero" style="${heroStyle}"><button class="close" aria-label="${esc(this._t("close"))}">×</button></div>
          <div class="body">
            <h2 id="pp-title">${esc(item.title)}</h2>
            ${item.subtitle ? `<div class="sub">${esc(item.subtitle)}${pct != null ? ` · ${esc(this._t("watched", pct))}` : ""}</div>` : (pct != null ? `<div class="sub">${esc(this._t("watched", pct))}</div>` : "")}
            ${item.tagline ? `<div class="sub"><i>${esc(item.tagline)}</i></div>` : ""}
            <div class="meta">${meta.map((m) => `<span>${esc(m)}</span>`).join("")}</div>
            ${item.genres && item.genres.length ? `<div class="chips">${item.genres.map((g) => `<span class="chip">${esc(g)}</span>`).join("")}</div>` : ""}
            ${reason}
            ${item.summary ? `<p class="summary">${esc(item.summary)}</p>` : ""}
            ${people ? `<p class="people">${people}</p>` : ""}
            <div class="actions">${buttons}<a class="btn secondary" href="${plexUrl}" target="_blank" rel="noopener">${esc(this._t("openPlex"))}</a><div class="status" aria-live="polite"></div></div>
          </div>`;
      dialog.addEventListener("click", (ev) => { if (ev.target === dialog) dialog.close(); });   // the backdrop
      dialog.querySelector(".close").addEventListener("click", () => dialog.close());
      dialog.querySelectorAll(".play").forEach((b) => b.addEventListener("click", () => this._play(item, b, dialog)));
      dialog.addEventListener("close", () => {
        dialog.remove();
        if (this._sheet === dialog) this._sheet = null;
        const tile = [...this.shadowRoot.querySelectorAll(".tile")].find((t) => t.dataset.row === rowId && t.dataset.key === String(key));
        if (tile) tile.focus({ preventScroll: true });
      });
      this.shadowRoot.appendChild(dialog);
      this._sheet = dialog;
      dialog.showModal();
    }

    async _play(item, button, dialog) {
      const status = dialog.querySelector(".status");
      const name = button.dataset.name;
      const buttons = dialog.querySelectorAll(".play");
      const wasDisabled = [...buttons].map((b) => b.disabled);
      buttons.forEach((b) => (b.disabled = true));
      status.className = "status";
      status.textContent = this._t("sending", name);
      try {
        await this._hass.callService("plex_picks", "play", {
          rating_key: String(item.play_key || item.rating_key),
          player: button.dataset.player,
          media_type: item.type === "episode" ? "episode" : item.type === "show" ? "show" : "movie",
          resume: item.progress != null && item.progress > 0.02,
        });
        status.textContent = this._t("sent", name);
        this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message: this._t("sent", name) }, bubbles: true, composed: true }));
      } catch (e) {
        status.className = "status err";
        status.textContent = `${this._t("failed")}: ${(e && (e.message || e.error)) || e}`;
      } finally {
        buttons.forEach((b, i) => (b.disabled = wasDisabled[i]));
      }
    }
  }

  /* Home Assistant's frontend replaces the custom element registry's methods as it starts (its scoped-registry
   * polyfill keeps its own table), so an element defined before that point is reported as not existing afterwards.
   * This file is imported by the page alongside the frontend itself, so it can run first: register once the frontend's
   * root element is known, which is immediate when the file arrives as a dashboard resource. */
  function whenFrontendReady(fn) {
    const ready = () => !!customElements.get("home-assistant");
    if (ready()) { fn(); return; }
    let tries = 0;
    const timer = setInterval(() => { if (ready() || ++tries > 600) { clearInterval(timer); fn(); } }, 50);
  }
  whenFrontendReady(() => {
    if (!customElements.get("plex-picks-card")) customElements.define("plex-picks-card", PlexPicksCard);
    window.customCards = window.customCards || [];
    if (!window.customCards.some((c) => c.type === "plex-picks-card")) {
      window.customCards.push({ type: "plex-picks-card", name: "Plex Picks", description: "Shortlist's recommendation rows from Plex, Netflix-style, with Play on a TV.", preview: true });
    }
  });
})();
