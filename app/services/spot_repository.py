from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class SpotNotFoundError(KeyError):
    """Raised when a spot_id is not present in the repository."""


@dataclass(frozen=True)
class Spot:
    spot_id: str
    name: str
    lat: float
    lng: float


class SpotRepository:
    """In-memory lookup for the PoC spot master (see data/spots/*.json).

    No nearest-stop mapping is stored here; callers resolve that on the
    fly via GTFSEngine.nearby_stops(spot.lat, spot.lng, ...).
    """

    def __init__(self, spots_file: str | Path) -> None:
        path = Path(spots_file)
        if not path.exists():
            raise FileNotFoundError(f"Spot master file does not exist: {path}")

        raw = json.loads(path.read_text(encoding="utf-8"))
        self._spots: dict[str, Spot] = {
            entry["spot_id"]: Spot(
                spot_id=entry["spot_id"],
                name=entry["name"],
                lat=float(entry["lat"]),
                lng=float(entry["lng"]),
            )
            for entry in raw
        }

    def get(self, spot_id: str) -> Spot:
        try:
            return self._spots[spot_id]
        except KeyError:
            raise SpotNotFoundError(spot_id) from None
