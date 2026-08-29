from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.services.route_planner as route_planner
from app.api.deps import get_gtfs_engine, get_spot_repository
from app.main import app
from app.services.gtfs_engine import GTFSEngine
from app.services.ors_client import RouteEstimate
from app.services.spot_repository import SpotRepository

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"
SPOTS_FILE = Path(__file__).parents[1] / "data" / "spots" / "nasu_spots.json"

app.dependency_overrides[get_gtfs_engine] = lambda: GTFSEngine(FIXTURE_DIR)
app.dependency_overrides[get_spot_repository] = lambda: SpotRepository(SPOTS_FILE)

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_walking_route(monkeypatch: pytest.MonkeyPatch):
    async def fake(lat1, lng1, lat2, lng2, *, client=None):
        return RouteEstimate(duration_min=5, distance_m=400.0)

    monkeypatch.setattr(route_planner, "get_walking_route", fake)


def test_route_plan_success() -> None:
    # Round trip: base_location_id listed again as the final target.
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "spot_nasu_yumoto_onsen",
            "target_spot_ids": [
                {"spot_id": "spot_nasu_ropeway"},
                {"spot_id": "spot_nasu_yumoto_onsen"},
            ],
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert len(body["plans"]) == 1
    assert [step["mode"] for step in body["plans"][0]["steps"]] == [
        "walking", "wait", "bus", "walking", "stay", "walking", "wait", "bus", "walking",
    ]


def test_route_plan_one_way_ends_at_last_target() -> None:
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "spot_nasu_yumoto_onsen",
            "target_spot_ids": [{"spot_id": "spot_nasu_ropeway"}],
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    modes = [step["mode"] for step in body["plans"][0]["steps"]]
    assert modes == ["walking", "wait", "bus", "walking"]
    assert "stay" not in modes


def test_route_plan_returns_200_with_error_status_when_infeasible() -> None:
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "station_nasushiobara",
            "target_spot_ids": [{"spot_id": "spot_nasu_ropeway"}],
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["plans"] == []
    assert body["ai_hint"]


def test_route_plan_returns_400_for_unknown_spot() -> None:
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "does_not_exist",
            "target_spot_ids": [{"spot_id": "spot_nasu_ropeway"}],
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 400


def test_route_plan_honors_custom_dwell_minutes() -> None:
    # Round trip so spot_nasu_ropeway isn't the final target.
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "spot_nasu_yumoto_onsen",
            "target_spot_ids": [
                {"spot_id": "spot_nasu_ropeway", "dwell_minutes": 90},
                {"spot_id": "spot_nasu_yumoto_onsen"},
            ],
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 200
    body = response.json()
    stay_step = next(step for step in body["plans"][0]["steps"] if step["mode"] == "stay")
    assert "90分" in stay_step["details"]


def test_route_plan_rejects_non_positive_dwell_minutes() -> None:
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "spot_nasu_yumoto_onsen",
            "target_spot_ids": [{"spot_id": "spot_nasu_ropeway", "dwell_minutes": 0}],
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 422


def test_route_plan_inserts_wait_step_for_reservation() -> None:
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "spot_nasu_yumoto_onsen",
            "target_spot_ids": [
                {"spot_id": "spot_nasu_ropeway", "required_arrival_time": "10:50"},
                {"spot_id": "spot_nasu_yumoto_onsen"},
            ],
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert [step["mode"] for step in body["plans"][0]["steps"]] == [
        "walking", "wait", "bus", "walking", "wait", "stay", "walking", "wait", "bus", "walking",
    ]


def test_route_plan_returns_error_when_reservation_cannot_be_met() -> None:
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "spot_nasu_yumoto_onsen",
            "target_spot_ids": [
                {"spot_id": "spot_nasu_ropeway", "required_arrival_time": "10:30"}
            ],
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["plans"] == []
    assert "集合時刻" in body["ai_hint"]


def test_route_plan_rejects_empty_target_list() -> None:
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "spot_nasu_yumoto_onsen",
            "target_spot_ids": [],
            "departure_time": "10:00",
            "departure_date": "2026-08-17",
        },
    )

    assert response.status_code == 422
