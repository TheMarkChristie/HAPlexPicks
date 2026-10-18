"""Plex Picks: Shortlist's per-person recommendation rows, the owner's Continue Watching and what is new on the
server, read from Plex and shown in Home Assistant as a Netflix-style card, with a Play button for the TVs.

The integration serves its own card, proxies posters so the Plex token never reaches a browser, and offers a
websocket command the card reads and two services (refresh, play)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .card_resource import async_remove_card_resource, async_schedule_card_resource
from .const import CARD_FILE, DOMAIN, URL_BASE
from .coordinator import PlexPicksCoordinator
from .services import async_register_services, async_unregister_services
from .views import PlexPicksImageView
from .websocket import async_register_websocket

PLATFORMS = [Platform.SENSOR]
_STATIC_REGISTERED = f"{DOMAIN}_static_registered"
_API_REGISTERED = f"{DOMAIN}_api_registered"
_CARD_URL = f"{DOMAIN}_card_url"


def _card_hash() -> str:
    try:
        return hashlib.sha256((Path(__file__).parent / "www" / CARD_FILE).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


async def _card_url(hass: HomeAssistant) -> str:
    """The card's URL with a version and a hash of the file, so a changed file is never served from a browser cache."""
    if url := hass.data.get(_CARD_URL):
        return url
    integration = await async_get_integration(hass, DOMAIN)
    digest = await hass.async_add_executor_job(_card_hash)
    url = f"{URL_BASE}/{CARD_FILE}?v={integration.version}-{digest}"
    hass.data[_CARD_URL] = url
    return url


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the card and put it in front of every dashboard; once per run."""
    if not hass.data.get(_STATIC_REGISTERED):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(URL_BASE, str(Path(__file__).parent / "www"), cache_headers=False)])
        hass.data[_STATIC_REGISTERED] = True
    url = await _card_url(hass)
    frontend.add_extra_js_url(hass, url)
    async_schedule_card_resource(hass, url)
    return True


def coordinators(hass: HomeAssistant) -> list[PlexPicksCoordinator]:
    """Every loaded entry's coordinator (there is normally one)."""
    out: list[PlexPicksCoordinator] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, PlexPicksCoordinator):
            out.append(coordinator)
    return out


def coordinator_for(hass: HomeAssistant, entry_id: str | None = None) -> PlexPicksCoordinator | None:
    for coordinator in coordinators(hass):
        if entry_id is None or coordinator.entry.entry_id == entry_id:
            return coordinator
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = PlexPicksCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    if not hass.data.get(_API_REGISTERED):
        hass.http.register_view(PlexPicksImageView(hass))
        async_register_websocket(hass)
        async_register_services(hass)
        hass.data[_API_REGISTERED] = True
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_changed))
    return True


async def _async_options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and not any(e.entry_id != entry.entry_id for e in hass.config_entries.async_entries(DOMAIN)):
        async_unregister_services(hass)
        hass.data.pop(_API_REGISTERED, None)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The last entry removed drops the card's dashboard resource."""
    if not any(e.entry_id != entry.entry_id for e in hass.config_entries.async_entries(DOMAIN)):
        frontend.remove_extra_js_url(hass, await _card_url(hass))
        await async_remove_card_resource(hass)
