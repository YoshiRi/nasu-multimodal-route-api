from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gtfs_multifeed import GTFSEngine

from app.api.deps import get_gtfs_engine, get_spot_repository
from app.main import app
import app.services.route_planner as route_planner
from app.services.ors_client import RouteEstimate
from app.services.spot_repository import SpotRepository
from tests.test_suggester import SPOTS, SUGGEST_NET_DIR


@pytest.fixture
def client(tmp_path: Path):
    spots_file = tmp_path / "spots.json"
    spots_file.write_text(json.dumps(SPOTS), encoding="utf-8")

    # Other test modules install their own overrides at import time, so
    # restore whatever was there rather than clearing the slot.
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_gtfs_engine] = lambda: GTFSEngine(SUGGEST_NET_DIR)
    app.dependency_overrides[get_spot_repository] = lambda: SpotRepository(spots_file)
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def _hint(client, **overrides) -> str:
    body = client.post("/plan/suggest", json={**BASE_REQUEST, **overrides}).json()
    assert body["status"] == "error"
    assert body["candidates"] == []
    return body["ai_hint"]


@pytest.fixture
def fake_walking_route(monkeypatch: pytest.MonkeyPatch):
    """/plan/route-plan routes its walks for real; /plan/suggest never does."""

    async def fake(lat1, lng1, lat2, lng2, *, client=None):
        return RouteEstimate(duration_min=5, distance_m=400.0, path=[[lat1, lng1], [lat2, lng2]])

    monkeypatch.setattr(route_planner, "get_walking_route", fake)


BASE_REQUEST = {
    "base_location_id": "hub",
    "destination_spot_id": "end",
    "departure_time": "08:00",
    "departure_date": "2026-03-02",
    "arrive_by": "18:00",
    "spot_count": 2,
}


def test_suggest_returns_candidates(client) -> None:
    response = client.post("/plan/suggest", json=BASE_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["candidates"]
    assert body["ai_hint"]

    candidate = body["candidates"][0]
    # spot_ids is what the caller feeds straight back into /plan/route-plan.
    assert candidate["spot_ids"] == [stop["spot_id"] for stop in candidate["stops"]]
    assert candidate["destination_arrival_time"]
    assert candidate["total_duration_min"] > 0


def test_suggest_candidate_feeds_into_route_plan(client, fake_walking_route) -> None:
    candidate = client.post("/plan/suggest", json=BASE_REQUEST).json()["candidates"][0]

    # The documented hand-off: append the destination and build it for real.
    response = client.post(
        "/plan/route-plan",
        json={
            "base_location_id": "hub",
            "target_spot_ids": [
                {"spot_id": spot_id} for spot_id in [*candidate["spot_ids"], "end"]
            ],
            "departure_time": "08:00",
            "departure_date": "2026-03-02",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_suggest_reports_an_impossible_deadline_without_failing(client) -> None:
    response = client.post("/plan/suggest", json={**BASE_REQUEST, "arrive_by": "08:30"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["candidates"] == []
    assert body["ai_hint"]


def test_suggest_returns_400_for_unknown_spot(client) -> None:
    response = client.post(
        "/plan/suggest", json={**BASE_REQUEST, "destination_spot_id": "nope"}
    )

    assert response.status_code == 400


def test_suggest_rejects_out_of_range_spot_count(client) -> None:
    response = client.post("/plan/suggest", json={**BASE_REQUEST, "spot_count": 0})

    assert response.status_code == 422


def test_spots_endpoint_exposes_tags(client) -> None:
    body = client.get("/spots").json()

    by_id = {spot["spot_id"]: spot for spot in body}
    assert by_id["a"]["tags"] == ["牧場"]


# --- when the data, not the request, is the problem -------------------------


def test_date_outside_the_timetable_says_so(client) -> None:
    """Advice to visit fewer places can't fix a date with no timetable."""

    hint = _hint(client, departure_date="2030-01-07")

    assert "有効期間外" in hint
    assert "減ら" not in hint.split("。")[0]


def test_fixture_gtfs_is_named_as_the_cause(client) -> None:
    from app.core.config import get_settings
    from app.main import app
    from tests.conftest import settings_with

    app.dependency_overrides[get_settings] = lambda: settings_with(using_fixture_gtfs=True)

    hint = _hint(client, arrive_by="08:30")

    assert "テスト用" in hint
    assert "download_gtfs.py" in hint


def test_ordinary_failure_keeps_the_request_advice(client) -> None:
    hint = _hint(client, arrive_by="08:30")

    assert "有効期間外" not in hint
    assert "テスト用" not in hint
