from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from app.services.opening_hours import OpeningHours


# For spots nobody has estimated a typical visit length for.
DEFAULT_STAY_MINUTES = 60


class SpotNotFoundError(KeyError):
    """Raised when a spot_id is not present in the repository."""


@dataclass(frozen=True)
class Spot:
    spot_id: str
    name: str
    lat: float
    lng: float
    # Free-form labels ("牧場", "登山", ...) used to narrow down what to
    # suggest visiting. A tuple so Spot stays hashable and immutable.
    tags: tuple[str, ...] = ()
    # None means the hours were never checked, not that it never closes.
    hours: OpeningHours | None = None
    # How long people typically stay; used when a request doesn't say.
    dwell_minutes: int | None = None

    def stay_minutes(self, requested: int | None = None) -> int:
        """How long to stay: the caller's figure, else this spot's, else an hour."""

        if requested is not None:
            return requested
        return self.dwell_minutes if self.dwell_minutes is not None else DEFAULT_STAY_MINUTES


class SpotRepository:
    """In-memory lookup for the PoC spot master (see data/spots/*.json).

    No nearest-stop mapping is stored here; callers resolve that on the
    fly via GTFSEngine.nearby_stops(spot.lat, spot.lng, ...).

    add() persists back to the same JSON file (atomic temp-file + rename)
    so additions survive a restart. This is a single-process, best-effort
    store meant for local debugging — not a substitute for a real
    database if this ever needs concurrent multi-user writes.
    """

    def __init__(self, spots_file: str | Path) -> None:
        path = Path(spots_file)
        if not path.exists():
            raise FileNotFoundError(f"Spot master file does not exist: {path}")

        self._path = path
        self._lock = Lock()
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._spots: dict[str, Spot] = {
            entry["spot_id"]: Spot(
                spot_id=entry["spot_id"],
                name=entry["name"],
                lat=float(entry["lat"]),
                lng=float(entry["lng"]),
                # Spot files predating tags stay loadable.
                tags=tuple(entry.get("tags") or ()),
                hours=OpeningHours.from_json(entry["hours"]) if entry.get("hours") else None,
                dwell_minutes=entry.get("dwell_minutes"),
            )
            for entry in raw
        }

    def get(self, spot_id: str) -> Spot:
        try:
            return self._spots[spot_id]
        except KeyError:
            raise SpotNotFoundError(spot_id) from None

    def list_all(self) -> list[Spot]:
        return list(self._spots.values())

    def add(
        self, name: str, lat: float, lng: float, tags: tuple[str, ...] = ()
    ) -> Spot:
        """Add a new spot with a generated spot_id and persist to disk."""

        with self._lock:
            spot_id = self._generate_spot_id()
            spot = Spot(spot_id=spot_id, name=name, lat=lat, lng=lng, tags=tuple(tags))
            self._spots[spot_id] = spot
            self._persist()
            return spot

    def _generate_spot_id(self) -> str:
        while True:
            candidate = f"spot_{uuid.uuid4().hex[:8]}"
            if candidate not in self._spots:
                return candidate

    def _persist(self) -> None:
        raw = [_spot_json(spot) for spot in self._spots.values()]
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp_path, self._path)


def _spot_json(spot: Spot) -> dict:
    raw = {
        "spot_id": spot.spot_id,
        "name": spot.name,
        "lat": spot.lat,
        "lng": spot.lng,
        "tags": list(spot.tags),
    }
    if spot.dwell_minutes is not None:
        raw["dwell_minutes"] = spot.dwell_minutes
    if spot.hours is not None:
        raw["hours"] = spot.hours.to_json()
    return raw
