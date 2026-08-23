from __future__ import annotations

from pathlib import Path

import pytest

import app.services.route_planner as route_planner
from app.models.route_plan import RoutePlanRequest
from app.services.gtfs_engine import GTFSEngine
from app.services.ors_client import OrsRequestError, RouteEstimate
from app.services.route_planner import RoutePlanInputError, build_route_plan
from app.services.spot_repository import SpotRepository

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal_gtfs"
SPOTS_FILE = Path(__file__).parents[1] / "data" / "spots" / "nasu_spots.json"


@pytest.fixture
def engine() -> GTFSEngine:
    return GTFSEngine(FIXTURE_DIR)


@pytest.fixture
def spots() -> SpotRepository:
    return SpotRepository(SPOTS_FILE)


@pytest.fixture(autouse=True)
def fake_walking_route(monkeypatch: pytest.MonkeyPatch):
    async def fake(lat1, lng1, lat2, lng2, *, client=None):
        return RouteEstimate(duration_min=5, distance_m=400.0)

    monkeypatch.setattr(route_planner, "get_walking_route", fake)
    return fake


@pytest.fixture
def fake_driving_route(monkeypatch: pytest.MonkeyPatch):
    async def fake(lat1, lng1, lat2, lng2, *, client=None):
        return RouteEstimate(duration_min=15, distance_m=5560.0)

    monkeypatch.setattr(route_planner, "get_driving_route", fake)
    return fake


def _spot_repository_at(tmp_path: Path, spots: list[dict]) -> SpotRepository:
    spots_file = tmp_path / "spots.json"
    import json

    spots_file.write_text(json.dumps(spots), encoding="utf-8")
    return SpotRepository(spots_file)


@pytest.mark.anyio
async def test_build_route_plan_success(engine: GTFSEngine, spots: SpotRepository) -> None:
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    assert response.plans
    plan = response.plans[0]
    modes = [step.mode for step in plan.steps]
    assert modes == ["walking", "bus", "walking", "stay", "walking", "bus", "walking"]
    bus_steps = [step for step in plan.steps if step.mode == "bus"]
    assert bus_steps[0].title == "バス（那須線）"
    assert plan.steps[0].step_order == 1

    first_walk = plan.steps[0]
    assert (first_walk.from_lat, first_walk.from_lng) == pytest.approx((37.0950, 140.0010))
    assert (first_walk.to_lat, first_walk.to_lng) == pytest.approx((37.0950, 140.0010))
    assert (bus_steps[0].from_lat, bus_steps[0].from_lng) == pytest.approx((37.0950, 140.0010))
    assert (bus_steps[0].to_lat, bus_steps[0].to_lng) == pytest.approx((37.1240, 139.9850))
    stay_step = next(step for step in plan.steps if step.mode == "stay")
    assert (stay_step.from_lat, stay_step.from_lng) == pytest.approx((37.1240, 139.9850))
    assert (stay_step.to_lat, stay_step.to_lng) == pytest.approx((37.1240, 139.9850))
    assert plan.steps[-1].step_order == len(plan.steps)


@pytest.mark.anyio
async def test_build_route_plan_returns_error_status_when_no_bus_available(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    request = RoutePlanRequest(
        base_location_id="station_nasushiobara",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "error"
    assert response.plans == []
    assert "バス" in response.ai_hint


@pytest.mark.anyio
async def test_build_route_plan_raises_input_error_for_unknown_spot(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    request = RoutePlanRequest(
        base_location_id="does_not_exist",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    with pytest.raises(RoutePlanInputError):
        await build_route_plan(request, engine, spots)


@pytest.mark.anyio
async def test_build_route_plan_returns_error_status_when_ors_fails(
    monkeypatch: pytest.MonkeyPatch, engine: GTFSEngine, spots: SpotRepository
) -> None:
    async def failing(lat1, lng1, lat2, lng2, *, client=None):
        raise OrsRequestError("boom")

    monkeypatch.setattr(route_planner, "get_walking_route", failing)

    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "error"
    assert response.plans == []


@pytest.mark.anyio
async def test_build_route_plan_uses_custom_dwell_minutes(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway", "dwell_minutes": 90}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    stay_step = next(step for step in response.plans[0].steps if step.mode == "stay")
    assert stay_step.start_time == "10:40"
    assert stay_step.end_time == "12:10"
    assert "90分" in stay_step.details


@pytest.mark.anyio
async def test_build_route_plan_defaults_dwell_minutes_when_omitted(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    stay_step = next(step for step in response.plans[0].steps if step.mode == "stay")
    assert "60分" in stay_step.details


@pytest.mark.anyio
async def test_build_route_plan_inserts_wait_step_when_early_for_reservation(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    # Computed arrival at spot_nasu_ropeway is 10:40; require 10:50.
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway", "required_arrival_time": "10:50"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    modes = [step.mode for step in response.plans[0].steps]
    assert modes == [
        "walking", "bus", "walking", "wait", "stay", "walking", "bus", "walking",
    ]
    wait_step = next(step for step in response.plans[0].steps if step.mode == "wait")
    assert wait_step.start_time == "10:40"
    assert wait_step.end_time == "10:50"
    stay_step = next(step for step in response.plans[0].steps if step.mode == "stay")
    assert stay_step.start_time == "10:50"


@pytest.mark.anyio
async def test_build_route_plan_returns_error_when_reservation_cannot_be_met(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    # Computed arrival at spot_nasu_ropeway is 10:40; require 10:30 (too early to meet).
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway", "required_arrival_time": "10:30"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "error"
    assert response.plans == []
    assert "集合時刻" in response.ai_hint


@pytest.mark.anyio
async def test_build_route_plan_walks_when_no_nearby_stop_and_distance_is_short(
    tmp_path: Path, engine: GTFSEngine
) -> None:
    # No GTFS stop is within NEAREST_STOP_RADIUS_M of (0, 0); the two
    # spots are ~556m apart (below MAX_DIRECT_WALKING_DISTANCE_M).
    spots = _spot_repository_at(
        tmp_path,
        [
            {"spot_id": "far_base", "name": "Far Base", "lat": 0.0, "lng": 0.0},
            {"spot_id": "far_near_target", "name": "Near Target", "lat": 0.005, "lng": 0.0},
        ],
    )
    request = RoutePlanRequest(
        base_location_id="far_base",
        target_spot_ids=[{"spot_id": "far_near_target"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    modes = [step.mode for step in response.plans[0].steps]
    assert modes == ["walking", "stay", "walking"]
    assert all(step.cost_jpy is None for step in response.plans[0].steps)


@pytest.mark.anyio
async def test_build_route_plan_uses_taxi_when_no_nearby_stop_and_distance_is_long(
    tmp_path: Path, engine: GTFSEngine, fake_driving_route
) -> None:
    # ~5.6km apart, above MAX_DIRECT_WALKING_DISTANCE_M — should use taxi.
    spots = _spot_repository_at(
        tmp_path,
        [
            {"spot_id": "far_base", "name": "Far Base", "lat": 0.0, "lng": 0.0},
            {"spot_id": "far_target", "name": "Far Target", "lat": 0.05, "lng": 0.0},
        ],
    )
    request = RoutePlanRequest(
        base_location_id="far_base",
        target_spot_ids=[{"spot_id": "far_target"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    modes = [step.mode for step in response.plans[0].steps]
    assert modes == ["taxi", "stay", "taxi"]
    taxi_step = response.plans[0].steps[0]
    assert taxi_step.cost_jpy is not None
    assert taxi_step.cost_jpy > 0
    assert "円" in taxi_step.details


MULTI_OPERATOR_NEAR_DIR = Path(__file__).parent / "fixtures" / "multi_operator_near"
MULTI_OPERATOR_FAR_DIR = Path(__file__).parent / "fixtures" / "multi_operator_far"


@pytest.mark.anyio
async def test_build_route_plan_finds_bus_via_a_farther_but_connected_stop(tmp_path: Path) -> None:
    # Real-world shape of a bug found via manual testing: two operators
    # each have a stop near the destination. "near"'s stop is 1.1m away
    # but belongs to an isolated route; "far"'s stop is 11.1m away (still
    # much closer than NEAREST_STOP_RADIUS_M) and is the one actually
    # served by a trip from the origin. Picking only the single nearest
    # stop per spot (ignoring the farther-but-connected one) makes this
    # leg wrongly infeasible.
    engine = GTFSEngine.from_feeds(
        {"near": MULTI_OPERATOR_NEAR_DIR, "far": MULTI_OPERATOR_FAR_DIR}
    )
    spots = _spot_repository_at(
        tmp_path,
        [
            {"spot_id": "origin", "name": "Origin", "lat": 0.0, "lng": 0.0},
            {"spot_id": "destination", "name": "Destination", "lat": 0.01, "lng": 0.0},
        ],
    )
    request = RoutePlanRequest(
        base_location_id="origin",
        target_spot_ids=[{"spot_id": "destination"}],
        departure_time="08:00",
        departure_date="2026-03-02",  # a Monday within both fixtures' calendars
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success", response.ai_hint
    bus_steps = [step for step in response.plans[0].steps if step.mode == "bus"]
    assert bus_steps
    assert bus_steps[0].details.startswith("Origin Stop")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
