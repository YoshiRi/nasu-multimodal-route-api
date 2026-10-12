from __future__ import annotations

from pathlib import Path

import pytest

import app.services.route_planner as route_planner
from app.models.route_plan import RoutePlanRequest
from gtfs_multifeed import GTFSEngine
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
        return RouteEstimate(duration_min=5, distance_m=400.0, path=[[lat1, lng1], [lat2, lng2]])

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
    # A round trip: base_location_id listed again as the final target.
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[
            {"spot_id": "spot_nasu_ropeway"},
            {"spot_id": "spot_nasu_yumoto_onsen"},
        ],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    assert response.plans
    plan = response.plans[0]
    modes = [step.mode for step in plan.steps]
    # A real wait-for-the-bus gap exists both ways (walk arrives before the
    # bus departs), so each bus leg is walking -> wait -> bus -> walking.
    assert modes == [
        "walking", "wait", "bus", "walking", "stay", "walking", "wait", "bus", "walking",
    ]
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

    # The bus step's path is the actual GTFS shape, not a straight line.
    assert bus_steps[0].path == [
        [37.0950, 140.0010], [37.1100, 139.9900], [37.1240, 139.9850],
    ]
    # The walking step's path comes straight through from the ORS response.
    assert first_walk.path == [[37.0950, 140.0010], [37.0950, 140.0010]]


@pytest.mark.anyio
async def test_build_route_plan_one_way_ends_at_last_target(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    # No return leg, and no stay step at the final (only) target since the
    # trip ends there.
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway"}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    modes = [step.mode for step in response.plans[0].steps]
    assert modes == ["walking", "wait", "bus", "walking"]
    assert "stay" not in modes


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
    # Round trip so spot_nasu_ropeway isn't the final target (the final
    # target gets no stay step, since the trip ends there).
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[
            {"spot_id": "spot_nasu_ropeway", "dwell_minutes": 90},
            {"spot_id": "spot_nasu_yumoto_onsen"},
        ],
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
        target_spot_ids=[
            {"spot_id": "spot_nasu_ropeway"},
            {"spot_id": "spot_nasu_yumoto_onsen"},
        ],
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
        target_spot_ids=[
            {"spot_id": "spot_nasu_ropeway", "required_arrival_time": "10:50"},
            {"spot_id": "spot_nasu_yumoto_onsen"},
        ],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    modes = [step.mode for step in response.plans[0].steps]
    # "wait" appears twice: once for the bus-boarding gap, once for the
    # required_arrival_time reservation.
    assert modes == [
        "walking", "wait", "bus", "walking", "wait", "stay", "walking", "wait", "bus", "walking",
    ]
    reservation_wait = next(
        step for step in response.plans[0].steps if step.mode == "wait" and "集合時刻" in step.details
    )
    assert reservation_wait.start_time == "10:40"
    assert reservation_wait.end_time == "10:50"
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
async def test_build_route_plan_honors_required_arrival_time_on_round_trip_return(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    # A round trip (base listed again as the final target) can still carry
    # its own required_arrival_time, e.g. "must be back by 12:35" — this is
    # honored even though it's the last leg, unlike dwell_minutes (which is
    # ignored on the final target since the trip ends there).
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[
            {"spot_id": "spot_nasu_ropeway"},
            {"spot_id": "spot_nasu_yumoto_onsen", "required_arrival_time": "12:35"},
        ],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success", response.ai_hint
    steps = response.plans[0].steps
    assert steps[-1].mode == "wait"
    assert "集合時刻" in steps[-1].details
    assert steps[-1].end_time == "12:35"
    # spot_nasu_ropeway (not the final target) still gets its stay step;
    # only the final target (the return to base) has none.
    assert [step.mode for step in steps].count("stay") == 1


@pytest.mark.anyio
async def test_build_route_plan_errors_when_round_trip_return_deadline_missed(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    # Computed return to base is 12:25; requiring 12:15 is too early to meet.
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[
            {"spot_id": "spot_nasu_ropeway"},
            {"spot_id": "spot_nasu_yumoto_onsen", "required_arrival_time": "12:15"},
        ],
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
        target_spot_ids=[{"spot_id": "far_near_target"}, {"spot_id": "far_base"}],
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
        target_spot_ids=[{"spot_id": "far_target"}, {"spot_id": "far_base"}],
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


@pytest.mark.anyio
async def test_build_route_plan_uses_supplied_fare_estimator(
    tmp_path: Path, engine: GTFSEngine, fake_driving_route
) -> None:
    # The bundled fare formula is an unverified reference value, so callers
    # must be able to substitute real local rates.
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

    response = await build_route_plan(
        request, engine, spots, fare_estimator=lambda distance_m: 12345
    )

    assert response.status == "success"
    taxi_step = next(step for step in response.plans[0].steps if step.mode == "taxi")
    assert taxi_step.cost_jpy == 12345
    assert "12345円" in taxi_step.details


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


MANY_DECOYS_NEAR_DIR = Path(__file__).parent / "fixtures" / "many_decoys_near"
MANY_DECOYS_REAL_DIR = Path(__file__).parent / "fixtures" / "many_decoys_real"


@pytest.mark.anyio
async def test_build_route_plan_finds_bus_via_stop_ranked_beyond_top_three(tmp_path: Path) -> None:
    # Real-world shape of a bug found while dogfooding real Nasu-area GTFS
    # data: the destination has 4 unconnected stops slightly closer than
    # the one actually served by a trip from the origin, so the connected
    # stop ranks 5th by distance. MAX_STOP_CANDIDATES must reach far enough
    # to still find it, not just tries a couple of near-tied nearest stops.
    engine = GTFSEngine.from_feeds(
        {"near": MANY_DECOYS_NEAR_DIR, "real": MANY_DECOYS_REAL_DIR}
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


@pytest.mark.anyio
async def test_build_route_plan_drops_optional_spot_that_does_not_fit(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    # station_nasushiobara (stop_station) has no bus connection to the
    # tourist route in this fixture; marking it non-required should drop
    # it rather than fail the whole plan.
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[
            {"spot_id": "spot_nasu_ropeway"},
            {"spot_id": "station_nasushiobara", "required": False},
        ],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    assert "那須塩原駅" in response.ai_hint
    plan_title = response.plans[0].title
    assert "那須ロープウェイ" in plan_title
    assert "那須塩原駅" not in plan_title


@pytest.mark.anyio
async def test_build_route_plan_returns_error_when_all_optional_spots_dropped(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[{"spot_id": "station_nasushiobara", "required": False}],
        departure_time="10:00",
        departure_date="2026-08-17",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "error"
    assert response.plans == []
    assert "必須でない訪問地をすべて除外" in response.ai_hint


@pytest.mark.anyio
async def test_build_route_plan_applies_transfer_buffer_minutes(
    engine: GTFSEngine, spots: SpotRepository
) -> None:
    # Without a buffer, trip_1 (departs 10:15) is boarded. A 15-minute
    # buffer pushes the effective search time to 10:20, so trip_1 no
    # longer qualifies and trip_2 (departs 10:50) is boarded instead.
    request = RoutePlanRequest(
        base_location_id="spot_nasu_yumoto_onsen",
        target_spot_ids=[{"spot_id": "spot_nasu_ropeway"}],
        departure_time="10:00",
        departure_date="2026-08-17",
        transfer_buffer_minutes=15,
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success"
    bus_steps = [step for step in response.plans[0].steps if step.mode == "bus"]
    assert bus_steps[0].start_time == "10:50"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


TRANSFER_NET_DIR = Path(__file__).parent / "fixtures" / "transfer_net"


@pytest.mark.anyio
async def test_build_route_plan_builds_an_itinerary_that_changes_buses(
    tmp_path: Path,
) -> None:
    # A -> C needs one change at the B Junction pair; no single vehicle
    # serves both. Spots sit right on top of their stops so the walking
    # legs stay trivial and the bus/transfer structure is what's asserted.
    engine = GTFSEngine(TRANSFER_NET_DIR)
    spots = _spot_repository_at(
        tmp_path,
        [
            {"spot_id": "a", "name": "A", "lat": 35.985, "lng": 140.0},
            {"spot_id": "c", "name": "C", "lat": 36.035, "lng": 140.0},
        ],
    )
    request = RoutePlanRequest(
        base_location_id="a",
        target_spot_ids=[{"spot_id": "c"}],
        departure_time="08:00",
        departure_date="2026-03-02",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success", response.ai_hint
    steps = response.plans[0].steps
    bus_steps = [step for step in steps if step.mode == "bus"]
    assert [step.title for step in bus_steps] == ["バス（Line 1）", "バス（Line 2）"]

    # The walk between the two B stops is its own step, not folded away.
    transfer_walk = next(step for step in steps if step.title == "乗り換え徒歩")
    assert "B Junction" in transfer_walk.details

    # Second bus departs after the first arrives plus the transfer walk.
    assert bus_steps[0].end_time == "09:20"
    assert transfer_walk.start_time == "09:20"
    assert bus_steps[1].start_time == "09:30"
    assert steps[-1].end_time >= "09:50"
    assert [step.step_order for step in steps] == list(range(1, len(steps) + 1))


@pytest.mark.anyio
async def test_build_route_plan_max_transfers_zero_rejects_the_connection(
    tmp_path: Path,
) -> None:
    engine = GTFSEngine(TRANSFER_NET_DIR)
    spots = _spot_repository_at(
        tmp_path,
        [
            {"spot_id": "a", "name": "A", "lat": 35.985, "lng": 140.0},
            {"spot_id": "c", "name": "C", "lat": 36.035, "lng": 140.0},
        ],
    )
    request = RoutePlanRequest(
        base_location_id="a",
        target_spot_ids=[{"spot_id": "c"}],
        departure_time="08:00",
        departure_date="2026-03-02",
        max_transfers=0,
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "error"
    assert response.plans == []


ORDER_NET_DIR = Path(__file__).parent / "fixtures" / "order_net"
ORDER_NET_SPOTS = [
    {"spot_id": "hub", "name": "Hub", "lat": 36.0, "lng": 140.0},
    {"spot_id": "p", "name": "P", "lat": 36.1, "lng": 140.0},
    {"spot_id": "q", "name": "Q", "lat": 36.2, "lng": 140.0},
]


def _order_net_request(tmp_path: Path, target_ids: list[str], **kwargs) -> RoutePlanRequest:
    return RoutePlanRequest(
        base_location_id="hub",
        target_spot_ids=[{"spot_id": spot_id, "dwell_minutes": 60} for spot_id in target_ids],
        departure_time="08:00",
        departure_date="2026-03-02",
        **kwargs,
    )


@pytest.mark.anyio
async def test_build_route_plan_reorders_targets_to_find_a_workable_trip(
    tmp_path: Path,
) -> None:
    # Buses only run Hub->P->Q->Hub. Asking for Q first strands the trip:
    # the only Hub->Q service is late, and nothing runs Q->P at all.
    engine = GTFSEngine(ORDER_NET_DIR)
    spots = _spot_repository_at(tmp_path, ORDER_NET_SPOTS)

    as_given = await build_route_plan(
        _order_net_request(tmp_path, ["q", "p", "hub"]), engine, spots
    )
    assert as_given.status == "error"

    reordered = await build_route_plan(
        _order_net_request(tmp_path, ["q", "p", "hub"], optimize_visit_order=True),
        engine,
        spots,
    )

    assert reordered.status == "success", reordered.ai_hint
    assert "Hub発 P → Q → Hub" in reordered.plans[0].title


@pytest.mark.anyio
async def test_build_route_plan_keeps_the_destination_last_when_reordering(
    tmp_path: Path,
) -> None:
    # The final target is where the trip ends, not a stop to shuffle: Hub
    # has to stay last even though visiting it earlier would be quicker.
    engine = GTFSEngine(ORDER_NET_DIR)
    spots = _spot_repository_at(tmp_path, ORDER_NET_SPOTS)

    response = await build_route_plan(
        _order_net_request(tmp_path, ["q", "p", "hub"], optimize_visit_order=True),
        engine,
        spots,
    )

    assert response.plans[0].title.endswith("Hub 行程プラン")


@pytest.mark.anyio
async def test_build_route_plan_leaves_order_alone_by_default(tmp_path: Path) -> None:
    engine = GTFSEngine(ORDER_NET_DIR)
    spots = _spot_repository_at(tmp_path, ORDER_NET_SPOTS)

    response = await build_route_plan(
        _order_net_request(tmp_path, ["p", "q", "hub"]), engine, spots
    )

    # The given order already works, so it is used untouched.
    assert response.status == "success", response.ai_hint
    assert "Hub発 P → Q → Hub" in response.plans[0].title


@pytest.mark.anyio
async def test_reordering_will_not_break_a_reservation_time(tmp_path: Path) -> None:
    # Q can only be reached by 10:50 via P first; a 10:00 deadline there is
    # unmeetable in any order, and reordering must not pretend otherwise.
    engine = GTFSEngine(ORDER_NET_DIR)
    spots = _spot_repository_at(tmp_path, ORDER_NET_SPOTS)
    request = RoutePlanRequest(
        base_location_id="hub",
        target_spot_ids=[
            {"spot_id": "p", "dwell_minutes": 60},
            {"spot_id": "q", "dwell_minutes": 60, "required_arrival_time": "10:00"},
            {"spot_id": "hub"},
        ],
        departure_time="08:00",
        departure_date="2026-03-02",
        optimize_visit_order=True,
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "error"
    assert response.plans == []


SLOW_NEAREST_NET_DIR = Path(__file__).parent / "fixtures" / "slow_nearest_net"


@pytest.mark.anyio
async def test_build_leg_prefers_a_farther_stop_that_arrives_sooner(
    tmp_path: Path,
) -> None:
    # The destination's nearest stop is served once a day and gets there at
    # 18:00; a stop 111m further along has a fast service arriving 09:30.
    # Taking the first stop that has *any* bus would pick the slow one.
    engine = GTFSEngine(SLOW_NEAREST_NET_DIR)
    spots = _spot_repository_at(
        tmp_path,
        [
            {"spot_id": "origin", "name": "Origin", "lat": 36.0, "lng": 140.0},
            {"spot_id": "dest", "name": "Destination", "lat": 36.03, "lng": 140.0},
        ],
    )
    request = RoutePlanRequest(
        base_location_id="origin",
        target_spot_ids=[{"spot_id": "dest"}],
        departure_time="09:00",
        departure_date="2026-03-02",
    )

    response = await build_route_plan(request, engine, spots)

    assert response.status == "success", response.ai_hint
    bus_step = next(step for step in response.plans[0].steps if step.mode == "bus")
    assert bus_step.title == "バス（Fast）"
    assert response.plans[0].steps[-1].end_time < "10:00"
