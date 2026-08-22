from __future__ import annotations

from dataclasses import dataclass

from app.models.route_plan import (
    DEFAULT_DWELL_MINUTES,
    ItineraryStep,
    Plan,
    RoutePlanRequest,
    RoutePlanResponse,
)
from app.services.gtfs_engine import GTFSEngine, parse_gtfs_time_to_minutes
from app.services.ors_client import OrsRequestError, get_walking_route
from app.services.spot_repository import Spot, SpotNotFoundError, SpotRepository

NEAREST_STOP_RADIUS_M = 1500


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

    try:
        return await _compute_plan(base, targets, dwell_minutes, request, engine)
    except OrsRequestError:
        return RoutePlanResponse(
            status="error",
            ai_hint=(
                "徒歩ルートの計算に失敗しました。しばらくしてから再試行するよう"
                "ユーザーに伝えてください。"
            ),
            plans=[],
        )


async def _compute_plan(
    base: Spot,
    targets: list[Spot],
    dwell_minutes: list[int],
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


def _infeasible_response(num_targets: int) -> RoutePlanResponse:
    hint = (
        f"指定した時間内に{num_targets}件の訪問地を回れるバスが見つかりませんでした。"
        "訪問地の数を減らすか、出発時刻を早めるようユーザーに提案してください。"
    )
    return RoutePlanResponse(status="error", ai_hint=hint, plans=[])


def _format_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"
