"""Sensors: one per person Shortlist built rows for, Continue Watching, recently added, and Shortlist's last run.

States are counts; attributes stay small (one line per row). The card reads the full lists over the websocket."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SHORTLIST_TOKEN, CONF_SHORTLIST_URL, DOMAIN, NAME
from .coordinator import PlexPicksCoordinator
from .model import KIND_CONTINUE, KIND_RECENT, Row, summarise

BIG = frozenset({"rows", "titles"})


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator: PlexPicksCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _sync() -> None:
        new: list[SensorEntity] = []
        for slug in coordinator.data.get("payload", {}).get("people", []) if coordinator.data else []:
            if slug not in known:
                known.add(slug)
                new.append(PicksSensor(coordinator, slug))
        if new:
            async_add_entities(new)

    entities: list[SensorEntity] = [KindSensor(coordinator, KIND_CONTINUE, "Continue watching", "mdi:play-circle-outline"),
                                    KindSensor(coordinator, KIND_RECENT, "Recently added", "mdi:new-box")]
    if entry.options.get(CONF_SHORTLIST_URL) and entry.options.get(CONF_SHORTLIST_TOKEN):
        entities.append(LastRunSensor(coordinator))
    async_add_entities(entities)
    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


def _device(coordinator: PlexPicksCoordinator) -> DeviceInfo:
    """One service device named plainly "Plex Picks", so entity ids read sensor.plex_picks_<thing>; the server is
    the model."""
    return DeviceInfo(identifiers={(DOMAIN, coordinator.entry.entry_id)}, name=NAME,
                      manufacturer="Plex Picks", model=str(coordinator.server.get("name") or "Plex Media Server"),
                      entry_type=DeviceEntryType.SERVICE, configuration_url=str(coordinator.server.get("url") or ""))


class _Base(CoordinatorEntity[PlexPicksCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _unrecorded_attributes = BIG

    def __init__(self, coordinator: PlexPicksCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = _device(coordinator)

    def _rows(self) -> list[Row]:
        return list(self.coordinator.rows)

    def _common(self) -> dict[str, Any]:
        # no "updated" time: it changed on every poll and made each sensor write a new state when nothing else did
        return {"server": self.coordinator.server.get("name")}


class PicksSensor(_Base):
    """Everything Shortlist built for one person: state = how many titles across their rows."""

    def __init__(self, coordinator: PlexPicksCoordinator, slug: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{coordinator.entry.entry_id}_picks_{slug}"
        self._attr_name = f"Picks {slug}"
        self._attr_icon = "mdi:movie-open-star-outline"

    def _mine(self) -> list[Row]:
        return [r for r in self._rows() if r.slug == self._slug]

    @property
    def native_value(self) -> int:
        return sum(r.count for r in self._mine())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rows = self._mine()
        return {**self._common(), "slug": self._slug, "row_count": len(rows), "rows": summarise(rows)}


class KindSensor(_Base):
    """A shared row by kind: Continue watching, Recently added."""

    def __init__(self, coordinator: PlexPicksCoordinator, kind: str, label: str, icon: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{kind}"
        self._attr_name = label
        self._attr_icon = icon

    def _row(self) -> Row | None:
        return next((r for r in self._rows() if r.kind == self._kind), None)

    @property
    def native_value(self) -> int:
        row = self._row()
        return row.count if row else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        row = self._row()
        titles = [f"{i.title}{' · ' + i.subtitle if i.subtitle else ''}" for i in (row.items if row else [])][:20]
        return {**self._common(), "kind": self._kind, "titles": titles}


class LastRunSensor(_Base):
    """Shortlist's latest run, when its API is configured."""

    def __init__(self, coordinator: PlexPicksCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_run"
        self._attr_name = "Shortlist last run"
        self._attr_icon = "mdi:robot-outline"

    @property
    def native_value(self) -> str | None:
        run = self.coordinator.shortlist or {}
        if "error" in run:
            return "unknown"
        return str(run.get("status") or ("ok" if run else "unknown"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {**self._common(), **(self.coordinator.shortlist or {})}
