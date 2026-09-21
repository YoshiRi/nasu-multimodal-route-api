from pathlib import Path

from fastapi.testclient import TestClient

from app.api.deps import get_gtfs_engine
from app.main import app
from gtfs_multifeed import GTFSEngine

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"

# Pin the engine to the bundled fixture regardless of whatever real GTFS
# data happens to exist under data/gtfs/ on this machine.
app.dependency_overrides[get_gtfs_engine] = lambda: GTFSEngine(FIXTURE_DIR)

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_transit_network_returns_stops_and_shapes() -> None:
    response = client.get("/transit/network")

    assert response.status_code == 200
    body = response.json()
    stop_ids = {stop["stop_id"] for stop in body["stops"]}
    assert {"stop_yumoto", "stop_ropeway", "stop_station", "stop_extra"} <= stop_ids

    shape_ids = [shape["shape_id"] for shape in body["shapes"]]
    assert "shape_nasu_line" in shape_ids
    nasu_shape = next(s for s in body["shapes"] if s["shape_id"] == "shape_nasu_line")
    assert nasu_shape["line_name"] == "那須線"
    assert len(nasu_shape["points"]) == 3


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
