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
        targets = [spots.get(target.spot_id) for target in request.target_spot_ids]
    except SpotNotFoundError as exc:
        raise RoutePlanInputError(f"Unknown spot_id: {exc}") from exc

    dwell_minutes = [
        target.dwell_minutes if target.dwell_minutes is not None else DEFAULT_DWELL_MINUTES
        for target in request.target_spot_ids
    ]
    required_arrival_minutes = [
        parse_gtfs_time_to_minutes(target.required_arrival_time)
        if target.required_arrival_time is not None
        else None
        for target in request.target_spot_ids
    ]

    try:
        return await _compute_plan(
            base, targets, dwell_minutes, required_arrival_minutes, request, engine
        )
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
    waypoints = [base, *targets, base]
    cursor = _Cursor(minutes=parse_gtfs_time_to_minutes(request.departure_time))
    steps: list[ItineraryStep] = []
    step_order = 1

    for index in range(len(waypoints) - 1):
        origin, destination = waypoints[index], waypoints[index + 1]
        leg_steps = await _build_leg(
            origin, destination, cursor, engine, request.departure_date, step_order
        )
        if leg_steps is None:
            return _infeasible_response(len(targets))

        steps.extend(leg_steps)
        step_order += len(leg_steps)

        is_last_leg = index == len(waypoints) - 2
        if not is_last_leg:
            # index-th leg arrives at targets[index] (waypoints[index + 1]).
            required = required_arrival_minutes[index]
            if required is not None:
                if cursor.minutes > required:
                    return _missed_reservation_response(destination.name, required)
                if cursor.minutes < required:
                    steps.append(_wait_step(destination, required, cursor, step_order))
                    step_order += 1

            steps.append(_stay_step(destination, dwell_minutes[index], cursor, step_order))
            step_order += 1

    title = f"{base.name}発 {' → '.join(target.name for target in targets)} 周遊プラン"
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
    step_order: int,
) -> list[ItineraryStep] | None:
    origin_stop = _nearest_stop(origin, engine)
    destination_stop = _nearest_stop(destination, engine)

    if (
        origin_stop is None
        or destination_stop is None
        or origin_stop["stop_id"] == destination_stop["stop_id"]
    ):
        direct_distance_m = haversine_distance_m(origin.lat, origin.lng, destination.lat, destination.lng)
        if direct_distance_m > MAX_DIRECT_WALKING_DISTANCE_M:
            return [
                await _taxi_step(
                    origin.name, origin.lat, origin.lng,
                    destination.name, destination.lat, destination.lng,
                    cursor, step_order,
                )
            ]
        return [
            await _walking_step(
                origin.name, origin.lat, origin.lng,
                destination.name, destination.lat, destination.lng,
                cursor, step_order,
            )
        ]

    steps = [
        await _walking_step(
            origin.name, origin.lat, origin.lng,
            origin_stop["stop_name"], origin_stop["stop_lat"], origin_stop["stop_lon"],
            cursor, step_order,
        )
    ]
    step_order += 1

    bus_options = engine.search_next_bus(
        origin_stop_id=origin_stop["stop_id"],
        dest_stop_id=destination_stop["stop_id"],
        departure_time=_format_minutes(cursor.minutes),
        departure_date=departure_date,
    )
    if not bus_options:
        return None

    bus = bus_options[0]
    start = cursor.minutes
    cursor.minutes = parse_gtfs_time_to_minutes(bus["arrival_time"])
    steps.append(
        ItineraryStep(
            step_order=step_order,
            mode="bus",
            start_time=_format_minutes(start),
            end_time=_format_minutes(cursor.minutes),
            title=f"バス（{bus['line_name']}）",
            details=(
                f"{origin_stop['stop_name']}から{destination_stop['stop_name']}まで"
                f"「{bus['line_name']}」で{bus['duration_min']}分。"
            ),
        )
    )
    step_order += 1

    steps.append(
        await _walking_step(
            destination_stop["stop_name"], destination_stop["stop_lat"], destination_stop["stop_lon"],
            destination.name, destination.lat, destination.lng,
            cursor, step_order,
        )
    )
    return steps


def _nearest_stop(spot: Spot, engine: GTFSEngine) -> dict[str, str | float] | None:
    nearby = engine.nearby_stops(lat=spot.lat, lng=spot.lng, radius_m=NEAREST_STOP_RADIUS_M)
    return nearby[0] if nearby else None


async def _walking_step(
    from_name: str,
    from_lat: float,
    from_lng: float,
    to_name: str,
    to_lat: float,
    to_lng: float,
    cursor: _Cursor,
    step_order: int,
) -> ItineraryStep:
    route = await get_walking_route(from_lat, from_lng, to_lat, to_lng)
    start = cursor.minutes
    cursor.minutes += route.duration_min
    return ItineraryStep(
        step_order=step_order,
        mode="walking",
        start_time=_format_minutes(start),
        end_time=_format_minutes(cursor.minutes),
        title="徒歩",
        details=f"{from_name}から{to_name}まで徒歩{route.duration_min}分（{route.distance_m:.0f}m）。",
    )


async def _taxi_step(
    from_name: str,
    from_lat: float,
    from_lng: float,
    to_name: str,
    to_lat: float,
    to_lng: float,
    cursor: _Cursor,
    step_order: int,
) -> ItineraryStep:
    route = await get_driving_route(from_lat, from_lng, to_lat, to_lng)
    cost_jpy = estimate_fare_jpy(route.distance_m)
    start = cursor.minutes
    cursor.minutes += route.duration_min
    return ItineraryStep(
        step_order=step_order,
        mode="taxi",
        start_time=_format_minutes(start),
        end_time=_format_minutes(cursor.minutes),
        title="タクシー",
        details=(
            f"{from_name}から{to_name}までタクシーで{route.duration_min}分"
            f"（{route.distance_m / 1000:.1f}km、概算{cost_jpy}円）。"
        ),
        cost_jpy=cost_jpy,
    )


def _stay_step(spot: Spot, dwell_minutes: int, cursor: _Cursor, step_order: int) -> ItineraryStep:
    start = cursor.minutes
    cursor.minutes += dwell_minutes
    return ItineraryStep(
        step_order=step_order,
        mode="stay",
        start_time=_format_minutes(start),
        end_time=_format_minutes(cursor.minutes),
        title=f"{spot.name}散策",
        details=f"{spot.name}に{dwell_minutes}分滞在します。",
    )


def _wait_step(spot: Spot, required_minutes: int, cursor: _Cursor, step_order: int) -> ItineraryStep:
    start = cursor.minutes
    cursor.minutes = required_minutes
    return ItineraryStep(
        step_order=step_order,
        mode="wait",
        start_time=_format_minutes(start),
        end_time=_format_minutes(cursor.minutes),
        title=f"{spot.name}で待機",
        details=(
            f"{spot.name}に{_format_minutes(start)}到着。"
            f"集合時刻{_format_minutes(required_minutes)}まで待機します。"
        ),
    )


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
