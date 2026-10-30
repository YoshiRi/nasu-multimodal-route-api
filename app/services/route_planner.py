from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import inf

from gtfs_multifeed import GTFSEngine, haversine_distance_m, parse_gtfs_time_to_minutes

from app.models.route_plan import (
    ItineraryStep,
    Plan,
    RoutePlanRequest,
    RoutePlanResponse,
)
from app.services.opening_hours import VisitRefused, plan_visit
from app.services.ors_client import OrsRequestError, get_driving_route, get_walking_route
from app.services.spot_repository import Spot, SpotNotFoundError, SpotRepository
from app.services.travel_estimate import (
    MAX_DIRECT_WALKING_DISTANCE_M,
    estimate_leg_arrival,
    estimated_walk_minutes,
)
from app.services.taxi_fare import estimate_fare_jpy

# (distance_m) -> fare in JPY. The default is an unverified reference
# formula — see app/services/taxi_fare.py.
FareEstimator = Callable[[float], int]

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

# How many of those candidates _choose_stop_pair actually compares, per
# endpoint. Measured against real timetables: comparing 2 recovers 76 of
# the 84 minutes per leg that first-workable was losing, 4 recovers 81,
# and 8 recovers all 84 — but the cost per leg runs 191ms / 707ms / 2371ms.
# Four is where the curve flattens.
STOP_PAIR_SEARCH_LIMIT = 4

# Swap passes over the greedy order. Each pass is O(n^2) order evaluations,
# but they reuse a leg cache and never touch the routing service, so a
# couple of passes stay cheap. Two is enough to clean up the obvious
# greedy mistakes without the runtime creeping up.
MAX_ORDER_IMPROVEMENT_PASSES = 2



class RoutePlanInputError(ValueError):
    """Raised for malformed requests (unknown spot_id, etc.) — maps to HTTP 4xx."""


@dataclass
class _Cursor:
    minutes: int


async def build_route_plan(
    request: RoutePlanRequest,
    engine: GTFSEngine,
    spots: SpotRepository,
    fare_estimator: FareEstimator = estimate_fare_jpy,
) -> RoutePlanResponse:
    try:
        base = spots.get(request.base_location_id)
        remaining = [(spots.get(target.spot_id), target) for target in request.target_spot_ids]
    except SpotNotFoundError as exc:
        raise RoutePlanInputError(f"Unknown spot_id: {exc}") from exc

    if request.optimize_visit_order:
        remaining = _optimize_visit_order(base, remaining, request, engine)

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
            dwell_minutes = [spot.stay_minutes(target.dwell_minutes) for spot, target in remaining]
            required_arrival_minutes = [
                parse_gtfs_time_to_minutes(target.required_arrival_time)
                if target.required_arrival_time is not None
                else None
                for _, target in remaining
            ]

            result = await _compute_plan(
                base, targets, dwell_minutes, required_arrival_minutes, request, engine,
                fare_estimator,
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
    fare_estimator: FareEstimator,
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
            fare_estimator,
            request.max_transfers,
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
            try:
                visit = plan_visit(
                    destination.name, destination.hours, request.departure_date,
                    cursor.minutes, dwell_minutes[index],
                )
            except VisitRefused as refused:
                return _closed_response(str(refused))
            if visit.start_minutes > cursor.minutes:
                wait_step, cursor.minutes = _wait_step(
                    destination, visit.start_minutes, cursor.minutes, step_order,
                    reason="開館",
                )
                steps.append(wait_step)
                step_order += 1
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
    fare_estimator: FareEstimator,
    max_transfers: int,
) -> list[ItineraryStep] | None:
    origin_candidates = _nearest_stop_candidates(origin, engine)
    destination_candidates = _nearest_stop_candidates(destination, engine)

    if not origin_candidates or not destination_candidates:
        step, cursor.minutes = await _direct_step(
            origin, destination, cursor.minutes, step_order, fare_estimator
        )
        return [step]

    if origin_candidates[0]["stop_id"] == destination_candidates[0]["stop_id"]:
        step, cursor.minutes = await _direct_step(
            origin, destination, cursor.minutes, step_order, fare_estimator
        )
        return [step]

    chosen = _choose_stop_pair(
        origin, destination, origin_candidates, destination_candidates,
        cursor.minutes, engine, departure_date, transfer_buffer_minutes, max_transfers,
    )
    if chosen is None:
        return None
    origin_stop, destination_stop = chosen

    walk_to_stop, board_minutes = await _walking_step(
        origin.name, origin.lat, origin.lng,
        origin_stop["stop_name"], origin_stop["stop_lat"], origin_stop["stop_lon"],
        cursor.minutes, step_order,
    )
    journeys = engine.search_journeys(
        origin_stop_id=origin_stop["stop_id"],
        dest_stop_id=destination_stop["stop_id"],
        departure_time=_format_minutes(board_minutes + transfer_buffer_minutes),
        departure_date=departure_date,
        max_transfers=max_transfers,
    )
    if not journeys:
        # The real walk ran longer than the estimate and pushed us past the
        # last departure this pair had to offer.
        return None

    leg_steps, next_order, arrival_minutes = _ride_steps(
        journeys[0], origin_stop, destination_stop, board_minutes,
        engine, transfer_buffer_minutes, step_order,
    )
    leg_steps.insert(0, walk_to_stop)

    final_walk_step, cursor.minutes = await _walking_step(
        destination_stop["stop_name"], destination_stop["stop_lat"], destination_stop["stop_lon"],
        destination.name, destination.lat, destination.lng,
        arrival_minutes, next_order,
    )
    leg_steps.append(final_walk_step)
    return leg_steps


def _choose_stop_pair(
    origin: Spot,
    destination: Spot,
    origin_candidates: list[dict[str, str | float]],
    destination_candidates: list[dict[str, str | float]],
    depart_minutes: int,
    engine: GTFSEngine,
    departure_date: str,
    transfer_buffer_minutes: int,
    max_transfers: int,
) -> tuple[dict[str, str | float], dict[str, str | float]] | None:
    """Pick the pair of stops that gets to the destination soonest.

    Taking the first pair that had *any* service instead — the nearest stop
    with a bus, rather than the stop with the useful bus — cost 84 minutes
    per leg on average against real timetables, and over seven hours on the
    worst one, because the closest stop is often served by something that
    runs twice a day.

    Comparison runs on estimated walking times so it costs no routing calls
    (see travel_estimate); only the winning pair is then built for real.

    Two phases, because finding a *good* pair and finding *any* pair are
    different problems. Comparing every nearby pair is what buys the
    quality, but comparing all of them everywhere is too slow — so only the
    nearest STOP_PAIR_SEARCH_LIMIT are weighed against each other. If none
    of those connects at all, the remaining candidates are swept for
    anything workable: in this data the only stop that links two spots can
    sit sixth or seventh by distance, behind duplicate registrations of
    closer ones (see
    test_build_route_plan_finds_bus_via_stop_ranked_beyond_top_three).
    """

    def arrival_via(
        origin_stop: dict[str, str | float], destination_stop: dict[str, str | float]
    ) -> int | None:
        if origin_stop["stop_id"] == destination_stop["stop_id"]:
            return None
        board_minutes = depart_minutes + estimated_walk_minutes(
            haversine_distance_m(
                origin.lat, origin.lng, origin_stop["stop_lat"], origin_stop["stop_lon"]
            )
        )
        journeys = engine.search_journeys(
            origin_stop_id=origin_stop["stop_id"],
            dest_stop_id=destination_stop["stop_id"],
            departure_time=_format_minutes(board_minutes + transfer_buffer_minutes),
            departure_date=departure_date,
            max_transfers=max_transfers,
        )
        if not journeys:
            return None
        return parse_gtfs_time_to_minutes(journeys[0]["arrival_time"]) + estimated_walk_minutes(
            haversine_distance_m(
                destination_stop["stop_lat"],
                destination_stop["stop_lon"],
                destination.lat,
                destination.lng,
            )
        )

    best: tuple[int, dict, dict] | None = None
    for origin_stop in origin_candidates[:STOP_PAIR_SEARCH_LIMIT]:
        for destination_stop in destination_candidates[:STOP_PAIR_SEARCH_LIMIT]:
            arrival = arrival_via(origin_stop, destination_stop)
            if arrival is not None and (best is None or arrival < best[0]):
                best = (arrival, origin_stop, destination_stop)
    if best is not None:
        return best[1], best[2]

    for origin_stop in origin_candidates:
        for destination_stop in destination_candidates:
            if arrival_via(origin_stop, destination_stop) is not None:
                return origin_stop, destination_stop

    return None


def _ride_steps(
    journey: dict,
    origin_stop: dict[str, str | float],
    destination_stop: dict[str, str | float],
    board_minutes: int,
    engine: GTFSEngine,
    transfer_buffer_minutes: int,
    step_order: int,
) -> tuple[list[ItineraryStep], int, int]:
    """Turn a journey's rides (and any transfer between them) into steps.

    Returns the steps, the next free step_order, and the arrival time at the
    final alighting stop. The walk to the first stop and the walk from the
    last one are the caller's to add.
    """

    steps: list[ItineraryStep] = []
    next_order = step_order + 1
    ready_minutes = board_minutes
    rides = journey["legs"]
    transfers = journey["transfers"]

    for index, ride in enumerate(rides):
        board_stop = (
            origin_stop if index == 0 else _stop_or_fallback(engine, ride["origin_stop_id"])
        )
        alight_stop = (
            destination_stop
            if index == len(rides) - 1
            else _stop_or_fallback(engine, ride["destination_stop_id"])
        )
        ride_departure = parse_gtfs_time_to_minutes(ride["departure_time"])
        ride_arrival = parse_gtfs_time_to_minutes(ride["arrival_time"])

        if ride_departure > ready_minutes:
            steps.append(
                _bus_wait_step(
                    board_stop["stop_name"], board_stop["stop_lat"], board_stop["stop_lon"],
                    ready_minutes, ride_departure, next_order,
                )
            )
            next_order += 1

        steps.append(
            ItineraryStep(
                step_order=next_order,
                mode="bus",
                start_time=_format_minutes(ride_departure),
                end_time=_format_minutes(ride_arrival),
                title=f"バス（{ride['line_name']}）",
                details=(
                    f"{board_stop['stop_name']}から{alight_stop['stop_name']}まで"
                    f"「{ride['line_name']}」で{ride['duration_min']}分。"
                ),
                from_lat=board_stop["stop_lat"],
                from_lng=board_stop["stop_lon"],
                to_lat=alight_stop["stop_lat"],
                to_lng=alight_stop["stop_lon"],
                path=engine.bus_leg_path(
                    ride["trip_id"], ride["origin_stop_id"], ride["destination_stop_id"]
                )
                or [],
            )
        )
        next_order += 1
        ready_minutes = ride_arrival

        if index < len(transfers):
            transfer = transfers[index]
            # A same-stop change needs no walking step: the traveller is
            # free the moment they step off, and the wait before the next
            # bus covers the rest. Advancing the clock here anyway left a
            # gap in the itinerary between arriving and starting to wait.
            # The minimum connection time is already honoured — the journey
            # these steps describe was chosen with it applied.
            if transfer["from_stop_id"] != transfer["to_stop_id"]:
                steps.append(_transfer_walk_step(transfer, engine, ready_minutes, next_order))
                next_order += 1
                ready_minutes += transfer["transfer_minutes"]

    return steps, next_order, ready_minutes


def _stop_or_fallback(engine: GTFSEngine, stop_id: str) -> dict[str, str | float]:
    info = engine.stop_info(stop_id)
    return info or {"stop_id": stop_id, "stop_name": stop_id, "stop_lat": 0.0, "stop_lon": 0.0}


def _transfer_walk_step(
    transfer: dict, engine: GTFSEngine, start_minutes: int, step_order: int
) -> ItineraryStep:
    """Walk between two nearby stops to change buses.

    Uses the walking time the GTFS layer estimated from straight-line
    distance rather than calling the routing service: a plan can contain
    several transfers, and the shared ORS budget is better spent on the
    walks between spots and stops.
    """

    from_stop = _stop_or_fallback(engine, transfer["from_stop_id"])
    to_stop = _stop_or_fallback(engine, transfer["to_stop_id"])
    minutes = int(transfer["transfer_minutes"])
    return ItineraryStep(
        step_order=step_order,
        mode="walking",
        start_time=_format_minutes(start_minutes),
        end_time=_format_minutes(start_minutes + minutes),
        title="乗り換え徒歩",
        details=(
            f"{from_stop['stop_name']}から{to_stop['stop_name']}まで徒歩{minutes}分"
            f"（約{transfer['distance_m']:.0f}m）で乗り換えます。"
        ),
        from_lat=from_stop["stop_lat"],
        from_lng=from_stop["stop_lon"],
        to_lat=to_stop["stop_lat"],
        to_lng=to_stop["stop_lon"],
    )


def _nearest_stop_candidates(
    spot: Spot, engine: GTFSEngine, limit: int = MAX_STOP_CANDIDATES
) -> list[dict[str, str | float]]:
    nearby = engine.nearby_stops(lat=spot.lat, lng=spot.lng, radius_m=NEAREST_STOP_RADIUS_M)
    return nearby[:limit]


def _optimize_visit_order(
    base: Spot,
    remaining: list[tuple[Spot, object]],
    request: RoutePlanRequest,
    engine: GTFSEngine,
) -> list[tuple[Spot, object]]:
    """Reorder the intermediate targets to finish the trip sooner.

    Two passes. First a greedy one: from wherever the traveller is, and
    whenever they are free, go to whichever remaining spot can be reached
    earliest. That alone is myopic — it will happily strand a spot next
    door to an early stop until the end of the day — so a second pass
    tries swapping pairs and keeps whatever lands at the destination
    sooner.

    The final target is left where it is throughout: it is the trip's
    destination (see _compute_plan), not a stop along the way. Anything
    unreachable keeps its given order at the end, to be dropped or to fail
    the plan by the usual rules rather than silently reshuffled.

    All of this runs on estimated walking times (see
    travel_estimate), so it costs no routing-service calls no matter
    how many orders it tries.
    """

    # One intermediate plus a pinned destination has only one arrangement.
    if len(remaining) < 3:
        return remaining

    *middle, last = remaining
    cache: dict[tuple[str, str, int], int | None] = {}
    order = _greedy_order(base, middle, request, engine, cache)
    order = _improve_order(base, order, last, request, engine, cache)
    return [*order, last]


def _greedy_order(
    base: Spot,
    middle: list[tuple[Spot, object]],
    request: RoutePlanRequest,
    engine: GTFSEngine,
    cache: dict,
) -> list[tuple[Spot, object]]:
    cursor = parse_gtfs_time_to_minutes(request.departure_time)
    position = base
    ordered: list[tuple[Spot, object]] = []
    pending = list(middle)

    while pending:
        best: tuple[int, int] | None = None
        for index, (spot, target) in enumerate(pending):
            arrival = _arrival_at(position, spot, target, cursor, request, engine, cache)
            if arrival is not None and (best is None or arrival < best[0]):
                best = (arrival, index)

        if best is None:
            ordered.extend(pending)
            break

        arrival, index = best
        spot, target = pending.pop(index)
        ordered.append((spot, target))
        # A spot it can't stay at still gets placed; _improve_order and the
        # real build sort out whether the order works at all.
        cursor = _visit_end(spot, target, arrival, request) or arrival + spot.stay_minutes(
            target.dwell_minutes
        )
        position = spot

    return ordered


def _improve_order(
    base: Spot,
    order: list[tuple[Spot, object]],
    last: tuple[Spot, object],
    request: RoutePlanRequest,
    engine: GTFSEngine,
    cache: dict,
) -> list[tuple[Spot, object]]:
    """Swap pairs while that gets the traveller to the destination sooner.

    An order that cannot be completed at all scores as infinitely late, so
    this also serves to find a workable order when the greedy pass left one
    that isn't — which happens whenever the last leg home runs out of buses.
    """

    best_order = list(order)
    best_finish = _score(_finish_time(base, best_order, last, request, engine, cache))

    for _ in range(MAX_ORDER_IMPROVEMENT_PASSES):
        improved = False
        for i in range(len(best_order)):
            for j in range(i + 1, len(best_order)):
                candidate = list(best_order)
                candidate[i], candidate[j] = candidate[j], candidate[i]
                finish = _score(_finish_time(base, candidate, last, request, engine, cache))
                if finish < best_finish:
                    best_order, best_finish, improved = candidate, finish, True
        if not improved:
            break

    return best_order


def _score(finish_time: int | None) -> float:
    """An order that never finishes is worse than any order that does."""

    return inf if finish_time is None else float(finish_time)


def _finish_time(
    base: Spot,
    order: list[tuple[Spot, object]],
    last: tuple[Spot, object],
    request: RoutePlanRequest,
    engine: GTFSEngine,
    cache: dict,
) -> int | None:
    """When this order would reach the destination, or None if it can't."""

    cursor = parse_gtfs_time_to_minutes(request.departure_time)
    position = base

    for spot, target in order:
        arrival = _arrival_at(position, spot, target, cursor, request, engine, cache)
        if arrival is None:
            return None
        end = _visit_end(spot, target, arrival, request)
        if end is None:
            return None
        cursor = end
        position = spot

    # No stay at the destination: the trip ends on arrival.
    destination, target = last
    return _arrival_at(position, destination, target, cursor, request, engine, cache)


def _arrival_at(
    origin: Spot,
    destination: Spot,
    target: object,
    depart_minutes: int,
    request: RoutePlanRequest,
    engine: GTFSEngine,
    cache: dict,
) -> int | None:
    """Arrival at destination, honoring its reservation time, or None."""

    key = (origin.spot_id, destination.spot_id, depart_minutes)
    if key not in cache:
        cache[key] = estimate_leg_arrival(
            origin,
            destination,
            depart_minutes,
            engine,
            request.departure_date,
            request.transfer_buffer_minutes,
            request.max_transfers,
        )
    arrival = cache[key]
    if arrival is None:
        return None

    if target.required_arrival_time is not None:
        required = parse_gtfs_time_to_minutes(target.required_arrival_time)
        if arrival > required:
            return None
        return required
    return arrival


def _visit_end(spot: Spot, target: object, arrival: int, request: RoutePlanRequest) -> int | None:
    """When a stay arriving then would end, or None if its hours rule it out."""

    try:
        return plan_visit(
            spot.name, spot.hours, request.departure_date, arrival,
            spot.stay_minutes(target.dwell_minutes),
        ).end_minutes
    except VisitRefused:
        return None


async def _direct_step(
    origin: Spot,
    destination: Spot,
    start_minutes: int,
    step_order: int,
    fare_estimator: FareEstimator,
) -> tuple[ItineraryStep, int]:
    direct_distance_m = haversine_distance_m(origin.lat, origin.lng, destination.lat, destination.lng)
    if direct_distance_m > MAX_DIRECT_WALKING_DISTANCE_M:
        return await _taxi_step(
            origin.name, origin.lat, origin.lng,
            destination.name, destination.lat, destination.lng,
            start_minutes, step_order, fare_estimator,
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
        path=route.path,
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
    fare_estimator: FareEstimator,
) -> tuple[ItineraryStep, int]:
    route = await get_driving_route(from_lat, from_lng, to_lat, to_lng)
    cost_jpy = fare_estimator(route.distance_m)
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
        path=route.path,
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
    spot: Spot, required_minutes: int, start_minutes: int, step_order: int,
    reason: str = "集合",
) -> tuple[ItineraryStep, int]:
    step = ItineraryStep(
        step_order=step_order,
        mode="wait",
        start_time=_format_minutes(start_minutes),
        end_time=_format_minutes(required_minutes),
        title=f"{spot.name}で待機",
        details=(
            f"{spot.name}に{_format_minutes(start_minutes)}到着。"
            f"{reason}時刻{_format_minutes(required_minutes)}まで待機します。"
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


def _closed_response(reason: str) -> RoutePlanResponse:
    hint = (
        f"{reason}訪問順・日付・滞在時間（dwell_minutes）を見直すか、その訪問地を"
        "外すようユーザーに提案してください。営業情報は手で集めた概算です。"
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
