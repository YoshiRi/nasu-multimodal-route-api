"""Taxi for a leg the bus serves, but too late to be any use.

The first taxi rescue only stepped in where no bus ran at all. Real data
showed that misses the common case: in winter the bus from the hotel to
南ヶ丘牧場 still runs, it just arrives at 14:15 instead of 10:15, and
then nothing gets back to the station by 16:00. Asking "would one taxi
make this work?" got "no", when a taxi to the farm would have.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gtfs_multifeed import GTFSEngine

import app.services.route_planner as route_planner
from app.api.deps import get_gtfs_engine, get_spot_repository
from app.main import app
from app.services.ors_client import RouteEstimate
from app.services.spot_repository import SpotRepository
from app.services.suggester import suggest_itineraries

LATE_BUS_NET = Path(__file__).parent / "fixtures" / "late_bus_net"

# Hub -> A: after 07:00 the next bus is 14:00. A -> End: a bus at 11:00.
SPOTS = [
    {"spot_id": "hub", "name": "Hub", "lat": 36.0, "lng": 140.0, "tags": []},
    {"spot_id": "a", "name": "A Farm", "lat": 36.05, "lng": 140.0, "tags": ["牧場"]},
    {"spot_id": "end", "name": "End Station", "lat": 36.1, "lng": 140.0, "tags": []},
]
TRIP = dict(
    base_location_id="hub", destination_spot_id="end",
    departure_time="09:00", departure_date="2026-03-02", arrive_by="13:00",
    spot_count=1, must_include_spot_ids=["a"], dwell_minutes=60,
)


@pytest.fixture
def spots(tmp_path: Path) -> SpotRepository:
    path = tmp_path / "spots.json"
    path.write_text(json.dumps(SPOTS), encoding="utf-8")
    return SpotRepository(path)


def _suggest(spots: SpotRepository, **kwargs):
    return suggest_itineraries(
        spots=spots, engine=GTFSEngine(LATE_BUS_NET), **{**TRIP, **kwargs}
    )


def test_the_bus_alone_is_too_late(spots) -> None:
    assert _suggest(spots) == []


def test_a_taxi_past_the_late_bus_rescues_the_trip(spots) -> None:
    [candidate] = _suggest(spots, max_taxi_legs=1)

    assert candidate.taxi_legs == 1
    assert candidate.stops[0].by_taxi
    assert not candidate.destination_by_taxi
    # The 11:00 bus from A, 20 minutes, plus the estimator's short walk.
    assert 11 * 60 + 20 <= candidate.destination_arrival_minutes <= 11 * 60 + 25


def test_no_taxi_where_the_bus_already_makes_it(spots) -> None:
    # With all day to spare the 14:00 bus is fine, so no taxi is offered.
    late_enough = dict(departure_time="06:30", arrive_by="23:00")

    for candidate in _suggest(spots, max_taxi_legs=1, **late_enough):
        assert candidate.taxi_legs == 0


# --- building it for real ---------------------------------------------------


@pytest.fixture
def client(spots: SpotRepository, monkeypatch: pytest.MonkeyPatch):
    async def fake_walk(lat1, lng1, lat2, lng2, *, client=None):
        return RouteEstimate(duration_min=0, distance_m=0.0, path=[[lat1, lng1], [lat2, lng2]])

    async def fake_drive(lat1, lng1, lat2, lng2, *, client=None):
        return RouteEstimate(duration_min=15, distance_m=6000.0, path=[[lat1, lng1], [lat2, lng2]])

    monkeypatch.setattr(route_planner, "get_walking_route", fake_walk)
    monkeypatch.setattr(route_planner, "get_driving_route", fake_drive)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_gtfs_engine] = lambda: GTFSEngine(LATE_BUS_NET)
    app.dependency_overrides[get_spot_repository] = lambda: spots
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def test_suggest_marks_which_legs_are_taxi(client) -> None:
    body = client.post("/plan/suggest", json={**TRIP, "max_taxi_legs": 1}).json()

    [candidate] = body["candidates"]
    assert candidate["stops"][0]["by_taxi"] is True
    assert candidate["destination_by_taxi"] is False
    assert candidate["estimated_taxi_fare_jpy"] > 0


def test_route_plan_takes_the_taxi_it_is_told_to(client) -> None:
    body = client.post("/plan/route-plan", json={
        "base_location_id": "hub",
        "target_spot_ids": [
            {"spot_id": "a", "dwell_minutes": 60, "arrive_by_taxi": True},
            {"spot_id": "end"},
        ],
        "departure_time": "09:00", "departure_date": "2026-03-02",
    }).json()

    assert body["status"] == "success", body["ai_hint"]
    modes = [step["mode"] for step in body["plans"][0]["steps"]]
    assert modes[0] == "taxi"
    assert "bus" in modes  # the 11:00 from A
    assert body["plans"][0]["steps"][-1]["end_time"] == "11:20"


def test_route_plan_never_takes_a_taxi_on_its_own(client) -> None:
    # Same trip without the flag: the 14:00 bus, then nothing back.
    body = client.post("/plan/route-plan", json={
        "base_location_id": "hub",
        "target_spot_ids": [{"spot_id": "a", "dwell_minutes": 60}, {"spot_id": "end"}],
        "departure_time": "09:00", "departure_date": "2026-03-02",
    }).json()

    assert body["status"] == "error"
