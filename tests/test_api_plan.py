from pathlib import Path

from fastapi.testclient import TestClient

from app.api.deps import get_gtfs_engine
from app.main import app
from app.services.gtfs_engine import GTFSEngine

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"

app.dependency_overrides[get_gtfs_engine] = lambda: GTFSEngine(FIXTURE_DIR)

client = TestClient(app)


def test_graph_data_returns_an_edge_per_ordered_pair() -> None:
    response = client.post(
        "/plan/graph-data",
        json={"stop_ids": ["stop_yumoto", "stop_ropeway"], "departure_time": "09:00"},
    )

    assert response.status_code == 200
    edges = response.json()["edges"]
    assert len(edges) == 2

    forward = next(
        e for e in edges if e["from_stop_id"] == "stop_yumoto" and e["to_stop_id"] == "stop_ropeway"
    )
    assert [o["trip_id"] for o in forward["bus_options"]] == ["trip_1", "trip_2", "trip_3"]
    assert forward["mode"] == "bus"

    backward = next(
        e for e in edges if e["from_stop_id"] == "stop_ropeway" and e["to_stop_id"] == "stop_yumoto"
    )
    assert [o["trip_id"] for o in backward["bus_options"]] == ["trip_reverse"]


def test_graph_data_returns_empty_options_when_no_direct_trip() -> None:
    response = client.post(
        "/plan/graph-data",
        json={"stop_ids": ["stop_yumoto", "stop_station"], "departure_time": "10:00"},
    )

    assert response.status_code == 200
    edges = response.json()["edges"]
    assert len(edges) == 2
    assert all(edge["bus_options"] == [] for edge in edges)


def test_graph_data_rejects_too_few_stop_ids() -> None:
    response = client.post(
        "/plan/graph-data",
        json={"stop_ids": ["stop_yumoto"], "departure_time": "10:00"},
    )

    assert response.status_code == 422


def test_graph_data_rejects_duplicate_stop_ids() -> None:
    response = client.post(
        "/plan/graph-data",
        json={"stop_ids": ["stop_yumoto", "stop_yumoto"], "departure_time": "10:00"},
    )

    assert response.status_code == 400
