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


def test_list_all_returns_every_spot() -> None:
    repo = SpotRepository(SPOTS_FILE)

    names = {spot.name for spot in repo.list_all()}

    assert "那須ロープウェイ" in names
    assert len(repo.list_all()) >= 3


def _empty_spots_repo(tmp_path: Path) -> SpotRepository:
    spots_file = tmp_path / "spots.json"
    spots_file.write_text("[]", encoding="utf-8")
    return SpotRepository(spots_file)


def test_add_returns_spot_with_generated_id_and_appears_in_list_all(tmp_path: Path) -> None:
    repo = _empty_spots_repo(tmp_path)

    spot = repo.add(name="New Spot", lat=1.0, lng=2.0)

    assert spot.spot_id.startswith("spot_")
    assert spot.name == "New Spot"
    assert repo.get(spot.spot_id) == spot
    assert spot in repo.list_all()


def test_add_persists_to_disk_and_is_loaded_by_a_new_repository(tmp_path: Path) -> None:
    repo = _empty_spots_repo(tmp_path)
    spots_file = tmp_path / "spots.json"

    added = repo.add(name="Persisted Spot", lat=3.0, lng=4.0)

    reloaded = SpotRepository(spots_file)
    reloaded_spot = reloaded.get(added.spot_id)
    assert reloaded_spot.name == "Persisted Spot"
    assert reloaded_spot.lat == pytest.approx(3.0)
    assert reloaded_spot.lng == pytest.approx(4.0)


def test_add_does_not_lose_existing_spots(tmp_path: Path) -> None:
    spots_file = tmp_path / "spots.json"
    spots_file.write_text(
        json.dumps([{"spot_id": "existing", "name": "Existing", "lat": 1.0, "lng": 1.0}]),
        encoding="utf-8",
    )
    repo = SpotRepository(spots_file)

    repo.add(name="New", lat=2.0, lng=2.0)

    reloaded = SpotRepository(spots_file)
    assert reloaded.get("existing").name == "Existing"
    assert len(reloaded.list_all()) == 2
