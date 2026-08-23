import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_spot_repository
from app.main import app
from app.services.spot_repository import SpotRepository


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    spots_file = tmp_path / "spots.json"
    spots_file.write_text(
        json.dumps([{"spot_id": "existing", "name": "Existing Spot", "lat": 1.0, "lng": 2.0}]),
        encoding="utf-8",
    )
    app.dependency_overrides[get_spot_repository] = lambda: SpotRepository(spots_file)
    try:
        yield TestClient(app)
    finally:
        del app.dependency_overrides[get_spot_repository]


def test_list_spots_returns_existing_spots(client: TestClient) -> None:
    response = client.get("/spots")

    assert response.status_code == 200
    body = response.json()
    assert [spot["spot_id"] for spot in body] == ["existing"]
    assert body[0]["name"] == "Existing Spot"


def test_create_spot_returns_generated_id_and_appears_in_list(client: TestClient) -> None:
    response = client.post("/spots", json={"name": "New Spot", "lat": 37.1, "lng": 140.0})

    assert response.status_code == 200
    created = response.json()
    assert created["spot_id"].startswith("spot_")
    assert created["name"] == "New Spot"

    listed = client.get("/spots").json()
    assert any(spot["spot_id"] == created["spot_id"] for spot in listed)


def test_create_spot_rejects_out_of_range_latitude(client: TestClient) -> None:
    response = client.post("/spots", json={"name": "Bad Spot", "lat": 999.0, "lng": 140.0})

    assert response.status_code == 422


def test_create_spot_rejects_empty_name(client: TestClient) -> None:
    response = client.post("/spots", json={"name": "", "lat": 37.1, "lng": 140.0})

    assert response.status_code == 422
