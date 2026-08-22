from __future__ import annotations

from pathlib import Path

import pytest

import app.services.route_planner as route_planner
from app.models.route_plan import RoutePlanRequest
from app.services.gtfs_engine import GTFSEngine
from app.services.ors_client import OrsRequestError, WalkingRoute
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
        return WalkingRoute(duration_min=5, distance_m=400.0)

    monkeypatch.setattr(route_planner, "get_walking_route", fake)
    return fake


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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
