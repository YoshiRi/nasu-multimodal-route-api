from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.spot_repository import SpotNotFoundError, SpotRepository

SPOTS_FILE = Path(__file__).parents[1] / "data" / "spots" / "nasu_spots.json"


def test_get_returns_known_spot() -> None:
    repo = SpotRepository(SPOTS_FILE)

    spot = repo.get("spot_nasu_ropeway")

    assert spot.name == "那須ロープウェイ"
    assert spot.lat == pytest.approx(37.1240)
    assert spot.lng == pytest.approx(139.9850)


def test_get_raises_for_unknown_spot() -> None:
    repo = SpotRepository(SPOTS_FILE)

    with pytest.raises(SpotNotFoundError):
        repo.get("does_not_exist")


def test_init_raises_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SpotRepository(tmp_path / "missing.json")


def test_init_loads_arbitrary_spots_file(tmp_path: Path) -> None:
    spots_file = tmp_path / "spots.json"
    spots_file.write_text(
        json.dumps([{"spot_id": "a", "name": "A", "lat": 1.0, "lng": 2.0}]),
        encoding="utf-8",
    )

    repo = SpotRepository(spots_file)

    assert repo.get("a").name == "A"
