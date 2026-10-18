# Shortlist add-on

Shortlist (by Steven Adams, MIT, https://github.com/stevezau/shortlist) builds per-person recommendation rows on Plex
Home from each person's watch history: Because you watched, Picked for You, Watch it again, and more. This add-on is
a thin wrapper around the published image; nothing is built or changed here.

## Requirements

- Plex Media Server 1.43.2.10687 or newer, with Plex Pass on the server owner's account.
- A free TMDB API key (themoviedb.org, no card).
- This Home Assistant host can reach the Plex server and plex.tv.

## Install

1. Copy the `shortlist` folder (this file's folder) to `/addons/shortlist` on the Home Assistant host, through the
   Samba share or the Terminal & SSH add-on.
2. Settings > Add-ons > Add-on store, the three-dots menu, **Check for updates**. Shortlist appears under
   **Local add-ons**.
3. Install, then Start. Open Web UI opens `http://<home-assistant>:5959`.
4. The setup wizard: sign in with Plex (PIN), pick the server, enter the TMDB key, choose **None** for the AI provider,
   tick the people who get rows, name the first row, and let the first run go.

The first sign-in claims this Shortlist instance: do it from the home network.

## Dry run first

`SHORTLIST_DRY_RUN` is `"1"` in `config.yaml` to begin with, so every run (the wizard's first run included) logs what
it would write to Plex and writes nothing. Read Runs > How we picked. When the picks look right, set
`SHORTLIST_DRY_RUN: "0"` in `config.yaml`, **Check for updates** again, and use the add-on's **Rebuild**.

## Updating Shortlist

Change `version:` in `config.yaml` to the new tag (the tags on ghcr.io/stevezau/shortlist match the releases:
`1.12.1`, `1.12.0`, ...), **Check for updates**, then Update. Back up `/addon_configs/shortlist` first; the releases
say so.

## Where things are

- Shortlist's data: `/addon_configs/shortlist` on the host (`/config` inside the container): `shortlist.db`,
  `secret.key`, `session.secret`, `client.id`, `backups/`, `logs/`.
- Web interface: port 5959 on the Home Assistant host. Inside Home Assistant, other add-ons and integrations reach it
  at `http://local-shortlist:5959`.
- Uninstalling Shortlist properly: in its web interface, Settings > System > Danger zone > Uninstall restores the
  Plex sharing filters and removes its collections. Removing the add-on alone leaves those in Plex.

## Environment

| Variable | Value here | Why |
| --- | --- | --- |
| `TZ` | `Europe/Oslo` | Schedules in local time |
| `PUID` / `PGID` | `0` | The add-on's config folder is root-owned |
| `SHORTLIST_DRY_RUN` | `"1"` until the first real run | Safety |
| `PORT` | image default `5959` | |
| `SHORTLIST_CONFIG` | image default `/config` | |
