from pathlib import Path

from fastapi.testclient import TestClient

from app.api.deps import get_gtfs_engine
from app.main import app
from app.services.gtfs_engine import GTFSEngine

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"

# Pin the engine to the bundled fixture regardless of whatever real GTFS
# data happens to exist under data/gtfs/ on this machine.
app.dependency_overrides[get_gtfs_engine] = lambda: GTFSEngine(FIXTURE_DIR)

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_nearby_stops_returns_stops_within_radius() -> None:
    response = client.get(
        "/transit/stops/nearby",
        params={"lat": 37.0950, "lng": 140.0010, "radius_m": 1000},
    )

    assert response.status_code == 200
    body = response.json()
    assert [stop["stop_id"] for stop in body] == ["stop_yumoto"]
    assert body[0]["distance_m"] == 0.0


def test_nearby_stops_returns_empty_when_nothing_in_radius() -> None:
    response = client.get(
        "/transit/stops/nearby",
        params={"lat": 0.0, "lng": 0.0, "radius_m": 500},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_transit_routes_returns_direct_trips() -> None:
    response = client.get(
        "/transit/routes",
        params={
            "origin_stop_id": "stop_yumoto",
            "destination_stop_id": "stop_ropeway",
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [trip["trip_id"] for trip in body] == ["trip_1", "trip_2", "trip_3"]


def test_transit_routes_rejects_same_origin_and_destination() -> None:
    response = client.get(
        "/transit/routes",
        params={
            "origin_stop_id": "stop_yumoto",
            "destination_stop_id": "stop_yumoto",
            "departure_time": "10:00",
        },
    )

    assert response.status_code == 400
