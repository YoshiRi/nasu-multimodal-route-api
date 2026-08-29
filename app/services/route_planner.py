from __future__ import annotations

from dataclasses import dataclass

from app.models.route_plan import (
    DEFAULT_DWELL_MINUTES,
    ItineraryStep,
    Plan,
    RoutePlanRequest,
    RoutePlanResponse,
)
from app.services.gtfs_engine import GTFSEngine, haversine_distance_m, parse_gtfs_time_to_minutes
from app.services.ors_client import OrsRequestError, get_driving_route, get_walking_route
from app.services.spot_repository import Spot, SpotNotFoundError, SpotRepository
from app.services.taxi_fare import estimate_fare_jpy

NEAREST_STOP_RADIUS_M = 1500

# When a spot has more than one GTFS stop nearby, the single closest one
# is not always usable (e.g. two operators serve the same area, and only
# one of them actually runs a trip to the other endpoint — see
# test_build_route_plan_finds_bus_via_a_farther_but_connected_stop). Real
# Nasu-area GTFS data also has the same physical stop registered multiple
# times (e.g. separate inbound/outbound entries a few meters apart), which
# eats candidate slots without adding real options — see
# test_build_route_plan_finds_bus_via_stop_ranked_beyond_top_three, found
# while dogfooding a real round-trip route where the connecting stop
# ("新屋") ranked 6th-7th by distance. Try up to this many nearest
# candidates per endpoint before giving up. Each untried candidate costs
# at most one extra ORS walking-route call (see _build_leg), so this is a
# deliberate trade-off against the shared ORS rate limit
# (ORS_MAX_REQUESTS_PER_MINUTE in ors_client.py).
MAX_STOP_CANDIDATES = 8

# Above this straight-line distance, a spot-to-spot "walking" leg (used
# when a spot has no GTFS stop within NEAREST_STOP_RADIUS_M) switches to
# taxi instead — walking the whole distance would otherwise be unrealistic
# (e.g. a multi-hour walk).
MAX_DIRECT_WALKING_DISTANCE_M = 3000


class RoutePlanInputError(ValueError):
    """Raised for malformed requests (unknown spot_id, etc.) — maps to HTTP 4xx."""


@dataclass
class _Cursor:
    minutes: int


async def build_route_plan(
    request: RoutePlanRequest,
    engine: GTFSEngine,
    spots: SpotRepository,
) -> RoutePlanResponse:
    try:
        base = spots.get(request.base_location_id)
        remaining = [(spots.get(target.spot_id), target) for target in request.target_spot_ids]
    except SpotNotFoundError as exc:
        raise RoutePlanInputError(f"Unknown spot_id: {exc}") from exc

    dropped_names: list[str] = []

    try:
        while True:
            if not remaining:
                hint = (
                    "必須でない訪問地をすべて除外しても行程が成立しませんでした。"
                    "出発時刻を早めるか、必須の訪問地を見直すようユーザーに提案してください。"
                )
                return RoutePlanResponse(status="error", ai_hint=hint, plans=[])

            targets = [spot for spot, _ in remaining]
            dwell_minutes = [
                target.dwell_minutes if target.dwell_minutes is not None else DEFAULT_DWELL_MINUTES
                for _, target in remaining
            ]
            required_arrival_minutes = [
                parse_gtfs_time_to_minutes(target.required_arrival_time)
                if target.required_arrival_time is not None
                else None
                for _, target in remaining
            ]

            result = await _compute_plan(
                base, targets, dwell_minutes, required_arrival_minutes, request, engine
            )

            if result.status == "success":
                if dropped_names:
                    result.ai_hint += (
                        f" （{'、'.join(dropped_names)}は必須でないため行程に含められず除外しました）"
                    )
                return result

            optional_indices = [i for i, (_, target) in enumerate(remaining) if not target.required]
            if not optional_indices:
                return result

            dropped_spot, _ = remaining.pop(optional_indices[0])
            dropped_names.append(dropped_spot.name)
    except OrsRequestError:
        return RoutePlanResponse(
            status="error",
            ai_hint=(
                "徒歩・タクシー区間の経路計算に失敗しました。しばらくしてから"
                "再試行するようユーザーに伝えてください。"
            ),
            plans=[],
        )


async def _compute_plan(
    base: Spot,
    targets: list[Spot],
    dwell_minutes: list[int],
    required_arrival_minutes: list[int | None],
    request: RoutePlanRequest,
    engine: GTFSEngine,
) -> RoutePlanResponse:
    # The trip ends at the last entry in `targets` — there is no automatic
    # return to `base`. A round trip is expressed by the caller listing
    # base_location_id again as the final target (see build_route_plan's
    # docstring-equivalent in the API docs).
    waypoints = [base, *targets]
    cursor = _Cursor(minutes=parse_gtfs_time_to_minutes(request.departure_time))
    steps: list[ItineraryStep] = []
    step_order = 1

    for index in range(len(waypoints) - 1):
        origin, destination = waypoints[index], waypoints[index + 1]
        leg_steps = await _build_leg(
            origin,
            destination,
            cursor,
            engine,
            request.departure_date,
            request.transfer_buffer_minutes,
            step_order,
        )
        if leg_steps is None:
            return _infeasible_response(len(targets))

        steps.extend(leg_steps)
        step_order += len(leg_steps)

        # index-th leg arrives at targets[index] (waypoints[index + 1]).
        # required_arrival_time is honored even on the final target, since
        # it may be the trip's real destination (e.g. a hotel check-in
        # deadline) rather than just "back at base".
        required = required_arrival_minutes[index]
        if required is not None:
            if cursor.minutes > required:
                return _missed_reservation_response(destination.name, required)
            if cursor.minutes < required:
                wait_step, cursor.minutes = _wait_step(destination, required, cursor.minutes, step_order)
                steps.append(wait_step)
                step_order += 1

        # No stay step after the final target — the trip ends there, so
        # there's nothing to wait out before moving on.
        is_last_leg = index == len(waypoints) - 2
        if not is_last_leg:
            stay_step, cursor.minutes = _stay_step(
                destination, dwell_minutes[index], cursor.minutes, step_order
            )
            steps.append(stay_step)
            step_order += 1

    title = f"{base.name}発 {' → '.join(target.name for target in targets)} 行程プラン"
    return RoutePlanResponse(
        status="success",
        ai_hint=f"「{title}」が成立しました。内容をユーザーに分かりやすく説明してください。",
        plans=[Plan(title=title, steps=steps)],
    )


async def _build_leg(
    origin: Spot,
    destination: Spot,
    cursor: _Cursor,
    engine: GTFSEngine,
    departure_date: str,
    transfer_buffer_minutes: int,
    step_order: int,
) -> list[ItineraryStep] | None:
    origin_candidates = _nearest_stop_candidates(origin, engine)
    destination_candidates = _nearest_stop_candidates(destination, engine)

    if not origin_candidates or not destination_candidates:
        step, cursor.minutes = await _direct_step(origin, destination, cursor.minutes, step_order)
        return [step]

    if origin_candidates[0]["stop_id"] == destination_candidates[0]["stop_id"]:
        step, cursor.minutes = await _direct_step(origin, destination, cursor.minutes, step_order)
        return [step]

    # Cache each origin candidate's walking step so it's computed at most
    # once even though it's evaluated against every destination candidate.
    origin_walks: dict[str, tuple[ItineraryStep, int]] = {}

    for origin_stop in origin_candidates:
        stop_id = origin_stop["stop_id"]
        if stop_id not in origin_walks:
            origin_walks[stop_id] = await _walking_step(
                origin.name, origin.lat, origin.lng,
                origin_stop["stop_name"], origin_stop["stop_lat"], origin_stop["stop_lon"],
                cursor.minutes, step_order,
            )
        walk_to_stop, board_minutes = origin_walks[stop_id]

        for destination_stop in destination_candidates:
            if origin_stop["stop_id"] == destination_stop["stop_id"]:
                continue

            bus_options = engine.search_next_bus(
                origin_stop_id=origin_stop["stop_id"],
                dest_stop_id=destination_stop["stop_id"],
                departure_time=_format_minutes(board_minutes + transfer_buffer_minutes),
                departure_date=departure_date,
            )
            if not bus_options:
                continue

            bus = bus_options[0]
            bus_departure_minutes = parse_gtfs_time_to_minutes(bus["departure_time"])
            bus_arrival_minutes = parse_gtfs_time_to_minutes(bus["arrival_time"])

            leg_steps = [walk_to_stop]
            next_order = step_order + 1
            if bus_departure_minutes > board_minutes:
                leg_steps.append(
                    _bus_wait_step(
                        origin_stop["stop_name"], origin_stop["stop_lat"], origin_stop["stop_lon"],
                        board_minutes, bus_departure_minutes, next_order,
                    )
                )
                next_order += 1

            bus_step = ItineraryStep(
                step_order=next_order,
                mode="bus",
                start_time=_format_minutes(bus_departure_minutes),
                end_time=_format_minutes(bus_arrival_minutes),
                title=f"バス（{bus['line_name']}）",
                details=(
                    f"{origin_stop['stop_name']}から{destination_stop['stop_name']}まで"
                    f"「{bus['line_name']}」で{bus['duration_min']}分。"
                ),
                from_lat=origin_stop["stop_lat"],
                from_lng=origin_stop["stop_lon"],
                to_lat=destination_stop["stop_lat"],
                to_lng=destination_stop["stop_lon"],
            )
            leg_steps.append(bus_step)
            next_order += 1

            final_walk_step, cursor.minutes = await _walking_step(
                destination_stop["stop_name"], destination_stop["stop_lat"], destination_stop["stop_lon"],
                destination.name, destination.lat, destination.lng,
                bus_arrival_minutes, next_order,
            )
            leg_steps.append(final_walk_step)
            return leg_steps

    return None


def _nearest_stop_candidates(spot: Spot, engine: GTFSEngine) -> list[dict[str, str | float]]:
    nearby = engine.nearby_stops(lat=spot.lat, lng=spot.lng, radius_m=NEAREST_STOP_RADIUS_M)
    return nearby[:MAX_STOP_CANDIDATES]


async def _direct_step(
    origin: Spot, destination: Spot, start_minutes: int, step_order: int
) -> tuple[ItineraryStep, int]:
    direct_distance_m = haversine_distance_m(origin.lat, origin.lng, destination.lat, destination.lng)
    if direct_distance_m > MAX_DIRECT_WALKING_DISTANCE_M:
        return await _taxi_step(
            origin.name, origin.lat, origin.lng,
            destination.name, destination.lat, destination.lng,
            start_minutes, step_order,
        )
    return await _walking_step(
        origin.name, origin.lat, origin.lng,
        destination.name, destination.lat, destination.lng,
        start_minutes, step_order,
    )


async def _walking_step(
    from_name: str,
    from_lat: float,
    from_lng: float,
    to_name: str,
    to_lat: float,
    to_lng: float,
    start_minutes: int,
    step_order: int,
) -> tuple[ItineraryStep, int]:
    route = await get_walking_route(from_lat, from_lng, to_lat, to_lng)
    end_minutes = start_minutes + route.duration_min
    step = ItineraryStep(
        step_order=step_order,
        mode="walking",
        start_time=_format_minutes(start_minutes),
        end_time=_format_minutes(end_minutes),
        title="徒歩",
        details=f"{from_name}から{to_name}まで徒歩{route.duration_min}分（{route.distance_m:.0f}m）。",
        from_lat=from_lat,
        from_lng=from_lng,
        to_lat=to_lat,
        to_lng=to_lng,
    )
    return step, end_minutes


async def _taxi_step(
    from_name: str,
    from_lat: float,
    from_lng: float,
    to_name: str,
    to_lat: float,
    to_lng: float,
    start_minutes: int,
    step_order: int,
) -> tuple[ItineraryStep, int]:
    route = await get_driving_route(from_lat, from_lng, to_lat, to_lng)
    cost_jpy = estimate_fare_jpy(route.distance_m)
    end_minutes = start_minutes + route.duration_min
    step = ItineraryStep(
        step_order=step_order,
        mode="taxi",
        start_time=_format_minutes(start_minutes),
        end_time=_format_minutes(end_minutes),
        title="タクシー",
        details=(
            f"{from_name}から{to_name}までタクシーで{route.duration_min}分"
            f"（{route.distance_m / 1000:.1f}km、概算{cost_jpy}円）。"
        ),
        cost_jpy=cost_jpy,
        from_lat=from_lat,
        from_lng=from_lng,
        to_lat=to_lat,
        to_lng=to_lng,
    )
    return step, end_minutes


def _bus_wait_step(
    stop_name: str,
    stop_lat: float,
    stop_lon: float,
    start_minutes: int,
    departure_minutes: int,
    step_order: int,
) -> ItineraryStep:
    return ItineraryStep(
        step_order=step_order,
        mode="wait",
        start_time=_format_minutes(start_minutes),
        end_time=_format_minutes(departure_minutes),
        title=f"{stop_name}でバス待ち",
        details=(
            f"{stop_name}に{_format_minutes(start_minutes)}到着。"
            f"バス発車（{_format_minutes(departure_minutes)}）まで待機します。"
        ),
        from_lat=stop_lat,
        from_lng=stop_lon,
        to_lat=stop_lat,
        to_lng=stop_lon,
    )


def _stay_step(
    spot: Spot, dwell_minutes: int, start_minutes: int, step_order: int
) -> tuple[ItineraryStep, int]:
    end_minutes = start_minutes + dwell_minutes
    step = ItineraryStep(
        step_order=step_order,
        mode="stay",
        start_time=_format_minutes(start_minutes),
        end_time=_format_minutes(end_minutes),
        title=f"{spot.name}散策",
        details=f"{spot.name}に{dwell_minutes}分滞在します。",
        from_lat=spot.lat,
        from_lng=spot.lng,
        to_lat=spot.lat,
        to_lng=spot.lng,
    )
    return step, end_minutes


def _wait_step(
    spot: Spot, required_minutes: int, start_minutes: int, step_order: int
) -> tuple[ItineraryStep, int]:
    step = ItineraryStep(
        step_order=step_order,
        mode="wait",
        start_time=_format_minutes(start_minutes),
        end_time=_format_minutes(required_minutes),
        title=f"{spot.name}で待機",
        details=(
            f"{spot.name}に{_format_minutes(start_minutes)}到着。"
            f"集合時刻{_format_minutes(required_minutes)}まで待機します。"
        ),
        from_lat=spot.lat,
        from_lng=spot.lng,
        to_lat=spot.lat,
        to_lng=spot.lng,
    )
    return step, required_minutes


def _infeasible_response(num_targets: int) -> RoutePlanResponse:
    hint = (
        f"指定した時間内に{num_targets}件の訪問地を回れるバスが見つかりませんでした。"
        "訪問地の数を減らすか、出発時刻を早めるようユーザーに提案してください。"
    )
    return RoutePlanResponse(status="error", ai_hint=hint, plans=[])


def _missed_reservation_response(spot_name: str, required_minutes: int) -> RoutePlanResponse:
    hint = (
        f"{spot_name}の集合時刻（{_format_minutes(required_minutes)}）に間に合いません。"
        "出発時刻を早めるか、訪問順序・訪問地の数を見直すようユーザーに提案してください。"
    )
    return RoutePlanResponse(status="error", ai_hint=hint, plans=[])


def _format_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"
